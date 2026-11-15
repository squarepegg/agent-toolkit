#!/usr/bin/env python3
"""spec-first: collect the evidence for a change, mechanically. Stdlib only. Never edits the repository.

  evidence.py SPEC --out DIR [--base REF] [--timeout SECONDS]

Runs every command check of SPEC from the repository root (bash, LC_ALL=C, TZ=UTC), records the literal output
and the exit code, and compares them with the expectation. A manual check, a timeout or a missing command is
NOT CHECKED, never PASS. Writes to DIR (must be outside the repository):
  evidence.json  checks, guards, changed files (sorted keys, no clock values: two runs on the same tree are equal)
  evidence.md    the literal output per check, for the reviewer
  diff.patch     git diff BASE (tracked) + the untracked files' content
  files.txt      every changed file: <status>\t<path>
Guards (each one blocks DONE): the spec's normative sections changed since accept; an existing test file was
modified or deleted. BASE defaults to the commit recorded at accept.
Exit 0 when every command check passed and no guard fired, 1 otherwise, 2 on bad input.
"""
import hashlib, json, os, re, subprocess, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec as S  # noqa: E402

KEEP = 8000  # chars kept from the head and from the tail of each stream


def glob_re(g):
    out, i = "", 0
    while i < len(g):
        if g.startswith("**/", i): out += "(?:.*/)?"; i += 3
        elif g.startswith("**", i): out += ".*"; i += 2
        elif g[i] == "*": out += "[^/]*"; i += 1
        elif g[i] == "?": out += "[^/]"; i += 1
        else: out += re.escape(g[i]); i += 1
    return re.compile("^" + out + "$")


def is_test(path, globs):
    return any(glob_re(g).match(path) for g in globs)


def clip(s):
    if len(s) <= 2 * KEEP: return s
    h = hashlib.sha256(s.encode("utf-8", "replace")).hexdigest()[:12]
    return f"{s[:KEEP]}\n... [{len(s) - 2 * KEEP} chars cut, sha256 {h}] ...\n{s[-KEEP:]}"


def judge(chk, rc, out, err):
    """-> (status, [failed clause descriptions])"""
    failed = []
    for kind, stream, op, val in chk["clauses"]:
        text = out if stream == "stdout" else out + err
        if kind == "exit": ok = rc == val; desc = f"exit {val} (got {rc})"
        elif kind == "text" and op == "contains": ok = val in text; desc = f'{stream} contains "{val}"'
        elif kind == "text": ok = val not in text; desc = f'{stream} lacks "{val}"'
        else: ok = re.search(val, text, re.M) is not None; desc = f"{stream} matches /{val}/"
        if not ok: failed.append(desc)
    return ("FAIL" if failed else "PASS"), failed


def run_check(chk, root, timeout):
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", LC_ALL="C", LANG="C", TZ="UTC", NO_COLOR="1", TERM="dumb")
    try:
        p = subprocess.run(["bash", "-c", chk["cmd"]], cwd=root, capture_output=True, text=True, errors="replace",
                           timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return {"status": "NOT CHECKED", "reason": f"timed out after {timeout}s", "exit": None, "stdout": "", "stderr": ""}
    r = {"exit": p.returncode, "stdout": clip(p.stdout), "stderr": clip(p.stderr)}
    if p.returncode == 127 and "command not found" in p.stderr:
        r.update(status="NOT CHECKED", reason="command not found"); return r
    status, failed = judge(chk, p.returncode, p.stdout, p.stderr)
    r.update(status=status, reason="; ".join("expected " + f for f in failed))
    return r


def changed_files(root, base):
    files = {}
    for line in S.git(["diff", "--name-status", "--no-renames", base, "--"], root).splitlines():
        st, _, path = line.partition("\t")
        files[path] = st[0]
    for path in S.git(["ls-files", "--others", "--exclude-standard"], root).splitlines():
        files[path] = "A"
    return dict(sorted(files.items()))


def diff_text(root, base, files):
    parts = [S.git(["diff", "--no-renames", base, "--"], root)]
    for path, st in files.items():
        if st == "A" and not S.git(["ls-files", "--", path], root).strip():
            try:
                with open(os.path.join(root, path), encoding="utf-8", errors="replace") as f: body = f.read()
            except OSError: continue
            lines = body.splitlines()
            parts.append(f"--- /dev/null\n+++ b/{path} (untracked)\n@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{l}\n" for l in lines))
    return "".join(parts)


