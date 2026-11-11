---- MODULE ToyInv ----
\* Fixture for eval-test.sh. Objects: A owns a, B owns b, ch is a message channel.
EXTENDS Naturals
CONSTANTS Mut, Atomic
VARIABLES a, b, ch
Init == a = 0 /\ b = 0 /\ ch = {}
IncA == a < 3 /\ a' = a + 1 /\ ch' = ch \cup {a} /\ UNCHANGED b
IncB == b < 1 /\ b' = b + 1 /\ UNCHANGED <<a, ch>>
Both == Atomic /\ a = 0 /\ b = 0 /\ a' = 1 /\ b' = 1 /\ UNCHANGED ch
Next == IncA \/ IncB \/ Both
Inv == FALSE
Con == a = 0 /\ b = 0
InvTrue == TRUE
====
