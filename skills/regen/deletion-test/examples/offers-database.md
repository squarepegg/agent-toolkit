# Deletion test: `offers-database`

| | |
|---|---|
| Repo | `example-service` @ `main` |
| Target | `offers-database/` (5 files, 243 LOC), plus its callers in `offers-restate` and `offers-api-service` |
| Date | 2026-10-14 |
| Skill | `deletion-test` |
| Inputs | `module_facts.py`, read of all 5 main files, `V001__cooldown.cql`, `OfferStateStoreTest`, `RecordDecision.kt`, `CooldownFilter.kt`, `RecordDecisionTest`, PRD v4 |

## Verdict

**Grain too fine.** `offers-database` is a thin wide-column database adapter. Deleting it is a mild
inconvenience: one real-Cassandra test suite would judge a regenerated version. The concept it
serves, *cooldown*, is spread across four modules: state and filter in `shared`, update rules
in `restate`, storage here, and reads in `api-service`. Deleting that concept would be close to
existential: no single place states its invariants, and part of it is unbuilt
(`purchasedInWindow` is never incremented).

## Findings

| dimension | score | evidence | what would raise it |
|---|---|---|---|
| comprehension | 3 | 243 LOC, 8 declarations. KDoc explains the non-obvious parts: prepared statements for shard-aware routing (`CassandraStoreReader.kt:12-17`), `lazy` so a missing table does not disable the reader, IO-pool confinement (`OfferStateWriter.kt:24-28`) | none |
| isolation | 2 | the module needs a real wide-column database (`OfferStateStoreTest:27`, `cassandra:5`). Reads go through the `OfferStateReader` interface (2 main impls), but the writer is a concrete class, so `RecordDecision.record` cannot run without the wide-column database | an `OfferStateWriter` interface beside `OfferStateReader` in `shared` |
| mutation ownership | 2 | one writer, `OfferStateWriter.upsert` (`:17`), called only from `RecordDecision.kt:51`. Restate keys the virtual object by `playerId-offerId` (`RestateClient.kt:47`), which serialises writes per row. But the write *rules* (`computeNewState`) live in `restate`. This module stores; something else decides | none at this grain. At the cooldown grain this would be a 3 |
| contract | 2 | the schema is `V001__cooldown.cql`; `OfferStateStoreTest` round-trips every column, including null timestamps. Versioning is fragile: `applyMigrations` loads that one file by name (`CassandraStore.kt:26`), so a `V002__*.cql` would be silently skipped | load every migration in order; test that each is applied |

Deletion dread: **mild inconvenience** for this module, **high** for the cooldown concept.
The cause: invariants are implied by `computeNewState` example tests and `CooldownFilter`,
and are not stated anywhere as properties.

## What would we rely on to judge a regenerated version?

| tier | oracle | judges |
|---|---|---|
| durable | `OfferStateStoreTest` (5 cases, real wide-column database: empty read, write→read, subset read, overwrite, null timestamps) | storage round-trip. Survives a rewrite: yes, it is a CQL round-trip |
| durable | `CooldownIntegrationTest` (api-service, Testcontainers) | end-to-end cooldown via `/evaluate`. Heavy |
| durable candidate | `RecordDecisionTest` (9 example cases on `computeNewState`) | update rules, as examples only |
| live | metrics `offers.cooldown.read_error`, `offers.cooldown.record_failed` (`DecisionMetrics.kt:148,152`) | failures only. **No monitor found in repo** |
| missing | invariants of the cooldown state machine; the migration set; the single-writer rule; who writes `purchasedInWindow` | |

Today a regenerated `offers-database` would be judged correctly. A regenerated cooldown
concept would be judged by 9 examples and one heavy integration suite.

## Three highest-leverage fixes

1. **State the cooldown invariants as properties** over `computeNewState` and `CooldownFilter`:
   lifetime count never decreases; window counters reset if and only if the window expired;
   every cap check is monotone in its counter. This raises the oracle for the whole concept
   without the wide-column database. → `eval-tiering` on `offers-restate` and `offers-shared/.../offer`.
2. **Make the migration set a contract:** apply every `migrations/V*.cql` in order, plus a test
   that the applied set equals the files on the classpath. Raises *contract* to 3.
   → `eval-tiering` (test) plus a small code change, on confirmation.
3. **Make ownership explicit:** an ArchUnit rule that only `offers-restate` depends on
   `OfferStateWriter`, and a decision record for who will write `purchasedInWindow`.
   Purchase recording is a PRD stretch goal (PRD v4:9, :73), so `isAtPurchaseCapPerWindow`
   (`CooldownFilter.kt:68`) can never trip today. → `architecture-constraints`, `decision-capture`.

## Proposed files

None. Fixes 1–3 are drafted by the skills named.

## Unknowns

| Unknown | Settles it | Who |
|---|---|---|
| Whether anything outside this repo writes `offers.cooldown` | the wide-column database audit or grants on the keyspace; other repos | Data platform |
| Whether the purchase cap is meant to be live | PRD owner | Offers PM |
| Whether Datadog monitors exist for the two cooldown metrics | Datadog monitor list | Offers on-call |

## Limitations of this run

- `docs/` is gitignored in this repo (project notes). Citations to `docs/...` (TDD, plan, ADRs)
  resolve only on the author's machine.
- Only this repo was checked for other writers.
- `module_facts.py` matched `Cassandra` (a short object name) in prose. Test counts were filtered to
  files that import the package.
- Dread is the model's rating. The team's may differ.

## Next

Run `eval-tiering` on `offers-restate/src/test` and `offers-shared/src/test/.../offer`
to draft fix 1.
