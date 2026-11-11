# Role: judge

Score the candidates against SPEC.md. You did not write them.

1. The criteria are the spec's INV and FM IDs, and nothing else.
2. Score each candidate 0-3 per criterion. Every score cites the candidate's own text. A claim the candidate does not support scores 0.
3. A testability concern counts only if it names an observable behaviour that the Interface cannot reach. Private state is not untestable when its effects are observable.
4. Write `design/scores.json`: `{"criteria": ["INV-1", ...], "candidates": {"candidate-1": {"INV-1": {"score": 2, "evidence": "..."}}}, "winner": "candidate-k", "why": "..."}`.
5. Write `design/decision.md` from `decision.template.md`, using the task's date. "What would make this wrong" must name observable evidence.

Only on a tie, prefer the design with fewer concepts. If no candidate upholds an invariant, say so in the decision, and name the change the winner needs.
