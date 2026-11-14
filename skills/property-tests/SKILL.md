---
name: property-tests-from-examples
description: Use whenever asked "are these tests good enough/OK/sufficient", to review, write or improve unit tests, TDD, or a PR/diff that adds tests (assertEquals/expect/Assert with a few hand-picked inputs) in Kotlin/JVM, TypeScript, C#/Unity or Python. Finds the dumbest implementation that passes (hard-coded returns) and turns examples into property-based tests (kotest, jqwik, fast-check, FsCheck).
---

# Property tests from examples

Example tests let an implementation cheat. If `add(2,2)==4` is the only test, `return 4` passes. Once the inputs are generated, you are writing properties.
Source: Sean Allen (Antithesis) on The Pragmatic Engineer, youtube OrXKvOjR-vI, 35:41.

## 1. Adversarial-implementer check
For each function under test, ask: **what is the dumbest implementation that passes these tests?** Candidates: hard-coded returns, `if input == X`, ignoring an argument, always succeeding.
If a dumb version passes, the tests have a gap. In a review, report the gap as a finding, with the dumb implementation as the evidence.

## 2. Convert to properties
Pick the kinds that fit:
- **Round-trip:** `decode(encode(x)) == x` (serde, schemas, codecs, hashing/mapping).
- **Invariant preserved:** a total is conserved, a size stays bounded, the output is sorted, no duplicate ids.
- **Idempotence:** `f(f(x)) == f(x)`, or applying the same event twice gives the same state (consumers, merges).
- **Oracle / model:** compare against a simple reference, such as an in-memory map or a naive algorithm.
- **Metamorphic:** a known change to the input gives a known change to the output (an unrelated record doesn't change the result).
- **Commutativity / order:** where order shouldn't matter, shuffle the inputs.

Generators: cover the edge cases on purpose (empty, max size, unicode, duplicates, skewed keys), not only uniform random values. When a failing case is found and shrunk, keep it as a regression example test.

## 3. Libraries
Check the repo's build file for an existing library before adding one.
- Kotlin/JVM: kotest-property, jqwik
- TypeScript: fast-check
- C#/.NET/Unity: FsCheck, CsCheck
- Python: hypothesis

## 4. Know the limits
Stronger types would make many of these properties unnecessary: model states as sealed types or enums so invalid states don't compile. For stateful or distributed behaviour, such as ordering, retries, caching or partial failure across calls, use `correctness-first-design` to define the invariants and workload, then `deterministic-simulation-testing` to run them. Don't stretch unit properties to cover that.
