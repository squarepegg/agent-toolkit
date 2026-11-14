## Correctness
Invariants: OneProfilePerIdentity; identifier set is conserved across merge/unmerge (the sum of identifiers never changes).
By construction: merge state is a sealed type, so a half-merged profile cannot compile.
Workload: merge/unmerge/link ops with skewed keys (hot key, long runs of merges in a row), checker runs concurrently.
Faults: kill the worker mid-merge, partition the store.
Gray failure: merges succeed but profile lookups return stale data for a share of users; alert on lookup mismatch rate.
