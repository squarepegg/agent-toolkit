#!/bin/bash
# Self-test for eval.py. Needs TLA2TOOLS_JAR and java (only the TLC cases use them). Safe to run in parallel:
# it uses its own temp dir and unique process names.
set -u
export JOBS=${JOBS:-1}
HERE=$(cd "$(dirname "$0")" && pwd)
EVAL="${EVAL_PY:-$HERE/../scripts/eval.py}"
SCREEN="${SCREEN_SH:-$HERE/../scripts/screen.sh}"
T=$(mktemp -d); trap 'rm -rf "$T"' EXIT
cp -r "$HERE/fixture" "$T/f"
cd "$T/f" && git init -q && git config user.email t@t && git config user.name t
PASS=0; FAILN=0
# STOP_ON (set by planted-bugs.sh): stop at the first failing test whose name contains it - a mutant is caught there.
check() { if eval "$2"; then PASS=$((PASS+1)); else FAILN=$((FAILN+1)); echo "FAIL: $1"
  if [ -n "${STOP_ON:-}" ] && [[ "$1" == *"$STOP_ON"* ]]; then echo "eval-test: stopped at the expected failure"; exit 1; fi; fi; }
run() { out=$(python3 "$EVAL" "$@" 2>&1); rc=$?; }
nojar() { out=$(env -u TLA2TOOLS_JAR python3 "$EVAL" "$@" 2>&1); rc=$?; }
commit() { git add -A && git commit -qm "$1"; }
SLEEP=$((600 + (RANDOM % 300)))   # unique per run, for the orphan test
sed -i.bak "s/@@ORPHAN@@/(sleep $SLEEP >\/dev\/null 2>\&1 \&); sleep $((SLEEP+3)) >\/dev\/null 2>\&1/" n17.yaml && rm -f n17.yaml.bak

# ---- registration: a snapshot of HEAD
run toy.yaml
check "uncommitted decision is refused (exit 2)" '[ $rc -eq 2 ] && grep -q "no commit" <<<"$out"'
run toy.yaml --draft
check "draft run is marked and never decisive, even on a clean tree" 'grep -q "NO: DRAFT run" <<<"$out" && grep -q "NOT DECISIVE: DRAFT" <<<"$out" && [ $rc -eq 1 ]'
commit fixture
run toy.yaml --draft
check "R16 --draft stays DRAFT when everything is committed" 'grep -q "NO: DRAFT run" <<<"$out" && ! grep -q "DECISIVE: split" <<<"$out" && [ $rc -eq 1 ]'

run toy.yaml --out "$T/r.md"
check "registered run exits 0" '[ $rc -eq 0 ]'
check "registration shows the commit and the snapshot" 'grep -q "Criteria registered: yes, evaluated on a snapshot of" <<<"$out"'
check "TLC ok verdict" 'grep -q "| split | C1 | TLC invariant .Inv. on .Toy.tla. | ok | ok |" <<<"$out"'
check "mutant caught for the named invariant" 'grep -q "| split | C1 | mutant .ToyMut.tla. of invariant .Inv. | VIOLATED:Inv | VIOLATED:Inv |" <<<"$out"'
check "data threshold passes" 'grep -q "| split | C2 | data .rate. | <= 0.01 | 0.004 |" <<<"$out"'
check "vacuity guard fails an option without a mutant" 'grep -q "| nomutant | C1 | vacuity guard |" <<<"$out"'
check "an unset threshold is untested, not a pass" 'grep -q "| nomutant | C2 | data | threshold not set" <<<"$out"'
check "nomutant is INVALID" 'grep -q -- "- nomutant: INVALID" <<<"$out"'
check "lint finds the two-object action" 'grep -q "| atomic | 3 | 3 | 1 | .* | Both: A+B |" <<<"$out"'
check "lint: a channel is not a second object" 'grep -q "| split | 3 | 2 | 0 | .* | none |" <<<"$out"'
check "M09 loc is pinned (comments and blank lines are not counted)" 'grep -q "| split | 3 | 2 | 0 | 13 | none |" <<<"$out"'
check "dominance: split is decisive" 'grep -q "DECISIVE: split" <<<"$out"'
check "--out writes the table" '[ -s "$T/r.md" ]'

