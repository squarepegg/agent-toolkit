---
name: spec-first
description: Write a checkable spec with the user before code exists (read the code, then interview only about what the code cannot answer, one decision at a time, biggest first, defaults marked assumed), then verify the finished change against it with a reviewer that sees only the spec, the diff and the raw output. Use when asked to spec, scope or think through a feature before building it, when a large request leaves its main decisions open, or when asked to verify/prove a change built from a spec. Not for a change one prompt fully describes; for a component that must survive adversarial attack use adversarial-sdlc.
---

# Spec first

Two failure modes this skill removes: a vague request gets the most common answer (or an over-specified one stops a
better design), and a builder that checks its own work praises it. The spec carries the user's decisions and a
definition of done; a script, not a model, decides what passed.

| Phase | Who | Output | Decides |
|---|---|---|---|
| A. Interview | this session, with the user | `specs/<topic>.md`, accepted + `.lock` | the user |
| B. Build | a fresh session | the change; `## Deviations` in the spec | the spec |
| C. Verify | `evidence.py` + a fresh reviewer process + `verdict.py` | `STATUS: DONE` or `NOT DONE` | the scripts |

Scripts are in `scripts/` next to this file (call them by absolute path). Stdlib Python 3.9+, git, bash.

## A. Interview

1. Ground yourself first. `git rev-parse --show-toplevel && git status -sb`; read the code the request touches;
   find one file that already does something similar (it becomes `Reference:`). Keep specs out of commits:
   `git check-ignore -q specs/x || echo 'specs/' >> "$(git rev-parse --git-path info/exclude)"`.
2. If the request does not say, ask once what the user already knows or decided about this area.
3. Show the unknowns as a table `Decision | Why it matters | Default I'd pick`: only decisions the request leaves
   open *and* whose answer changes the result for the user (data model, an interface between parts, what the user
   sees, migrations of saved data, a second platform, the empty/error state). A choice you can change later at no
   cost to the user is yours: leave it out.
4. Interview **one question per message**, biggest first, because early answers change later questions:
   1. data model / interfaces / what the user sees; 2. scope and how complete (empty, error, loading states);
   3. edge cases and what must happen; 4. how we will know it is done.
   Give 2–4 options, your default first. Skip what the code already settles. Never ask how to build it.
   When the user can't describe it but would know it on sight, offer a throwaway prototype outside the repo.
5. Every decision not asked becomes `(assumed)` with your default; asked ones are `(confirmed)`.
6. Write `specs/<topic>.md` from `templates/spec.md`. Rules the linter enforces:
   `- R<n>:` requirements, `- E<n>:` edge cases, `- D<n>: … (confirmed|assumed)`, and checks
   ``- C<n> [R1, E2]: `cmd` -> exit 0; stdout contains "…"`` or `- C<n> [R3]: manual: <do> -> see: <what>`.
   Clauses: `exit N`, `stdout|output contains|lacks "…"`, `stdout|output matches /re/`. Every R/E is covered by
   a check. Requirements describe observable behaviour (the linter warns on implementation words). Don't pin
   test counts. Keep it to a page when the work allows. `- Tests: <globs>` under Constraints if the repo's test
   files don't match the defaults.
7. `python3 scripts/spec.py lint specs/<topic>.md` until 0 errors. Show the decisions (assumed ones marked),
   fix until the user accepts, then `python3 scripts/spec.py accept specs/<topic>.md` (sets Status, writes the
   lock with the normative hash and the base commit). Do not accept with open questions; the linter refuses.
8. Hand off; do not build in this session unless asked:
   ```text
   Build specs/<topic>.md (spec-first skill, phase B).
   ```

## B. Build (fresh session)

Build to the spec. Where the code forces a departure, take the conservative option, write it under
`## Deviations`, and continue. Never edit any other section (the lock catches it) and never modify or delete an
existing test (the evidence guard catches it); add tests instead. When done, run phase C.

## C. Verify

Use for bigger changes: one review cost 50–77k tokens in the original author's runs; `review.sh` prints the brief
size. For a small change, the build and the tests are enough.

1. Deviations that contradict a requirement: ask the user first. Accepted → edit the spec and re-run
   `spec.py accept`; rejected → fix the code.
2. `python3 scripts/evidence.py specs/<topic>.md --out "$TMPDIR/spec-first/<topic>"` — runs every command check,
   records literal output, marks manual checks NOT CHECKED, and raises guards (spec changed since accept, existing
   test modified/deleted). The evidence dir must be outside the repo.
3. `bash scripts/review.sh specs/<topic>.md "$TMPDIR/spec-first/<topic>"` — a fresh process (`pi -p --no-session
   --no-context-files --no-skills --tools read`, override with `SPEC_REVIEW_RUNNER`) gets only
   `templates/reviewer-brief.md` + spec + evidence + diff. It voids the report if the reviewer touched the repo,
   refuses a 4th round, and runs `verdict.py`.
   In Claude Code set `SPEC_REVIEW_RUNNER` to a headless `claude -p` call limited to read tools (check the flags of your version). If no fresh process is
   possible, write the report yourself and say in the result that the review was **not independent**.
4. `verdict.py` is the verdict: a reviewer can make a row stricter, never looser. A failed covering check is
   FAIL; an unrun one NOT CHECKED; a PASS without a citation that holds (a passing check covering the id, or a
   real line in a changed file; a test file for an edge case) is UNSUPPORTED; stale evidence blocks DONE.
5. Fix each FAIL/UNSUPPORTED in code or by adding a test; never by changing a requirement or an existing test.
   If the spec seems wrong, or a requirement has two readings, stop and ask. Re-collect evidence after every fix;
   re-review only if code changed. After round 3, stop and report what still fails.
6. Report: the `verdict-<n>.md` table (one line per id, passes included), every changed file from `files.txt`,
   each Deviation, what the user must check by hand (NOT CHECKED rows), and reviewer findings not fixed with why.
   Don't commit.

## Relation to other skills

- `adversarial-sdlc`: heavyweight, sandboxed roles, mutation gate. An accepted spec here is a good requirements
  file for `sdlc.sh spec`.
- `doubt-driven-development`: in-flight doubt on one decision; phase C is the end-of-change check.
- `evidence-gated-decisions`: for design trade-offs surfaced during the interview.

Tests: `python3 tests/test_spec_first.py` (unit + determinism), `python3 tests/planted_bugs.py` (each planted
bug must be caught by its named test), `python3 evals/run_evals.py` (live agent evals, costs tokens).
Idea credit: Lorenzo Mazzarotto's write-spec/verify-spec (no code reused; that repo has no license).
