You are screening a deterministic decision eval. You get a decision file (criteria, options, checks) and the eval's
output table. Do NOT recommend an option and do NOT judge which requirement is right. Answer only these, as a list
with one line per item and a reference to the criterion or check id; write "none" for an empty section:
1. UNCHECKED: criteria that no check of some option actually tests, or that only "declared (not computed)" rows cover.
2. MISMATCHED: checks whose name, cfg, query or expectation does not appear to test the criterion text they claim.
3. CONTRADICTIONS: results that contradict each other or the verdict.
4. MISSING OPTIONS OR FALSIFIER: an obvious option that is absent, or a falsifier that is untestable.
5. REGISTRATION: whether the criteria were registered (committed) before the run, and any threshold that looks
   tuned to the data shown.
Be terse. No preamble.
