// Deterministic simulation engine, template v1.
// Additions over the origin: chance(), shuffled(), nextLong(until), per-id sometimes hit counting.
// Existing methods mix the transcript exactly as the origin does. Rename the package for your codebase.
package dst

import java.util.ArrayDeque
import kotlin.random.Random

/**
 * Per-seed execution context for Antithesis-style deterministic simulation testing.
 *
 * - Seeded PRNG. Draw EVERY random choice through this context (nextInt/choose/chance/...), never through
 *   `Random.Default`, `ThreadLocalRandom`, `UUID.randomUUID()` or `System.nanoTime()`. Each draw is mixed into
 *   [transcriptHash], which is how the runner detects non-determinism.
 * - [clock]: the only time source the system under test may see.
 * - [always]: safety assertion. Fails immediately with seed, virtual time, a repro hint and the trace ring.
 *   NOTE: like Antithesis' `alwaysOrUnreachable`, it passes if never evaluated. Pair each important `always`
 *   whose guard is conditional with a declared [sometimes] on that guard, so a vacuous `always` is caught.
 * - [sometimes]: reachability / anti-vacuity obligation. IDs must be declared up front; the runner fails the
 *   suite if a declared ID is never reached across all seeds.
 * - [recordStep]: appends to a bounded trace ring printed on failure. Record every operation and fault.
 */
class SimulationContext(
    val seed: Long,
    val clock: VirtualClock = VirtualClock(),
    val declaredSometimesIds: Set<String> = emptySet(),
    private val traceCapacity: Int = DEFAULT_TRACE_CAPACITY,
) {
    init {
        require(traceCapacity > 0) { "traceCapacity must be positive, got $traceCapacity" }
    }

    val rng: Random = Random(seed)
    private val traceRing = ArrayDeque<String>(traceCapacity)
    private val reachedSometimes = mutableSetOf<String>()

    var transcriptHash: Long = FNV_OFFSET_BASIS xor seed
        private set

    var stepCount: Int = 0
        private set

    fun recordStep(description: String) {
        stepCount++
        val entry = "#$stepCount @${clock.nowMillis()}ms (${clock.observationNanos()}ns): $description"
        if (traceRing.size == traceCapacity) {
            traceRing.removeFirst()
        }
        traceRing.addLast(entry)
        mixTranscript(clock.observationNanos())
        mixTranscript(description)
    }

    fun always(
        id: String,
        condition: Boolean,
        detail: () -> String = { "" },
    ) {
        require(id.isNotBlank()) { "always invariant ID must not be blank" }
        mixTranscript("always:$id:$condition")
        if (!condition) {
            throw AssertionError(failureReport("Simulation invariant [$id] violated", detail()))
        }
    }

    fun sometimes(
        id: String,
        condition: Boolean,
    ): Boolean {
        require(id in declaredSometimesIds) {
            "Undeclared sometimes ID '$id'. Declare it in declaredSometimesIds=$declaredSometimesIds before running the suite."
        }
        mixTranscript("sometimes:$id:$condition")
        if (condition) {
            reachedSometimes.add(id)
        }
        return condition
    }

    fun reachedSometimesIds(): Set<String> = reachedSometimes.toSet()

    fun traceSnapshot(): List<String> = traceRing.toList()

    /** Failure text with seed, step, virtual time, repro hint and the trace ring. Used by [always] and the runner. */
    fun failureReport(
        headline: String,
        detail: String = "",
    ): String =
        buildString {
            appendLine("$headline on seed=$seed at step=$stepCount")
            appendLine("Virtual time: ${clock.nowMillis()}ms (obs=${clock.observationNanos()}ns, ticker=${clock.tickerNanos()}ns)")
            appendLine("Reproduce single seed with env: SIM_SEED=$seed (or -Dsim.seed=$seed)")
            if (detail.isNotBlank()) {
                appendLine("Detail: $detail")
            }
            appendLine("Recent trace (${traceRing.size} steps):")
            traceRing.forEach { appendLine("  $it") }
        }

    fun nextInt(until: Int): Int = rng.nextInt(until).also { mixTranscript(it.toLong()) }

    fun nextInt(
        from: Int,
        until: Int,
    ): Int = rng.nextInt(from, until).also { mixTranscript(it.toLong()) }

    fun nextLong(until: Long): Long = rng.nextLong(until).also { mixTranscript(it) }

    fun nextLong(
        from: Long,
        until: Long,
    ): Long = rng.nextLong(from, until).also { mixTranscript(it) }

    fun nextBoolean(): Boolean = rng.nextBoolean().also { mixTranscript(if (it) 1L else 0L) }

    /** True with probability [percent]/100. Use for fault injection rates: `if (sim.chance(5)) injectTimeout()`. */
    fun chance(percent: Int): Boolean {
        require(percent in 0..100) { "percent must be in 0..100, got $percent" }
        return nextInt(100) < percent
    }

    fun <T> choose(items: List<T>): T {
        require(items.isNotEmpty()) { "Cannot choose from an empty list" }
        return items[nextInt(items.size)]
    }

    /** Deterministic Fisher-Yates shuffle through the context (use for delivery/interleaving order). */
    fun <T> shuffled(items: List<T>): List<T> {
        val out = items.toMutableList()
        for (i in out.indices.reversed()) {
            val j = nextInt(i + 1)
            val tmp = out[i]
            out[i] = out[j]
            out[j] = tmp
        }
        return out
    }

    private fun mixTranscript(value: Long) {
        transcriptHash = (transcriptHash xor value) * FNV_PRIME
    }

    private fun mixTranscript(text: String) {
        var h = transcriptHash
        for (i in text.indices) {
            h = (h xor text[i].code.toLong()) * FNV_PRIME
        }
        transcriptHash = h
    }

    companion object {
        const val DEFAULT_TRACE_CAPACITY = 200
        private const val FNV_OFFSET_BASIS = -0x340d631b7bdddcdbL
        private const val FNV_PRIME = 0x100000001b3L
    }
}
