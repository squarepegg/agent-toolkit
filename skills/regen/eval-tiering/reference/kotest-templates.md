# Durable-evaluation templates (Kotest)

Pick the variant that matches the repo's runner. Check `build.gradle.kts`:
`kotest-runner-junit5` means Kotest specs; otherwise use JUnit 5 with Kotest assertions.

Dependencies (test scope; match the version the repo already uses for Kotest):

```kotlin
testImplementation("io.kotest:kotest-property:5.9.1")          // checkAll, Arb
testImplementation("io.kotest:kotest-assertions-core:5.9.1")   // shouldBe, etc.
// only if the repo runs Kotest specs:
testImplementation("io.kotest:kotest-runner-junit5:5.9.1")
```

**JUnit 5 trap.** A `@Test` function with an expression body that returns a value is not a
test: JUnit skips it silently. `fun x() = runBlocking { list shouldHaveSize 2 }` returns a
list. Always write `fun x(): Unit = runBlocking { … }` or use a block body. Kotest specs do not
have this problem.

**Test-hygiene traps** (each was a real review finding on Offers a review):

- **Close what you construct.** A client built by hand (not by the DI container) never gets its
  `@PreDestroy`. Close it in `finally`, including on expected-exception paths.
- **A stub must frame HTTP by bytes.** `Content-Length` counts bytes, and one `read` may return
  fewer than asked. Read headers to CRLF CRLF and the body with `readNBytes(len)`. A stub's
  `close()` must also close the connections it accepted, not just its listening socket.
- **Async side effects need a held assertion.** If the code updates a metric in a callback,
  "still zero" asserted once can pass before the callback runs. Use
  `continually(<longer than the client timeout>)`, and `eventually` for "becomes N".
- **Config assertions read the shipped file, not the environment.** See template 5.

Every draft starts with a header:

```kotlin
/**
 * Durable evaluation (proposed by eval-tiering, YYYY-MM-DD).
 * Requirement: <one sentence, domain words>
 * Source: <audit row / ADR / PR / doc:line>
 * Survives a rewrite because: <asserted at the wire | universal property | schema>
 */
```

## 1. Property test, Kotest spec

```kotlin
class CooldownStateProperties : FunSpec({
    test("lifetime count never decreases") {
        checkAll(arbOfferState().orNull(), Arb.instant(), Arb.long(1, 86_400).orNull()) { existing, at, window ->
            val next = computeNewState(existing, "p", "o", at, window?.let(Duration::ofSeconds))
            next.returnedLifetime shouldBeGreaterThanOrEqual (existing?.returnedLifetime ?: 0)
        }
    }
})
```

## 2. Property test, JUnit 5 runner

```kotlin
class CooldownStatePropertiesTest {
    @Test
    fun `lifetime count never decreases`(): Unit = runBlocking {
        checkAll(arbOfferState().orNull(), Arb.instant()) { existing, at ->
            val next = computeNewState(existing, "p", "o", at, null)
            next.returnedLifetime shouldBeGreaterThanOrEqual (existing?.returnedLifetime ?: 0)
        }
    }
}
```

Build domain generators with `arbitrary { … }` and keep them in one `Arbs.kt` next to the drafts.

## 3. Wire contract test (outbound HTTP)

Stand up the dependency at the wire with the JDK `HttpServer` or a raw `ServerSocket` (no new
libraries), point the real client at it, and assert what crosses the wire. A raw socket can do
what an embedded server cannot: drop a connection mid-request, or stall past a timeout. See
`examples/RawHttpStub.kt`.

```kotlin
class VendorWireContractTest {
    private val requests = CopyOnWriteArrayList<RecordedRequest>()
    private lateinit var server: HttpServer

    @BeforeEach fun start() {
        server = HttpServer.create(InetSocketAddress(0), 0).apply {
            createContext("/") { ex -> requests += RecordedRequest.of(ex); respond(ex) }
            start()
        }
    }
    @AfterEach fun stop() = server.stop(0)

    @Test
    fun `vendor 401 or 403 is an auth rejection, not an outage`(): Unit = runBlocking {
        for (status in listOf(401, 403)) {
            respondWith(status)
            shouldThrow<VendorAuthRejectedException> { client().fetchOffersByTriggers(release, listOf("t.v1"), "p", "k") }
        }
    }
}
```

## 4. Contract test (inbound or stored shape)

Assert the shape, field by field, in domain terms. Do not compare to an object built by the
code under test.