# uncommitted edits cannot change the verdict
cp Toy.tla Toy.tla.orig; sed -i.bak 's/Inv == a <= 2/Inv == a <= 0/' Toy.tla; rm -f Toy.tla.bak
run toy.yaml
check "R05 an uncommitted edit to a spec does not change the verdict (HEAD is evaluated)" '[ $rc -eq 0 ] && grep -q "DECISIVE: split" <<<"$out"'
run toy.yaml --draft
check "a draft does see the uncommitted edit" 'grep -q "NO: DRAFT" <<<"$out" && grep -q -- "- split: INVALID" <<<"$out"'
mv Toy.tla.orig Toy.tla
echo "# edited" >> toy.yaml
run toy.yaml
check "a modified decision file is refused" '[ $rc -eq 2 ] && grep -q "differs from its committed version" <<<"$out"'
git checkout -q toy.yaml
git update-index --assume-unchanged toy.yaml; echo "# local edit" >> toy.yaml
run toy.yaml
check "R05 assume-unchanged does not hide a modified decision file" '[ $rc -eq 2 ] && grep -q "differs from its committed version" <<<"$out"'
git update-index --no-assume-unchanged toy.yaml; git checkout -q toy.yaml
git update-index --skip-worktree toy.yaml; echo "# local edit" >> toy.yaml
run toy.yaml
check "R05 skip-worktree does not hide a modified decision file" '[ $rc -eq 2 ] && grep -q "differs from its committed version" <<<"$out"'
git update-index --no-skip-worktree toy.yaml; git checkout -q toy.yaml
echo "stray" > stray.txt
run toy.yaml
check "an untracked file elsewhere is irrelevant" '[ $rc -eq 0 ]'
rm -f stray.txt
run n12.yaml
check "N12 a data command writes only into the throwaway snapshot" '[ ! -e side-effect.txt ] && grep -q "DECISIVE: writer" <<<"$out"'

run tie.yaml
check "equal metrics are not decisive (exit 1)" 'grep -q "NOT DECISIVE:\*\* several valid options and none is no worse" <<<"$out" && [ $rc -eq 1 ]'
check "a mutant that is the same file as the real spec does not count (tie)" 'grep -q "the mutant is the same file as the real spec" <<<"$out" && grep -q -- "- wrongreason: INVALID" <<<"$out"'
run wr.yaml
check "a mutant that TLC catches for the wrong reason (deadlock, not the invariant) does not count" 'grep -q "mutant .WrMut.tla. of invariant .Inv. | VIOLATED:Inv | DEADLOCK |" <<<"$out" && grep -q -- "- wrong: INVALID" <<<"$out"'

run untested.yaml
check "declared-only option is UNTESTED, not VALID" 'grep -q -- "- declared: UNTESTED" <<<"$out"'
check "an untested option blocks DECISIVE" 'grep -q "NOT DECISIVE:\*\* untested options (declared)" <<<"$out"'
out=$(cd /tmp && python3 "$EVAL" "$T/f/untested.yaml" 2>&1)
check "data cmd runs in the decision root of the snapshot" 'grep -q "| measured | C2 | data .rate. | <= 0.01 | 0.004 |" <<<"$out"'
mkdir -p sub && cd sub && out=$(python3 "$EVAL" ../toy.yaml 2>&1); rc=$?; cd ..
check "eval works from a subdirectory of the repo" '[ $rc -eq 0 ]'
rmdir sub

# ---- TLA2TOOLS_JAR is optional for data-only decisions, and checked early otherwise
nojar nota.yaml
check "data-only eval needs no TLA2TOOLS_JAR" '[ $rc -eq 0 ] && grep -q "DECISIVE: low" <<<"$out"'
nojar toy.yaml
check "tlc check without the jar fails (exit 2)" '[ $rc -eq 2 ] && grep -q "TLA2TOOLS_JAR must point to" <<<"$out"'
out=$(TLA2TOOLS_JAR=/nonexistent.jar python3 "$EVAL" toy.yaml 2>&1); rc=$?
check "R17 a jar path that does not exist fails (exit 2)" '[ $rc -eq 2 ] && grep -q "TLA2TOOLS_JAR must point to" <<<"$out"'
out=$(JOBS=0 python3 "$EVAL" toy.yaml 2>&1); rc=$?
check "R17 JOBS=0 fails (exit 2)" '[ $rc -eq 2 ] && grep -q "JOBS must be" <<<"$out"'

# ---- data-check operators and error paths
run edge.yaml
check "op == passes" 'grep -q -- "- eq: VALID" <<<"$out"'
check "R19 op == fails when the value differs" 'grep -q -- "- eqfail: INVALID" <<<"$out"'
check "op >= passes at the boundary" 'grep -q -- "- ge: VALID" <<<"$out"'
check "op < fails at the boundary" 'grep -q -- "- lt: INVALID" <<<"$out"'
check "op > fails at the boundary" 'grep -q -- "- gt: INVALID" <<<"$out"'
check "R28 the data regex is multi-line (^ matches after a newline)" 'grep -q -- "- anchored: VALID" <<<"$out"'
check "regex that does not match fails" 'grep -q "ERROR:regex did not match" <<<"$out"'
check "non-numeric match fails" 'grep -q "ERROR:not a number" <<<"$out"'
check "nonzero exit fails with its message" 'grep -q "ERROR:exit 3: boom" <<<"$out"'
check "data timeout fails" 'grep -q "| timeout | C1 | data .* ERROR:timeout" <<<"$out"'
check "TLC with no verdict is an error, not ok" 'grep -q "| nover | C1 | TLC invariant .Nope. .* | ok | ERROR:no verdict" <<<"$out" && grep -q -- "- nover: INVALID" <<<"$out"'
check "ignore: removes a variable from the lint" 'grep -q "| ignorelint | 2 | 2 | 0 |" <<<"$out"'
check "ignore: leaves no unmapped variable" '! grep -q "variables mapped to objects" <<<"$out"'
check "owner other than engineering is flagged" 'grep -q "Owner is product" <<<"$out"'

