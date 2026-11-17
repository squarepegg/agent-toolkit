# Evaluation tiers

| Tier | Verifies | Lifetime | Typical forms |
|---|---|---|---|
| **ephemeral** | implementation decisions | until the implementation changes | unit tests on internal/private functions, mock-and-verify interaction tests, structural assertions |
| **durable** | behavioural intent | outlives any implementation | invariants, contract tests at a wire or schema, property tests, end-to-end behavioural checks |
| **live** | production reality | continuous | monitors, SLOs, drift and anomaly alerts, business-metric checks |

## Classifying a case

Ask in order:

1. **Does it call something internal or private, or verify call sequences?** → ephemeral.
   `verify { … }`, `internal` symbols, reflection, and asserting on log lines are coupling.
2. **Is its input and output a wire, file, table or schema shape?** → durable.
   HTTP against an embedded server, a JSON body asserted field by field, a CQL round-trip,
   JSON-schema validation.
3. **Does it state a universal property?** (`for all inputs …`) → durable.
4. **Is it an example-based check on a public pure function?** → *durable candidate*. The
   requirement may be durable but is stated as one example. Promote it by restating it as a
   property.
5. **Ephemeral harness, durable intent.** A case can fail rule 1 yet encode a rule the system
   owes the outside world (e.g. "never retry a read timeout"). Keep it, and draft a durable
   twin at the boundary.

## The rewrite check

> If this module were rewritten in another language, would the suite still say what is correct?

Answer per case: `yes`, `yes, if run against the wire`, `intent yes, harness no`, `no`.
Summarise as: *N of M cases state a requirement that survives a rewrite.*

## Good durable evaluations

- State the requirement in the test name, in domain words, without class names.
- Assert at the boundary: inputs and outputs, not calls.
- Fail for one reason only.
- Cite the source: audit row, ADR, PR, doc line.
- Prefer properties over examples when a rule is universal (`retries ≤ maxRetries` for all
  failure sequences; `computeNewState` never decreases `returnedLifetime`).

## Live gaps

A metric with no monitor is not a live evaluation. Check that a monitor exists before calling
something "live". If monitors are not in the repo, say `not found in repo`.
