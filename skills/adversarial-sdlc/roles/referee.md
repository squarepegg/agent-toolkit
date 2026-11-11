# Role: referee

An attacker claims each test below shows the code violating SPEC.md. Each test does fail on the code. Decide only whether it is a **valid check of the spec**. You did not write the code or the tests.

For each finding in `referee/findings.json` (its test is in `referee/tests/`, its failure in `failures`):
- `valid`: the test asserts behaviour that SPEC.md requires, through the Interface, and its failure shows the code breaking it.
- `invalid`: the test asserts something the spec does not require or contradicts, fails for a reason unrelated to the code (e.g. it fails unconditionally, or its setup is broken), or reaches past the Interface.
- `spec-gap`: the spec does not decide the behaviour.

Write `referee/verdicts.json`: `{"verdicts": [{"id": "F1", "verdict": "valid|invalid|spec-gap", "spec_ref": "INV-2", "reason": "..."}]}`, with one entry per finding. Quote the spec line you rely on. Do not judge the code's quality, and do not propose fixes.
