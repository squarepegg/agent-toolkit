---
name: tests-as-data
description: Use when an AI agent writes or changes the code and you need tests a human can trust and the agent cannot quietly weaken - TDD with an agent, golden/approval/snapshot tests, evals for LLM or agent output, flaky or non-deterministic tests, "do you trust these tests", "the agent keeps rewriting the tests", writing an eval/probe/grader for a prompt, skill or model change, "did the change work", table-driven or parameterized tests, characterization tests before a rewrite. Puts the contract (input, expected output) in small human-readable data files run by a tiny protected runner, and covers red-first TDD, golden test hygiene, deterministic eval graders and removing sources of non-determinism. Not for one-off unit tests of a pure function (use property-tests) or concurrency and fault behaviour (use dst).
---

# Tests as data

When an agent writes the code, you are its customer. The tests are your contract with it. Contract = **data** (input,
expected output, setup) in small text files you can read in a diff, run by a **small runner** the agent is not
expected to touch. Test code that the agent rewrites on every refactor is not a contract.
Source: Matteo Vaccari, "Tests as data" (martinfowler.com/articles/tests-as-data.html), plus Ördög's Constrained
Tests and Approval Testing. The guard script, the golden/determinism rules and the eval rules are this repo's additions.

## 1. Pick the form
| Behaviour | Form | Cases live in |
|---|---|---|
| stateless input -> output | **table**, one stateless row per case | CSV / YAML / JSON |
| needs setup state, a command, resulting state | **scenario DSL**: `given` / `when` / `then`, `then` mandatory | text file, small parser |
| output is big or structured (rendered UI, report, generated code, log) | **golden** (approval): a readable, diffable rendering a human approved | `*.golden` next to the input |
| output varies run to run (LLM, agent, search ranking) | **eval**: deterministic grader, N samples, pre-set threshold | probes file |
| any input must satisfy a rule | property test -> `property-tests-from-examples` | code |
| concurrency, retries, faults, time | simulation -> `deterministic-simulation-testing` | code |

Rows must be stateless. If a table needs "login, then go to admin page", it is a scenario, not a table.
Make the assertion part of the syntax (an external DSL or a column that cannot be left blank). A test with no
assertion must fail to parse, so an agent cannot fake coverage.
Write in the customer's words and black-box: no mocks, no function names from the implementation. Test a service layer or
one important component directly when end-to-end is too slow or too far away; it still gets its own table.

## 2. TDD with an agent (red first, in big steps)
1. Draft **one** example of the data format with the user. Name the three things: input, expected, setup.
2. Have the agent write the parser and runner. **Check the runner yourself** against real code and a deliberately wrong
   expectation (it must go red). Commit runner and format, then treat them as frozen.
3. Add the new rows **before** the code. Run them and **show them red for the right reason** (the diff shows the wrong
   value, not an import error or a typo). Red that you did not see does not count.
4. Implement until green. Do not change a row to match the output. A row changes only because the requirement changed,
   and then the user is told.
5. Refactor with the data untouched. Green after a refactor with unchanged data is the point of the whole setup.
6. More rows come from the user, from bugs (every bug becomes a row first), and from the agent, which proposes rows
   and the human reviews the table at a glance for missing and redundant cases.
Bigger steps than classic TDD are fine: a table of 10 rows, then the implementation. Keep one behaviour per row.

## 3. Protect the contract
Which files may change depends on the task. Declare the classes once in `.tests-as-data.json`
(`{"data": [globs], "golden": [globs], "runner": [globs]}`; everything else is production code) and check the diff:

| Task | production | data | golden | runner |
|---|---|---|---|---|
| `feature` (incl. bug fix) | yes | yes | yes | **no** |
| `refactor` | yes | **no** | **no** | **no** |
| `coverage` | **no** | yes | yes | **no** |
| `dsl` (change the format or runner) | **no** | yes | yes | yes |

