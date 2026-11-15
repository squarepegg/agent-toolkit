"""Registry of outcome tasks (laptop tier). A task = fixture repo + prompt + scripted user + deterministic grader +
pre-registered thresholds (`prereg`) + the files whose hash pins the task (`files`)."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import task_correctness_first  # noqa: E402
import task_dst_doubt  # noqa: E402
import task_property_tests  # noqa: E402

TASKS = {
    "dst": task_dst_doubt.DST,
    "doubt": task_dst_doubt.DOUBT,
    "property-tests": task_property_tests.TASK,
    "correctness-first": task_correctness_first.TASK,
}
