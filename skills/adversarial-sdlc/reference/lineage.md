# Where the rules come from, and how each is enforced

"Mechanical" means a script fails or refuses. "Prompt" means only a role prompt asks for it, and
the report treats it as unenforced.

## doubt-driven-development

| Doubt cycle | Here | Enforcement |
|---|---|---|
| Fresh-context reviewer biased to disprove | attacker role in a fresh sandboxed copy | mechanical (workspace + sandbox); bias: prompt |
| Pass ARTIFACT + CONTRACT, never the CLAIM | attacker gets code, tests and SPEC.md only; `.sdlc/`, git and transcripts unreachable | mechanical (include-list copy + sandbox deny) |
| Cross-model second opinion | `SDLC_MODEL_attack`; the report flags a shared model or family | report flag (mechanical); model choice is the user's |
| Never pipe artifacts through shell-quoted args | prompt and task on stdin | mechanical (`run-role.sh`) |
| RECONCILE | reproduced / discarded / flaky / spec-gap; disputes and spec gaps go to a human | mechanical (`validate-findings`, exit 3/7) |
| STOP: 3 cycles | `SDLC_MAX_ROUNDS` ∈ 1..3, stop at the first dry round on the current code | mechanical (config validation, loop) |
| Doubt-theatre red flag | a dry round triggers calibration on planted bugs | mechanical (`SDLC_CALIBRATE=auto`) |

## Phoenix skills

| Phoenix lesson | Here | Enforcement |
|---|---|---|
| A first-run green proves nothing | tests must exist and fail against TODO() stubs before the build; vacuous ones become spec gaps | mechanical (`inventory-check`, `spec-gaps`) |
| Every draft must kill a mutant | Pitest KILLED ratio ≥ minimum, unrounded; survivors listed | mechanical (score); per-test kill attribution: not enforced |
| Random generators miss boundaries | edge-biased generators | prompt |
| Reference model across components | reference model for stateful rules | prompt |
| `no-build` is not a kill | a repro that does not compile or run is discarded | mechanical |
| Silent skips pass CI | skipped > 0 fails; every inventory case must run and pass | mechanical |
| `unknown` beats a guess | Open questions with `proposed:` defaults, listed as "decided by default" | prompt + report |
| Never weaken a test to make it pass | everything except main paths and docs is locked; builder output is imported from main paths only | mechanical |
| A check must be shown able to fail | calibration: planted bugs; a hit must fail on the bug and pass on the real code | mechanical |
| Decision records | judge writes `decision.md` from the template; criteria must be spec IDs | mechanical (`judge-check`); content: prompt |
| Read the scar tissue first | SKILL.md step 0 | manual |
| Feed lessons back | a field-notes file per run, kept by the user | manual |
