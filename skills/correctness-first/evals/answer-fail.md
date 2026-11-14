## Correctness
Invariants: each identity key maps to exactly one live profile. Merges are idempotent and order-independent.
Unit tests cover merge, unmerge and conflicting attributes; integration tests run against a test database.
We monitor error rates and latency on the merge endpoint.
