// Skeleton deterministic simulation suite, template v1. Copy into src/test, rename the package, then REPLACE the
// stand-in "production" classes (marked STAND-IN) with imports of your real ones. Keep the structure:
//   ops generated up front from the seed  ->  one interpreter  ->  run against the simulated dependency here,
//   and against the real dependency in SkeletonConformanceTest (same ops, same interpreter).
// Assumes JUnit 5 (org.junit.jupiter) and kotest-assertions-core. Engine files: VirtualClock, SimulationContext,
// DeterministicSimulationRunner, SimulationTestModes.
package dst.example

import dst.DeterministicSimulationRunner
import dst.SimulationContext
import dst.VirtualClock
import dst.expectViolation
import io.kotest.matchers.shouldBe
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.Timeout

// =====================================================================================================================
// STAND-IN production code. In a real suite these are imports from src/main, never copies. The simulation is only
// worth anything if the class under test is the shipped one.
// =====================================================================================================================

/** STAND-IN: the dependency's interface (a repository, an HTTP client, a vendor SDK...). */
interface Store {
    fun read(key: String): String?

    fun write(
        key: String,
        value: String,
    )
}

/** STAND-IN: interface of the component under test, so a mutant can be swapped in. */
interface Lookup {
    fun get(key: String): Result<String?>

    fun invalidate(key: String)

    val hits: Int
}

/**
 * STAND-IN system under test: a TTL memo in front of [Store]. A read that started before an [invalidate] must not
 * publish its (older) answer: the per-key version guard below. Time comes ONLY from the injected [ticker].
 */
class MemoizingLookup(
    private val store: Store,
    private val ttlNanos: Long,
    private val ticker: () -> Long,
) : Lookup {
    private data class Entry(
        val value: String,
        val loadedAtNanos: Long,
    )

    private val entries = HashMap<String, Entry>()
    private val versions = HashMap<String, Long>()
    override var hits: Int = 0
        private set

    override fun get(key: String): Result<String?> {
        val now = ticker()
        entries[key]?.let {
            if (now - it.loadedAtNanos < ttlNanos) {
                hits++
                return Result.success(it.value)
            }
        }
        val versionAtStart = versions[key] ?: 0L
        val loaded = runCatching { store.read(key) }
        loaded.getOrNull()?.let { value ->
            if ((versions[key] ?: 0L) == versionAtStart) entries[key] = Entry(value, now)
        }
        return loaded
    }

    override fun invalidate(key: String) {
        versions[key] = (versions[key] ?: 0L) + 1
        entries.remove(key)
    }
}

// =====================================================================================================================
// Test-local MUTANT: the same class without the publish guard. Mutants live in test code (a copy, a subclass or a
// harness mode), never as flags in production code.
// =====================================================================================================================

class UnguardedMemoizingLookup(
    private val store: Store,
    private val ttlNanos: Long,
    private val ticker: () -> Long,
) : Lookup {
    private val entries = HashMap<String, Pair<String, Long>>()
    override var hits: Int = 0
        private set

    override fun get(key: String): Result<String?> {
        val now = ticker()
        entries[key]?.let { (v, at) ->
            if (now - at < ttlNanos) {
                hits++
                return Result.success(v)
            }
        }
        val loaded = runCatching { store.read(key) }
        loaded.getOrNull()?.let { entries[key] = it to now } // BUG (deliberate): no version check
        return loaded
    }

    override fun invalidate(key: String) {
        entries.remove(key)
    }
}

// =====================================================================================================================
// Simulated dependency + fault injection.
//
// Abstractions (write every one down; each is a place a real bug can hide):
// | Real behaviour                         | Modelled?  | How / why not                                                |
// |----------------------------------------|------------|--------------------------------------------------------------|
// | read/write semantics (last write wins) | yes        | InMemoryStore                                                |
// | value size limit (MAX_VALUE_BYTES)     | yes        | InMemoryStore rejects; PutLarge op exercises it              |
// | read failure / timeout                 | yes        | FaultInjectingStore.failNextRead                             |
// | read racing a concurrent write         | yes        | FaultInjectingStore.onInsideRead hook (re-entrant interleave)|
// | replication lag / stale replica reads  | NO         | single copy; conformance runs against a single real node     |
// | request timeout + late success         | NO         | TODO if your client retries                                  |
// | clock: monotonic ticker only           | yes        | VirtualClock.tickerNanos (no backward steps: prod uses nanoTime) |
// =====================================================================================================================

