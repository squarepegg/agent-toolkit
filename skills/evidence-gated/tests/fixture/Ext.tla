---- MODULE Ext ----
EXTENDS Naturals, Helper
VARIABLE x
Init == x = 0
Next == x' = x
Inv == x <= Limit
====
