---
name: code-review
description: Use when reviewing a PR, diff or design, deciding how much review a change needs, or when asked "is this safe to merge" or "do we need a human on this". Risk-triages the change first (tier T0-T3 from deterministic rules), then runs two-axis review (Standards vs Spec) in separate passes with a finder/verifier split, confirmed / needs_validation / rejected verdicts, union of repeated runs, and targeted inline comments.
---

# Code review

Read-only until the user approves posting anything. Two scripts make the parts that should not vary deterministic: `scripts/triage.py` (how much review) and `scripts/findings.py` (what counts as a finding). The judgement in between is yours.

## 0. Triage before opening the diff

```bash
python3 scripts/triage.py --base origin/main          # or --diff-file x.diff, --files a b c
```

Prints a tier from four factors, each 0-3: **impact** (blast radius of a failure), **boundary** (security and personal-data edges), **detectability** (would a failure be noticed, or fail silently), **recovery** (how hard to undo). Tier = highest factor, plus one when two or more factors are material.

| Tier | Finder passes | Human |
|---|---|---|
| T0 docs/tests only | 1 | none |
| T1 low impact | 1 | optional, author owns it |
| T2 material risk | 1 | one reviewer |
| T3 severe, or several factors material | 2 | independent reviewer; design check before coding |

- The rules are data: `reference/risk-rules.json`. Copy it, add the paths and patterns that are dangerous in your repos, pass `--rules`. A rule that never fires is dead; a risk nobody wrote a rule for is a gap: add one when a review misses it.
- Triage can be overruled upwards by a human, never downwards by the agent. Say so when you do.
- `split: yes` means two or more risky domains are mixed (or it is very large). Suggest splitting before reviewing; the groups are listed.
- Reducing human review is a decision that needs evidence: the tests, gates and monitoring that would catch what the human would. If you cannot name them for a tier, keep the human.

## 1. Fix the frame
- Fixed point: the PR base (`git merge-base origin/main HEAD`), never "recent commits".
- Spec source: PR description, linked ticket, design doc, or the user's request. If there is none, say so and ask. Do not invent one.
- Standards source: the repo's contributing/agent docs, lint and test config.

## 2. Two axes, two passes, separate context
- **Standards**: conventions, tests, error handling, security. Smell baseline: duplication, dead code, speculative abstraction, drive-by edits.
- **Spec**: does the diff do what the spec says: nothing missing, nothing extra, the edge behaviour it names.
- Run them as independent passes (parallel subagents if the user asked for fan-out; else two sequential passes where the second does not read the first's findings). Merge after both finish.

## 3. Finder vs verifier
- A finding is a claim with evidence: `file:line` and a quoted line, a failing test, or a command whose output shows it. No evidence, no finding.
- A separate verifier pass tries to refute each one. Verdicts: `confirmed` (reproduced, or the code plainly shows it); `needs_validation` (plausible, needs something not runnable here: name the exact check); `rejected` (refuted: keep one line why so it is not re-raised).
- Finder passes read `defects.md` and hunt every class. A class is a place to look, never a finding.
- Passes needed come from the tier (T3 = 2, a different model if available). `findings.py merge` takes the union; a single pass finds only part of what repeated passes find.

Record findings as JSON (shape in the `scripts/findings.py` docstring), then:

```bash
python3 scripts/findings.py validate run1.json
python3 scripts/findings.py merge run1.json run2.json -o all.json
python3 scripts/findings.py report all.json --tier T3     # exit 1 if invalid or too few passes
```

## 4. Report
Use the `report` output: merge-ready line, confirmed (blocking first), needs_validation, count of rejected, and **what you did not review**. If you say tests pass, show the command and counts.

## 5. Posting
Nothing without approval. Then targeted inline comments on the lines, not one summary comment. A comment asking the author to investigate is fine for `needs_validation`. Reply to existing threads with evidence; resolve only what you addressed.

## 6. Capture
When a human review catches something the finders missed, add the class to `defects.md`; if the risk was not triaged, add a rule to your rules file. Never edit either during a review.

## Tests
`python3 tests/{triage,findings,corpus}_unit_test.py`, and `python3 tests/planted_bugs.py` (mutates the scripts one way at a time; every mutant must be caught by the unit tests).

To compare models or prompts: have the reviewer run on `tests/corpus/change.diff` (3 planted defects, 1 decoy), write findings JSON, then `python3 tests/score_findings.py findings.json`. Recall and false positives are computed, not judged. The unit tests are deterministic; model output is not, so repeat runs and read the spread.
