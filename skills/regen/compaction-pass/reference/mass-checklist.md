# Conceptual mass checklist

Mass = distinct concepts + invariants + public interfaces + dependencies + exception paths.
Count concepts, not lines.

## What to look for

| Kind | Signal (from `module_facts.py` or reading) | Rent it can pay |
|---|---|---|
| single-implementation interface | `impls (main/test)` = `1/0` | none; `1/1+` pays only if the test fake is needed for isolation |
| pass-through layer | a class whose methods only delegate | an isolation or contract boundary, named as such |
| factory or builder for one product | one call site | hiding construction that varies by config |
| special case | `special-case`, `fallback` scar categories | a known upstream or customer quirk (check the audit) |
| duplicated source of truth | the same default or constant in code and config, or two parsers of one format | none |
| nullable-for-tests parameter | `x: Foo? = null` only null in tests | an optional feature in production, stated |
| dead declaration | `refs in other main files` = 0 and test files = 0 | none, unless used via DI, reflection or serialisation |
| test-only main code | used only by test files | a test seam that would otherwise need reflection |
| dependency | `build_deps`, `imports_out_external` | used by more than one call site, or hard to replace |
| exception path | broad catches, mapped errors | a policy (fail-open, auth vs outage), not a habit |
| near-duplicate types | DTOs that mirror each other across layers | a real version or ownership boundary |

## Risk

| Risk | Meaning | Before applying |
|---|---|---|
| low | no behaviour change; compile errors would catch misuse; no scar | existing tests pass |
| medium | behaviour preserved, but relied on by tests, DI, config, or another module | add a durable test pinning the behaviour first |
| high | removes a behaviour, a branch, or a public type; touches persistence or a wire | a decision record plus a durable test; owner sign-off |
| excluded | scar tissue, or origin unknown | cannot be proposed until the audit status changes |

## Proposal shape

> **#N: Collapse X into Y.** X is a single-implementation interface (`module_facts`: 1/0).
> Y is the only implementation. Callers: A, B. Removes 1 concept (X). Risk: low.
> Guard: none needed beyond compile. Replaces: callers depend on Y.
