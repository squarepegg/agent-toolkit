# Current skill bodies: first batch

**42 real native trials; no demonstrated quality gain over both comparators. No skill was removed or rewritten.**

This batch tested `property-tests-from-examples` and `correctness-first-design`, using their current bodies from
squarepegg/agent-toolkit. Both SKILL.md files were verified byte-identical to the personal brain's installed copies.
The remaining toolkit skills and the brain protocol have not been graded by this batch.

## Results

Scores are mean faulty implementations caught, gated to zero when generated tests fail the reference implementation,
change production source, or fail a repeated real-code run. Labels come from the registered decision function.

| Skill / model | Runs per arm | No supplied skill | Skill | Generic control | Verdict |
|---|---:|---:|---:|---:|---|
| Property tests / Sol | 2 | 7.0 / 7 | 7.0 / 7 | 7.0 / 7 | CEILING |
| Property tests / Terra | 5 | 6.8 / 7 | 7.0 / 7 | 7.0 / 7 | INCONCLUSIVE |
| Correctness first / Sol | 2 | 6.0 / 6 | 6.0 / 6 | 6.0 / 6 | CEILING |
| Correctness first / Terra | 5 | 5.0 / 6 | 5.4 / 6 | 4.2 / 6 | INCONCLUSIVE |

Terra's exact one-sided permutation p-values:

| Skill | Skill vs no supplied skill | Skill vs control |
|---|---:|---:|
| Property tests | 0.5000 | 1.0000 |
| Correctness first | 0.3214 | 0.2738 |

Neither meets alpha=0.0125 against both comparators and a mean gain of at least one fault against each. Sol stopped
under the original pilot ceiling rule: both no-skill pilot runs caught every fault in each task, with all arms sampled
twice. Terra had misses in its pilot, so all three arms completed the registered five runs for each task.

There was one reference-code failure, in the Terra correctness control arm; all 42 final production-source snapshots
were unchanged. All 42 exposure reports declared only the permitted scope. That exposure is **self-reported**, not
independently verified tool telemetry. Every reference-passing artifact also passed the repeated real-code checks.

### Interpretation

- **Property tests:** Sol already generated strong independent oracles and property tests without the body. Terra's
  body caught one extra fault across five runs, but the generic control matched it. This task does not establish a
  unique benefit from the skill body.
- **Correctness first:** Terra showed a small directional difference, but it was inconsistent. The skill arm missed
  the falsy-value fault in three runs; no-skill/control runs missed compaction faults in some histories. The observed
  +0.4 faults versus no skill does not establish a reliable advantage at this sample size.
- **Reference failure caveat:** one Terra control tested a stricter reading of the docstring, expecting standalone
  `put('a', [])` to reject a value. The fixture restricts values for `put_many`, while the following sentence also
  mentions `put` and `delete`. The agent explicitly identified that ambiguity. The fixed reference implementation
  accepts the standalone put, so the registered scorer assigned zero. Treat this as a fixture-contract limitation,
  not proof that the control made an unreasonable production claim. No task or contract was changed after this result.

**These are task- and model-specific results, not a universal grade or evidence that skills are useless.** In
particular, a ceiling task cannot measure added quality, and this study cannot establish cost efficiency.

## Method and amendment

The initial native protocol, task inputs and thresholds were committed in `5ca07d0` before task trials. The adapter
reuses the existing outcome fixture setups, reference/naive calibration solutions, hidden faulty implementations,
generic control and exact permutation function. No historical Claude/Pi rows are pooled with these observations.

The first five artifacts exposed a measurement defect in the legacy harness: its regex parser ignored failures
inside `unittest.subTest`, returning only other passing test methods. It therefore scored two strong artifacts as
zero faults caught. These are invalid V1 measurements, preserved with their registration and source commit in
[`native-skill-value-history`](../native-skill-value-history/).

V2 was committed as `6560215`: structured unittest result callbacks capture subtest failures and fail fast, preventing
huge repeated tracebacks. A standalone witness demonstrates legacy false green versus structured failure. SHA-256
and AST checks confirm the task/skill/shared-grader sources and request/arm construction did not change. All retained
artifacts were regraded with V2; no generated answer/test was edited and no fault, quality target or threshold changed.

This is an **amended exploratory study**, not a claim of an untouched preregistration or independent fresh validation
of its first five artifacts. The current experiment fingerprint is
`b7a48fcc3d6754dfb55dd7630818586e20972faa30ca8c55e73ca3604451f369`.
The invalid zeroes are not in the current study ledger. Other historical outcome runs using subtests may also need
regrading; this batch does not silently rewrite their records.

## Native conditions and limits

Agents were fresh (`fork_turns=none`), model/effort fixed to GPT-6.1 Sol or GPT-6.1 Terra at medium effort. Each got
an opaque fixture path, the same task and a response/exposure capture contract. No-skill received no body; skill
received its actual body/supporting files; control received the same skill name/description and generic careful-work
text of the body's character length. No arm could read ambient skills, brains, other trials or the grader by instruction.

Native platform skill descriptions remain visible in every arm, and read isolation is not OS-enforced. Therefore
this measures **incremental skill-body value conditional on a shared platform catalog**, not hermetic removal of all
skill references. Attributable native tokens and dollars are unavailable. Dispatch-to-response elapsed time includes
orchestration/platform delays and is descriptive only; generated test bytes are not model-token estimates.

These are two small synthetic fixtures, not broad production traffic. Hidden mutants were withheld from agents;
their behavior was used to grade artifacts after completion. Tests written for the correct implementation had to
pass there and exercise public behavior, rather than inspect source. A static artifact scan found no direct source
introspection patterns; it is a heuristic, not proof against every form of cheating.

An isolated Codex CLI could initialize, but model workspace routing failed. Native model preflights succeeded; no
Fortis gateway was used for this comparison. A full legacy outcome-suite check passed 30/31 tests, with its existing
committed-ledger validation failing on duplicate historical records. This new study uses a separate validated ledger;
its seven offline tests, including positive/negative artifact calibration and the subtest regression, pass.

## Reproduction and retained evidence

```sh
python3 evals/native-skill-value/test_study.py
python3 evals/native-skill-value/study.py report
python3 evals/native-skill-value/study.py next  # [] after the registered stopping conditions
```

The [plan](plan.json), [input registration](registration.json), and [42-row ledger](ledger.jsonl) retain the original
criteria and corrected observations. [Evidence bundles](../native-skill-value-evidence/) preserve every generated
test/document, final response, normalized exposure report, summary and checksums. Test code is stored inside JSON
so it is inspectable without accidentally being collected as the repository's own tests. Local raw transcripts and
workspaces remain under ignored `results/native-skill-value/` and the temporary paths in its manifest.

The body comparison itself is complete for this first batch: both Sol cells ended at the registered pilot ceiling;
both Terra cells reached n=5. More grading should begin with a genuinely observed workflow failure and a separate
fixed task set, not repeated edits to these fixtures until a skill wins. No removal decision follows from this study.
