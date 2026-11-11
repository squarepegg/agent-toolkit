#!/usr/bin/env python3
"""Deterministic decision eval.

Runs every check of every option of a decision file and prints a markdown table. A check is a TLC run, a data
query, or the simplicity lint. Nothing here is judgement: a check passes or fails against an expectation written
down in the decision file before the run.

Usage: eval.py <decision.yaml> [--draft] [--out FILE]

Exit code: 0 = DECISIVE verdict; 1 = NOT DECISIVE (no valid option, several, untested, draft...); 2 = error (bad
decision file, not registered, missing jar or java, internal error).

Registration = the run happens on a SNAPSHOT of git HEAD: the decision file must equal its committed version, and
every spec, input and evidence file must be committed. Files are read from git as raw blobs (no attributes, filters
or symlinks), so uncommitted edits, untracked and ignored files cannot affect the verdict. Data commands run in a
separate throwaway copy. The snapshot holds the decision file's directory plus every spec, `inputs:` entry and
evidence file. `--draft` runs on the working tree instead; the output is then marked DRAFT and is never decisive.

TLC checks never read a cfg you wrote. Eval writes it from the check: init/next, constants (booleans, integers,
lists of integers) and exactly one INVARIANT.

A MUTANT is a separate spec file: the real spec with a small edit (at most MAX_MUTANT_DIFF changed lines, not
counting the MODULE line), checked with exactly the same init/next, constants, invariant and deadlock flag as the
real check. TLC must violate that invariant on it. The edit must not touch the invariant's own definition.

Environment: TLA2TOOLS_JAR (a file; needed only if there is a tlc check), JOBS (TLC workers, default 4),
EVAL_TLC_TIMEOUT (seconds per TLC run, default 900). `java` is taken from PATH.
"""
import argparse
import atexit
import difflib
import math
import operator
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile

import yaml

OPS = {"<=": operator.le, "<": operator.lt, ">=": operator.ge, ">": operator.gt, "==": operator.eq}
CHANNEL = "channel"  # objects with this name are message channels: sending is not a second object change
MAX_MUTANT_DIFF = 6
MAX_INT = 2**31 - 1
ID = re.compile(r"[A-Za-z0-9_.\-]+")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
TOP_KEYS = {"id", "question", "criteria", "options", "reversible", "owner", "root", "inputs", "falsifier",
            "objects", "ignore", "attack_report", "blind_check"}
CRITERION_KEYS = {"id", "text", "kind"}
OPTION_KEYS = {"id", "checks", "lint"}
OK_TLC_KEYS = {"spec", "init", "next", "constants", "invariant", "deadlock", "expect"}
MUTANT_TLC_KEYS = {"spec", "invariant"}
DATA_KEYS = {"name", "cmd", "regex", "op", "value", "timeout"}
CHECK_KEYS = {"criterion", "mutant_of", "tlc", "data", "declared"}

TEMPS = []


def mktemp(prefix):
    d = tempfile.mkdtemp(prefix=prefix)
    TEMPS.append(d)
    return d


atexit.register(lambda: [shutil.rmtree(d, ignore_errors=True) for d in TEMPS])


def fail(msg):
    print(f"eval: {msg}", file=sys.stderr)
    sys.exit(2)


def cell(x):
    """Make arbitrary text safe inside a markdown table cell or bullet: no pipes, backticks or line breaks."""
    return " ".join(str(x).replace("|", "/").replace("`", "'").split())


def git(args, cwd, binary=False):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=not binary)


def resolve(base, p):
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(base, p))


def same_file(a, b):
    if os.path.realpath(a) == os.path.realpath(b):
        return True
    return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)


# ---------------------------------------------------------------- registration and snapshot


def real(p):
    """Absolute path with directory symlinks resolved, final component kept (so a symlink file is still visible)."""
    return os.path.join(os.path.realpath(os.path.dirname(os.path.abspath(p))), os.path.basename(p))


