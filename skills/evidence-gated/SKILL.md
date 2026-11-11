---
name: evidence-gated-decisions
description: Use when putting any design, architecture, vendor or tooling trade-off to the user, or when asked "which option", "should we", "is X the right choice" or "recommend". Also use when the same corner has produced findings more than once (the re-architect signal). Not for one-way implementation details with a single obvious option.
---

# Evidence-gated decisions

Put a trade-off to the owner as an **eval table, not a recommendation**. Pre-registered, deterministic checks decide which options are valid. The owner decides the requirements. Nobody's preference is an input, least of all the agent's.

Why: on one design project the agent that wrote eight rounds of design patches also recommended what to do next. Its "(Recommended)" labels nudged towards more patches. Result: a pile of review findings, most caused by earlier fixes. Rules the user set: lean into simplicity; a corner that yields findings twice means re-architect, not patch; "(Recommended)" only on a DECISIVE verdict.

## When to use

- Any trade-off you are about to put to the user.
- Same corner produced findings more than once, or a fix added a mechanism that interacts with other fixes. That is the **re-architect trigger**: stop patching, name the pattern, run the playbook below.
- Skip for one-way implementation details with one obvious option.

## The method (in order)

1. **Criteria before options.** Write what a valid option must satisfy before you list options.
2. **Tag each requirement with its source:** `product`, `law`, `vendor-contract`, `consumer`, `ops`, `old-behaviour-only`, `new-design-only`. Old-behaviour-only rows are candidates to drop. Collect **owner questions**. Template: `templates/requirements.md`.
3. **Register.** Write the decision file (`templates/decision.yaml`) and **commit it before running**. `eval.py` refuses an uncommitted or modified file. `--draft` runs anyway, marked DRAFT and never decisive.
4. **Deterministic checks** per option: `tlc` (model check of a named `invariant:` or `property:`, each paired with a mutant), `data` (command + regex + threshold), `declared` (text only; option is UNTESTED), `lint` (simplicity metrics from TLC's state graph).
5. **Falsifier per option and for the decision:** what observation would show the choice was wrong. State it before the run.
6. **Reversibility** sets how much evidence a choice needs. Reversible (config, internal layout): data checks suffice. Hard to reverse (store, vendor, public contract): set `reversible: false` and add `attack_report` and `blind_check` (committed files from an independent attack round and an independent blind check). `eval.py` will not return DECISIVE without both; use TLC where the property can be modelled.
7. **Owner.** Product or legal requirements go to their owner. The eval only checks options against them.
8. **Blind screen** (`scripts/screen.sh`): a cheap model from another family sees only the decision file and the table. It lists unchecked criteria, mismatched checks, contradictions, missing options, registration problems. It never picks.
9. **Verdict.** "(Recommended)" only next to a DECISIVE verdict (one valid option and none untested; or one valid option no worse on every metric and better on one). Otherwise present the table with **no pick**.

How `eval.py` keeps a verdict honest (each rule exists because an attacker broke the version without it):
- **Registration is a snapshot of git HEAD.** The decision file must equal its committed version. Every spec, mutant spec, `inputs:` entry (file or directory) and evidence file must be committed, regular files in the same repo. Eval writes the committed blobs of the decision file's directory plus those paths into a temp dir (raw bytes: no `.gitattributes`, filters or symlinks; a committed symlink or submodule in that export is refused) and runs there. Uncommitted edits, untracked or ignored files, `assume-unchanged` and `skip-worktree` cannot change a verdict. Data commands run in a second, throwaway copy. `--draft` runs on the working tree instead and is never decisive. Files outside the decision's directory (an `EXTENDS`-ed module, say) must be listed under `inputs:`.
- **You never write a cfg.** A `tlc` check gives `spec`, `init`, `next`, `constants` and one `invariant`. Eval writes the cfg. Constants are booleans, integers 0..2^31-1 or lists of such integers; a name the spec defines with `==` is refused. Unknown keys are rejected. Temporal and action properties (`PROPERTY`) are not supported, and neither is `specification:`.
- **A mutant is a separate spec file**, a small edit of the real one: at most 6 changed lines (the `MODULE` line does not count), not the same or an identical file, and it must not touch the invariant's own definition (`Name == ...` at the top level of both files). The mutant check names only `spec` and `invariant`; init/next, constants and the deadlock flag are inherited from the real check, so the real check can never be crippled to make the mutant look sensitive. TLC must violate exactly that invariant on it. Each real TLC check, per criterion and invariant, needs one caught mutant. A deadlock is reported as `DEADLOCK`, never as an invariant violation.
- **`ok` means** TLC exit code 0, the completion line, and at least one state.
- **Every criterion needs a computed check** (TLC, or data with a threshold) on every option. Otherwise the option is UNTESTED and blocks DECISIVE. `declared` rows, mutants and `TBD` thresholds do not count.
- **Lint** measures the option's first passing TLC check (`lint: {}`); with lint, all of an option's TLC checks must use one model (only the invariant may differ). Empty graphs fail. Unmapped variables fail.
- **Input validation (exit 2):** unique string ids, unknown keys anywhere, known ops, finite thresholds or `TBD`, finite timeouts, a regex with a capture group, a boolean `reversible`, a variable in one object only. Free text in the output is sanitised. `inf`/`nan` readings fail. The jar must be a file, `java` on PATH and `JOBS` a positive integer, checked before anything runs. Temp dirs are removed on every exit path.
- **Irreversible decisions** (`reversible: false`) can't be DECISIVE without `attack_report` and `blind_check`: two different, non-empty, committed files, neither the decision file.
- **Exit codes:** 0 = DECISIVE, 1 = NOT DECISIVE, 2 = error (bad file, not registered, missing jar or java, crash).

Residual risk: a mutant that edits a helper operator the invariant uses (not the invariant itself) can make a violation trivial. The screen and the owner should read the mutant diff.

## Re-architect playbook (template: `templates/architecture-eval-plan.md`)

requirements → workload facts → invariant suite (observation interface; reference model used only for mutants) → clean-sheet candidates from designers who never see the current design → each candidate modelled using only declared store primitives → eval → attack → owner decision.

Anti-bias rules:
- Requirements and invariant suite are committed before any candidate is modelled.
- Designer, modeller and attacker of an option are different agents, from two or more model families.
- The current design enters as one candidate, not the baseline.
- Thresholds are committed before the data is applied to them.
- A candidate broken twice in the same corner is dropped or redesigned, never patched.

## Running the tooling

```bash
S=~/.agents/skills/evidence-gated-decisions
export TLA2TOOLS_JAR=$HOME/.local/share/tla/tla2tools-1.7.4.jar   # only for tlc/lint checks; java on PATH
git add d.yaml B.tla && git commit -m "register d"                # registration: eval runs on a snapshot of HEAD
python3 $S/scripts/eval.py d.yaml --out /tmp/d.md                     # exit 0 only if DECISIVE, 1 not decisive, 2 error
$S/scripts/screen.sh d.yaml /tmp/d.md                           # SCREEN_MODEL selects a cheap model from a different family
bash $S/tests/eval-test.sh && bash $S/tests/planted-bugs.sh        # self-test (133 cases) and planted-bug check (95 bugs, each must fail its intended test; ~35 min, PAR=6)
```

Env: `TLA2TOOLS_JAR` (required only if the file has a `tlc` check or `lint` block; fails fast otherwise), `JOBS` (TLC workers, 4), `EVAL_TLC_TIMEOUT` (900 s). `root:` in the decision file is relative to the file; data commands run there. Needs `pyyaml`.

## Limits

- Only as good as its criteria. Mutants catch vacuous checks, not missing criteria. The screen and the owner catch those.
- A threshold set after seeing the data is weaker. Say so in the file (as in a threshold file that was revised after the first run).
- `declared` rows are not evidence. If an option only has those, the verdict stays NOT DECISIVE.
- Registration is a git commit of the decision file and everything it uses; eval runs on that snapshot. It proves ordering (the criteria existed in HEAD before the run), not honesty: nothing stops someone committing criteria after seeing data. Data commands must not touch the repo by absolute path.

Worked example: the method was exercised over four independent attack rounds on one high-stakes decision. Three candidate designs were replaced because their guard kept failing: parsing a user cfg, judging git cleanliness, and mutants that differ only by constants. The current design (generated cfg, snapshot of HEAD, mutant = small edit of the spec) has had no attack round of its own yet.
- Not supported: temporal/action properties, `specification:`, string or model-value constants, invariants defined outside the spec file.
