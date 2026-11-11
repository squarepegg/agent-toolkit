---- MODULE VacMut ----
EXTENDS Naturals
CONSTANT Cap
VARIABLE x
Init == x = 0
Next == x < Cap + 2 /\ x' = x + 1
InvTrue == TRUE
InvReal == x <= 2
====