def toplevel(path):
    r = git(["rev-parse", "--show-toplevel"], os.path.dirname(real(path)))
    return os.path.realpath(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def registration(decision, deps):
    """deps: [(path, allow_dir)]. Return (registered, commit, problems, top). Everything is judged against HEAD."""
    problems = []
    top = toplevel(decision)
    if top is None:
        return False, None, [f"{decision}: not in a git repo"], None
    ls = git(["ls-tree", "-r", "--name-only", "-z", "HEAD"], top)
    if ls.returncode != 0:
        return False, None, ["git HEAD has no commit"], top
    tracked = set(ls.stdout.split("\0"))

    def committed(rel, is_dir):
        if rel == ".":
            return bool(tracked)
        return any(t.startswith(rel + "/") for t in tracked) if is_dir else rel in tracked

    def check(p, allow_dir):
        if not os.path.exists(p):
            problems.append(f"{p}: missing")
            return
        if os.path.islink(p):
            problems.append(f"{p}: must not be a symlink")
            return
        rel = os.path.relpath(real(p), top)
        if rel.startswith(".."):
            problems.append(f"{p}: outside this git repo")
            return
        is_dir = os.path.isdir(p)
        if is_dir and not allow_dir:
            problems.append(f"{p}: must be a file")
        elif not committed(rel, is_dir):
            problems.append(f"{p}: not committed")

    check(decision, False)
    rel = os.path.relpath(real(decision), top)
    if rel in tracked:
        blob = git(["show", f"HEAD:{rel}"], top, binary=True).stdout
        if blob != open(decision, "rb").read():
            problems.append(f"{decision}: differs from its committed version (commit it first)")
    for d, allow_dir in deps:
        check(d, allow_dir)
    head = git(["log", "-1", "--format=%h %cs", "HEAD"], top).stdout.strip()
    return (not problems), head, problems, top


def make_snapshot(top, paths):
    """Write the committed blobs under `paths` (repo-relative) into a temp dir. Raw bytes: no attributes, no
    filters. A symlink or a submodule anywhere in the exported paths is refused."""
    snap = mktemp("eval-snap-")
    ls = git(["ls-tree", "-r", "-z", "HEAD", "--", *paths], top, binary=True)
    if ls.returncode != 0:
        fail("could not read the snapshot paths from HEAD")
    for ent in ls.stdout.split(b"\0"):
        if not ent:
            continue
        meta, path = ent.split(b"\t", 1)
        mode, _typ, sha = meta.split(b" ")
        path = path.decode()
        if mode == b"120000":
            fail(f"{path}: a symlink in the exported tree is refused")
        if mode == b"160000":
            fail(f"{path}: a submodule in the exported tree is refused")
        blob = git(["cat-file", "blob", sha.decode()], top, binary=True)
        if blob.returncode != 0:
            fail(f"could not read {path} from HEAD")
        dest = os.path.join(snap, path)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(blob.stdout)
        os.chmod(dest, 0o755 if mode == b"100755" else 0o644)
    return snap


def dependencies(dec, root):
    """[(path, allow_dir)] the verdict depends on, resolved against root."""
    deps = {}
    for o in dec["options"]:
        for c in o.get("checks", []):
            if "tlc" in c:
                deps[resolve(root, c["tlc"]["spec"])] = False
    for i in dec.get("inputs", []):
        deps.setdefault(resolve(root, i), True)
    for k in ("attack_report", "blind_check"):
        if dec.get(k):
            deps[resolve(root, dec[k])] = False
    return sorted(deps.items())


# ---------------------------------------------------------------- mutants


def normalized(text):
    return [re.sub(r"MODULE\s+\w+", "MODULE M", l.rstrip()) for l in text.splitlines() if l.strip()]


def changed_lines(a, b):
    d = difflib.unified_diff(normalized(a), normalized(b), lineterm="", n=0)
    return sum(1 for l in d if l[:1] in "+-" and not l.startswith(("+++", "---")))


def definition(text, name):
    """The top-level definition `Name ==` and its indented continuation lines, or None."""
    lines = text.splitlines()
    for i, l in enumerate(lines):
        if re.match(rf"{re.escape(name)}\s*(\([^)]*\))?\s*==", l):
            region = [l.rstrip()]
            for nxt in lines[i + 1:]:
                if nxt.strip() == "" or nxt[0] in " \t":
                    region.append(nxt.rstrip())
                else:
                    break
            return "\n".join(region).rstrip()
    return None


def mutant_problem(ok_spec, mut_spec, invariant):
    """Why `mut_spec` is not an acceptable mutant of `ok_spec`, or None."""
    if same_file(ok_spec, mut_spec):
        return "the mutant is the same file as the real spec"
    a, b = open(ok_spec, errors="replace").read(), open(mut_spec, errors="replace").read()
    da, db = definition(a, invariant), definition(b, invariant)
    if da is None or db is None:
        return f"invariant {invariant} must be defined at the top level of both spec files"
    if da != db:
        return f"the mutant changes the definition of invariant {invariant}"
    n = changed_lines(a, b)
    if n == 0:
        return "the mutant is identical to the real spec"
    if n > MAX_MUTANT_DIFF:
        return f"the mutant changes {n} lines (at most {MAX_MUTANT_DIFF})"
    return None


# ---------------------------------------------------------------- TLC


def render_cfg(t, tmpdir, defined):
    """Write the cfg for one tlc check from its fields. Nothing user-written is copied."""
    lines = [f"INIT {t['init']}", f"NEXT {t['next']}"]
    for k, v in sorted(t.get("constants", {}).items()):
        if k in defined:
            fail(f"constant {k} is defined in the spec with ==: a cfg would override it")
        val = "TRUE" if v is True else "FALSE" if v is False else str(v) if isinstance(v, int) else \
            "{" + ", ".join(str(i) for i in v) + "}"
        lines.append(f"CONSTANT {k} = {val}")
    lines.append(f"INVARIANT {t['invariant']}")
    fd, out = tempfile.mkstemp(prefix="check-", suffix=".cfg", dir=tmpdir)   # unique per check
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    return out


def defined_names(spec):
    text = re.sub(r"\(\*.*?\*\)", "", open(spec, errors="replace").read(), flags=re.S)
    return set(re.findall(r"^\s*([A-Za-z_]\w*)\s*(?:\([^)]*\))?\s*==", text, re.M))


def run_tlc(spec, cfg, deadlock, dump=None):
    """Run TLC. Returns (result, distinct_states). result: 'ok', 'VIOLATED:<invariant>', 'DEADLOCK' or
    'ERROR:<why>'. 'ok' needs exit code 0, the completion line and at least one state."""
    spec = os.path.abspath(spec)
    cfg = os.path.abspath(cfg)
    meta = mktemp("eval-tlc-")      # also java.io.tmpdir: parallel runs must not share it
    cmd = ["java", f"-Djava.io.tmpdir={meta}", "-XX:+UseParallelGC", "-cp", os.environ["TLA2TOOLS_JAR"], "tlc2.TLC",
           "-workers", os.environ.get("JOBS", "4"), "-metadir", meta, "-config", cfg]
    if not deadlock:
        cmd.append("-deadlock")  # TLC's flag DISABLES deadlock checking
    if dump:
        cmd += ["-dump", "dot,actionlabels", dump]
    cmd.append(os.path.basename(spec))
    try:
        p = subprocess.run(cmd, cwd=os.path.dirname(spec), capture_output=True, text=True, errors="replace",
                           timeout=int(os.environ.get("EVAL_TLC_TIMEOUT", "900")))
        out = p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        return "ERROR:timeout", None
    finally:
        shutil.rmtree(meta, ignore_errors=True)
    found = re.findall(r"(\d[\d,]*) distinct states found", out)
    states = int(found[-1].replace(",", "")) if found else None
    m = re.search(r"^Error: Invariant (\S+) is violated", out, re.M)
    if m:
        return f"VIOLATED:{m.group(1)}", states
    if re.search(r"^Error: Deadlock reached", out, re.M):
        return "DEADLOCK", states
    if p.returncode == 0 and re.search(r"^Model checking completed\. No error has been found\.", out, re.M):
        if not states:
            return "ERROR:no states explored", states
        return "ok", states
    return "ERROR:no verdict", states


# ---------------------------------------------------------------- simplicity lint


def parse_dot(path):
    """Parse TLC's -dump dot,actionlabels output. Returns (states: id -> {var: value}, edges: [(src, dst, label)])."""
    states, edges = {}, []
    node_re = re.compile(r'^(-?\d+) \[label="(.*)"(?:,style = filled)?\];?$')
    edge_re = re.compile(r'^(-?\d+) -> (-?\d+) \[label="([^"]*)"')
    with open(path) as f:
        for line in f:
            line = line.rstrip("\n")
            m = edge_re.match(line)
            if m:
                edges.append((m.group(1), m.group(2), m.group(3)))
                continue
            m = node_re.match(line)
            if m:
                vals, cur = {}, None
                for part in m.group(2).split("\\n"):
                    pm = re.match(r"^(?:/\\\\ )?(\w+) = (.*)$", part)
                    if pm:
                        cur = pm.group(1)
                        vals[cur] = pm.group(2)
                    elif cur is not None:  # continuation of a long value
                        vals[cur] += " " + part.strip()
                states[m.group(1)] = vals
    return states, edges


def lint(spec, cfg, deadlock, objects, ignore):
    """Semantic lint: for every action label in the state graph, which variables (and so which objects) change.

    cfg is the generated cfg of the option's first ok tlc check, so the lint measures the model that was checked.
    objects: {object_name: [var, ...]}; an object named 'channel' is a message channel. ignore: vars left out
    (model bookkeeping such as pc). Returns a dict of metrics or an error string."""
    with tempfile.TemporaryDirectory(prefix="eval-dump-") as d:
        dump = os.path.join(d, "g")
        result, states_n = run_tlc(spec, cfg, deadlock, dump=dump)
        if result != "ok":
            return f"lint run did not pass ({result}); lint needs a passing check"
        dot = dump if os.path.exists(dump) else dump + ".dot"
        states, edges = parse_dot(dot)
    if not states or not edges:
        return "lint found no states or no transitions in the state graph; nothing to measure"
    var_obj = {v: o for o, vs in objects.items() for v in vs}
    all_vars = set()
    for s in states.values():
        all_vars |= set(s)
    unmapped = sorted(v for v in all_vars if v not in var_obj and v not in ignore)
    changed = {}
    for src, dst, label in edges:
        a, b = states.get(src), states.get(dst)
        if a is None or b is None:
            continue
        diff = {v for v in a if a.get(v) != b.get(v)}
        changed.setdefault(label, set()).update(diff)
    multi = {}
    for label, vs in sorted(changed.items()):
        objs = {var_obj[v] for v in vs if v in var_obj} - {CHANNEL}
        if len(objs) > 1:
            multi[label] = sorted(objs)
    with open(spec) as f:
        loc = sum(1 for line in f if line.strip() and not line.strip().startswith("\\*"))
    return {
        "variables": len(all_vars - set(ignore)),
        "actions": len(changed),
        "multi_object_actions": len(multi),
        "multi_object_detail": multi,
        "loc": loc,
        "states": states_n,
        "unmapped": unmapped,
    }


# ---------------------------------------------------------------- data


def run_data(check, cwd):
    p = subprocess.Popen(["bash", "-c", check["cmd"]], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace", start_new_session=True)
    try:
        out, _ = p.communicate(timeout=float(check.get("timeout", 300)))
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.communicate()
        return None, "ERROR:timeout"
    if p.returncode != 0:
        last = out.strip().splitlines()[-1][:120] if out.strip() else ""
        return None, f"ERROR:exit {p.returncode}: {last}"
    m = re.search(check["regex"], out, re.M)
    if not m:
        return None, "ERROR:regex did not match"
    if m.group(1) is None:
        return None, "ERROR:capture group 1 did not match"
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return None, f"ERROR:not a number: {m.group(1)!r}"
    if not math.isfinite(val):
        return None, f"ERROR:not finite: {m.group(1)!r}"
    return val, None


# ---------------------------------------------------------------- validation


def is_id(x):
    return isinstance(x, str) and ID.fullmatch(x) is not None


def is_ident(x):
    return isinstance(x, str) and IDENT.fullmatch(x) is not None


def is_int(x):
    return isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= MAX_INT


def validate(dec):
    """Reject malformed decision files up front (exit 2) instead of crashing or silently mis-scoring."""
    errs = []
    if not isinstance(dec, dict):
        return ["the file must be a YAML mapping"]
    for k in dec:
        if k not in TOP_KEYS and not str(k).startswith("x-"):
            errs.append(f"unknown key {k!r} (keys starting with x- are free for YAML anchors)")
    for k in ("id", "question", "criteria", "options"):
        if k not in dec:
            errs.append(f"missing key: {k}")
    if errs:
        return errs
    if not is_id(dec["id"]):
        errs.append("id must be a string of letters, digits, _ . -")
    if not isinstance(dec["question"], str):
        errs.append("question must be a string")
    if not isinstance(dec.get("reversible"), bool):
        errs.append("reversible must be true or false")
    for k in ("owner", "falsifier", "root", "attack_report", "blind_check"):
        if dec.get(k) is not None and not isinstance(dec[k], str):
            errs.append(f"{k} must be a string")
    if bool(dec.get("attack_report")) != bool(dec.get("blind_check")):
        errs.append("attack_report and blind_check go together: set both or neither")
    for k in ("inputs", "ignore"):
        v = dec.get(k, [])
        if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
            errs.append(f"{k} must be a list of strings")
    obj = dec.get("objects", {})
    if not (isinstance(obj, dict) and all(is_ident(k) and isinstance(v, list) and all(is_ident(x) for x in v)
                                          for k, v in obj.items())):
        errs.append("objects must map names to lists of variable names")
    else:
        flat = [x for v in obj.values() for x in v]
        if len(flat) != len(set(flat)):
            errs.append("a variable may belong to only one object (channel included)")
    for k in ("criteria", "options"):
        if not (isinstance(dec[k], list) and dec[k] and all(isinstance(x, dict) for x in dec[k])):
            errs.append(f"{k} must be a non-empty list of mappings")
    if errs:
        return errs
    for c in dec["criteria"]:
        if set(c) - CRITERION_KEYS:
            errs.append(f"unknown criterion keys {sorted(set(c) - CRITERION_KEYS)}")
    cids = [c.get("id") for c in dec["criteria"]]
    if not all(is_id(c) for c in cids) or len(set(cids)) != len(cids):
        errs.append("criteria ids must be unique strings of letters, digits, _ . -")
    oids = [o.get("id") for o in dec["options"]]
    if not all(is_id(o) for o in oids) or len(set(oids)) != len(oids):
        errs.append("option ids must be unique strings of letters, digits, _ . -")
    for o in dec["options"]:
        oid = o.get("id")
        if set(o) - OPTION_KEYS:
            errs.append(f"option {oid}: unknown keys {sorted(set(o) - OPTION_KEYS)}")
        checks = o.get("checks", [])
        if not (isinstance(checks, list) and all(isinstance(c, dict) for c in checks)):
            errs.append(f"option {oid}: checks must be a list of mappings")
            continue
        oks, muts = {}, {}
        for c in checks:
            if set(c) - CHECK_KEYS:
                errs.append(f"option {oid}: unknown check keys {sorted(set(c) - CHECK_KEYS)}")
            kinds = [k for k in ("tlc", "data", "declared") if k in c]
            if len(kinds) != 1:
                errs.append(f"option {oid}: a check needs exactly one of tlc/data/declared")
                continue
            if c.get("criterion") not in cids:
                errs.append(f"option {oid}: unknown criterion {c.get('criterion')!r}")
            is_mut = "mutant_of" in c
            if is_mut and ("tlc" not in c or c["mutant_of"] != c.get("criterion")):
                errs.append(f"option {oid}: mutant_of needs a tlc check and must equal the check's criterion")
            if "tlc" in c:
                t = c["tlc"]
                if not isinstance(t, dict):
                    errs.append(f"option {oid}: tlc must be a mapping")
                    continue
                allowed = MUTANT_TLC_KEYS if is_mut else OK_TLC_KEYS
                if set(t) - allowed:
                    errs.append(f"option {oid}: unknown tlc keys {sorted(set(t) - allowed)}"
                                + (" (a mutant takes only spec and invariant; the rest is inherited from the real check)" if is_mut else ""))
                if not isinstance(t.get("spec"), str):
                    errs.append(f"option {oid}: tlc needs a spec file")
                if not is_ident(t.get("invariant")):
                    errs.append(f"option {oid}: tlc needs an invariant (an identifier)")
                    continue
                key = (c.get("criterion"), t["invariant"])
                if is_mut:
                    if key in muts:
                        errs.append(f"option {oid}: two mutants for {key[0]}/{key[1]}")
                    muts[key] = t
                    continue
                if key in oks:
                    errs.append(f"option {oid}: two tlc checks for {key[0]}/{key[1]}")
                oks[key] = t
                for k in ("init", "next"):
                    if not is_ident(t.get(k)):
                        errs.append(f"option {oid}: tlc needs {k} (an identifier)")
                cst = t.get("constants", {})
                if not (isinstance(cst, dict) and all(
                        is_ident(k) and (isinstance(v, bool) or is_int(v) or (isinstance(v, list) and all(is_int(i) for i in v)))
                        for k, v in cst.items())):
                    errs.append(f"option {oid}: constants map identifiers to booleans, integers 0..{MAX_INT} or lists of such integers")
                if "deadlock" in t and not isinstance(t["deadlock"], bool):
                    errs.append(f"option {oid}: deadlock must be true or false")
                if t.get("expect", "ok") != "ok":
                    errs.append(f"option {oid}: a tlc check must expect ok")
            if "data" in c:
                d = c["data"]
                if not isinstance(d, dict) or not all(isinstance(d.get(k), str) for k in ("cmd", "regex", "op")):
                    errs.append(f"option {oid}: data needs cmd, regex, op strings")
                    continue
                if set(d) - DATA_KEYS:
                    errs.append(f"option {oid}: unknown data keys {sorted(set(d) - DATA_KEYS)}")
                if d["op"] not in OPS:
                    errs.append(f"option {oid}: unknown op {d['op']!r} (use one of {', '.join(OPS)})")
                v = d.get("value")
                if not (v is None or v == "TBD" or (isinstance(v, (int, float)) and not isinstance(v, bool)
                                                   and math.isfinite(v))):
                    errs.append(f"option {oid}: threshold must be a finite number or TBD, got {v!r}")
                try:
                    if re.compile(d["regex"]).groups < 1:
                        errs.append(f"option {oid}: regex must contain a capture group")
                except re.error as e:
                    errs.append(f"option {oid}: bad regex: {e}")
                if not isinstance(d.get("name", "query"), str):
                    errs.append(f"option {oid}: data name must be a string")
                t_o = d.get("timeout", 1)
                if not (isinstance(t_o, (int, float)) and not isinstance(t_o, bool) and math.isfinite(t_o) and t_o > 0):
                    errs.append(f"option {oid}: timeout must be a positive, finite number")
            if "declared" in c and not isinstance(c["declared"], str):
                errs.append(f"option {oid}: declared must be a string")
        for key in muts:
            if key not in oks:
                errs.append(f"option {oid}: mutant for {key[0]}/{key[1]} has no real tlc check to mutate")
        if "lint" in o:
            if not isinstance(o["lint"], dict):
                errs.append(f"option {oid}: lint must be a mapping (use lint: {{}})")
            elif not oks:
                errs.append(f"option {oid}: lint needs a tlc check to measure")
            else:
                models = {repr(sorted((k, v) for k, v in t.items() if k not in ("invariant", "expect")))
                          for t in oks.values()}
                if len(models) > 1:
                    errs.append(f"option {oid}: with lint, all tlc checks must use one model (same spec, init, next, "
                                "constants and deadlock; only the invariant may differ)")
    return errs


# ---------------------------------------------------------------- main


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("decision")
    ap.add_argument("--draft", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args()

    with open(a.decision) as f:
        dec = yaml.safe_load(f)
    errs = validate(dec)
    if errs:
        fail("invalid decision file:\n  " + "\n  ".join(errs))
    base = os.path.dirname(os.path.abspath(a.decision))
    root = resolve(base, dec.get("root", "."))
    deps = dependencies(dec, root)
    dep_paths = [d for d, _ in deps]

    needs_tlc = any("tlc" in c for o in dec["options"] for c in o.get("checks", []))
    if needs_tlc:
        jar = os.environ.get("TLA2TOOLS_JAR")
        if not (jar and os.path.isfile(jar)):
            fail("TLA2TOOLS_JAR must point to tla2tools.jar (this decision file has a tlc check)")
        if not shutil.which("java"):
            fail("java is not on PATH (this decision file has a tlc check)")
        if not os.environ.get("JOBS", "4").isdigit() or int(os.environ.get("JOBS", "4")) < 1:
            fail("JOBS must be a positive integer")
        if not os.environ.get("EVAL_TLC_TIMEOUT", "900").isdigit():
            fail("EVAL_TLC_TIMEOUT must be a number of seconds")

    if a.out and any(same_file(a.out, x) for x in [a.decision, *dep_paths]):
        fail("--out would overwrite the decision file or one of its dependencies")

    registered, commit, problems, top = registration(a.decision, deps)
    if not registered and not a.draft:
        fail("not registered: " + "; ".join(problems) + ". Commit the decision file and everything it uses "
             "(that registers them before the run), or pass --draft.")
    if a.draft:
        registered = False
        spath = lambda p: p            # specs: the working tree
        run_root = root                # data commands: the working tree
    else:
        rel = lambda p: os.path.relpath(real(p), top)
        if rel(root).startswith(".."):
            fail("root is outside the git repo")
        snap = make_snapshot(top, sorted({os.path.dirname(rel(a.decision)) or ".", *[rel(d) for d in dep_paths]}))
        data_snap = mktemp("eval-data-")
        shutil.copytree(snap, data_snap, dirs_exist_ok=True)        # data commands get their own copy
        spath = lambda p: os.path.join(snap, rel(p))
        run_root = os.path.join(data_snap, rel(root))
        os.makedirs(run_root, exist_ok=True)
        ev = [dec[k] for k in ("attack_report", "blind_check") if dec.get(k)]
        if ev:
            ra, rb = (os.path.realpath(spath(resolve(root, e))) for e in ev)
            if ra == rb or os.path.realpath(spath(a.decision)) in (ra, rb):
                fail("attack_report and blind_check must be two different files, and not the decision file")
            if any(os.path.getsize(p) == 0 for p in (ra, rb)):
                fail("attack_report and blind_check must not be empty")

    crit = {c["id"]: c for c in dec["criteria"]}
    tmp = mktemp("eval-cfg-")
    lines = [f"# Decision eval: {dec['id']}", ""]
    lines.append(f"- Question: {cell(dec['question'])}")
    lines.append(f"- Owner: {cell(dec.get('owner', '?'))}; reversible: {dec['reversible']}")
    lines.append(f"- Criteria registered: {'yes, evaluated on a snapshot of ' + commit if registered else '**NO: DRAFT run** (' + cell('; '.join(problems) or 'draft requested') + ')'}")
    lines.append(f"- Falsifier: {cell(dec.get('falsifier', '**none given**'))}")
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    lines.append("| option | criterion | check | expected | actual | states | pass |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")

    verdicts, metrics, missing_by_opt = {}, {}, {}
    for opt in dec["options"]:
        oid = opt["id"]
        ok_all = True
        computed_crit = set()   # criteria with a real (non-mutant, non-declared, threshold-set) check
        oks = {(c["criterion"], c["tlc"]["invariant"]): c["tlc"] for c in opt.get("checks", [])
               if "tlc" in c and "mutant_of" not in c}
        ok_runs = []            # (criterion, invariant) of tlc checks that passed
        mutant_caught = {}      # (criterion, invariant) -> a valid mutant that TLC violates
        first_ok = None         # (spec, generated cfg, deadlock) for the lint
        for chk in opt.get("checks", []):
            cid = chk["criterion"]
            if "tlc" in chk and "mutant_of" in chk:
                name = chk["tlc"]["invariant"]
                t = oks[(cid, name)]
                ok_spec, m_spec = spath(resolve(root, t["spec"])), spath(resolve(root, chk["tlc"]["spec"]))
                lab = f"mutant `{cell(os.path.basename(chk['tlc']['spec']))}` of invariant `{cell(name)}`"
                why = mutant_problem(ok_spec, m_spec, name)
                if why:
                    lines.append(f"| {oid} | {cid} | {lab} | an acceptable mutant | {cell(why)} | | **NO** |")
                    ok_all = False
                    continue
                cfg = render_cfg(t, tmp, defined_names(m_spec))
                result, states = run_tlc(m_spec, cfg, t.get("deadlock", True))
                caught = result == f"VIOLATED:{name}"
                mutant_caught[(cid, name)] = caught
                lines.append(f"| {oid} | {cid} | {lab} | VIOLATED:{cell(name)} | {cell(result)} | {states or ''} | "
                             f"{'yes' if caught else '**NO**'} |")
                ok_all &= caught
            elif "tlc" in chk:
                t = chk["tlc"]
                name = t["invariant"]
                spec = spath(resolve(root, t["spec"]))
                dl = t.get("deadlock", True)
                gcfg = render_cfg(t, tmp, defined_names(spec) if os.path.isfile(spec) else set())
                result, states = run_tlc(spec, gcfg, dl)
                computed_crit.add(cid)
                passed = result == "ok"
                if passed:
                    ok_runs.append((cid, name))
                    if first_ok is None:
                        first_ok = (spec, gcfg, dl)
                lines.append(f"| {oid} | {cid} | TLC invariant `{cell(name)}` on `{cell(os.path.basename(t['spec']))}` | ok | "
                             f"{cell(result)} | {states or ''} | {'yes' if passed else '**NO**'} |")
                ok_all &= passed
            elif "data" in chk:
                dchk = chk["data"]
                if dchk.get("value") in (None, "TBD"):
                    lines.append(f"| {oid} | {cid} | data | threshold not set | | | **NO** (untested) |")
                    continue
                computed_crit.add(cid)
                val, err = run_data(dchk, run_root)
                passed = err is None and OPS[dchk["op"]](val, float(dchk["value"]))
                lines.append(f"| {oid} | {cid} | data `{cell(dchk.get('name', 'query'))}` | {dchk['op']} {dchk['value']} | "
                             f"{cell(err or val)} | | {'yes' if passed else '**NO**'} |")
                ok_all &= passed
            else:
                lines.append(f"| {oid} | {cid} | declared (not computed) | | {cell(chk['declared'])} | | n/a |")
        # Every passing TLC check needs a caught mutant: a small edit of the same spec, checked the same way.
        for cid, name in ok_runs:
            if not mutant_caught.get((cid, name)):
                lines.append(f"| {oid} | {cid} | vacuity guard | a caught mutant of the real spec that breaks "
                             f"invariant `{cell(name)}` | none | | **NO** |")
                ok_all = False
        if "lint" in opt:
            spec, gcfg, dl = first_ok if first_ok else (None, None, True)
            m = lint(spec, gcfg, dl, dec.get("objects", {}), dec.get("ignore", [])) if spec else \
                "no passing tlc check to measure"
            metrics[oid] = m
            if isinstance(m, str):
                lines.append(f"| {oid} | lint | simplicity lint | runs | {cell(m)} | | **NO** |")
                ok_all = False
            elif m["unmapped"]:
                lines.append(f"| {oid} | lint | variables mapped to objects | all | unmapped: "
                             f"{cell(', '.join(m['unmapped']))} | | **NO** |")
                ok_all = False
        missing = sorted(set(crit) - computed_crit)
        missing_by_opt[oid] = missing
        # False: a check failed. None: nothing failed but some criterion has no computed check (untested).
        verdicts[oid] = False if not ok_all else (None if missing else True)

    lines += ["", "## Simplicity (computed from the state graph; lower is simpler)", ""]
    keys = ["variables", "actions", "multi_object_actions", "loc"]
    lines.append("| option | " + " | ".join(keys) + " | multi-object actions |")
    lines.append("| --- | " + " | ".join("---" for _ in keys) + " | --- |")
    for oid, m in metrics.items():
        if isinstance(m, dict):
            detail = "; ".join(f"{k}: {'+'.join(v)}" for k, v in m["multi_object_detail"].items()) or "none"
            lines.append(f"| {oid} | " + " | ".join(str(m[k]) for k in keys) + f" | {cell(detail)} |")

    valid = [o for o, v in verdicts.items() if v]
    untested = [o for o, v in verdicts.items() if v is None]
    lines += ["", "## Verdict", ""]
    for o, v in verdicts.items():
        why = ""
        if v is None:
            why = " (criteria without a computed check: " + ", ".join(missing_by_opt[o]) + ")"
        lines.append(f"- {o}: {'UNTESTED' + why if v is None else 'VALID' if v else 'INVALID'}")
    decisive = None
    if len(valid) == 1:
        decisive = (valid[0], "the only valid option")
    elif len(valid) > 1:
        lm = {o: metrics.get(o) for o in valid}
        if all(isinstance(m, dict) for m in lm.values()):
            for o in valid:
                others = [p for p in valid if p != o]
                if all(all(lm[o][k] <= lm[p][k] for k in keys) and any(lm[o][k] < lm[p][k] for k in keys)
                       for p in others):
                    decisive = (o, "valid, no worse on every metric and better on at least one")
    lines.append("")
    is_decisive = False
    if not registered:
        lines.append("**NOT DECISIVE: DRAFT run (criteria not registered).**")
    elif dec["reversible"] is False and not dec.get("attack_report"):
        lines.append("**NOT DECISIVE:** reversible: false needs `attack_report` and `blind_check` (committed files) "
                     "before a verdict can be decisive.")
    elif untested:
        lines.append(f"**NOT DECISIVE:** untested options ({', '.join(untested)}): some criterion has no computed check.")
    elif decisive:
        is_decisive = True
        lines.append(f"**DECISIVE: {decisive[0]}** ({decisive[1]}).")
    else:
        lines.append("**NOT DECISIVE:** " + ("no valid option." if not valid else
                                            "several valid options and none is no worse on every metric and better on one. "
                                            "The choice is a trade-off for the owner."))
    if dec.get("owner") not in (None, "engineering"):
        lines.append(f"Owner is {cell(dec['owner'])}: they choose the requirement; this eval only checks options against it.")
    text = "\n".join(lines) + "\n"
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, "w") as f:
            f.write(text)
    print(text)
    sys.exit(0 if is_decisive else 1)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(2))
    try:
        main()
    except SystemExit:
        raise
    except KeyboardInterrupt:
        fail("interrupted")
    except Exception as e:  # never exit 1 (= "not decisive") on a crash
        fail(f"internal error: {type(e).__name__}: {e}")
