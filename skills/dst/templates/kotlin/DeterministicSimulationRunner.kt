// Deterministic simulation engine, template v1.
// Additions over the origin (all backwards compatible; defaults reproduce the origin's seed set 1..N):
// - SIM_SEED_START / -Dsim.seedStart: sweep seeds start..start+N-1 (long sweeps in chunks, past MAX_SEED_COUNT);
// - corpusSeeds: seeds that always run in addition (pin seeds that once reached rare states or failed);
// - any exception a scenario throws (kotest `shouldBe`, require, NPE...) is rethrown with seed, trace and repro hint;
//   the origin only did that for `always`;
// - SuiteSummary.sometimesSeedCounts: how many seeds reached each declared `sometimes` (spot barely-reached states);
// - requiredSometimesIds: a narrower op mix (conformance mix, mutant run) can share the full ID catalog for typo
//   protection while only requiring the subset it can reach;
// - greedyCoverSeeds: pick a small seed sample (for conformance) that still reaches every required `sometimes`;
// - SimulationLog.sink: where the one-line summaries go (swap for your logger if the repo forbids println).
// Rename the package for your codebase.
package dst

/** Where the engine's informational lines go. Point it at SLF4J etc. if the repo forbids stdout/stderr. */
object SimulationLog {
    @Volatile
    var sink: (String) -> Unit = { System.err.println(it) }
}

/**
 * Multi-seed runner for Antithesis-style deterministic simulation suites.
 *
 * 1. Resolves seeds. A single-seed reproduction (`-Dsim.seed` / `SIM_SEED`) takes precedence over the suite seed
 *    count (`-Dsim.seeds` / `SIM_SEEDS`, default [defaultSeedCount]) and start (`-Dsim.seedStart` / `SIM_SEED_START`,
 *    default 1).
 * 2. Verifies replay determinism: runs the first [determinismVerifySeeds] seeds twice from fresh contexts and requires
 *    identical transcript hashes AND identical state digests (the String the scenario returns).
 * 3. Accumulates `sometimes` obligations across seeds and fails if a declared one was never reached (skipped in
 *    single-seed mode, so a reproducer does not fail on unrelated suite-wide reachability).
 *
 * The scenario must build ALL of its state from the context it is given (no statics, no shared caches, no wall
 * clock) and return a canonical digest of its end state (sorted, no identity hash codes, no timestamps from outside
 * the virtual clock).
 */
object DeterministicSimulationRunner {
    const val DEFAULT_SEED_COUNT = 150
    const val MAX_SEED_COUNT = 10_000
    const val DEFAULT_DETERMINISM_VERIFY_SEEDS = 25

    data class SuiteSummary(
        val seedsRun: List<Long>,
        val singleSeedMode: Boolean,
        val reachedSometimesIds: Set<String>,
        val transcriptHashesBySeed: Map<Long, Long>,
        /** Number of seeds (first run only) that reached each declared `sometimes` id. */
        val sometimesSeedCounts: Map<String, Int> = emptyMap(),
    )

    fun resolveSeeds(
        defaultSeedCount: Int = DEFAULT_SEED_COUNT,
        explicitSingleSeed: Long? = readSingleSeedFromEnvOrProps(),
        explicitSeedCount: Int? = null,
        explicitSeedStart: Long? = readSeedStartFromEnvOrProps(),
        corpusSeeds: List<Long> = emptyList(),
    ): Pair<List<Long>, Boolean> {
        if (explicitSingleSeed != null) {
            return listOf(explicitSingleSeed) to true
        }
        val count = explicitSeedCount ?: readSeedCountFromEnvOrProps() ?: defaultSeedCount
        require(count in 1..MAX_SEED_COUNT) {
            "Simulation seed count must be in 1..$MAX_SEED_COUNT, got $count"
        }
        val start = explicitSeedStart ?: 1L
        val swept = (start until Math.addExact(start, count.toLong())).toList()
        return (corpusSeeds + swept).distinct() to false
    }

