---- MODULE Vac ----
EXTENDS Naturals
CONSTANT Cap
VARIABLE x
Init == x = 0
Next == x < Cap /\ x' = x + 1
InvTrue == TRUE
InvReal == x <= 2
====
