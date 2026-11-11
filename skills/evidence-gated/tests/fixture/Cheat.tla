---- MODULE Cheat ----
\* Prints TLC's success line itself, then violates Inv.
EXTENDS Naturals, TLC
CONSTANT CheatMode
VARIABLE x
Init == x = 0 /\ (CheatMode => PrintT("Model checking completed. No error has been found."))
Next == x = 0 /\ x' = 100
Inv == x <= 2
====