# ---- registration of dependencies and other repos
git rm -q -f Helper.tla 2>/dev/null; true
echo "---- MODULE Helper ----
Limit == 5
====" > Helper.tla
run r07.yaml
check "R07 a module the spec EXTENDS but that was never committed is not in the snapshot" 'grep -q -- "- ext: INVALID" <<<"$out" && ! grep -q "\*\*DECISIVE" <<<"$out"'
rm -f Helper.tla
run m10b.yaml
check "M10 reversible: false with both committed files is decisive" 'grep -q "DECISIVE: lockin" <<<"$out" && [ $rc -eq 0 ]'
run dd/m10e.yaml
check "M10 evidence files outside the decision directory are exported, and the verdict is decisive" 'grep -q "DECISIVE: o" <<<"$out" && [ $rc -eq 0 ]'
ln -s Toy.tla Link.tla; commit link
sed 's/spec: Toy.tla/spec: Link.tla/g; s/^id: tie/id: tielink/' tie.yaml > tielink.yaml; commit tl
run tielink.yaml
check "N08 a symlinked dependency is refused" '[ $rc -eq 2 ] && grep -q "Link.tla: must not be a symlink" <<<"$out"'
git rm -q -f Link.tla tielink.yaml; git commit -qm rmlink
mkdir "$T/nogit" && cp toy.yaml nota.yaml "$T/nogit/"
run "$T/nogit/nota.yaml"
check "outside git the run is refused" '[ $rc -eq 2 ] && grep -q "not in a git repo" <<<"$out"'
mkdir -p "$T/other" && (cd "$T/other" && git init -q && git config user.email t@t && git config user.name t && echo "q 1" > far.txt && git add far.txt && git commit -qm far)
sed "s|^inputs:.*||; s|^falsifier: none, fixture|falsifier: none, fixture\ninputs: [$T/other/far.txt]|; s/^id: n13b/id: crossrepo/" n13b.yaml | sed 's/name: "a | b"/name: ab/' > crossrepo.yaml; commit cr
run crossrepo.yaml
check "P05 a dependency from another git repo is refused" '[ $rc -eq 2 ] && grep -q "outside this git repo" <<<"$out"'
git rm -q -f crossrepo.yaml; git commit -qm rmcr
echo "x" > untracked-input.txt
sed 's/^inputs: \[input.txt\]/inputs: [untracked-input.txt]/; s/^id: edge/id: untrackedin/' edge.yaml > untrackedin.yaml; commit u2; git rm -q --cached untracked-input.txt 2>/dev/null; git commit -qm u3 2>/dev/null
run untrackedin.yaml
check "an untracked input is refused" '[ $rc -eq 2 ] && grep -q "untracked-input.txt: not committed" <<<"$out"'
git rm -q -f untrackedin.yaml; rm -f untracked-input.txt; git commit -qm rmu

# ---- --out
run toy.yaml --out toy.yaml
check "N16 --out cannot overwrite the decision file" '[ $rc -eq 2 ] && grep -q -- "--out" <<<"$out"'
run toy.yaml --out Toy.tla
check "N16 --out cannot overwrite a dependency" '[ $rc -eq 2 ] && grep -q -- "--out" <<<"$out"'
mkdir -p sub; run toy.yaml --out sub/r.md
check "R26 --out in a subdirectory of the tree is fine (the snapshot is untouched)" '[ $rc -eq 0 ] && [ -s sub/r.md ]'
rm -rf sub

