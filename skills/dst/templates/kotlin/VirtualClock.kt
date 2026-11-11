// Deterministic simulation engine, template v1.
// Behaviour unchanged. Rename the package for your codebase.
package dst

/**
 * Deterministic virtual clock for in-process simulation tests.
 *
 * One monotonic nanosecond counter ([monotonicNanos]) drives both millisecond time ([nowMillis]) and
 * nanosecond time ([observationNanos]), so the two views never drift apart.
 *
 * Two clocks, on purpose:
 * - [tickerNanos] is strictly monotonic. Hand it to anything with a monotonic contract (Caffeine's `Ticker`,
 *   `System.nanoTime` substitutes, timeouts).
 * - [observationNanos] / [nowMillis] are what the application reads as "the time". [stepBackObservationClockNanos]
 *   can skew them backwards to test wall-clock-skew handling without breaking the ticker's contract.
 *
 * A backward step is MORE adversarial than `System.nanoTime` in production. Any finding that needs one must
 * say whether production's clock source can actually produce it.
 */
class VirtualClock(
    initialNanos: Long = DEFAULT_INITIAL_NANOS,
) {
    init {
        require(initialNanos >= 0L) { "initialNanos must be non-negative, got $initialNanos" }
    }

    var monotonicNanos: Long = initialNanos
        private set

    var clockSkewNanos: Long = 0L
        private set

    /** Application-visible nanosecond clock (includes any injected backward/forward skew). */
    fun observationNanos(): Long = Math.addExact(monotonicNanos, clockSkewNanos)

    /** Strictly monotonic nanosecond ticker, safe for monotonic contracts such as Caffeine's `Ticker`. */
    fun tickerNanos(): Long = monotonicNanos

    /** Application-visible epoch milliseconds, derived directly from [observationNanos]. */
    fun nowMillis(): Long = Math.floorDiv(observationNanos(), NANOS_PER_MILLI)

    fun advanceNanos(deltaNanos: Long) {
        require(deltaNanos >= 0L) { "deltaNanos must be non-negative, got $deltaNanos" }
        monotonicNanos = Math.addExact(monotonicNanos, deltaNanos)
    }

    fun advanceMillis(deltaMillis: Long) {
        require(deltaMillis >= 0L) { "deltaMillis must be non-negative, got $deltaMillis" }
        advanceNanos(Math.multiplyExact(deltaMillis, NANOS_PER_MILLI))
    }

    fun advanceSeconds(deltaSeconds: Long) {
        require(deltaSeconds >= 0L) { "deltaSeconds must be non-negative, got $deltaSeconds" }
        advanceNanos(Math.multiplyExact(deltaSeconds, NANOS_PER_SECOND))
    }

    /**
     * Steps the application observation clock backward by [deltaNanos] while keeping [tickerNanos] monotonic.
     * Rejects a backward step that would drive [observationNanos] below zero.
     */
    fun stepBackObservationClockNanos(deltaNanos: Long) {
        require(deltaNanos > 0L) { "deltaNanos must be positive, got $deltaNanos" }
        val newSkew = Math.subtractExact(clockSkewNanos, deltaNanos)
        val newObservation = Math.addExact(monotonicNanos, newSkew)
        require(newObservation >= 0L) {
            "Backward step of ${deltaNanos}ns would drive observationNanos negative ($newObservation)"
        }
        clockSkewNanos = newSkew
    }

    fun resetClockSkew() {
        clockSkewNanos = 0L
    }

    companion object {
        const val NANOS_PER_MILLI = 1_000_000L
        const val NANOS_PER_SECOND = 1_000_000_000L
        const val DEFAULT_INITIAL_NANOS = 1_000_000_000_000L // 1,000s (1,000,000ms)
    }
}
