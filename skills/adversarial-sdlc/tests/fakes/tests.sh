#!/usr/bin/env bash
# Fake tests role. FAKE_TESTS=good | impl (writes a real implementation instead of a stub)
mkdir -p src/main/kotlin/clamp src/test/kotlin/clamp
if [ "${FAKE_TESTS:-good}" = impl ]; then
  printf 'package clamp\n\nfun clamp(x: Int, lo: Int, hi: Int): Int {\n    require(lo <= hi)\n    return maxOf(lo, minOf(x, hi))\n}\n' > src/main/kotlin/clamp/Clamp.kt
else
  printf 'package clamp\n\nfun clamp(x: Int, lo: Int, hi: Int): Int = TODO()\n' > src/main/kotlin/clamp/Clamp.kt
fi
cat > src/test/kotlin/clamp/ClampTest.kt <<'K'
package clamp

import io.kotest.assertions.throwables.shouldThrow
import io.kotest.core.spec.style.FunSpec
import io.kotest.matchers.ints.shouldBeInRange
import io.kotest.matchers.shouldBe
import io.kotest.property.Arb
import io.kotest.property.arbitrary.int
import io.kotest.property.checkAll

class ClampTest : FunSpec({
    test("AC-1 clamps above hi") { clamp(5, 0, 3) shouldBe 3 }
    test("AC-2 equal bounds") { clamp(7, 4, 4) shouldBe 4 }
    test("INV-1 result within bounds and identity inside") {
        checkAll(Arb.int(-50..50), Arb.int(-20..20), Arb.int(0..20)) { x, lo, width ->
            val hi = lo + width
            val r = clamp(x, lo, hi)
            r shouldBeInRange lo..hi
            if (x in lo..hi) r shouldBe x
        }
    }
    test("FM-1 lo above hi is rejected, and retrying gives the same rejection") {
        repeat(2) { shouldThrow<IllegalArgumentException> { clamp(1, 3, 2) } }
    }
})
K
cat > src/test/tests-inventory.json <<'J'
{"cases": [
 {"class": "clamp.ClampTest", "name": "AC-1 clamps above hi", "kind": "example", "spec": ["AC-1"]},
 {"class": "clamp.ClampTest", "name": "AC-2 equal bounds", "kind": "example", "spec": ["AC-2"]},
 {"class": "clamp.ClampTest", "name": "INV-1 result within bounds and identity inside", "kind": "property", "spec": ["INV-1"]},
 {"class": "clamp.ClampTest", "name": "FM-1 lo above hi is rejected, and retrying gives the same rejection", "kind": "crash-retry-replay", "spec": ["FM-1"]}
]}
J
printf '# Tests\n\nOne spec, four cases.\n\n## Spec gaps\n\n' > TESTS.md
if [ "${FAKE_TESTS:-good}" = symlink ]; then ln -s "$HOME/.pi/agent/settings.json" src/test/kotlin/clamp/Leak.kt; fi