`python3 <this-skill-dir>/scripts/guard.py --task refactor --base origin/main` exits 1 and names each violation. Run it
in CI and as an agent hook; a hook gives fast feedback but is not a wall, CI is. The runner is code and can still be
altered, so keep it tiny and review any diff to it by hand. Never give the agent the golden-update switch (section 4).

## 4. Golden tests
- The rendering is the contract: make it small, stable and readable (a printer from HTML/DOM/JSON to plain text, not raw
  markup). One golden per behaviour, not one giant snapshot. A clean line diff is the failure message.
- **Approve, never regenerate blindly.** A failing golden prints the diff and stops. Updating is a separate, human action
  (`APPROVE=1`, refused when `CI` is set). The agent proposes the new golden in the PR; a human reads the diff.
- Run each case **twice** and require identical output before approving. A golden that differs between two runs is
  flaky by construction (section 5).
- Use goldens to **characterize** old behaviour before a rewrite: generate, review, approve, then run the same data
  against old and new implementations (the data file drives several runners: in-process, integration, old, new).
- A golden of mostly irrelevant detail is noise: scrub or drop fields that are not part of the requirement.
`templates/table_runner.py` is a ~70-line stdlib runner with table cases, golden compare, the double run and guarded approve.

## 5. Determinism
Every flaky test has a source. Remove it in the code under test, not with retries:
| Source | Fix |
|---|---|
| wall clock | inject a clock; the data gives `now` |
| randomness | inject a seeded RNG; log the seed in the failure |
| ids, uuids, timestamps in output | injected generator, or scrub to `<id1>`, `<id2>` in the printer |
| map/set/dir/thread ordering | sort in the printer, or compare as multisets |
| network, other services | local fake or recorded response, or stop at the boundary |
| floats, locale, time zone, line endings | fixed tolerance, fixed locale/TZ, normalize in the printer |
| shared state between tests | fresh state per case; the runner's order must not matter (run shuffled once in CI) |
Gate: run the suite twice (and once shuffled); any difference is a defect in the test or the code, to be fixed or
quarantined by name with an owner and a date. Do not retry until green. Genuinely concurrent or fault-dependent
behaviour is out of scope here: hand it to `deterministic-simulation-testing`.

## 6. Evals (output you cannot make deterministic)
Same shape as the table: **probe = question + expected facts**. Different judge:
1. **Deterministic grader first**: required substring groups (any-of inside a group, all groups needed), regexes, a
   schema or a parse. No model judge until one has been calibrated against labelled answers you wrote.
2. A probe only measures something the model **cannot answer without** the thing under test. If the baseline already
   passes, the probe is dead; replace it.
3. **Pre-register** before running: samples per probe (3+), the pass threshold, and the minimum gain. Do not tune probes
   or the grader against the results. Keep a `dev` split to decide and a `heldout` split that must not regress.
4. Always run a **baseline arm** (without the change) and report with vs without, with cost, not an absolute score.
5. Fixtures that pin the grader both ways: a known-good answer must pass and a known-bad answer must fail.
6. Read the failures. A grader that misses a correct paraphrase gets loosened once, then all stored answers are re-graded.
For choosing between options on such evidence use `evidence-gated-decisions`; for specs that tests verify, `spec-first`.

## 7. Check the tests themselves
- **Mutation**: break the production code on purpose (flip `+` to `-`, delete a branch). A test must go red with a useful
  message. Do it by hand on the 3 most important rows; automate in CI with a mutation tool when it pays.
- **Oracle**: confirm the expected values with something other than the code: the official doc, a slow reference
  implementation, a domain expert, or the real system. Mutation shows the tests catch faults, not that they are right.
- **Dumbest implementation** that passes the table (a lookup of the rows)? Then the table is too small: add rows or a
  property (`property-tests-from-examples`).
- Design the invariants and failure signals first with `correctness-first-design`.

## 8. When not to use it
Prototypes and spikes (learn first, add tests when you stabilize). Trivial code where a handwritten test is shorter than
the runner. Anything where you have another cheap way to validate. Test data does not replace exploratory testing by a human.