def main(argv):
    args = {"--base": None, "--out": None, "--timeout": "600"}
    pos, i = [], 0
    while i < len(argv):
        if argv[i] in args and i + 1 < len(argv): args[argv[i]] = argv[i + 1]; i += 2
        else: pos.append(argv[i]); i += 1
    if len(pos) != 1 or not args["--out"]:
        print(__doc__.strip()); return 2
    try:
        spec = S.parse(pos[0])
        errs, _ = S.lint(spec, accepted=True)
        root = S.repo_root(pos[0])
        out = os.path.realpath(args["--out"])
        if out == root or out.startswith(root + os.sep):
            print(f"error: --out {out} is inside the repository; put the evidence outside it"); return 2
        base = args["--base"]
        lock_reason = S.check_lock(spec)
        if not base:
            try: base = json.loads(S.read(S.lock_path(pos[0])))["base"]
            except (S.SpecError, ValueError, KeyError):
                print("error: no --base and no lock to read it from (run spec.py accept first)"); return 2
        base = S.git(["rev-parse", "--verify", base + "^{commit}"], root).strip()
        timeout = int(args["--timeout"])
    except (S.SpecError, ValueError) as e:
        print(f"error: {e}"); return 2

    guards = []
    if lock_reason: guards.append(f"spec: {lock_reason}")
    for e in errs: guards.append(f"spec lint: {e}")
    files = changed_files(root, base)
    spec_rel = os.path.relpath(os.path.realpath(pos[0]), root)
    for path, st in files.items():
        if st in ("M", "D") and path not in (spec_rel, spec_rel + ".lock") and is_test(path, spec["test_globs"]):
            guards.append(f"existing test {'modified' if st == 'M' else 'deleted'}: {path}")

    checks = []
    for chk in spec["checks"]:
        row = {"id": chk["id"], "covers": chk["covers"], "kind": chk["kind"]}
        if chk["kind"] == "manual":
            row.update(status="NOT CHECKED", reason="manual check: a person must do it", action=chk["action"], see=chk["see"])
        else:
            row.update(cmd=chk["cmd"], expect=chk["expect"], **run_check(chk, root, timeout))
        checks.append(row)

    os.makedirs(out, exist_ok=True)
    head = S.git(["rev-parse", "HEAD"], root).strip()
    patch = diff_text(root, base, files)
    data = {"diff_sha": hashlib.sha256(patch.encode()).hexdigest(), "spec": spec_rel, "spec_hash": S.normative_hash(spec), "root": root, "base": base, "head": head,
            "files": files, "guards": guards, "checks": checks,
            "test_globs": spec["test_globs"],
            "ids": sorted(list(spec["requirements"]) + list(spec["edges"]), key=S.idkey)}
    with open(os.path.join(out, "evidence.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True); f.write("\n")
    with open(os.path.join(out, "diff.patch"), "w", encoding="utf-8") as f: f.write(patch)
    with open(os.path.join(out, "files.txt"), "w", encoding="utf-8") as f:
        f.write("".join(f"{st}\t{p}\n" for p, st in files.items()))
    md = [f"# Evidence for {spec_rel}", "", f"Base {base}, head {head}. Literal output; no summary.", ""]
    md += ["## Guards", ""] + ([f"- {g}" for g in guards] or ["- none"]) + [""]
    for c in checks:
        md += [f"## {c['id']} [{', '.join(c['covers'])}]: {c['status']}", ""]
        if c["kind"] == "manual":
            md += [f"Manual: {c['action']} -> see: {c['see']}", "Not run here.", ""]; continue
        md += ["Command:", "```", c["cmd"], "```", f"Expected: {c['expect']}", f"Exit: {c['exit']}"]
        if c["reason"]: md += [f"Reason: {c['reason']}"]
        for s in ("stdout", "stderr"):
            md += [f"{s}:", "```", c[s].rstrip("\n"), "```"]
        md += [""]
    with open(os.path.join(out, "evidence.md"), "w", encoding="utf-8") as f: f.write("\n".join(md))

    for c in checks: print(f"{c['id']} {c['status']}" + (f" ({c['reason']})" if c.get("reason") else ""))
    for g in guards: print(f"GUARD {g}")
    ok = not guards and all(c["status"] == "PASS" for c in checks if c["kind"] == "cmd")
    print(f"evidence: {out}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
