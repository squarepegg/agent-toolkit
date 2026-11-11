// Deterministic simulation engine, template v1. New in the skill (the reference engine has no equivalent yet; see the
// a related cleanup effort). Rename the package for your codebase.
package dst

/**
 * Helpers that make a simulation test's MODE mechanical instead of a naming convention someone forgets.
 *
 * | Mode               | Test name prefix               | Body                                   | Goes red when                    |
 * |--------------------|--------------------------------|----------------------------------------|----------------------------------|
 * | regression guard   | `regression guard - ...`       | plain assertions of the FIXED behaviour| the bug comes back               |
 * | known bug          | `known bug #<issue> - ...`     | [knownBug] around the WRONG behaviour  | someone fixes the bug (flip it!) |
 * | mutant catch       | `mutant catch - <mutant> - ...`| [expectViolation] around the mutant run| the property stops seeing it     |
 * | accepted           | `accepted - ...`               | assertions of the documented trade-off | the trade-off changes            |
 * | seed sweep         | `seed sweep - ...`             | [DeterministicSimulationRunner.runSuite]| any seed violates an invariant  |
 *
 * Tag known-bug tests with `@Tag("known-bug")` so they can be listed (`./gradlew test -PincludeTags=known-bug`
 * or a report grep). They PASS while the bug reproduces, so a dashboard of green tests is not "no bugs".
 */
object SimulationTestModes {
    const val KNOWN_BUG_TAG = "known-bug"
}

/**
 * Pins CURRENT, WRONG behaviour of shipped code. [reproduces] must contain assertions that hold only while the bug
 * exists (e.g. `scylla["LEVEL"] shouldBe "40"` where the correct answer is `null`).
 *
 * - While the bug reproduces: passes, and prints a `KNOWN BUG` line so it is visible in the test log.
 * - Once the bug stops reproducing (the block throws an AssertionError): FAILS with an instruction to flip the test
 *   into a `regression guard` asserting [correctBehaviour].
 *
 * [issue] must be a tracker reference such as `"#123"` or a URL; a pin without an issue is rejected.
 */
fun knownBug(
    issue: String,
    correctBehaviour: String,
    reproduces: () -> Unit,
) {
    require(Regex("""#\d+|https?://\S+""").containsMatchIn(issue)) {
        "knownBug needs a tracker reference ('#123' or a URL), got '$issue'"
    }
    require(correctBehaviour.isNotBlank()) { "knownBug must say what the correct behaviour is" }
    try {
        reproduces()
    } catch (e: AssertionError) {
        throw AssertionError(
            "known bug $issue no longer reproduces. If it was fixed on purpose: rename this test to " +
                "'regression guard - ...', replace the pinned assertions with the correct behaviour " +
                "($correctBehaviour), and close $issue. If not, the scenario drifted and no longer reaches the bug.\n" +
                "First assertion that no longer holds: ${e.message}",
            e,
        )
    }
    SimulationLog.sink("[sim] KNOWN BUG $issue still reproduces (correct behaviour: $correctBehaviour)")
}

/**
 * Mutant catch: runs [block] (usually a scenario or a whole [DeterministicSimulationRunner.runSuite] against a
 * deliberately faulty variant) and requires it to fail on invariant [invariantId]. A failure on a DIFFERENT
 * invariant or exception does not count: it would prove the harness can fail, not that this property sees the fault.
 * Returns the failure message (it carries the seed, for the test's KDoc).
 *
 * Only AssertionErrors count. Make a real contract class's `require`/`check` visible as a named property at the call
 * site (`sim.always("IntentLifecycleContract", runCatching { Intent(...) }.isSuccess)`), so mutants that trip it
 * can be attributed. With the template runner, any other exception thrown by a scenario arrives wrapped as
 * "scenario threw ..." and is reported as "caught, but not by [id]": harness errors are never silently accepted.
 *
 * Shadowing: if a stronger check (a step-level oracle, a real contract class) always fires first, either record
 * "shadowed by X" in the test KDoc, or run the mutant with that check disabled to show [invariantId] also sees it.
 */
fun expectViolation(
    invariantId: String,
    block: () -> Unit,
): String {
    val failure =
        try {
            block()
            null
        } catch (e: AssertionError) {
            e
        }
    checkNotNull(failure) { "Mutant survived: expected invariant [$invariantId] to be violated, but the run passed" }
    val message = failure.message.orEmpty()
    if ("[$invariantId]" !in message) {
        throw AssertionError("Mutant was caught, but not by [$invariantId]. Actual failure:\n$message", failure)
    }
    return message
}
