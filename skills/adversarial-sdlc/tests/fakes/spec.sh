#!/usr/bin/env bash
# Fake spec role: writes a fixed spec for the clamp fixture.
cat > spec.md <<'S'
# Spec: clamp

## Interface

```kotlin
package clamp
fun clamp(x: Int, lo: Int, hi: Int): Int
```

## Invariants

- INV-1: For every x and every lo <= hi, clamp(x, lo, hi) is in [lo, hi], and equals x when x is in [lo, hi].

## Failure modes

| ID | Trigger | Required behaviour | Must never happen |
|---|---|---|---|
| FM-1 | lo > hi | IllegalArgumentException | a value is returned |

## Acceptance criteria

- AC-1: clamp(5, 0, 3) == 3.
- AC-2: clamp(7, 4, 4) == 4.

## Non-goals

- NG-1: Long and floating-point overloads.

## Open questions

- none

## Escalation triggers

- ESC-1: exactly-once: touches: selftest marker, a replayed call must be reviewed by a human.
- ESC-2: concurrency: does-not-touch: a pure function.
- ESC-3: auth: does-not-touch: no identities.
- ESC-4: data-migration: does-not-touch: nothing is persisted.
S
