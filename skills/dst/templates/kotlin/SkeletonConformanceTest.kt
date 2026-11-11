// Skeleton model-vs-real conformance test, template v1. REQUIRED companion of every simulation that models a
// dependency by hand. Same ops, same interpreter, same fault wrapper; only the Store differs.
// Tag it as your repo tags Docker/real-infra tests (for example: @Tag("docker"), @RestateTest(containerImage = RESTATE_TEST_IMAGE)).
package dst.example

import dst.SimulationContext
import org.junit.jupiter.api.Disabled
import org.junit.jupiter.api.Tag
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Timeout
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Replays a sample of seeds' op sequences against (a) the in-memory model and (b) the REAL dependency, and requires
 * identical per-op results and end state. A divergence is a finding in its own right: either the model is wrong (the
 * sweep has been testing fiction) or the real dependency does something the model never considered.
 *
 * Seed sample: [CONFORMANCE_SEEDS], chosen BY COVERAGE (DeterministicSimulationRunner.greedyCoverSeeds over the
 * model), not by taste. A hand-picked sample (1, 2, 3, 7, 42) missed every offset rejection in the durable-execution dry run, so
 * a model drift passed conformance. SkeletonSimulationTest guards that the sample still covers every `sometimes`.
 *
 * Proving it detects drift: change the MODEL in a way only the diff can see (an output label, a value the model's own
 * invariants do not check). A drift that trips the model's invariants first proves nothing about this test.
 *
 * Real stacks fail differently from models. Two harness rules:
 * - every op gets its own timeout ([OP_TIMEOUT_MS]): a real runtime that retries a failing call forever (Restate does
 *   for non-terminal errors) otherwise hangs the whole class until @Timeout;
 * - never assert INSIDE a callback the real runtime runs (a journaled run block, a retried handler): the runtime
 *   catches and retries, and the failure vanishes. Record it ([deferred]) and assert after the op.
 *
 * Covers: per-op results and end state for the op mix in [generateOps], under the fault wrapper's schedule.
 * Does NOT cover (stays model-only; list it in the suite README):
 * - interleavings the real stack cannot be driven into deterministically (here: none beyond the onInsideRead hook);
 * - TODO: anything else your real harness cannot express.
 */
@Tag("docker")
@Timeout(value = 10, unit = TimeUnit.MINUTES)
class SkeletonConformanceTest {
    /** TODO: return a client for real infrastructure (Testcontainers, @RestateTest, ...), empty, one per seed. */
    private fun realStore(seed: Long): Store = TODO("wire the real dependency for seed $seed")

    private val deferred = java.util.concurrent.ConcurrentLinkedQueue<String>()

    /** Runs real work with a deadline. This stub wraps a whole seed; in your harness wrap EACH real call. */
    private fun <T> withOpTimeout(
        label: String,
        block: () -> T,
    ): T {
        val pool = Executors.newSingleThreadExecutor()
        try {
            return pool.submit<T> { block() }.get(OP_TIMEOUT_MS, TimeUnit.MILLISECONDS)
        } catch (e: java.util.concurrent.TimeoutException) {
            throw AssertionError("real op '$label' did not finish in $OP_TIMEOUT_MS ms (retrying forever? paused?)", e)
        } finally {
            pool.shutdownNow()
        }
    }

    @Disabled("TODO: wire realStore(), then delete this annotation")
    @Test
    fun `conformance - model and real store agree on every op result and end state for sampled seeds`() {
        val divergences =
            CONFORMANCE_SEEDS.mapNotNull { seed ->
                val model = runScenario(SimulationContext(seed, declaredSometimesIds = SOMETIMES), store = InMemoryStore())
                val real =
                    withOpTimeout("seed $seed") {
                        runScenario(SimulationContext(seed, declaredSometimesIds = SOMETIMES), store = realStore(seed))
                    }
                if (model == real) null else "seed=$seed\n  model=$model\n  real =$real\n  first diff at op ${firstDiff(model, real)}"
            }
        check(deferred.isEmpty()) { "failures recorded inside real callbacks:\n${deferred.joinToString("\n")}" }
        if (divergences.isNotEmpty()) {
            throw AssertionError(
                "Model diverges from the real dependency (repro: SIM_SEED=<seed> on both tests):\n" +
                    divergences.joinToString("\n"),
            )
        }
    }

    private companion object {
        const val OP_TIMEOUT_MS = 20_000L
    }

    private fun firstDiff(
        a: String,
        b: String,
    ): String {
        val ax = a.split('|')
        val bx = b.split('|')
        val i = ax.indices.firstOrNull { it >= bx.size || ax[it] != bx[it] } ?: ax.size
        return "${ax.getOrNull(i)} vs ${bx.getOrNull(i)}"
    }
}
