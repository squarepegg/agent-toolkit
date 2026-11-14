No. They only cover the happy path, and they'd miss the usual bugs:

- **Integer rounding:** `discount(99, 10)` gives 89.1. Should the result be 89 or 90? Neither test pins this down, and price 100 hides it.
- **Boundaries:** nothing tests `pct = 100` (should give 0), `pct > 100` or a negative `pct`. Should those clamp or throw?
- **Negative or zero price:** `discount(0, 10)` and `discount(-100, 10)` are untested.
- **Overflow:** a large price such as `Int.MAX_VALUE` overflows if the code computes `price * pct` before dividing.
- **Order of operations:** with price 100 every formula looks the same. `price - price / 100 * pct` and `price * (100 - pct) / 100` only give different answers on prices that aren't multiples of 100, e.g. `discount(55, 10)`.

At minimum, add a price that isn't 100, `pct = 100` and a test for invalid input. A parameterized table works well here. The rounding rule and how invalid input is handled are spec decisions, so settle those before writing the tests.
