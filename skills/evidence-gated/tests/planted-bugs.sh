#!/bin/bash
# Mutation check of the self-test. Each planted bug is applied to a copy of eval.py (or screen.sh). A bug counts as
# caught only if eval-test.sh reports a FAIL line containing the INTENDED test name: a crash or an unrelated
# failure is "WRONG REASON", not a catch. A mutant that is not valid Python is rejected.
# Mutants run PAR at a time (default 6). ONLY=<substring> runs just the mutants whose name contains it.
# Usage: planted-bugs.sh (needs TLA2TOOLS_JAR and java)
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
SRC="$HERE/../scripts/eval.py"
PAR=${PAR:-6}
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
[ -n "${TLA2TOOLS_JAR:-}" ] || { echo "planted-bugs: TLA2TOOLS_JAR is not set"; exit 2; }
bash "$HERE/eval-test.sh" >/dev/null 2>&1 || { echo "planted-bugs: the unmutated self-test fails; fix it first"; exit 2; }
n=0; running=0
want() { [ -z "${ONLY:-}" ] || [[ "$1" == *$ONLY* ]]; }
finish() { # name, expected, outfile
  nfail=$(grep -c "^FAIL" "$3")
  if [ "$nfail" -gt 15 ]; then echo "BROKEN MUTANT: $1 ($nfail failing tests: it probably crashes eval.py)"
  elif grep "^FAIL" "$3" | grep -qF -- "$2"; then echo "caught: $1"
  elif [ "$nfail" -gt 0 ]; then echo "WRONG REASON: $1 (wanted FAIL ~ '$2')"
  else echo "MISSED: $1"; fi
}
unique() { # the expected name must match exactly one test name in eval-test.sh
  [ "$(grep -F 'check "' "$HERE/eval-test.sh" | grep -cF -- "$1")" -eq 1 ]
}
launch() { running=$((running+1)); if [ $running -ge "$PAR" ]; then wait; running=0; fi; }
mutate() { # name, expected FAIL substring, old, new
  want "$1" || return 0
  n=$((n+1)); local id=$n
  unique "$2" || { echo "AMBIGUOUS NAME: $1 (expected '$2' matches $(grep -F 'check "' "$HERE/eval-test.sh" | grep -cF -- "$2") tests)" > "$T/r$id"; return 0; }
  (
    python3 - "$SRC" "$T/m$id.py" "$3" "$4" <<'P' || { echo "PATTERN NOT FOUND: $1" > "$T/r$id"; exit 0; }
import sys
s=open(sys.argv[1]).read()
if sys.argv[3] not in s: sys.exit(1)
m=s.replace(sys.argv[3],sys.argv[4],1)
compile(m,"m.py","exec")   # a mutant must be valid Python, else it proves nothing
open(sys.argv[2],"w").write(m)
P
    sd=$(mktemp -d); cp "$HERE/fixture/nota.yaml" "$sd/"
    (cd "$sd" && git init -q && git config user.email t@t && git config user.name t && git add -A && git commit -qm s)
    if ! python3 "$T/m$id.py" "$sd/nota.yaml" 2>&1 | grep -q "DECISIVE: low"; then
      echo "BROKEN MUTANT: $1 (it no longer evaluates a trivial decision: it probably crashes eval.py)" > "$T/r$id"; rm -rf "$sd"; exit 0
    fi
    rm -rf "$sd"
    STOP_ON="$2" EVAL_PY="$T/m$id.py" bash "$HERE/eval-test.sh" > "$T/o$id" 2>&1
    finish "$1" "$2" "$T/o$id" > "$T/r$id"
  ) &
  launch
}
smutate() { # screen.sh mutant: name, expected, sed expression
  want "$1" || return 0
  n=$((n+1)); local id=$n
  unique "$2" || { echo "AMBIGUOUS NAME: $1 (expected '$2' matches $(grep -F 'check "' "$HERE/eval-test.sh" | grep -cF -- "$2") tests)" > "$T/r$id"; return 0; }
  (
    mkdir -p "$T/s$id"; cp "$HERE/../scripts/screen-prompt.md" "$T/s$id/"
    sed "$3" "$HERE/../scripts/screen.sh" > "$T/s$id/screen.sh"
    if cmp -s "$T/s$id/screen.sh" "$HERE/../scripts/screen.sh"; then echo "PATTERN NOT FOUND: $1" > "$T/r$id"; exit 0; fi
    STOP_ON="$2" SCREEN_SH="$T/s$id/screen.sh" bash "$HERE/eval-test.sh" > "$T/o$id" 2>&1
    finish "$1" "$2" "$T/o$id" > "$T/r$id"
  ) &
  launch
}

# --- pairing, mutants and the vacuity guard
mutate "no vacuity guard" "vacuity guard fails an option without a mutant" 'if not mutant_caught.get((cid, name)):' 'if False:'
mutate "the real spec may be its own mutant" "S30 the real spec as its own mutant" 'if same_file(ok_spec, mut_spec):' 'if False:'
mutate "same_file compares only paths" "S13 --out through a symlink" 'return os.path.exists(a) and os.path.exists(b) and os.path.samefile(a, b)' 'return False'
mutate "a mutant may rewrite the invariant" "S30 a mutant that rewrites" 'if da != db:' 'if False:'
mutate "mutant size not bounded" "S30 a mutant with too many changed lines" 'if n > MAX_MUTANT_DIFF:' 'if False:'
mutate "an identical mutant is accepted" "S30 an identical mutant" 'if n == 0:' 'if False:'
mutate "module name counted in the diff" "S30 an identical mutant" '[re.sub(r"MODULE\s+\w+", "MODULE M", l.rstrip()) for l in text.splitlines() if l.strip()]' '[l.rstrip() for l in text.splitlines() if l.strip()]'
mutate "any non-ok result counts as caught" "TLC catches for the wrong reason" 'caught = result == f"VIOLATED:{name}"' 'caught = result != "ok"'
mutate "uncaught mutants are ignored" "R22" 'ok_all &= caught' 'ok_all &= True'
mutate "a deadlock looks like an invariant violation" "R13 an invariant named Deadlock" 'return "DEADLOCK", states' 'return "VIOLATED:Deadlock", states'
mutate "a mutant may carry its own settings" "M02 a mutant with its own expect" 'allowed = MUTANT_TLC_KEYS if is_mut else OK_TLC_KEYS' 'allowed = OK_TLC_KEYS'
mutate "a mutant needs no real check" "M05" 'if key not in oks:' 'if False:'
mutate "non-mutant may expect a violation" "N02 a non-mutant" 'if t.get("expect", "ok") != "ok":' 'if False:'
mutate "mutant_of may differ from criterion" "N14 mutant_of must equal" 'c["mutant_of"] != c.get("criterion")' 'False'
mutate "untested option counted as valid" "declared-only option is UNTESTED" '(None if missing else True)' 'True'
mutate "untested option does not block DECISIVE" "untested option blocks" 'elif untested:' 'elif False:'
mutate "TBD counted as computed" "N07" 'if dchk.get("value") in (None, "TBD"):' 'if False:'
# --- TLC runner
mutate "TLC exit code ignored" "P03 a non-zero TLC exit code" 'if p.returncode == 0 and re.search(' 'if re.search('
mutate "zero states accepted" "N03 a spec with no states" 'if not states:' 'if False:'
mutate "TLC no-verdict reported ok" "TLC with no verdict" 'return "ERROR:no verdict", states' 'return "ok", states'
mutate "first state count wins" "R28 the final state count" 'int(found[-1].replace(",", ""))' 'int(found[0].replace(",", ""))'
mutate "state count commas not stripped" "R28 the final state count" 'int(found[-1].replace(",", ""))' 'int(found[-1].split(",")[0])'
mutate "violation regex unanchored" "S11 error lines printed by the spec" 'r"^Error: Invariant (\S+) is violated"' 'r"Error: Invariant (\S+) is violated"'
mutate "deadlock regex unanchored" "S11 error lines printed by the spec" 'r"^Error: Deadlock reached"' 'r"Error: Deadlock reached"'
mutate "TLC runs share a java tmpdir" "S07" 'f"-Djava.io.tmpdir={meta}", ' ''
mutate "temp dirs are not removed" "S14" 'atexit.register(lambda: [shutil.rmtree(d, ignore_errors=True) for d in TEMPS])' 'pass'
# --- generated cfg and validation
mutate "constant overriding a definition accepted" "R02 a constant named like an operator" 'if k in defined:' 'if False:'
mutate "integer lists render empty" "S11 integer-list constants" '"{" + ", ".join(str(i) for i in v) + "}"' '"{}"'
mutate "invariant name not validated" "R09" 'if not is_ident(t.get("invariant")):' 'if False:'
mutate "string constants accepted" "R02 a string constant" '(isinstance(v, bool) or is_int(v) or' '(True or'
mutate "integer range not enforced" "S12 s12a" '0 <= x <= MAX_INT' 'True'
mutate "unknown top-level keys accepted" "R26 an unknown top-level" 'if k not in TOP_KEYS and not str(k).startswith("x-"):' 'if False:'
mutate "a check may carry two kinds" "R18" 'if len(kinds) != 1:' 'if len(kinds) < 1:'
mutate "duplicate option ids allowed" "M07" 'len(set(oids)) != len(oids)' 'False'
mutate "duplicate criteria ids allowed" "N14 duplicate criteria" 'len(set(cids)) != len(cids)' 'False'
mutate "ids need not be strings" "N10 yaml booleans" 'return isinstance(x, str) and ID.fullmatch(x) is not None' 'return x is not None'
mutate "reversible need not be boolean" "N14 reversible must be" 'if not isinstance(dec.get("reversible"), bool):' 'if False:'
mutate "question may be any type" "S11 a non-string question" 'if not isinstance(dec["question"], str):' 'if False:'
mutate "deadlock may be any type" "S11 non-bool deadlock" 'if "deadlock" in t and not isinstance(t["deadlock"], bool):' 'if False:'
mutate "declared may be any type" "S11 non-bool deadlock" 'if "declared" in c and not isinstance(c["declared"], str):' 'if False:'
mutate "data name may be any type" "S11 non-bool deadlock" 'if not isinstance(d.get("name", "query"), str):' 'if False:'
mutate "a bool timeout is accepted" "S11 non-bool deadlock" 'isinstance(t_o, (int, float)) and not isinstance(t_o, bool) and math.isfinite(t_o)' 'isinstance(t_o, (int, float)) and math.isfinite(t_o)'
mutate "unknown op not validated" "M12 an unknown op" 'if d["op"] not in OPS:' 'if False:'
mutate "empty threshold not validated" "M12 an empty threshold" 'if not (v is None or v == "TBD" or' 'if not (True or'
mutate "regex capture group not required" "P17" 'if re.compile(d["regex"]).groups < 1:' 'if False:'
mutate "a regex group that did not match crashes" "S13 a regex whose group 1" 'if m.group(1) is None:' 'if False:'
mutate "infinite timeout accepted" "R25 an infinite timeout" 'and math.isfinite(t_o) and t_o > 0' 'and t_o > 0'
mutate "nested objects accepted" "R25 nested lists in objects" 'isinstance(v, list) and all(is_ident(x) for x in v)' 'isinstance(v, list)'
mutate "a variable may belong to two objects" "S10" 'if len(flat) != len(set(flat)):' 'if False:'
mutate "unknown criterion keys accepted" "S13 unknown option and criterion keys" 'if set(c) - CRITERION_KEYS:' 'if False:'
mutate "unknown option keys accepted" "S13 unknown option and criterion keys" 'if set(o) - OPTION_KEYS:' 'if False:'
mutate "lint need not be a mapping" "S13 lint: false" 'if not isinstance(o["lint"], dict):' 'if False:'
mutate "lint on mixed models allowed" "S04" 'if len(models) > 1:' 'if False:'
mutate "one evidence file allowed" "S13 a single evidence file" 'bool(dec.get("attack_report")) != bool(dec.get("blind_check"))' 'False'
mutate "markdown not sanitised" "N13 forged table rows" 'return " ".join(str(x).replace("|", "/").replace("`", "'"'"'").split())' 'return str(x)'
mutate "owner not sanitised" "R27" "lines.append(f\"- Owner: {cell(dec.get('owner', '?'))}; reversible" "lines.append(f\"- Owner: {dec.get('owner', '?')}; reversible"
# --- irreversible decisions and evidence
mutate "irreversible needs nothing extra" "M10 reversible: false is not decisive" 'elif dec["reversible"] is False and not dec.get("attack_report"):' 'elif False:'
mutate "evidence files not registered" "M10 evidence files outside" 'deps[resolve(root, dec[k])] = False' 'pass'
mutate "identical evidence files accepted" "P06 the same file" 'if ra == rb or os.path.realpath(spath(a.decision)) in (ra, rb):' 'if False:'
mutate "evidence may be the decision file" "R21" 'if ra == rb or os.path.realpath(spath(a.decision)) in (ra, rb):' 'if ra == rb:'
mutate "empty evidence file accepted" "P06 an empty evidence" 'if any(os.path.getsize(p) == 0 for p in (ra, rb)):' 'if False:'
mutate "directories accepted as evidence" "S06" 'if is_dir and not allow_dir:' 'if False:'
# --- registration and snapshot
mutate "missing dependencies tolerated" "R20" 'if not os.path.exists(p):
            problems.append(f"{p}: missing")' 'if False:
            problems.append(f"{p}: missing")'
mutate "no registration check" "uncommitted decision is refused" 'if not registered and not a.draft:' 'if False:'
mutate "draft can be decisive" "R16" 'if a.draft:
        registered = False' 'if a.draft:
        pass'
mutate "decision may differ from HEAD" "a modified decision file is refused" 'if blob != open(decision, "rb").read():' 'if False:'
mutate "symlinked dependency accepted" "N08 a symlinked" 'if os.path.islink(p):' 'if False:'
mutate "dependency outside the repo accepted" "P05" 'rel = os.path.relpath(real(p), top)
        if rel.startswith(".."):' 'rel = os.path.relpath(real(p), top)
        if False:'
mutate "untracked dependency accepted" "an untracked input is refused" 'elif not committed(rel, is_dir):' 'elif False:'
mutate "inputs: not registered" "an untracked input is refused" 'deps.setdefault(resolve(root, i), True)' 'pass'
mutate "mutant specs not registered" "S11 a mutant spec that was never committed" 'deps[resolve(root, c["tlc"]["spec"])] = False' 'pass'
mutate "symlinks in the export accepted" "S01" 'if mode == b"120000":' 'if False:'
mutate "data commands share the spec copy" "S02" 'data_snap = mktemp("eval-data-")
        shutil.copytree(snap, data_snap, dirs_exist_ok=True)        # data commands get their own copy' 'data_snap = snap'
mutate "evaluates the working tree, not the snapshot" "R05 an uncommitted edit" 'spath = lambda p: os.path.join(snap, rel(p))' 'spath = lambda p: p'
mutate "data commands run in the real tree" "N12" 'run_root = os.path.join(data_snap, rel(root))' 'run_root = root'
mutate "--out may overwrite inputs" "N16 --out cannot overwrite the decision" 'if a.out and any(same_file(a.out, x) for x in [a.decision, *dep_paths]):' 'if False:'
mutate "root outside the repo accepted" "root outside the git repo is refused" 'if rel(root).startswith(".."):' 'if False:'
# --- environment checks
mutate "jar path not checked as a file" "R17 a jar path" 'if not (jar and os.path.isfile(jar)):' 'if not jar:'
mutate "JOBS not validated" "R17 JOBS=0" 'if not os.environ.get("JOBS", "4").isdigit() or int(os.environ.get("JOBS", "4")) < 1:' 'if False:'
mutate "EVAL_TLC_TIMEOUT not validated" "S11 a non-numeric EVAL_TLC_TIMEOUT" 'if not os.environ.get("EVAL_TLC_TIMEOUT", "900").isdigit():' 'if False:'
mutate "TLC timeout is not an error" "S11 a TLC run that exceeds" 'return "ERROR:timeout", None' 'return "ok", 1'
mutate "TLA required even for data-only evals" "data-only eval needs no" 'if needs_tlc:
        jar' 'if True:
        jar'
mutate "TLA not required when a tlc check exists" "tlc check without the jar fails" 'if needs_tlc:
        jar' 'if False:
        jar'
# --- lint
mutate "channel counted as an object" "channel is not a second" 'objs = {var_obj[v] for v in vs if v in var_obj} - {CHANNEL}' 'objs = {var_obj[v] for v in vs if v in var_obj}'
mutate "unmapped variables ignored" "unmapped variable fails the option" 'unmapped = sorted(v for v in all_vars if v not in var_obj and v not in ignore)' 'unmapped = []'
mutate "ignore: list not applied" "ignore: leaves no unmapped" 'if v not in var_obj and v not in ignore)' 'if v not in var_obj)'
mutate "loc counts every line" "M09" 'sum(1 for line in f if line.strip() and not line.strip().startswith("\\*"))' 'sum(1 for line in f)'
mutate "single-variable states not parsed" "N11" '(?:/\\\\ )?' '/\\\\ '
mutate "multi-line states not joined" "R28 a multi-line state" 'vals[cur] += " " + part.strip()' 'pass'
mutate "empty graph accepted by lint" "N14 a lint on a graph" 'if not states or not edges:' 'if False:'
# --- dominance and verdict
mutate "weak dominance" "equal metrics are not decisive" 'and any(lm[o][k] < lm[p][k] for k in keys)' ''
mutate "dominance needs only one other option" "S11 two options tied at the top" 'if all(all(lm[o][k] <= lm[p][k] for k in keys) and any(lm[o][k] < lm[p][k] for k in keys)' 'if any(all(lm[o][k] <= lm[p][k] for k in keys) and any(lm[o][k] < lm[p][k] for k in keys)'
mutate "exit 0 whenever any option is valid" "M14 exit 1 when not decisive" 'sys.exit(0 if is_decisive else 1)' 'sys.exit(0 if valid else 1)'
mutate "exit always 0" "M14 exit 0 only when decisive" 'sys.exit(0 if is_decisive else 1)' 'sys.exit(0)'
mutate "owner note dropped" "owner other than engineering" 'if dec.get("owner") not in (None, "engineering"):' 'if False:'
# --- data
mutate "data command in wrong cwd" "data cmd runs in the decision root" 'p = subprocess.Popen(["bash", "-c", check["cmd"]], cwd=cwd,' 'p = subprocess.Popen(["bash", "-c", check["cmd"]], cwd="/",'
mutate "operator < is <=" "op < fails" '"<": operator.lt' '"<": operator.le'
mutate "operator > is >=" "op > fails" '">": operator.gt' '">": operator.ge'
mutate "operator == is >=" "R19" '"==": operator.eq' '"==": operator.ge'
mutate "operator <= is <" "S11 integer-list constants" '"<=": operator.le' '"<=": operator.lt'
mutate "timeout not an error" "data timeout fails" 'return None, "ERROR:timeout"' 'return 0.0, None'
mutate "orphans survive a timeout" "N17 no process" 'os.killpg(p.pid, signal.SIGKILL)' 'p.kill()'
mutate "non-utf8 output crashes" "N17 bad utf-8" 'text=True, errors="replace", start_new_session=True' 'text=True, start_new_session=True'
mutate "data regex is single-line" "R28 the data regex" 're.search(check["regex"], out, re.M)' 're.search(check["regex"], out)'
mutate "non-number accepted as 0" "non-numeric match" 'return None, f"ERROR:not a number: {m.group(1)!r}"' 'return 0.0, None'
mutate "nonzero exit ignored" "nonzero exit fails" 'if p.returncode != 0:
        last' 'if False:
        last'
mutate "inf accepted" "M08" 'if not math.isfinite(val):' 'if False:'
mutate "comma not stripped" "M13" 'float(m.group(1).replace(",", ""))' 'float(m.group(1))'
# --- screen.sh
smutate "screen not blind" "screen is blind" 's/--no-tools --no-session --no-skills --no-context-files/--no-session/'
smutate "screen not headless" "M16" 's/pi -p --model/pi --model/'
smutate "SCREEN_MODEL ignored" "SCREEN_MODEL overrides" 's/MODEL=\${SCREEN_MODEL:-/MODEL=${XSCREEN_MODEL:-/'
smutate "fixed nonce" "P16 the nonce differs" 's/^NONCE=.*/NONCE=00000000deadbeef/'
smutate "no untrusted warning" "P16 the screen input marks" '/untrusted data/d'
smutate "screen does not create the directory" "P15" '/^mkdir -p/d'
smutate "screen has no default output" "N14 the documented two-argument" 's/OUT=\${3:-\${RES%.md}.screen.md}/OUT=$3/'
smutate "screen ignores model failure" "S1 a failing model call" 's/^set -euo pipefail/set -u/'
smutate "screen sends only the top of the eval" "S2 S3" 's/cat "\$RES"/head -1 "$RES"/'
smutate "screen sends only the top of the decision" "S2 S3" 's/cat "\$DEC"/head -3 "$DEC"/'
smutate "screen appends to old output" "writes the model output and overwrites" 's/> "\$OUT"$/>> "$OUT"/'
smutate "screen swaps decision and eval" "S03 the decision file precedes" 's/echo "=== DECISION FILE \$NONCE (\$DEC) ==="; cat "\$DEC"; echo "=== END DECISION FILE \$NONCE ==="/echo "=== EVAL OUTPUT \$NONCE (\$RES) ==="; cat "\$RES"; echo "=== END EVAL OUTPUT \$NONCE ==="/'
wait
total=0; caught=0
for r in $(seq 1 $n); do [ -f "$T/r$r" ] || continue; total=$((total+1)); cat "$T/r$r"; grep -q "^caught" "$T/r$r" && caught=$((caught+1)); done
echo "planted bugs caught: $caught/$total"
[ $caught -eq $total ]
