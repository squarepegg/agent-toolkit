# Kotlin engine templates (v1)

| File | What | Origin |
|---|---|---|
| `VirtualClock.kt` | monotonic ticker + skewable observation clock | the origin engine, unchanged |
| `SimulationContext.kt` | seeded RNG, transcript hash, trace ring, separate fault schedule, `always` / `sometimes` | the origin engine, plus `chance`, `shuffled`, `nextLong(until)`, `failureReport`, `recordFault` |
| `DeterministicSimulationRunner.kt` | seed sweep, replay check, anti-vacuity gate | the origin engine, plus `SIM_SEED_START`, `corpusSeeds`, `requiredSometimesIds`, `greedyCoverSeeds`, seed-tagged wrapping of non-`always` failures, `sometimesSeedCounts`, `SimulationLog.sink` |
| `SimulationTestModes.kt` | `knownBug(...)`, `expectViolation(...)`, mode table | new |
| `SimulationEngineSelfTest.kt` | proves the harness can fail: replay leaks, outside randomness, vacuity, seed range, failure text, helpers | new |
| `SkeletonSimulationTest.kt` | worked skeleton: real class, simulated dependency, fault wrapper, op generator, interpreter, sweep, mutant catch, regression guard | new |
| `SkeletonConformanceTest.kt` | same ops against the real dependency, `@Tag("docker")` | new |

These templates were extracted from a working engine and then extended. With default settings the template visits
seeds 1..N and mixes the transcript deterministically. Package `dst` is a placeholder; rename it for your codebase.

Checked on 2026-11-04: compiled, `lintKotlin` clean (kotlinter 5.6.0 defaults), and run with Kotlin
2.3.21, JDK 25, JUnit 5.11 and kotest-assertions 5.9.1. The skeleton sweep (150 seeds × 60 ops) takes about 0.2 s.
`SimulationEngineSelfTest` passes (9 tests). A 10th, the fault-schedule test, was added later; the engine, skeletons and
all 10 self-tests were compiled and run with kotlinc on 2026-11-17, not rerun under Gradle or `lintKotlin`. Five deliberate breaks were each caught by the intended
mechanism:

| Break | Caught by |
|---|---|
| TTL `<` → `<=` | `ExpiresAtExactTtl` |
| read failure cached as `null` | `FailureSurfaced` |
| invalidate no longer bumps the version | `NoStaleHit` |
| generator draws from `Random.Default` | the non-determinism check |
| generator never emits the race op | the vacuity gate (`race_hook_fired_and_guard_held`) |

## Assumptions
- **JUnit 5** (`org.junit.jupiter`) for `@Test`, `@Tag` and `@Timeout`. The engine files have no dependencies outside
  the Kotlin stdlib.
- **kotest-assertions-core**, used only by the skeletons (`shouldBe`). Any assertion library works, as long as its
  failure extends `AssertionError`; the runner tags those with the seed.
- Package `dst`: rename it. Put the engine in a `testFixtures` source set if several modules share it
  (`java-test-fixtures` plugin, `testImplementation(testFixtures(project(":module")))`).

## Gradle wiring (required, or the repro instructions lie)
Gradle runs tests in a separate JVM, and treats the test task as up to date unless its inputs change. Review of one
suite found both problems: `-Dsim.seed` was never forwarded, and changing `SIM_SEED` left the task UP-TO-DATE, so
nothing ran.

```kotlin
tasks.withType<Test>().configureEach {
    listOf("sim.seed", "sim.seeds", "sim.seedStart").forEach { prop ->
        System.getProperty(prop)?.let { value ->
            systemProperty(prop, value)
            inputs.property(prop, value)
        }
    }
    listOf("SIM_SEED", "SIM_SEEDS", "SIM_SEED_START").forEach { envVar ->
        inputs.property("env.$envVar", System.getenv(envVar) ?: "")
    }
}
```

Keep simulations out of the Docker tag, so they run in the PR tier. Put conformance tests in the Docker / real-infra
tier.

## Timeouts versus long sweeps
A class-level `@Timeout(30)` is right for the PR tier, but it caps long sweeps. In one engine, `SIM_SEEDS=10000` timed
out a sim at 30 s (measured 2026-11-04; 2,500 seeds passed). For long sweeps, use
`scripts/seed-sweep.sh --chunk <n>`, which keeps each run under the timeout.

## Self-tests must pin their seed settings
A test that expects the default seed set must pass `explicitSingleSeed = null`, `explicitSeedCount` and
`explicitSeedStart`. Otherwise a developer's `SIM_SEEDS` leaks into it. With `SIM_SEEDS=10000` exported,
`SimulationEngineSelfTest > skipping replay verification requires a stated reason` failed (`expected:<3> but was:<10000>`).
