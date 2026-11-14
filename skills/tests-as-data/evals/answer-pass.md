**Reject both.** Retries hide the real defect, and a re-bless would approve whatever the flaky run produced.

1. Run the case twice in one process and diff the outputs. If they differ, the output is non-deterministic and the test is wrong by construction.
2. Find the source: clock, random, ids, map or directory ordering, a network call. Fix it in the code or the printer (inject a fixed clock and a seeded RNG, scrub ids to `<id1>`, sort collections).
3. Run the suite twice, and once shuffled, in CI.
4. Updating the golden is a human action: the agent shows the diff, a reviewer reads it and approves. Never regenerate it automatically.
