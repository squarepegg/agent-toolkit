"""Outcome task dst: the harness-ab fixtures and graders (evals/harness-ab/tasks.py), with pre-registered
primary checks. `no_false_alarm_on_fixed_cache` is not primary: RESULTS.md shows it is unsound (it fails on things the
skill tells the agent to build, such as known-bug pins and source tripwires)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HAB = os.path.join(os.path.dirname(HERE), "harness-ab")
sys.path.insert(0, HAB)
import tasks as T  # noqa: E402

DST_PREREG = {"n": 5, "alpha": 0.05, "ceiling": 0.8,
              "primary": ["sim_suite_exists", "detects_planted_race", "replays_identically", "cache_py_unchanged"]}

DST = {"skill": "dst", "prompt": T.DST_PROMPT, "setup": T.dst_setup, "user": T.dst_user, "turn_timeout": 1800,
       "grade": lambda r, final, all_text, bash: T.dst_grade(r, final, all_text),
       "prereg": DST_PREREG, "files": [__file__, os.path.join(HAB, "tasks.py"), os.path.join(HAB, "dst_lint.py")]}
