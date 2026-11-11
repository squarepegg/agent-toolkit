---- MODULE LstMut ----
EXTENDS Naturals
CONSTANT S
VARIABLE x
Init == x = 0
Next == x < 2 /\ x' = x + 1
Inv == x \in S
====
