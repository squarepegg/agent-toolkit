#!/usr/bin/env bash
# Fake attacker. FAKE_ATTACK=dry | malformed
mkdir -p attack
case "${FAKE_ATTACK:-dry}" in
  malformed) echo '{"findings": ""}' > attack/findings.json ;;
  always|wrongtest|hang)
    R="$(grep -o 'src/test/[^ ]*round[0-9]*' attack/TASK.md | head -1)"; PKG="$(grep -o 'package attack.round[0-9]*' attack/TASK.md | cut -d' ' -f2)"
    mkdir -p "$R"
    case "$FAKE_ATTACK" in
      always) BODY='error("always fails")' ;;
      wrongtest) BODY='clamp(5, 0, 3) shouldBe 4' ;;
      hang) BODY='Thread.sleep(600_000)' ;;
    esac
    printf 'package %s\n\nimport clamp.clamp\nimport io.kotest.core.spec.style.FunSpec\nimport io.kotest.matchers.shouldBe\n\nclass F1_ClaimTest : FunSpec({\n    test("F1 AC-1 claim") { %s }\n})\n' "$PKG" "$BODY" > "$R/F1_ClaimTest.kt"
    printf '{"findings": [{"id": "F1", "title": "claim", "spec_refs": ["AC-1"], "test_file": "%s/F1_ClaimTest.kt", "expected": "3", "actual": "x", "category": "acceptance"}]}\n' "$R" > attack/findings.json ;;
  catch)  # a real repro: fails on the planted lower-bound bug, passes on correct code
    R="$(grep -o 'src/test/[^ ]*round[0-9]*' attack/TASK.md | head -1)"; PKG="$(grep -o 'package attack.round[0-9]*' attack/TASK.md | cut -d' ' -f2)"
    mkdir -p "$R"
    printf 'package %s\n\nimport clamp.clamp\nimport io.kotest.core.spec.style.FunSpec\nimport io.kotest.matchers.shouldBe\n\nclass F1_LowerBoundTest : FunSpec({\n    test("F1 INV-1 value at lo stays lo") { clamp(0, 0, 5) shouldBe 0 }\n})\n' "$PKG" > "$R/F1_LowerBoundTest.kt"
    printf '{"findings": [{"id": "F1", "title": "lower bound shifted", "spec_refs": ["INV-1"], "test_file": "%s/F1_LowerBoundTest.kt", "expected": "0", "actual": "1", "category": "boundary"}]}\n' "$R" > attack/findings.json ;;
  *) echo '{"findings": [], "attempted": ["boundaries", "lo > hi"]}' > attack/findings.json ;;
esac
