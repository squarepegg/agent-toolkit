# What counts as scar tissue

Code that looks wrong, odd or redundant until the day it is removed.

## Categories the scanner detects

| Category | Looks like | Typical lesson |
|---|---|---|
| `timing` | `timeoutMs = 200`, `Duration.ofSeconds(2)`, `delay(…)`, idle/TTL/poll values | latency budget, LB idle limits, upstream SLAs |
| `retry` | retry loops, `maxAttempts`, backoff | transient failure observed in production |
| `rethrow-guard` | `catch (e: CancellationException) { throw e }` placed before a broad catch | cancellation or interrupts being misreported as failures |
| `broad-catch` | `catch (e: Exception)`, `runCatching`, `except Exception`, bare `except:` | fail-open policy, one bad record must not kill the batch |
| `defensive` | `require`, `check`, `?: return`, null/blank guards | bad input seen in practice |
| `fallback` | `a ?: b ?: c` chains over field names, `x.get(k) or y` | upstream schema drift, version skew |
| `special-case` | equality against a literal, status-code branches (`== 401`) | a customer, game, tenant or error that behaves differently |
| `idempotency` | idempotency keys, dedup, locks, `synchronized`, `compareAndSet`, `IF NOT EXISTS` | double-delivery, races |
| `index-hint` | `FORCE INDEX`, `/*+ … */`, ClickHouse `SETTINGS`, `FINAL`, `ALLOW FILTERING` | a slow query in production |
| `constant` / `limit` | `const val MAX_BATCH = 500`, pool/buffer sizes | memory, rate limits, payload caps |
| `marker` | `HACK`, `workaround`, "deliberately", "do not remove", "legacy" | someone knew, and wrote half of it down |
| `suppression` | `@Suppress`, `# noqa`, `eslint-disable` | usually incidental; sometimes hides a known hazard |

## Scanner blind spots (find these by reading)

- **Ordering.** Catch-clause order, filter order, "publish schemas before the release",
  write-then-read sequences.
- **Concurrency structure.** A mutex held across a whole section, single-threaded executors,
  `@Singleton` used to serialise access.
- **Bean or feature conditions.** `@Requires(property = …, value = "")`, profile switches, Noop
  fallbacks that keep startup from failing.
- **Absent code.** Deliberately *no* retry, *no* cache, *no* health probe of a dependency. Look
  for docs or comments that say "no …" and confirm the code honours them.
- **Config-only scars.** Values that exist only in YAML, Helm, Terraform or env defaults.
  Defaults duplicated in code and config are two sources of truth.
- **Error mapping.** Which exceptions become which HTTP status. A 401 vs 503 split is policy.
- **Wire details.** Header names, URL encoding, field-name casing, singular vs plural keys.

## Not scar tissue

Formatting, generated code, logging wording, imports, plain DTOs. Do not list them. If a site
was traced and turned out to be incidental, list it on one line under the table.
