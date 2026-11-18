#!/usr/bin/env python3
"""Stand-in for `pi --mode rpc` in the outcome tests (no model). With a skill whose dir ends in `-skill` it writes the
task's reference solution into its cwd, otherwise the naive one; then it answers and settles, once per prompt.
FAKE_TASK=<task id> picks the task."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import registry  # noqa: E402

assert "--mode" in sys.argv and "rpc" in sys.argv and "--no-session" in sys.argv, sys.argv
skill = sys.argv[sys.argv.index("--skill") + 1] if "--skill" in sys.argv else ""
for line in sys.stdin:
    json.loads(line)
    registry.TASKS[os.environ["FAKE_TASK"]]["write_solution"](os.getcwd(), "reference" if skill.endswith("-skill") else "naive")
    print(json.dumps({"type": "message_end", "message": {"role": "assistant", "usage": {"cost": {"total": 0.001}},
                                                          "content": [{"type": "text", "text": "Done."}]}}))
    print(json.dumps({"type": "agent_settled"}), flush=True)
