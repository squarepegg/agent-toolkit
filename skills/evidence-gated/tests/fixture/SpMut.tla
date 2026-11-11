---- MODULE SpMut ----
EXTENDS Naturals, TLC
VARIABLE x
Init == x = 0 /\ PrintT("spoof Error: Invariant Inv is violated") /\ PrintT("spoof Error: Deadlock reached")
Next == x < 1 /\ x' = x + 1
Inv == x <= 5
====
