# Architecture constraints: Offers

| | |
|---|---|
| Repo | `example-service` @ `<commit>` |
| Target | `offers-api-service` classpath (api-service, database, shared) |
| Date | 2026-10-14 |
| Skill | `architecture-constraints` |
| Inputs | audit, deletion-test and eval-tiering reports of 2026-10-14; `README`; `HealthController` KDoc; `check_provenance.py`; drafts compiled and run in a worktree; one mutation check |

## Verdict

Seven fitness functions have a recorded reason and hold today: four ArchUnit rules and three
config assertions. One structural rule the code mostly follows has no recorded reason, and it
**fails today**. It is drafted disabled and flagged. One wanted guard, a non-blank prod
`base-url`, cannot be expressed in this repo.

## Findings

| # | rule | regeneration test | reason (source) | mechanism | holds today? | status |
|---|---|---|---|---|---|---|
| 1 | only `restate` / `database` use `OfferStateWriter` | yes: a second writer races the per-key read-modify-write | single logical writer (deletion-test) | ArchUnit | yes | proposed |
| 2 | `offers-shared` uses no framework (Micronaut, Koin, Restate, Cassandra driver, Micrometer) | yes: breaks the restate runtime | two runtimes consume shared (project README) | ArchUnit | yes | proposed |
| 3 | only `newVendorHttpClient` builds Micronaut HTTP clients | yes: sporadic prod fail-opens, invisible to tests | LB idle reaping (production incident; audit #2, #3) | ArchUnit | yes | proposed |
| 4 | `HealthController` depends on no downstream | yes: rollout deadlock | `HealthController.kt` KDoc: "a deadlock with no escape" | ArchUnit | yes. **Mutation:** injecting `SnapshotProvider` fails the rule | proposed |
| 5 | pool idle timeout in 1..59 999 ms | yes | incident review; revisit when the ALB idle is measured | config assertion | yes (30000) | proposed |
| 6 | Vendor timeout ≤ 200 ms and retries ≤ 1 | yes: trades the SLO for availability | p99 < 200 ms (`service-slo.yaml:8`); incident review | config assertion | yes (200, 1) | proposed |
| 7 | `VendorConfig` defaults equal `application.yml` defaults | yes: an edit to one is silently ignored | audit tension 4 | config assertion | yes | proposed, pending team agreement |
| 8 | controllers do not use clients directly | **don't know** | none found | ArchUnit | **no**: `EvaluateController.kt:3` imports `RestateClient` | flagged: no reason (disabled) |
| 9 | prod `base-url` is non-blank (no Noop client in prod) | yes: offers silently disabled | audit #14 | not expressible here: prod env lives in `the deployment repo` | n/a | needs a startup check or a gitops-repo test |

Not architecture (regeneration test: no): which HTTP library reaches Vendor (only its hardening
matters, #3); Koin vs Micronaut DI in `restate`; one `ObjectMapper` per client.

Behavioural, so handed to `eval-tiering`: idempotent decision recording, the retry policy, and
cancellation propagation. These are drafted or pinned already.

## Proposed files

`docs/phoenix/proposals/2026-10-14-offers-constraints/`. Compiled and run on `<commit>`:
7 pass, 1 skipped (flagged). `check_provenance.py --strict`: 7 ok, 1 flagged, exit 0.

| File | Purpose |
|---|---|
| `ArchitectureConstraintsTest.kt` | rules 1–4 |
| `ConfigConstraintsTest.kt` | rules 5–7 (read the shipped defaults in `application.yml`) |
| `FlaggedConstraintsTest.kt` | rule 8, `@Disabled` until it has an owner and a reason |

Needs `testImplementation("com.tngtech.archunit:archunit:1.4.1")` in
`offers-api-service/build.gradle.kts`. CI option: run `check_provenance.py --strict` on
`src/test` so that no rule can be enforced without a reason.

## Unknowns

| Unknown | Settles it | Who |
|---|---|---|
| Rule 8: should controllers avoid clients? If yes, `EvaluateController → RestateClient` is a violation to fix; if no, drop the rule | owner decision | the platform team |
| Rule 5's real upper bound | ALB `idle_timeout.timeout_seconds` | platform / infra |
| Rule 9's home | a test in `the deployment repo`, or a prod-profile startup assertion here | the platform team + infra |

## Limitations of this run

- `offers-restate` is not on the api-service test classpath. Rule 1 cannot see a restate
  class misusing the writer (it is the allowed writer anyway).
- Config rules check shipped defaults, not deploy-time env overrides.
- `docs/` is gitignored here (per the project README). The design-doc citations resolve only on the author's
  machine; every header cites a tracked source where one exists.
- Mutation-checked: rule 4 only.

## Next

Ask the the platform team about rule 8 and capture the answer with `decision-capture`.
