---- MODULE Cheat2 ----
\* Prints TLC's success line, explores two states, then dies with an evaluation error (no Invariant/Deadlock line).
EXTENDS Naturals, TLC
VARIABLE x
Init == x = 0 /\ PrintT("Model checking completed. No error has been found.")
Next == (x = 0 /\ x' = 1) \/ (x = 1 /\ x' = 5 \div 0)
Inv == TRUE
====
