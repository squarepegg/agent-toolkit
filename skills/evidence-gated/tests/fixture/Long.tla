---- MODULE Long ----
EXTENDS Naturals
CONSTANT Cap
VARIABLES x, f
Init == x = 0 /\ f = [i \in 1..14 |-> i * 100000]
Next == x < Cap /\ x' = x + 1 /\ f' = [i \in 1..14 |-> IF i = 14 THEN 7 ELSE i * 100000]
Inv == x <= 1
====
