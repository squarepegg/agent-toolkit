# <System> requirements baseline (draft for owner review)

Status: draft, <date>. Says **what** the system must do, never **how**. Statements avoid naming stores and runtimes. Evidence may name them.
Commit this file, with the owner's review, before any candidate is modelled.

## Legend

**Tags** (one primary tag per row; a second may appear in the evidence):

| tag | meaning |
| `product` | a product document asks for it (PRD, user docs, migration page). Docs that only describe the old behaviour are weaker. |
| `law` | GDPR, CCPA etc., as cited by a product doc or the legal process |
| `vendor-contract` | how a vendor API behaves |
| `consumer` | a named consumer depends on it (cite `file:line`) |
| `ops` | an incident, runbook, alert, or brain fact |
| `old-behaviour-only` | the old system does it and no source requires it: candidate to drop |
| `new-design-only` | exists only in the new design's own docs |

**Criticality:** `must` / `should` / `nice` only where a source says so (quote it); otherwise `owner to set`. A design doc's own SHALL is not criticality.

**Evidence abbreviations:** table of `abbr | source path`.

## 1. Requirements

### 1.1 <Area>

| id | statement | tag | evidence | crit |
| R-001 | <what, no how> | product | <abbr + section / file:line> | owner to set |

## 2. Quantitative requirements (open numbers)

| id | quantity | value or "OPEN" | source | who sets it |

## 3. Non-requirements (old system does it; no source requires it)

| id | behaviour | why it is a candidate to drop | evidence |

## 4. Contradictions between sources

| id | source A says | source B says | affects | owner decision needed |

## 5. Owner questions (ordered by how much of the architecture depends on the answer)

| id | question | what changes with each answer | requirements affected |

## 6. Owner answers (<date>)

| question id | answer | decided by | date |