/** The model of the dependency. Conformance replaces THIS with the real one; nothing else changes. */
class InMemoryStore : Store {
    private val data = HashMap<String, String>()

    override fun read(key: String): String? = data[key]

    override fun write(
        key: String,
        value: String,
    ) {
        require(value.length <= MAX_VALUE_BYTES) { "value too large: ${value.length} > $MAX_VALUE_BYTES" }
        data[key] = value
    }

    companion object {
        const val MAX_VALUE_BYTES = 1_024 // STAND-IN: copy the real limit and cite where it comes from
    }
}

/**
 * Wraps ANY [Store] (the in-memory model or the real client) with deterministic faults. Shared by the simulation
 * and the conformance test, so both see exactly the same fault schedule.
 */
class FaultInjectingStore(
    private val inner: Store,
) : Store {
    var failNextRead: Boolean = false

    /** Runs INSIDE the next read, after the value was read and before it is returned: a concurrent op lands here. */
    var onInsideRead: (() -> Unit)? = null
    var reads: Int = 0
        private set

    override fun read(key: String): String? {
        reads++
        if (failNextRead) {
            failNextRead = false
            throw IllegalStateException("injected read timeout")
        }
        val value = inner.read(key)
        onInsideRead?.let {
            onInsideRead = null
            it()
        }
        return value
    }

    override fun write(
        key: String,
        value: String,
    ) = inner.write(key, value)
}

// =====================================================================================================================
// Ops: generated up front from the seed, independent of state, so the SAME list can be replayed against real infra.
// =====================================================================================================================

sealed interface Op {
    data class Get(
        val pod: Int,
        val key: String,
    ) : Op

    data class Put(
        val key: String,
        val value: String,
    ) : Op

    /** A slow Get on [pod] during which another writer puts [value] and invalidates every pod. */
    data class GetRacingPut(
        val pod: Int,
        val key: String,
        val value: String,
    ) : Op

    data class GetWithReadFailure(
        val pod: Int,
        val key: String,
    ) : Op

    data class PutLarge(
        val key: String,
    ) : Op

    data class Advance(
        val millis: Long,
    ) : Op
}

const val PODS = 3
val KEYS = listOf("k1", "k2", "k3")
const val TTL_MILLIS = 500L

fun generateOps(
    sim: SimulationContext,
    count: Int,
): List<Op> =
    List(count) { i ->
        val pod = sim.nextInt(PODS)
        val key = sim.choose(KEYS)
        when (sim.nextInt(100)) {
            in 0..44 -> Op.Get(pod, key)

            in 45..64 -> Op.Put(key, "v$i")

            // unique values, so a stale answer is always detectable
            in 65..74 -> Op.GetRacingPut(pod, key, "v$i")

            in 75..82 -> Op.GetWithReadFailure(pod, key)

            in 83..85 -> Op.PutLarge(key)

            else -> Op.Advance(sim.choose(listOf(1L, 100L, TTL_MILLIS - 1, TTL_MILLIS, TTL_MILLIS + 1)))
        }
    }

/** Sometimes targets, declared up front. Each names a state the generator MUST reach, or the sweep is vacuous. */
val SOMETIMES =
    setOf(
        "cache_hit",
        "cache_miss",
        "race_hook_fired_and_guard_held",
        "read_failure_not_cached",
        "value_too_large_rejected",
        "entry_expired_at_ttl",
    )

/**
 * The one interpreter. Applies [ops] to [pods] over [store], checks invariants through [sim] after every op, and
 * returns a canonical transcript (per-op observable results + end state) for replay and conformance comparison.
 */
