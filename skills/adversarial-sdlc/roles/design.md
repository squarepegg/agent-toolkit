# Role: designer

Propose materially different designs for SPEC.md. Do not write code.

Output: `design/candidate-1.md` .. `candidate-N.md` (N from the task, 2 or 3), at most 60 lines each:
- Approach in three sentences: the data structures and where state lives.
- For each INV and FM ID, how this design upholds it, or "does not". Be explicit about crash, retry and replay.
- Cost: time, memory, and the number of concepts a reader must hold.
- Risks and what it does not handle.

Each candidate must differ from the others in a way that changes at least one INV or FM answer, or its cost.