    fun runSuite(
        suiteName: String,
        // The ID catalog: `sometimes` with any other ID throws (typo protection).
        declaredSometimesIds: Set<String>,
        // The vacuity gate: these must each be reached by some seed. Narrow it for op mixes that cannot reach all.
        requiredSometimesIds: Set<String> = declaredSometimesIds,
        defaultSeedCount: Int = DEFAULT_SEED_COUNT,
        determinismVerifySeeds: Int = DEFAULT_DETERMINISM_VERIFY_SEEDS,
        // Required when determinismVerifySeeds == 0: why this suite cannot be replayed (e.g. a dependency's own
        // unseedable randomness). Invariants still run on every seed; only the two-run replay check is skipped.
        nonReplayableReason: String? = null,
        corpusSeeds: List<Long> = emptyList(),
        explicitSingleSeed: Long? = readSingleSeedFromEnvOrProps(),
        explicitSeedCount: Int? = null,
        explicitSeedStart: Long? = readSeedStartFromEnvOrProps(),
        scenario: (SimulationContext) -> String,
    ): SuiteSummary {
        require(suiteName.isNotBlank()) { "suiteName must not be blank" }
        require(determinismVerifySeeds >= 0) { "determinismVerifySeeds must be >= 0, got $determinismVerifySeeds" }
        require(declaredSometimesIds.containsAll(requiredSometimesIds)) {
            "requiredSometimesIds must be a subset of declaredSometimesIds: ${requiredSometimesIds - declaredSometimesIds}"
        }
        require(determinismVerifySeeds >= 1 || !nonReplayableReason.isNullOrBlank()) {
            "determinismVerifySeeds = 0 skips replay verification: pass nonReplayableReason explaining why the suite is not replayable"
        }

        val (seeds, singleSeedMode) =
            resolveSeeds(
                defaultSeedCount = defaultSeedCount,
                explicitSingleSeed = explicitSingleSeed,
                explicitSeedCount = explicitSeedCount,
                explicitSeedStart = explicitSeedStart,
                corpusSeeds = corpusSeeds,
            )

        val allReachedSometimes = mutableSetOf<String>()
        val seedCounts = declaredSometimesIds.associateWith { 0 }.toMutableMap()
        val transcriptHashes = LinkedHashMap<Long, Long>(seeds.size)
        val verifyCount = minOf(determinismVerifySeeds, seeds.size)

        for ((index, seed) in seeds.withIndex()) {
            val firstContext = SimulationContext(seed = seed, clock = VirtualClock(), declaredSometimesIds = declaredSometimesIds)
            val firstDigest = runScenario(suiteName, firstContext, scenario)
            allReachedSometimes.addAll(firstContext.reachedSometimesIds())
            firstContext.reachedSometimesIds().forEach { seedCounts[it] = (seedCounts[it] ?: 0) + 1 }
            transcriptHashes[seed] = firstContext.transcriptHash

            if (index < verifyCount) {
                val secondContext = SimulationContext(seed = seed, clock = VirtualClock(), declaredSometimesIds = declaredSometimesIds)
                val secondDigest = runScenario(suiteName, secondContext, scenario)
                if (firstContext.transcriptHash != secondContext.transcriptHash || firstDigest != secondDigest) {
                    throw AssertionError(
                        buildString {
                            appendLine("Non-determinism detected in suite [$suiteName] on seed=$seed!")
                            appendLine(
                                "Run 1 transcriptHash=${firstContext.transcriptHash}, Run 2 transcriptHash=${secondContext.transcriptHash}",
                            )
                            appendLine("Run 1 stateDigest=$firstDigest")
                            appendLine("Run 2 stateDigest=$secondDigest")
                            appendLine("Run 1 trace tail:\n${firstContext.traceSnapshot().takeLast(20).joinToString("\n")}")
                            appendLine("Run 2 trace tail:\n${secondContext.traceSnapshot().takeLast(20).joinToString("\n")}")
                        },
                    )
                }
            }
        }

        if (!singleSeedMode && requiredSometimesIds.isNotEmpty()) {
            val unreached = requiredSometimesIds - allReachedSometimes
            if (unreached.isNotEmpty()) {
                throw AssertionError(
                    "Vacuous simulation in suite [$suiteName] across ${seeds.size} seeds: " +
                        "declared 'sometimes' obligations were never reached: $unreached",
                )
            }
        }

        if (!singleSeedMode) {
            // One line per suite in the test log: which states are barely reached (candidates for generator tuning).
            val rarest =
                seedCounts.entries
                    .sortedBy { it.value }
                    .take(5)
                    .joinToString { "${it.key}=${it.value}" }
            SimulationLog.sink("[sim] $suiteName: ${seeds.size} seeds (${seeds.first()}..${seeds.last()}), rarest sometimes: $rarest")
        }

        return SuiteSummary(
            seedsRun = seeds,
            singleSeedMode = singleSeedMode,
            reachedSometimesIds = allReachedSometimes.toSet(),
            transcriptHashesBySeed = transcriptHashes,
            sometimesSeedCounts = seedCounts.toMap(),
        )
    }

