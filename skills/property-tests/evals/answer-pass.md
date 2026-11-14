**No, they aren't good enough.** Both tests use `price = 100`, and at that price "percent off" and "subtract pct" give the same answer. So wrong implementations pass both tests:

- `price - pct` gives 90 and 100. Both pass.
- `100 - pct` ignores `price` entirely. Both still pass.

**Not covered:**
- Any price other than 100. For example, `discount(200, 10)` should be 180.
- Rounding: `discount(99, 10)` should be 89 or 90, but the expected rule isn't specified.
- `pct = 100` should give 0.
- Out-of-range input: `pct` below 0 or above 100, or a negative `price`. Should these throw or be clamped?
- Overflow when `price * pct` is large.

**Fix:** keep the two examples and add property tests (kotest-property or jqwik), with `price` in 0..1_000_000 and `pct` in 0..100:
- **Oracle:** `discount(p, k) == p * (100 - k) / 100`, using whatever rounding rule you pick.
- **Bounds:** `0 <= discount(p, k) <= p`.
- **Monotonic:** a higher `pct` never gives a higher price.
- **Endpoints:** `discount(p, 0) == p` and `discount(p, 100) == 0`.
- **Invalid input:** `pct` outside 0..100 throws (or is clamped, if that's the rule you choose).