fun interpret(
    sim: SimulationContext,
    ops: List<Op>,
    store: FaultInjectingStore,
    pods: List<Lookup>,
): String {
    val truth = HashMap<String, String>() // reference model: the latest committed value per key
    val results = StringBuilder()

    fun putEverywhere(
        key: String,
        value: String,
    ) {
        store.write(key, value)
        truth[key] = value
        pods.forEach { it.invalidate(key) }
    }

    for ((index, op) in ops.withIndex()) {
        sim.recordStep("op[$index] $op")
        when (op) {
            is Op.Get -> {
                val pod = pods[op.pod]
                val hitsBefore = pod.hits
                val readsBefore = store.reads
                val res = pod.get(op.key)
                val hit = pod.hits > hitsBefore
                sim.sometimes("cache_hit", hit)
                sim.sometimes("cache_miss", !hit)
                // Safety: no op ran concurrently, so any answer, hit or miss, is the latest committed value.
                sim.always("NoStaleRead", res == Result.success(truth[op.key])) {
                    "pod=${op.pod} key=${op.key} got=$res truth=${truth[op.key]} hit=$hit"
                }
                sim.always("HitSkipsStore", !hit || store.reads == readsBefore)
                results.append("$index:${res.getOrNull()}|")
            }

            is Op.Put -> {
                putEverywhere(op.key, op.value)
                results.append("$index:put|")
            }

            is Op.GetRacingPut -> {
                val pod = pods[op.pod]
                pod.invalidate(op.key) // force this Get to reach the store
                val before = truth[op.key]
                var fired = false
                store.onInsideRead = {
                    fired = true
                    putEverywhere(op.key, op.value)
                }
                val res = pod.get(op.key)
                // The racing read may legitimately return the pre-write value (it was read before the write)...
                sim.always("RacingReadReturnsBeforeOrAfter", res == Result.success(before) || res == Result.success(op.value))
                // ...but must not leave it cached: the next read must see the write.
                val next = pod.get(op.key)
                sim.always("NoStaleHit", next == Result.success(op.value)) {
                    "pod=${op.pod} key=${op.key}: racing read returned $res, next read $next, truth ${op.value}"
                }
                sim.sometimes("race_hook_fired_and_guard_held", fired && res == Result.success(before) && before != op.value)
                results.append("$index:${res.getOrNull()}>${next.getOrNull()}|")
            }

            is Op.GetWithReadFailure -> {
                val pod = pods[op.pod]
                pod.invalidate(op.key)
                store.failNextRead = true
                val res = pod.get(op.key)
                sim.always("FailureSurfaced", res.isFailure)
                val readsBefore = store.reads
                val again = pod.get(op.key)
                sim.always("FailureNotCached", store.reads == readsBefore + 1 && again == Result.success(truth[op.key]))
                sim.sometimes("read_failure_not_cached", true)
                results.append("$index:fail>${again.getOrNull()}|")
            }

            is Op.PutLarge -> {
                val rejected = runCatching { store.write(op.key, "x".repeat(InMemoryStore.MAX_VALUE_BYTES + 1)) }.isFailure
                sim.always("OversizeRejected", rejected)
                sim.sometimes("value_too_large_rejected", rejected)
                results.append("$index:large-rejected=$rejected|")
            }

            is Op.Advance -> {
                sim.clock.advanceMillis(op.millis)
                if (op.millis == TTL_MILLIS) {
                    // Exact boundary: an entry loaded exactly TTL ago must miss.
                    val pod = pods[0]
                    val key = KEYS[0]
                    pod.get(key) // load (or hit)
                    sim.clock.advanceMillis(TTL_MILLIS)
                    val hitsBefore = pod.hits
                    pod.get(key)
                    val expired = pod.hits == hitsBefore
                    sim.always("ExpiresAtExactTtl", expired)
                    sim.sometimes("entry_expired_at_ttl", expired)
                }
                results.append("$index:t=${sim.clock.nowMillis()}|")
            }
        }
    }

    // Quiescent end-state check: with nothing in flight, every pod agrees with the reference model for every key.
    for ((p, pod) in pods.withIndex()) {
        for (key in KEYS) {
            sim.always("QuiescentReadsMatchTruth", pod.get(key) == Result.success(truth[key])) { "pod=$p key=$key" }
        }
    }
    return "results=$results;truth=${truth.toSortedMap()}"
}

