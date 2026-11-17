# Compaction pass: Offers outbound clients and config

| | |
|---|---|
| Repo | `example-service` @ `main` |
| Target | `offers-api-service/.../api/clients/`, `.../api/config/` (6 files, 346 LOC) |
| Date | 2026-10-14 |
| Skill | `compaction-pass` |
| Inputs | `module_facts.py`; audit 2026-10-14 (exclusion list); `RestateClientFactory.kt`; `ErrorHandlers.kt`; tests constructing `RestateClient` |

## Verdict

Little mass to remove. Most of what looks odd here is scar tissue, and it is excluded. Two low- to
medium-risk compactions remove one concept each: a duplicated source of truth and a
nullable-for-tests branch. Three abstractions were questioned and pay their rent. **Nothing
has been applied. Reply with item numbers to apply.**

## Findings

| # | item | kind | rent it pays | proposal | mass removed | risk | guard first |
|---|---|---|---|---|---|---|---|
| 1 | Vendor defaults set twice: `VendorConfig` (200 / 30000 / 1) and `application.yml:38-40` | duplicated truth | none: YAML wins at runtime, so an edit to `VendorConfig` is dead (audit tension 4) | make YAML the only source. `VendorConfig` keeps the KDoc (the *why*) but its initialisers must not look authoritative: either point the KDoc at the YAML, or bind with `@Bindable(defaultValue=…)` from one constant. Env-var names (`VENDOR_*`) unchanged | 1 concept (which default is real) | **medium**: the env-var mapping lives in YAML and in gitops; changing names is cross-repo, so don't | `ConfigConstraintsTest` "single effective value" (drafted, passing) |
| 2 | `RestateClient(metrics: DecisionMetrics? = null)`, with `metrics?.` at two sites | nullable-for-tests | none in production: `RestateClientFactory.kt:17` always passes metrics; only `RestateClientTest:25,51` omit it | make it non-null; the tests pass `DecisionMetrics(SimpleMeterRegistry())` | 1 exception path (failures that are silently uncounted) | **low**: the compiler finds every caller | `RestateRecordDecisionContractTest` (drafted, passing) |

### Considered, and they pay their rent

| item | why it stays |
|---|---|
| `RestateClientFactory` + `RestateConfig` (a factory for one product) | keeps `RestateClient` framework-free (a plain URL), so tests build it without Micronaut. `@Requires(ingress-url ≠ "")` gives a no-Restate mode for local dev |
| `isRetryableConnectionFailure` as `internal` (0 main refs outside its file) | a test seam for `VendorRetryTest`'s 11 cases. Revisit once `VendorWireContractTest` lands; it could then become `private` |
| `ErrorHandlers` (0 references) | not dead: Micronaut wires it through `@Error(global = true)`. A textual count cannot see that |

## Excluded (scar or unknown)

| item | audit row | why it stays |
|---|---|---|
| `contentId → id → offerId`, `layout` and `active` fallbacks | #12 unknown | Vendor's DTO suggests they are dead, but no one has confirmed it. Path out: characterisation test (drafted), then Vendor team confirmation, then a decision record, then a removal proposal |
| null body or non-JSON → empty offers | #9, #10 unknown | may be deliberate fail-open. Needs an owner decision (audit tension 2) |
| `NoopVendorClient` + `@Requires(base-url ≠ "")` | #14 | stated reason: local dev and misconfigured deploys must not crash |
| retry wrapper, retry predicate, pool idle eviction, `CancellationException` rethrows | #1, #2, #3, #7 | lessons from a production incident (PR-B) and from `commit-H` |
| broad catch → `VendorUnreachableException`; 401/403 → auth rejection | #6, #8 | fail-open policy and the auth-vs-outage split |
| Restate fire-and-forget and the `idempotency-key` header | #15, #16 | TDD:333; the header is pinned by `RestateClientTest` |

## Proposed files

None beyond what eval-tiering and architecture-constraints already drafted. Those are the
guards for items 1 and 2.

## Unknowns

- Whether anything outside this repo reads `VendorConfig`'s code defaults. Inferred no: it is
  an internal `@ConfigurationProperties` class.

## Limitations of this run

- Usage counts are textual. `ErrorHandlers` shows 0 references but is live through DI.
- Mass was judged in concepts. Both items are small; this module is already compact.
- `docs/` is gitignored here. TDD citations resolve only on the author's machine.

## Next

Reply "apply 2" (and/or "apply 1") to apply. Each lands as its own commit after its guard test
is in `src/test`.