# ---- regressions from the attack rounds
run m01.yaml
check "M01 a vacuous invariant gets no valid mutant" 'grep -q "| cheat | C1 | vacuity guard |" <<<"$out" && grep -q -- "- cheat: INVALID" <<<"$out" && ! grep -q "DECISIVE: cheat" <<<"$out"'
run m02.yaml
check "M02 a mutant with its own expect is rejected (exit 2)" '[ $rc -eq 2 ] && grep -q "unknown tlc keys .*expect" <<<"$out"'
run m02b.yaml
check "M02 a mutant with its own constants is rejected (exit 2)" '[ $rc -eq 2 ] && grep -q "unknown tlc keys .*constants" <<<"$out"'
run m03.yaml
check "M03 a criterion with no computed check makes the option UNTESTED" 'grep -q -- "- lazy: UNTESTED (criteria without a computed check: C1)" <<<"$out" && ! grep -q "DECISIVE: lazy" <<<"$out" && [ $rc -eq 1 ]'
run r02c.yaml
check "R01 a user-written cfg key is rejected (there is no cfg input)" '[ $rc -eq 2 ] && grep -q "unknown tlc keys" <<<"$out"'
run r01.yaml
check "R09 an invariant name with a newline is rejected" '[ $rc -eq 2 ] && grep -q "needs an invariant (an identifier)" <<<"$out"'
run r02.yaml
check "R02 a constant named like an operator defined in the spec is refused" '[ $rc -eq 2 ] && grep -q "constant Inv is defined in the spec" <<<"$out"'
run r02b.yaml
check "R02 a string constant value is rejected" '[ $rc -eq 2 ] && grep -q "constants map identifiers to booleans" <<<"$out"'
run r13b.yaml
check "R13 an invariant named Deadlock is not confused with a deadlock" 'grep -q -- "- collide: INVALID" <<<"$out" && grep -q "VIOLATED:Deadlock | DEADLOCK" <<<"$out" && ! grep -q "\*\*DECISIVE" <<<"$out"'
run r13c.yaml
check "R13 a mutant cannot bring its own next or deadlock flag" '[ $rc -eq 2 ] && grep -q "a mutant takes only spec and invariant" <<<"$out"'
run m05.yaml
check "M05 a mutant with no real check to mutate is rejected (exit 2)" '[ $rc -eq 2 ] && grep -q "has no real tlc check to mutate" <<<"$out"'
run m07.yaml
check "M07 duplicate option ids are rejected (exit 2)" '[ $rc -eq 2 ] && grep -q "option ids must be unique" <<<"$out"'
run m07b.yaml
check "N14 duplicate criteria ids are rejected" '[ $rc -eq 2 ] && grep -q "criteria ids must be unique" <<<"$out"'
run m08.yaml
check "M08 inf is not a measurement" 'grep -q "ERROR:not finite" <<<"$out" && grep -q -- "- broken: INVALID" <<<"$out" && [ $rc -eq 1 ]'
run m10.yaml
check "M10 reversible: false is not decisive without attack_report and blind_check" 'grep -q "NOT DECISIVE:\*\* reversible: false needs" <<<"$out" && [ $rc -eq 1 ]'
run m10c.yaml
check "P06 the same file for attack_report and blind_check is refused" '[ $rc -eq 2 ] && grep -q "two different files" <<<"$out"'
run m10d.yaml
check "P06 an empty evidence file is refused" '[ $rc -eq 2 ] && grep -q "must not be empty" <<<"$out"'
run r29.yaml
check "R29 attack_report without blind_check is rejected" '[ $rc -eq 2 ] && grep -q "go together" <<<"$out"'
run r20.yaml
check "R20 a missing evidence file is refused" '[ $rc -eq 2 ] && grep -q "ghost.md: missing" <<<"$out"'
run r21.yaml
check "R21 the decision file as its own evidence is refused" '[ $rc -eq 2 ] && grep -q "two different files, and not the decision file" <<<"$out"'
run m11.yaml
check "M11 the vacuity guard is per criterion" 'grep -q "| partial | C3 | vacuity guard" <<<"$out" && grep -q -- "- partial: INVALID" <<<"$out"'
run r22.yaml
check "R22 an uncaught mutant (it cannot violate InvTrue) makes the option INVALID" 'grep -q -- "- two: INVALID" <<<"$out" && grep -q "mutant .ToyMut.tla. of invariant .InvTrue. | VIOLATED:InvTrue | ok" <<<"$out" && ! grep -q "\*\*DECISIVE" <<<"$out"'
run r30.yaml
check "S30 a mutant that rewrites the invariant is refused" 'grep -q "the mutant changes the definition of invariant Inv" <<<"$out"'
check "S30 a mutant with too many changed lines is refused" 'grep -q "the mutant changes 10 lines (at most 6)" <<<"$out"'
check "S30 an identical mutant is refused" 'grep -q "the mutant is identical to the real spec" <<<"$out"'
check "S30 the real spec as its own mutant is refused" 'grep -q "the mutant is the same file as the real spec" <<<"$out" && ! grep -q "\*\*DECISIVE" <<<"$out"'
run r18.yaml
check "R18 a check with both tlc and data keys is rejected" '[ $rc -eq 2 ] && grep -q "exactly one of tlc/data/declared" <<<"$out"'
run m12a.yaml
check "M12 an unknown op is a clean error (exit 2)" '[ $rc -eq 2 ] && grep -q "unknown op" <<<"$out" && ! grep -q Traceback <<<"$out"'
run m12b.yaml
check "M12 an empty threshold is a clean error (exit 2)" '[ $rc -eq 2 ] && grep -q "threshold must be a finite number" <<<"$out"'
run m13.yaml
check "M13 comma-grouped numbers parse" 'grep -q "| grouped | C1 | data .v. | <= 2000 | 1234.0 |" <<<"$out"'
run tie.yaml
check "M14 exit 1 when not decisive (several valid)" '[ $rc -eq 1 ]'
run m08.yaml
check "M14 exit 1 when no option is valid" '[ $rc -eq 1 ]'
run toy.yaml
check "M14 exit 0 only when decisive" '[ $rc -eq 0 ]'
run n02.yaml
check "N02 a non-mutant tlc check must expect ok (exit 2)" '[ $rc -eq 2 ] && grep -q "must expect ok" <<<"$out"'
run n03.yaml
check "N03 every ok check needs its own mutant" 'grep -q "vacuity guard | a caught mutant of the real spec that breaks invariant .InvTrue." <<<"$out" && grep -q -- "- rides: INVALID" <<<"$out"'
run n_spec.yaml
check "N14 a mutant identical to the real spec does not certify the ok check" 'grep -q "| cheat | C1 | vacuity guard |" <<<"$out" && grep -q -- "- cheat: INVALID" <<<"$out"'
run long.yaml
check "R28 a multi-line state value is joined for the lint" 'grep -q "| wide | 2 | 1 | 1 |" <<<"$out"'
run rootout.yaml
check "root outside the git repo is refused" '[ $rc -eq 2 ] && grep -q "root is outside the git repo" <<<"$out"'
run n05.yaml
check "N05 the lint measures the options own checked model" 'grep -q "| atomic | 3 | 3 | 1 | 13 | Both: A+B |" <<<"$out"'
run n05b.yaml
check "N05 a mutant with no real check is rejected before any lint" '[ $rc -eq 2 ] && grep -q "has no real tlc check to mutate" <<<"$out"'
run r28.yaml
check "S04 with lint, tlc checks on different models are rejected (no decoy first check)" '[ $rc -eq 2 ] && grep -q "all tlc checks must use one model" <<<"$out"'
run n07.yaml
check "N07 a TBD competitor is UNTESTED, so the other option is not DECISIVE" 'grep -q -- "- unfinished: UNTESTED" <<<"$out" && ! grep -q "\*\*DECISIVE" <<<"$out" && [ $rc -eq 1 ]'
run n11.yaml
check "N11 a single-variable spec is parsed by the lint" 'grep -q "| one | 1 | 1 | 0 |" <<<"$out" && grep -q -- "- one: VALID" <<<"$out"'
run p03.yaml
check "P03 a spec printing the success line does not make a violation ok" 'grep -q "| spoof | C1 | TLC invariant .Inv. .* | ok | VIOLATED:Inv" <<<"$out" && grep -q -- "- spoof: INVALID" <<<"$out"'
run p03b.yaml
check "P03 a success line printed by the spec does not hide a TLC failure" 'grep -q "| spoof | C1 | TLC invariant .Inv. .* | ok | ERROR:no verdict" <<<"$out" && grep -q -- "- spoof: INVALID" <<<"$out"'
mkdir -p "$T/jbin"
cat > "$T/jbin/java" <<'J'
#!/bin/bash
echo "Progress(1) at 12:00:00: 50 distinct states found, 0 states left on queue."
echo "Model checking completed. No error has been found."
echo "9 states generated, 3,450 distinct states found, 0 states left on queue."
exit ${STUB_JAVA_RC:-0}
J
chmod +x "$T/jbin/java"
out=$(STUB_JAVA_RC=3 PATH="$T/jbin:$PATH" python3 "$EVAL" p03.yaml 2>&1)
check "P03 a non-zero TLC exit code is never ok, even with the success line printed" 'grep -q "| spoof | C1 | TLC invariant .Inv. .* | ok | ERROR:no verdict" <<<"$out"'
out=$(STUB_JAVA_RC=0 PATH="$T/jbin:$PATH" python3 "$EVAL" p03.yaml 2>&1)
check "P03 control: exit 0 with the success line and states is ok" 'grep -q "| spoof | C1 | TLC invariant .Inv. .* | ok | ok | 3450 |" <<<"$out"'
check "R28 the final state count wins over progress lines, and commas are stripped" 'grep -q "| ok | ok | 3450 |" <<<"$out"'
run z0.yaml
check "N03 a spec with no states is not ok" 'grep -q "ERROR:no states explored" <<<"$out" && grep -q -- "- empty: INVALID" <<<"$out"'
run z_lint0.yaml
check "N14 a lint on a graph with no transitions fails" 'grep -q "no states or no transitions" <<<"$out" && grep -q -- "- still: INVALID" <<<"$out"'
run rev.yaml
check "N14 reversible must be a boolean" '[ $rc -eq 2 ] && grep -q "reversible must be true or false" <<<"$out"'
run mo.yaml
check "N14 mutant_of must equal the check criterion" '[ $rc -eq 2 ] && grep -q "mutant_of needs a tlc check and must equal" <<<"$out"'
run r26.yaml
check "R26 an unknown top-level key (a typo) is rejected" '[ $rc -eq 2 ] && grep -q "unknown key .scope." <<<"$out"'
run r27.yaml
check "R27 a forged verdict in owner does not reach the report" '! grep -q "^\*\*DECISIVE: evil" <<<"$out" && ! grep -q "^- evil: VALID" <<<"$out"'
run n13.yaml
check "N13 forged table rows and verdict lines do not appear" '! grep -q "^\*\*DECISIVE: evil" <<<"$out" && ! grep -q "^- evil: VALID" <<<"$out" && ! grep -q "^| victim | C1 | data fake" <<<"$out"'
printf 'id: crlf\nquestion: "a\\rb"\nowner: engineering\nreversible: true\nroot: .\nfalsifier: "x\\r| y"\ncriteria: [{id: C1, text: x, kind: data}]\noptions:\n  - id: o\n    checks: [{criterion: C1, data: {name: r, cmd: "echo r 1", regex: "r (\\\\d+)", op: "<=", value: 5}}]\n' > crlf.yaml; commit crlf
run crlf.yaml
check "R28 carriage returns and pipes in free text are neutralised" '! grep -q $'"'"'\r'"'"' <<<"$out" && grep -q "Falsifier: x / y" <<<"$out"'
run n10a.yaml
check "N10 yaml booleans and ints as ids are rejected" '[ $rc -eq 2 ] && grep -q "ids must be unique strings" <<<"$out"'
run n10b.yaml
check "N10 null collections are a clean error" '[ $rc -eq 2 ] && grep -q "non-empty list" <<<"$out" && ! grep -q Traceback <<<"$out"'
run r25a.yaml
check "R25 an infinite timeout is rejected" '[ $rc -eq 2 ] && grep -q "timeout must be a positive, finite number" <<<"$out"'
run r25b.yaml
check "R25 nested lists in objects are rejected" '[ $rc -eq 2 ] && grep -q "objects must map names" <<<"$out"'
run p17.yaml
check "P17 a regex without a capture group is rejected" '[ $rc -eq 2 ] && grep -q "capture group" <<<"$out"'
run n13b.yaml
check "a pipe and a backtick in a data name are neutralised in the table" '! grep -q "| a | b" <<<"$out" && grep -q "a / b . c" <<<"$out"'
run n17.yaml
check "N17 bad utf-8 fails only its check; the timed-out option is INVALID" 'grep -q -- "- orphan: INVALID" <<<"$out" && grep -q -- "- utf: VALID" <<<"$out"'
check "N17 no process from this run survives a timeout" '! pgrep -f "sleep $SLEEP" >/dev/null'

