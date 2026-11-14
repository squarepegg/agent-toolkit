#!/usr/bin/env python3
"""Stand-in for `pi --mode rpc` in tests: reads one prompt and answers from the skill's answer-pass.md when --skill is
given, else answer-fail.md. FAKE_PI_NO_SETTLE=1 never settles (timeout path)."""
import json, os, sys

assert "--mode" in sys.argv and "rpc" in sys.argv and "--no-session" in sys.argv, sys.argv
json.loads(sys.stdin.readline())
skill = sys.argv[sys.argv.index("--skill") + 1] if "--skill" in sys.argv else os.environ.get("FAKE_PI_SKILL", "")
name = "answer-pass.md" if "--skill" in sys.argv else "answer-fail.md"
text = open(os.path.join(skill, "evals", name)).read() if skill else ""
print(json.dumps({"type": "message_end", "message": {"role": "assistant", "usage": {"cost": {"total": 0.001}},
                                                      "content": [{"type": "text", "text": text}]}}))
if os.environ.get("FAKE_PI_NO_SETTLE") != "1":
    print(json.dumps({"type": "agent_settled"}))
sys.stdout.flush()
