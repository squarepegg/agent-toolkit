# Spec: <component>

- Status: draft (approval recorded in .sdlc/spec.approved, never here)
- Source: .sdlc/requirements.md

## Purpose

Two or three sentences: what the component does and for whom.

## Interface

Exact signatures in the target language, including injected dependencies (clock, store, handler).
Tests compile against this alone.

```kotlin
// package and signatures
```

## Invariants

- INV-1: For every <input space>, <property that always holds>.

## Failure modes

| ID | Trigger | Required behaviour | Must never happen |
|---|---|---|---|
| FM-1 | handler throws after ... | ... | ... |

## Acceptance criteria

- AC-1: Given ..., when ..., then ...

## Non-goals

- NG-1: ...

## Open questions

- <question>. proposed: <default the tests assume>

## Escalation triggers

One line per category, all four. `touches` and `unknown` go to a human.

- ESC-1: concurrency: <touches | does-not-touch | unknown>: <reason>
- ESC-2: auth: <touches | does-not-touch | unknown>: <reason>
- ESC-3: data-migration: <touches | does-not-touch | unknown>: <reason>
- ESC-4: exactly-once: <touches | does-not-touch | unknown>: <reason>
