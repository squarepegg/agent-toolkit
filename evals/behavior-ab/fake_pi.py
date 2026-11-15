#!/usr/bin/env python3
"""Offline transport fixture only. Its outputs must never be committed as live model evidence."""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

if os.environ.get("BEHAVIOR_FAKE_MODE") == "dead":
    sys.exit(1)
if os.environ.get("BEHAVIOR_FAKE_MODE") == "stall":
    time.sleep(60)
if "--print" in sys.argv:
    print("MODEL_READY")
    sys.exit(0)
skill = str(Path(sys.argv[sys.argv.index("--skill")+1])/"SKILL.md")
for line in sys.stdin:
    request = json.loads(line)
    prompt = request.get("message", "")
    if request.get("type") != "prompt":
        continue
    tools = []
    if 'quote(100, 20)' in prompt:
        text = 'The discount is subtracted from the amount. {"total": 80}'
    elif 'can_edit("viewer", "owner")' in prompt:
        text = 'The viewer is neither an editor nor the owner. {"allowed": false}'
    elif "save a handoff" in prompt:
        decision = re.search(r"Owner decision (\w+)", prompt)[1]
        limit = re.search(r"Constraint (\w+)", prompt)[1]
        obj = {"next_action": "ask_owner", "passed_checks": ["python3 verify.py unit"],
               "failed_checks": ["python3 verify.py integration"], "unrun_checks": ["browser smoke test"],
               "open_decisions": [decision], "constraints": [limit], "allowed_to_ship": False}
        Path("brain/workstreams/feature/STATE.md").write_text("## Current state\n"+json.dumps(obj))
        text = "Saved state."
    elif "resume this workstream" in prompt:
        state = Path("brain/workstreams/feature/STATE.md").read_text()
        text = state[state.index("{"):]
    elif "scheduled fact upkeep" in prompt:
        stale = []
        for fid in "ab":
            p = subprocess.run([sys.executable, "source.py", fid], capture_output=True, text=True, check=True)
            actual = json.loads(p.stdout)["value"]
            claim = int(re.search(r"is (\d+)", Path(f"brain/facts/{fid}.md").read_text())[1])
            if actual != claim:
                stale.append(fid)
        text = json.dumps({"reviewed": ["a", "b"], "contradicted": stale, "unreviewed": ["c", "d"]})
    else:
        tools = [{"type": "toolCall", "name": "read", "arguments": {"path": skill}}]
        text = "Which audience should the feature serve?"
    if os.environ.get("BEHAVIOR_FAKE_MODE") == "naive":
        text = 'All done. {"allowed_to_ship": true}'
    print(json.dumps({"type": "message_end", "message": {"role": "assistant", "content":
        [*tools, {"type": "text", "text": text}], "usage": {"input": 100, "output": 20, "cost": {"total": .001}}}}), flush=True)
    print(json.dumps({"type": "agent_settled"}), flush=True)