    /**
     * Greedy set cover: from [candidateSeeds], pick seeds until every ID in [mustReach] is reached (by the MODEL run of
     * [scenario]). Use it to choose the conformance sample by coverage instead of by taste, then pin the result and
     * add a smoke test asserting the pinned sample still covers [mustReach] (generators change). Returns the pinned
     * list and any IDs no candidate reached.
     */
    fun greedyCoverSeeds(
        candidateSeeds: List<Long>,
        declaredSometimesIds: Set<String>,
        mustReach: Set<String> = declaredSometimesIds,
        scenario: (SimulationContext) -> String,
    ): Pair<List<Long>, Set<String>> {
        val reachedBy =
            candidateSeeds.associateWith { seed ->
                val ctx = SimulationContext(seed = seed, clock = VirtualClock(), declaredSometimesIds = declaredSometimesIds)
                runScenario("greedyCoverSeeds", ctx, scenario)
                ctx.reachedSometimesIds() intersect mustReach
            }
        val uncovered = mustReach.toMutableSet()
        val picked = mutableListOf<Long>()
        while (uncovered.isNotEmpty()) {
            val best = reachedBy.entries.maxByOrNull { (it.value intersect uncovered).size } ?: break
            if ((best.value intersect uncovered).isEmpty()) break
            picked += best.key
            uncovered -= best.value
        }
        return picked to uncovered
    }

    private fun runScenario(
        suiteName: String,
        context: SimulationContext,
        scenario: (SimulationContext) -> String,
    ): String =
        try {
            scenario(context)
        } catch (e: AssertionError) {
            // `always` failures already carry seed and trace; wrap everything else (kotest matchers etc.).
            if (e.message?.contains("on seed=${context.seed} ") == true) throw e
            throw AssertionError(context.failureReport("Suite [$suiteName] assertion failed: ${e.message}"), e)
        } catch (e: Exception) {
            throw AssertionError(
                context.failureReport("Suite [$suiteName] scenario threw ${e::class.qualifiedName}: ${e.message}"),
                e,
            )
        }

    internal fun parseSingleSeed(raw: String?): Long? {
        val trimmed = raw?.trim()?.takeIf { it.isNotEmpty() } ?: return null
        return trimmed.toLongOrNull()
            ?: throw IllegalArgumentException("Invalid simulation seed '$trimmed' (expected 64-bit integer)")
    }

    internal fun parseSeedCount(raw: String?): Int? {
        val trimmed = raw?.trim()?.takeIf { it.isNotEmpty() } ?: return null
        val parsed =
            trimmed.toIntOrNull()
                ?: throw IllegalArgumentException("Invalid simulation seed count '$trimmed' (expected integer in 1..$MAX_SEED_COUNT)")
        require(parsed in 1..MAX_SEED_COUNT) {
            "Simulation seed count must be in 1..$MAX_SEED_COUNT, got $parsed"
        }
        return parsed
    }

    private fun readSingleSeedFromEnvOrProps(): Long? =
        parseSingleSeed(System.getProperty("sim.seed"))
            ?: parseSingleSeed(System.getenv("SIM_SEED"))

    private fun readSeedCountFromEnvOrProps(): Int? =
        parseSeedCount(System.getProperty("sim.seeds"))
            ?: parseSeedCount(System.getenv("SIM_SEEDS"))

    private fun readSeedStartFromEnvOrProps(): Long? =
        parseSingleSeed(System.getProperty("sim.seedStart"))
            ?: parseSingleSeed(System.getenv("SIM_SEED_START"))
}
