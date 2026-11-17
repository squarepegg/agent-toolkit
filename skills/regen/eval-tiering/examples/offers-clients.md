# Eval tiering: Offers outbound clients (Vendor, Restate)

| | |
|---|---|
| Repo | `example-service` @ `main` |
| Target | tests importing `com.example.offers.api.clients` (3 files, 28 cases) |
| Date | 2026-10-14 |
| Skill | `eval-tiering` |
| Inputs | `list_tests.py . --for …/api/clients`, the 3 test files read in full, audit `2026-10-14-implementation-memory-audit-api-clients.md`, drafts compiled and run in a worktree, three mutation checks |

## Verdict

All 28 cases state a true requirement, but only 7 would still run unchanged against a
rewritten client. The 11 retry cases call an `internal` helper directly. A regeneration that
inlines or renames that helper takes the retry tests with it, and the 15 wire tests stay green
(shown below). Four drafted test files (16 cases) close the gaps. All pass on `main`. Each
of three mutation checks failed a draft while every existing Vendor client test passed.

## Findings

| test file › cases | tier | rewrite-safe? | protects | note |
|---|---|---|---|---|
| `VendorHttpClientTest` › 5 request-side cases (path and body, single call per family, only `triggers[]`, `vendor-api-key` header, live-package URL) | durable | yes, against the wire | wire contract with Vendor; audit #13 | keep |
| `VendorHttpClientTest` › 9 response-side cases (parse envelope, singular `trigger`, segments, empty entries, 5xx/404/timeout → `VendorUnreachableException`, optional fields, live path parse) | durable intent, harness tied to Kotlin types | intent yes, harness no | parsing and fail-open classification; audit #8 | keep; port with the client API |
| `VendorHttpClientTest` › cancelled caller sees `CancellationException` | durable intent, coroutine-specific harness | intent yes, harness no | audit #7 | keep |
| `VendorRetryTest` › 11 cases | **ephemeral harness, durable intent** | no: calls `internal fun retryOnConnectionReset` / `isRetryableConnectionFailure` with Micronaut exception types | retry policy; audit #1 | keep; durable twin drafted |
| `RestateClientTest` › 2 cases (body + `Idempotency-key`; no throw when ingress unreachable) | durable | yes, against the wire | audit #15, #16 | keep |

Rewrite check: 28 of 28 cases state a requirement that survives. 7 survive with their
harness intact.

Live: the code emits metrics `offers.decision.fail_open{reason}`,
`offers.cooldown.record_failed` and 6 others. The repo defines **no monitor**.
`entity.datadog.yaml` states the p99 < 200 ms SLO in prose only. (`list_tests.py` also flagged
`infra/load/loadtest-config.yml` as k6; it is a Micronaut config file, a false positive.)

## Gaps and drafts

| # | missing evaluation | kind | source | draft | mutation check |
|---|---|---|---|---|---|
| 1 | a connection dropped before the response is retried once, then succeeds | wire contract | audit #2, PR-B | `VendorWireContractTest` | unwrapping `retryOnConnectionReset` at `VendorHttpClient.kt:74`: **this test fails; all 26 existing Vendor client tests pass** |
| 2 | with retries off, a drop is an outage; a stall or an HTTP status is never retried (request count at Vendor = 1) | wire contract | audit #1, #4; commit-B | `VendorWireContractTest` (3 cases) | not mutated |
| 3 | Vendor 401 and 403 → auth rejection, not outage | wire contract | audit #6 | `VendorWireContractTest` | deleting the branch at `:78-80`: **this test fails; all 26 existing Vendor client tests pass** |
| 4 | one malformed entry never drops the others; parsing never throws | property (50 iterations) | audit #11; commit-A | `VendorEntryParsingProperties` | deleting the per-entry `catch` at `:143`: **fails after 2 attempts**, shrunk to `{contentId, contentData:"not-an-object"}`; `VendorHttpClientTest` passes |
| 5 | a failed cooldown write is counted (5xx, unreachable); a 2xx is not | wire contract + metric | audit #15; TDD:333 | `RestateRecordDecisionContractTest` | not mutated |
| 6 | pin current handling of empty body, non-JSON, missing `entries`, `id`/`offerId`/`layout` fallbacks, default `active` | characterisation (not endorsement) | audit #9, #10, #12 (unknown) | `VendorResponseCharacterisationTest` (7 cases) | n/a: pins, does not judge |
| 7 | pool idle timeout and retry default within bounds; prod `base-url` non-blank | config assertion | audit #3, #4, #14 | → `architecture-constraints` | |
| 8 | monitors on `fail_open` and `record_failed`; p99 > 200 ms alert | live | TDD:78, :494 | none: monitors live in Datadog, not this repo | |

## Proposed files

All in `docs/phoenix/proposals/2026-10-14-api-clients/`. Status: proposed, compiled and
passing on `main` in a throwaway worktree. Not applied.

| File | Purpose |
|---|---|
| `RawHttpStub.kt` | raw-socket stub that can drop or stall a connection (java.net only) |
| `VendorWireContractTest.kt` | gaps 1–3 (5 cases) |
| `VendorEntryParsingProperties.kt` | gap 4. Needs `testImplementation("io.kotest:kotest-property:5.9.1")` |
| `RestateRecordDecisionContractTest.kt` | gap 5 (3 cases) |
| `VendorResponseCharacterisationTest.kt` | gap 6 (7 cases) |

To apply: copy into `offers-api-service/src/test/kotlin/com/example/offers/api/clients/`
and add the dependency above.

## Unknowns

- Whether the Datadog monitors in gap 8 exist. Ask Offers on-call.
- Whether the characterised behaviours (gap 6) are wanted. If one is not, change it with a
  decision record, then update the characterisation test.

## Limitations of this run

- `docs/` is gitignored in this repo (project notes). Citations to `docs/...` (TDD, plan, ADRs)
  resolve only on the author's machine.
- The first compile surfaced a JUnit 5 trap: expression-bodied `@Test` functions that return a
  value are skipped silently, so only 1 of the first 19 draft cases ran. Fixed with
  `(): Unit =`. This repo already warns about it at `OfferStateStoreTest:47`.
- Running this skill exposed two wrong rows in the memory audit (#15 and #16 were pinned, not
  unguarded). The audit is corrected.
- Mutation checks covered gaps 1, 3 and 4 only.

## Next

Run `architecture-constraints` on Offers for gap 7 and the deletion-test ownership rule.
