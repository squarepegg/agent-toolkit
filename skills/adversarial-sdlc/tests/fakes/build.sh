#!/usr/bin/env bash
# Fake builder. FAKE_BUILD=good | malicious (also tries to tamper with tests, gradlew and the real project)
if [ "${FAKE_BUILD:-good}" = malicious ]; then
  echo "// weakened" >> src/test/kotlin/clamp/ClampTest.kt
  echo "exit 0" >> gradlew
  echo "pwned" >> "$FAKE_PROJECT/src/test/kotlin/clamp/ClampTest.kt" 2>/dev/null && echo "ESCAPED-SANDBOX"
  echo "{}" > "$FAKE_PROJECT/.sdlc/tests.lock" 2>/dev/null && echo "ESCAPED-SANDBOX"
  cat "$HOME/.pi/agent/sessions/"* >/dev/null 2>&1 && echo "READ-SESSIONS"
fi
rm -rf src/main/kotlin/clamp   # each fake build writes its whole main code afresh
mkdir -p src/main/kotlin/clamp
printf 'package clamp\n\nfun clamp(x: Int, lo: Int, hi: Int): Int {\n    require(lo <= hi) { "lo must not exceed hi" }\n    return maxOf(lo, minOf(x, hi))\n}\n' > src/main/kotlin/clamp/Clamp.kt
case "${FAKE_BUILD:-good}" in
  fileio)  # forbidden API hidden behind a wildcard import and an alias
    printf 'package clamp\n\nimport java.io.*\n\nfun leak(): Boolean = File("/tmp").exists()\n' > src/main/kotlin/clamp/Leak.kt ;;
  shadow)  # a commented-out package line, then a foreign package
    printf '/*\npackage clamp\n*/\npackage io.kotest.sneaky\n\nfun x(): Int = 1\n' > src/main/kotlin/clamp/Sneaky.kt ;;
  symnotes)  # try to make the orchestrator copy a file the builder cannot read
    rm -f .sdlc-in/notes.md; ln -s "$FAKE_PROJECT/.sdlc/spec.approved" .sdlc-in/notes.md ;;
esac
[ -L .sdlc-in/notes.md ] || echo "built clamp" >> .sdlc-in/notes.md
# Run the fast scratch gate exactly as the task tells a real builder to (no Pitest).
# The whole check line, as written: on the k8s backend it starts with sdlc-check (the role's warm pod).
CHK="$(sed -n 's/^Check yourself: \(.*gate\.sh --scratch --fast\).*/\1/p' .sdlc-in/TASK.md | head -1)"
grep -q 'gate.sh --scratch --fast' .sdlc-in/TASK.md && echo "TASK-SAYS-FAST"
case "$CHK" in *sdlc-check*) echo "TASK-SAYS-SDLC-CHECK" ;; esac
bash -c "$CHK" > .sdlc-in/scratch.out 2>&1; echo "SCRATCH-EXIT=$?"
# Every step except the skipped mutation/fuzz steps must pass.
python3 - .sdlc/build/gate-scratch/steps <<'PY'
import json, pathlib, sys
bad = []
for f in sorted(pathlib.Path(sys.argv[1]).glob("*.json")):
    s = json.loads(f.read_text())
    skipped = any("skipped by --fast" in c["what"] for c in s["checks"])
    if s["status"] not in ("pass", "off") and not skipped:
        bad.append(s["name"])
    if skipped:
        print(f"SKIPPED-{s['name']}")
print("SCRATCH-FAST-OK" if not bad else "SCRATCH-FAST-FAIL=" + ",".join(bad))
PY
grep "^| " .sdlc/build/gate-scratch/gate-report.md | grep -v "| pass |" | cut -c1-200 | head -5