# ---- round 4 (S01..S14)
ln -s /etc/hostname evil-link; commit evilsym
run toy.yaml
check "S01 a committed symlink anywhere in the exported directory is refused" '[ $rc -eq 2 ] && grep -q "a symlink in the exported tree is refused" <<<"$out"'
git rm -q -f evil-link; git commit -qm rmevil
printf 'Toy.tla export-ignore\nToy.tla export-subst\n' > .gitattributes; commit attrs
run toy.yaml
check "S09 .gitattributes (export-ignore) does not change what is exported" '[ $rc -eq 0 ] && grep -q "DECISIVE: split" <<<"$out"'
git rm -q -f .gitattributes; git commit -qm rmattrs
run s02.yaml
check "S02 a data command that rewrites the spec only damages its own copy" 'grep -q "DECISIVE: rewriter" <<<"$out"'
run s06.yaml
check "S06 a directory as evidence is refused" '[ $rc -eq 2 ] && grep -q "must be a file" <<<"$out"'
run s10.yaml
check "S10 a variable in two objects is rejected" '[ $rc -eq 2 ] && grep -q "a variable may belong to only one object" <<<"$out"'
run s12a.yaml
check "S12 s12a a negative constant is rejected (exit 2)" '[ $rc -eq 2 ] && grep -q "constants map identifiers to booleans" <<<"$out"'
run s12b.yaml
check "S12 s12b a constant above 2^31-1 is rejected (exit 2)" '[ $rc -eq 2 ] && grep -q "constants map identifiers to booleans" <<<"$out"'
run s12c.yaml
check "S12 s12c a boolean in an integer list is rejected (exit 2)" '[ $rc -eq 2 ] && grep -q "constants map identifiers to booleans" <<<"$out"'
run s13a.yaml
check "S13 a regex whose group 1 did not take part fails the check, not the run" 'grep -q "ERROR:capture group 1 did not match" <<<"$out" && ! grep -q Traceback <<<"$out"'
run s13b.yaml
check "S13 lint: false is rejected" '[ $rc -eq 2 ] && grep -q "lint must be a mapping" <<<"$out"'
run s13c.yaml
check "S13 unknown option and criterion keys are rejected" '[ $rc -eq 2 ] && grep -q "unknown criterion keys" <<<"$out" && grep -q "unknown keys .*lints" <<<"$out"'
run s13d.yaml
check "S13 a single evidence file is rejected whatever reversible says" '[ $rc -eq 2 ] && grep -q "go together" <<<"$out"'
run s11a.yaml
check "S11 integer-list constants render, and <= passes at the boundary" 'grep -q "DECISIVE: lst" <<<"$out"'
cp ToyMut.tla Untracked.tla
run s11b.yaml
check "S11 a mutant spec that was never committed is refused" '[ $rc -eq 2 ] && grep -q "Untracked.tla: not committed" <<<"$out"'
rm -f Untracked.tla
run s11c.yaml
check "S11 error lines printed by the spec mid-line do not count as a violation" 'grep -q "mutant .SpMut.tla. of invariant .Inv. | VIOLATED:Inv | ok" <<<"$out" && grep -q -- "- o: INVALID" <<<"$out"'
run s11d.yaml
check "S11 two options tied at the top are not decisive, even with a worse third" 'grep -q "several valid options" <<<"$out" && [ $rc -eq 1 ]'
run s11e.yaml
check "S11 yaml anchors under x- keys work" 'grep -q "DECISIVE: anchored" <<<"$out"'
run s11f.yaml
check "S11 non-bool deadlock, non-string declared and name, and a bool timeout are rejected" '[ $rc -eq 2 ] && grep -q "deadlock must be true or false" <<<"$out" && grep -q "declared must be a string" <<<"$out" && grep -q "data name must be a string" <<<"$out" && grep -q "timeout must be a positive, finite number" <<<"$out"'
run s11g.yaml
check "S11 a non-string question is rejected" '[ $rc -eq 2 ] && grep -q "question must be a string" <<<"$out"'
run s11h.yaml
check "S11 an input directory that does not exist is refused" '[ $rc -eq 2 ] && grep -q "nodir: missing" <<<"$out"'
out=$(EVAL_TLC_TIMEOUT=abc python3 "$EVAL" toy.yaml 2>&1); rc=$?
check "S11 a non-numeric EVAL_TLC_TIMEOUT fails (exit 2)" '[ $rc -eq 2 ] && grep -q "EVAL_TLC_TIMEOUT must be" <<<"$out"'
mkdir -p "$T/jbin2"; printf '#!/bin/bash\necho "$@" >> "$JLOG"\nsleep 5\n' > "$T/jbin2/java"; chmod +x "$T/jbin2/java"
out=$(JLOG="$T/jlog" EVAL_TLC_TIMEOUT=1 PATH="$T/jbin2:$PATH" python3 "$EVAL" p03.yaml 2>&1)
check "S11 a TLC run that exceeds EVAL_TLC_TIMEOUT is ERROR:timeout" 'grep -q "ERROR:timeout" <<<"$out"'
rm -f "$T/jlog"; JLOG="$T/jlog" EVAL_TLC_TIMEOUT=1 PATH="$T/jbin2:$PATH" python3 "$EVAL" toy.yaml >/dev/null 2>&1
check "S07 every TLC run gets its own java.io.tmpdir" '[ "$(grep -o "java.io.tmpdir=[^ ]*" "$T/jlog" | sort -u | wc -l)" -ge 2 ]'
mkdir -p "$T/tmpx"; out=$(TMPDIR="$T/tmpx" python3 "$EVAL" s14.yaml 2>&1); rc=$?
check "S14 a failure after the snapshot leaves no temp dirs behind" '[ $rc -eq 2 ] && grep -q "defined in the spec" <<<"$out" && [ -z "$(ls -A "$T/tmpx")" ]'
ln -s toy.yaml lnk.yaml
run toy.yaml --out lnk.yaml
check "S13 --out through a symlink to the decision file is refused" '[ $rc -eq 2 ] && grep -q -- "--out" <<<"$out"'
rm -f lnk.yaml

