// Engine self-test, template v1. Copy it with the engine: it is what proves the harness can fail.
// Every runSuite call pins explicitSingleSeed/explicitSeedCount/explicitSeedStart, so exported SIM_* do not leak in.
package dst

import io.kotest.assertions.throwables.shouldThrow
import io.kotest.matchers.shouldBe
import io.kotest.matchers.string.shouldContain
import org.junit.jupiter.api.Test

class SimulationEngineSelfTest {
    private fun run(
        declared: Set<String> = setOf("a"),
        required: Set<String> = declared,
        count: Int = 20,
        start: Long = 1L,
        scenario: (SimulationContext) -> String,
    ) = DeterministicSimulationRunner.runSuite(
        suiteName = "self",
        declaredSometimesIds = declared,
        requiredSometimesIds = required,
        explicitSingleSeed = null,
        explicitSeedCount = count,
        explicitSeedStart = start,
        scenario = scenario,
    )

    @Test
    fun `seed start and count select the seed range`() {
        run(count = 3, start = 41L) { sim -> sim.sometimes("a", true).toString() }.seedsRun shouldBe listOf(41L, 42L, 43L)
    }

    @Test
    fun `replay check catches state leaking across runs`() {
        var leaked = 0
        shouldThrow<AssertionError> { run { sim -> sim.sometimes("a", true).also { leaked++ }.toString() + leaked } }
            .message shouldContain "Non-determinism detected"
    }

    @Test
    fun `replay check catches randomness outside the context`() {
        shouldThrow<AssertionError> {
            run { sim ->
                sim.sometimes("a", true)
                sim.recordStep("draw ${kotlin.random.Random.Default.nextInt()}")
                "x"
            }
        }.message shouldContain "Non-determinism detected"
    }

    @Test
    fun `vacuity gate fails on a never reached required sometimes, but not on an optional one`() {
        shouldThrow<AssertionError> { run(declared = setOf("a", "b")) { sim -> sim.sometimes("a", true).toString() } }
            .message shouldContain "never reached: [b]"
        run(declared = setOf("a", "b"), required = setOf("a")) { sim -> sim.sometimes("a", true).toString() }
    }

    @Test
    fun `undeclared sometimes and non-always failures are reported with the seed`() {
        shouldThrow<AssertionError> { run { sim -> sim.sometimes("typo", true).toString() } }.message shouldContain "seed=1"
        shouldThrow<AssertionError> { run { sim -> error("boom on ${sim.seed}") } }.message shouldContain "SIM_SEED=1"
    }

    @Test
    fun `always failure carries seed, repro hint and trace`() {
        val msg =
            shouldThrow<AssertionError> {
                run { sim ->
                    sim.recordStep("op-one")
                    sim.always("Inv", sim.seed != 3L)
                    sim.sometimes("a", true).toString()
                }
            }.message!!
        msg shouldContain "[Inv] violated on seed=3"
        msg shouldContain "SIM_SEED=3"
        msg shouldContain "op-one"
    }

    @Test
    fun `expectViolation requires the named invariant`() {
        expectViolation("Inv") { run { sim -> sim.always("Inv", false).let { "x" } } }
        shouldThrow<AssertionError> { expectViolation("Inv") { run { sim -> sim.always("Other", false).let { "x" } } } }
            .message shouldContain "not by [Inv]"
        shouldThrow<IllegalStateException> { expectViolation("Inv") { run { sim -> sim.sometimes("a", true).toString() } } }
    }

    @Test
    fun `knownBug passes while the bug reproduces and fails once it is fixed`() {
        knownBug("#1", "returns 2") { (1 + 1) shouldBe 2 }
        shouldThrow<AssertionError> { knownBug("#1", "returns 3") { (1 + 1) shouldBe 3 } }.message shouldContain "no longer reproduces"
        shouldThrow<IllegalArgumentException> { knownBug("TODO", "x") {} }
    }

    @Test
    fun `greedy cover picks a small seed sample that reaches every id`() {
        val (picked, missed) =
            DeterministicSimulationRunner.greedyCoverSeeds((1L..10L).toList(), setOf("even", "three")) { sim ->
                sim.sometimes("even", sim.seed % 2 == 0L)
                sim.sometimes("three", sim.seed == 3L)
                ""
            }
        missed shouldBe emptySet()
        picked.size shouldBe 2
    }
}
