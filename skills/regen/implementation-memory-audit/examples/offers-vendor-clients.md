# Implementation memory audit: Offers outbound clients (Vendor, Restate)

| | |
|---|---|
| Repo | `example-service` @ `main` |
| Target | `offers-api-service/src/main/kotlin/.../api/clients/`, `.../api/config/VendorConfig.kt`, `src/main/resources/application.yml` (`offers.vendor.*`) |
| Date | 2026-10-14 |
| Skill | `implementation-memory-audit` |
| Inputs | `scar_scan.py` (29 sites, 0 untraced), manual read of all 3 client files, PRs A, C, D, B, E via `gh`, `docs/tdd-offers-phase1-v1.md`, `docs/plan-trigger-conditions-and-segments.md`, `docs/adversarial-review-…md`, Vendor DTOs in `vendor-service` @ `commit-I` |

## Verdict

The retry, timeout and cancellation behaviour is well explained and mostly pinned by tests.
The response-parsing fallbacks, the null-body and bad-JSON handling, and the Restate timeouts
have no recorded reason. Treat them as **unknown, do not remove**. Six sites have a known
lesson but nothing would fail if a regeneration dropped them (#2, #3, #4, #6, #11, #14).

> Corrected 2026-10-14: #15 and #16 were first reported as unguarded. `RestateClientTest`
> pins both. The scanner matched test text case-sensitively (`idempotency-key` vs the JDK's
> `Idempotency-key`); fixed in `scar_scan.py`. Found during `eval-tiering`.

## Findings

| # | location | what it does | origin (commit · PR · incident) | lesson | guard today | proposed guard | status |
|---|---|---|---|---|---|---|---|
| 1 | `VendorHttpClient.kt:207-221` | retries only `ResponseClosedException` / "connection reset" / "closed before response"; never `ConnectException`, read timeout, or HTTP status | `commit-B` · PR-B · prod: "~6 such fail-open events over ~7h" | stated: "fresh-dial failures … where a retry just burns the 200ms hot-path budget" (commit-B) | pinned: `VendorRetryTest` (11 tests) | none needed | scar: guarded |
| 2 | `VendorHttpClient.kt:74`, `SnapshotCache.kt:257` | wraps both Vendor calls in `retryOnConnectionReset(maxConnectionRetries)` | `commit-C` · PR-B · same incident | stated: pooled connections reused "after the upstream LB has already closed them" (#11) | none. #11: "Retry wiring isn't integration-tested" | contract test: stub server drops the first connection; call succeeds with one retry; hot path and poll both | scar: unguarded |
| 3 | `VendorConfig.kt:34`, `application.yml:39`, `VendorHttpClient.kt:170` | evicts idle pooled connections after 30 s (Micronaut default 0 = never) | `commit-C` · PR-B · same incident | stated: "below the common 60s ALB idle default". Also stated: "30s is an inference, not a measured LB value" (#11) | none | config assertion: `0 < poolIdleTimeoutMs < LB idle timeout`; record the measured LB value | scar: unguarded, value unverified |
| 4 | `VendorConfig.kt:43`, `application.yml:40` | at most 1 retry | `commit-C` · PR-B | inferred: one retry fits the 200 ms budget, two do not (basis: PR-B budget wording) | mentioned: `VendorRetryTest` tests `maxRetries=0`, not the default | config assertion: default ≤ 1 | scar: unguarded |
| 5 | `VendorConfig.kt:20`, `application.yml:38`, `VendorHttpClient.kt:168-169` | 200 ms read **and** connect timeout on the hot path | `commit-A` · PR-A (initial build PR) · none found | stated: "200ms keeps the decision-path inside the 200ms p99 SLO" (KDoc); "p99 < 200ms server-side (PRD §5)" (TDD:333) | pinned (behaviour): `VendorHttpClientTest:285` times out → `VendorUnreachableException`. Value not pinned | live: Datadog "p99 > 200ms for 5min" (TDD:494). Confirm the monitor exists | scar: guarded (behaviour) |
| 6 | `VendorHttpClient.kt:78-80` | Vendor 401/403 → `VendorAuthRejectedException` (→ HTTP 401), not fail-open 503 | `commit-F` · PR-D (no PR body) · none found | inferred: a rejected `vendor-api-key` is a caller fault and must not look like a Vendor outage (basis: commit "add VendorAuthRejectedException → 401") | mentioned: `EvaluateControllerFailureHandlingTest:156` pins the controller mapping, not the client's 401/403 detection | contract test at the client: stub 401 and 403 → `VendorAuthRejectedException`; 500 → `VendorUnreachableException` | scar: unguarded at client |
| 7 | `VendorHttpClient.kt:82-83`, `:189-190` | rethrows `CancellationException` before the broad catch | `commit-H` · PR-E · none found | stated: "so a cancelled caller is reported as cancelled rather than as an unreachable Vendor" (commit-H) | pinned: `VendorHttpClientTest:300`, `VendorRetryTest:105`, `SnapshotCacheScheduledRefreshRaceTest:124` | none needed | scar: guarded |
| 8 | `VendorHttpClient.kt:84-86` | any other failure → `VendorUnreachableException` (→ 503 fail-open) | `commit-A` · PR-A | stated: "typed exception for transport/protocol failures (controller maps to 503 fail-open per TDD §12)" (commit-A) | pinned: `VendorHttpClientTest:253,270,285` | none needed | scar: guarded |
| 9 | `VendorHttpClient.kt:88` | null response body → empty offer list | `commit-A` · PR-A | unknown. The commit says an empty *list* is a valid outcome; it does not mention a null body | none | contract test pinning the current behaviour, then decide | unknown, do not remove |
| 10 | `VendorHttpClient.kt:92-108` | unparseable JSON or missing `entries` → empty list + WARN, not an exception | `commit-A` · PR-A | unknown | none | contract test pinning the current behaviour; see Tension 2 | unknown, do not remove |
| 11 | `VendorHttpClient.kt:118-121`, `:143-146` | skips an entry with no id or that fails to map; keeps the rest | `commit-A` · PR-A | stated: "malformed individual entries are skipped with a WARN, the rest of the response still returns" (commit-A) | mentioned: `VendorHttpClientTest:334` covers missing *optional* fields only | property test: for any entry list, result = the well-formed entries, never throws | scar: unguarded |
| 12 | `VendorHttpClient.kt:114-117`, `:126-127`, `:132` | id falls back `contentId` → `id` → `offerId`; layout `#schema.layout` → `layout`; `active` defaults to `true` | `commit-A` · PR-A | unknown. Vendor's DTO (`PackageContentDataEntryResponseDto`, vendor-service `commit-I`) sends `contentId` and `#schema`. It sends no `id`, `offerId`, top-level `layout` or `active` | none | contract test against Vendor's response schema; then run `compaction-pass` | unknown, do not remove |
| 13 | `VendorHttpClient.kt:60-64` *(found by reading)* | sends only `triggers[]`, never the singular `trigger` | added `commit-G` (dual-send), narrowed `commit-L` · PR-E · none | stated: "deployment-ordering dependency: Vendor must not lag Offers' deploy … No runtime fallback exists by design" (docs/plan…:161) | pinned: `VendorHttpClientTest:100` | decision record: the doc holds the reason, but no ADR does | scar: guarded |
| 14 | `VendorHttpClient.kt:30`, `NoopVendorClient.kt:11` *(found by reading)* | blank `base-url` → Noop client returns no offers, WARN at startup | `commit-A` · PR-A | stated: "local dev / misconfigured deploys" won't crash (VendorConfig KDoc, commit-A) | none found | config assertion for the prod profile: `base-url` non-blank; or a monitor on the Noop WARN | scar: unguarded |
| 15 | `RestateClient.kt:34-68` | `RecordDecision` is fire-and-forget (`sendAsync`); every failure is swallowed and counted | `commit-E` · PR-C · none found | stated: "doesn't block the response; durability is Restate's job" (TDD:333) | pinned (no-throw): `RestateClientTest` "recordDecision does not throw on ingress failure". Not pinned: metric increment, non-2xx path | contract test: Restate 5xx increments `offers.cooldown.record_failed` and never fails `/evaluate` | scar: guarded (partly) |
| 16 | `RestateClient.kt:49` | `idempotency-key: <decisionId>` | `commit-E` · PR-C | inferred: Restate ingress dedupes a re-sent decision (basis: header semantics; no text found) | pinned: `RestateClientTest:33` asserts `Idempotency-key` contains the decision id | Restate side: same key twice → one cooldown record | scar: guarded |
| 17 | `RestateClient.kt:24`, `:51`; `VendorConfig.kt:23` | 2 s Restate connect/request timeouts; 2 s Vendor poll timeout | `commit-E` · PR-C; `commit-F` · PR-D | unknown for the values. Poll: stated "not on the hot path" (KDoc) | none | none until a reason is known | unknown, do not remove |

Incidental (traced, not scar tissue): `@Suppress("UNCHECKED_CAST")` `:139`, `retryLog` logger
`:174`, loop counter `:185`.

## Tensions

1. **Timeout vs retry vs SLO.** Read and connect timeouts both equal the whole 200 ms p99
   budget (#5). A reset late in the first attempt followed by one retry (#4) can take
   ~400 ms. #11 argues resets are fast, but nothing measures this. Ask for a p99 broken down
   by retried vs not retried.
2. **Bad response = "no offer".** #9 and #10 turn a protocol failure into a valid empty
   decision. The TDD wants "fail-open count (by reason: null, timeout, 5xx)" (TDD:78). These
   cases are not counted as fail-opens.
3. **Not a conflict:** TDD:50 says "No retries." That is the Unity client → Offers call.
   #1 and #2 are Offers → Vendor. Different layers. Do not "fix" one to match the other.
4. **Two sources of defaults.** `VendorConfig` and `application.yml` both default 200 /
   30000 / 1. YAML wins at runtime. A change to one is silently ignored.
5. **Blame is misleading here.** `commit-J` ("rewrite comments to describe the finished system,
   not its history") and `commit-K` (CI) are the last touch on many lines. Use pickaxe.

## Proposed files

None written. Guards above go to `eval-tiering` (tests) and `architecture-constraints`
(config assertions).

## Unknowns

| Unknown | Settles it | Who |
|---|---|---|
| Real idle timeout of the ALB in front of Vendor | ALB attribute `idle_timeout.timeout_seconds` | platform / infra |
| Why `id`, `offerId`, `layout`, `active` fallbacks exist (#12) | Vendor response history before `commit-I`; author memory | Vendor team, original author of commit-A |
| Whether a null body or bad JSON from Vendor has ever happened (#9, #10) | Datadog logs for "Could not parse Vendor response" / "missing `entries`" | Offers on-call |
| Reason for 2 s Restate timeouts (#17) | author memory; Restate ingress SLO | Offers team |
| Whether the p99 > 200ms monitor exists (#5) | Datadog monitor list | Offers team |

## Limitations of this run

- `docs/` is gitignored in this repo (project notes). Citations to `docs/...` (TDD, plan, ADRs)
  resolve only on the author's machine.
- Scope was the clients package. `SnapshotCache` was read only where it calls these helpers.
- Five of 29 scanner sites were noise or incidental. Two behaviours (#13, #14) were found only
  by reading.
- PR-A is the initial-build PR. "Origin PR-A" means "since the start", not a reason.
- No chat, ticket tracker or monitoring tool was searched. "none found" means none in git, PRs or docs.
- Commit bodies here are AI co-authored. They are quoted as what the author believed at the
  time.

## Next

Run `eval-tiering` on `offers-api-service/src/test/.../clients/`. Draft the contract tests
for #2, #6, #9–#12, #16 and the property test for #11.