# ---- screen.sh plumbing with a stubbed model (the real model is not deterministic and is not tested here)
mkdir "$T/bin"
cat > "$T/bin/pi" <<'S'
#!/bin/bash
echo "ARGS: $*" > "$STUB_LOG"; cat >> "$STUB_LOG"; echo "STUB-SCREEN"
exit ${STUB_PI_RC:-0}
S
chmod +x "$T/bin/pi"
export STUB_LOG="$T/stub.log"
for i in $(seq 1 30); do echo "filler $i" >> "$T/r.md"; done; echo "LAST-RESULT-LINE" >> "$T/r.md"
echo "# LAST-DECISION-LINE" >> nota.yaml
echo "OLD-CONTENT" > "$T/s.md"
PATH="$T/bin:$PATH" bash "$SCREEN" nota.yaml "$T/r.md" "$T/s.md" >/dev/null
git checkout -q nota.yaml
check "screen writes the model output and overwrites an old file (S6)" 'grep -q STUB-SCREEN "$T/s.md" && ! grep -q OLD-CONTENT "$T/s.md"'
check "screen uses the default model" 'grep -q "^ARGS: .*--model some-provider/some-model" "$STUB_LOG"'
check "M16 screen runs headless (-p)" 'grep -q "^ARGS: -p " "$STUB_LOG"'
check "screen is blind: no tools, no session, no context files" 'grep -q -- "--no-tools --no-session --no-skills --no-context-files" "$STUB_LOG"'
check "screen sends the prompt, decision file and eval table" 'grep -q "Do NOT recommend" "$STUB_LOG" && grep -q "id: nota" "$STUB_LOG" && grep -q "Decision eval: " "$STUB_LOG"'
check "S2 S3 the screen sends the END of the eval table and of the decision file" 'grep -q "LAST-RESULT-LINE" "$STUB_LOG" && grep -q "LAST-DECISION-LINE" "$STUB_LOG"'
check "P16 the screen input marks the data as untrusted, between nonce delimiters" 'grep -q "untrusted" "$STUB_LOG" && grep -Eq "=== DECISION FILE [0-9a-f]{8,} " "$STUB_LOG"'
check "S03 the decision file precedes the eval table, and both delimiters are closed with the nonce" 'a=$(grep -n "id: nota" "$STUB_LOG" | head -1 | cut -d: -f1); b=$(grep -n "Decision eval: " "$STUB_LOG" | head -1 | cut -d: -f1); [ "$a" -lt "$b" ] && grep -Eq "=== END DECISION FILE [0-9a-f]{8,} ===" "$STUB_LOG" && grep -Eq "=== END EVAL OUTPUT [0-9a-f]{8,} ===" "$STUB_LOG"'
cp "$STUB_LOG" "$T/stub1.log"
PATH="$T/bin:$PATH" bash "$SCREEN" nota.yaml "$T/r.md" "$T/s2.md" >/dev/null
check "P16 the nonce differs between runs" '! cmp -s <(grep -E "^=== DECISION FILE" "$T/stub1.log") <(grep -E "^=== DECISION FILE" "$STUB_LOG")'
SCREEN_MODEL=other/model PATH="$T/bin:$PATH" bash "$SCREEN" nota.yaml "$T/r.md" "$T/s3.md" >/dev/null
check "SCREEN_MODEL overrides the model" 'grep -q -- "--model other/model" "$STUB_LOG"'
PATH="$T/bin:$PATH" bash "$SCREEN" nota.yaml "$T/r.md" >/dev/null 2>&1; rc=$?
check "N14 the documented two-argument call writes <result>.screen.md" '[ $rc -eq 0 ] && [ -s "$T/r.screen.md" ]'
PATH="$T/bin:$PATH" bash "$SCREEN" nota.yaml "$T/r.md" "$T/deep/er/s.md" >/dev/null 2>&1; rc=$?
check "P15 the screen creates the output directory" '[ $rc -eq 0 ] && [ -s "$T/deep/er/s.md" ]'
out=$(STUB_PI_RC=7 PATH="$T/bin:$PATH" bash "$SCREEN" nota.yaml "$T/r.md" "$T/s4.md" 2>&1); rc=$?
check "S1 a failing model call fails the screen and prints no success line" '[ $rc -ne 0 ] && ! grep -q "^screen:" <<<"$out"'

# ---- the shipped template runs end to end (option-b removed: it needs specs that do not exist)
python3 - "$HERE/../templates/decision.yaml" decision-template.yaml <<'P'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
d["options"] = [o for o in d["options"] if o["id"] == "option-a"]
d.pop("objects"); d.pop("ignore")
d["criteria"] = [c for c in d["criteria"] if c["id"] == "C1"]
d["options"][0]["checks"] = [c for c in d["options"][0]["checks"] if "declared" not in c]
yaml.safe_dump(d, open(sys.argv[2], "w"))
P
commit tmpl
nojar decision-template.yaml
check "template data option evaluates" '[ $rc -eq 0 ] && grep -q "over-cap share" <<<"$out" && grep -q "DECISIVE: option-a" <<<"$out"'

# Unmapped variable fails the option.
sed -i.bak 's/channel: \[ch\]/channel: []/' toy.yaml && rm -f toy.yaml.bak && git commit -qam unmapped
run toy.yaml
check "unmapped variable fails the option" 'grep -q "unmapped: ch" <<<"$out"'

echo "eval-test: $PASS passed, $FAILN failed"
[ $FAILN -eq 0 ]