```kotlin
@Test
fun `cooldown row round-trips every field, including null timestamps`() { /* write, read, compare */ }
```

## 5. Config assertion (fitness function for a scar value)

Assert on the **declared** default in the packaged config file. Do not boot a context or resolve
the environment: `${VENDOR_X:30000}` resolves to whatever `VENDOR_X` is on the test machine, so an
env var can mask or fake the default. Give it the provenance header from
`architecture-constraints` (it is a fitness function).

```kotlin
private val shipped: Map<String, Any?> by lazy {   // dotted keys, placeholders unresolved
    javaClass.getResourceAsStream("/application.yml")!!.use { YamlPropertySourceLoader().read("application", it) }
}
private fun shippedDefault(key: String): Long {
    val raw = shipped[key]?.toString()?.trim() ?: throw AssertionError("$key not set in application.yml")
    return (Regex("""^\$\{[^:}]+:([^}]*)}$""").matchEntire(raw)?.groupValues?.get(1) ?: raw).toLong()
}

@Test
fun `pool idle timeout is positive and below the LB idle timeout`() {
    shippedDefault("offers.vendor.pool-idle-timeout-ms") shouldBeInRange 1L..59_999L
}
```

Check it with the override set: `VENDOR_X=0 ./gradlew test --tests '*ConfigConstraintsTest'` must still pass.

## 6. Boundary-biased generators

Uniform random inputs almost never land exactly on a boundary. Over a 90-day range, a draw hits
`start + window` with odds of about one in eight million, so an off-by-one at the edge survives
every property. Mix in values at the edge on purpose, and give the boundary its own property.

```kotlin
// Gaps between events: seconds to weeks, plus exactly one window ±1 s.
private val windowEdge: Arb<Long> =
    Arb.bind(Arb.element(24L, 168L, 720L), Arb.long(-1L..1L)) { h, d -> h * 3_600 + d }
private val gap: Arb<Long> =
    Arb.choice(Arb.long(0L..3_600), Arb.long(0L..2L * 86_400), Arb.long(0L..40L * 86_400), windowEdge)

@Test
fun `a window closes exactly at its end`(): Unit = runBlocking {
    checkAll(arbState(), arbInstant(), arbWindow(), Arb.long(-3L..3L)) { s, start, w, offset ->
        val at = start.plus(w).plusSeconds(offset)
        val next = computeNewState(s.copy(windowStartedAt = start), "p", "o", at, w)
        next.windowStartedAt shouldBe (if (offset >= 0) at else start)   // open before the end, closed from it
    }
}
```

## 7. Reference model across components

When two components must agree, for example a writer and a reader or a filter and a recorder,
drive them together the way production does. Compare the result with a short model of the
**product rule**, written without reference to the code. This was the strongest test in the
Offers run: it caught bugs in both components, including two boundary bugs every example
test missed.

```kotlin
/** Evaluate at each time; record when shown, like /evaluate → RecordDecision. */
private fun shown(rules: Rules, times: List<Instant>): List<Instant> {
    var state: OfferState? = null
    return times.filter { at ->
        val show = CooldownFilter.apply(listOf(offer(rules)), state?.let { mapOf("o" to it) } ?: emptyMap(), at).isNotEmpty()
        if (show) state = computeNewState(state, "p", "o", at, rules.window)
        show
    }
}

/** The rule as a product owner states it: at most N per window, at most M ever. */
private fun expected(rules: Rules, times: List<Instant>): List<Instant> { /* ~15 lines */ }

@Test
fun `the offer is shown exactly when the rules allow`(): Unit = runBlocking {
    checkAll(arbRules(), arbTimes()) { r, times -> shown(r, times) shouldBe expected(r, times) }
}
```

Assert equality with the model, not only the caps. A filter that blocks everything satisfies
"at most N" but fails "shown whenever allowed".

## 8. Environment-dependent suites: skip locally, fail in CI

A suite that needs Docker, a sibling checkout or a credential may skip on a laptop. It must not
skip in CI, or the job passes having tested nothing.

```kotlin
// build.gradle.kts, on the integration Test task
systemProperty("integration.required", (System.getenv("CI") == "true").toString())

// in the test's prerequisite check
private fun skip(reason: String): Boolean {
    check(System.getProperty("integration.required") != "true") { "Integration stack unavailable in CI: $reason" }
    skipReason = reason
    return false
}
```

Also set `testLogging { exceptionFormat = FULL }`. Gradle's short format prints only
`PSQLException at X.kt:57`, and CI rarely keeps the reports that hold the message.
