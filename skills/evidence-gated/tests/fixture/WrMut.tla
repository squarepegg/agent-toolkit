---- MODULE WrMut ----
\* Stuck=TRUE has no next state: with deadlock checking on, the run ends in a deadlock, not an invariant violation.
EXTENDS Naturals
CONSTANT Stuck
VARIABLE x
Init == x = 0
Next == Stuck /\ x' = x
Inv == TRUE
Deadlock == TRUE
====