/**
 * Conformance sample, chosen by coverage: `DeterministicSimulationRunner.greedyCoverSeeds((1L..50L).toList(), SOMETIMES)
 * { runScenario(it) }`, then pinned. Here the cover was [1]; 2 and 3 are extra breadth. Add every seed that ever failed.
 */
val CONFORMANCE_SEEDS = listOf(1L, 2L, 3L)

fun runScenario(
    sim: SimulationContext,
    store: Store = InMemoryStore(),
    lookupFactory: (Store, Long, () -> Long) -> Lookup = ::MemoizingLookup,
    opCount: Int = 60,
): String {
    val ops = generateOps(sim, opCount)
    val faulty = FaultInjectingStore(store)
    val ttlNanos = TTL_MILLIS * VirtualClock.NANOS_PER_MILLI
    val pods = List(PODS) { lookupFactory(faulty, ttlNanos) { sim.clock.tickerNanos() } }
    return interpret(sim, ops, faulty, pods)
}

@Timeout(30)
class SkeletonSimulationTest {
    @Test
    fun `seed sweep - memo never serves a value older than the latest write, across 150 seeds`() {
        val summary =
            DeterministicSimulationRunner.runSuite(
                suiteName = "MemoizingLookupSweep",
                declaredSometimesIds = SOMETIMES,
                defaultSeedCount = 150,
            ) { sim -> runScenario(sim) }
        if (!summary.singleSeedMode) summary.reachedSometimesIds shouldBe SOMETIMES
    }

    @Test
    fun `mutant catch - unguarded publish - the sweep sees a racing read cache the pre-write value`() {
        // Proves NoStaleHit can see the fault this suite exists for. Pin seed/count so env overrides don't change it.
        expectViolation("NoStaleHit") {
            DeterministicSimulationRunner.runSuite(
                suiteName = "MemoizingLookupMutant",
                declaredSometimesIds = SOMETIMES,
                explicitSingleSeed = null,
                explicitSeedCount = 150,
                explicitSeedStart = 1L,
            ) { sim -> runScenario(sim, lookupFactory = ::UnguardedMemoizingLookup) }
        }
    }

    @Test
    fun `regression guard - the conformance seed sample still reaches every sometimes on the model`() {
        // Generators change; a pinned sample that stops covering a state silently weakens conformance.
        val (_, missed) =
            DeterministicSimulationRunner.greedyCoverSeeds(CONFORMANCE_SEEDS, SOMETIMES) { sim -> runScenario(sim) }
        missed shouldBe emptySet()
    }

    @Test
    fun `regression guard - a read racing a write does not cache the pre-write value`() {
        // Deterministic scenario: the minimal repro of the race, kept even though the sweep covers it.
        val clock = VirtualClock()
        val store = FaultInjectingStore(InMemoryStore().apply { write("k", "old") })
        val pod = MemoizingLookup(store, 1_000_000_000L) { clock.tickerNanos() }
        store.onInsideRead = {
            store.write("k", "new")
            pod.invalidate("k")
        }
        pod.get("k") shouldBe Result.success("old")
        pod.get("k") shouldBe Result.success("new")
    }

    // Known-bug pin, for when the shipped code is WRONG and not fixed yet. Delete if you have none.
    //
    // @Tag(SimulationTestModes.KNOWN_BUG_TAG)
    // @Test
    // fun `known bug #123 - <what goes wrong, in user-visible terms>`() {
    //     knownBug(issue = "#123", correctBehaviour = "next read returns 'new'") {
    //         ...minimal scenario...
    //         pod.get("k") shouldBe Result.success("old") // WRONG behaviour, pinned. The fix flips this.
    //     }
    // }
}
