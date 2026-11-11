---- MODULE Init0 ----
VARIABLE x
Init == x = 0 /\ x = 1
Next == x' = x
Inv == TRUE
====
