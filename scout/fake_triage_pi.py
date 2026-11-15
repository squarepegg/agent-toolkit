#!/usr/bin/env python3
"""Stand-in for `pi --mode rpc` for triage tests. Replies per url from FAKE_TRIAGE_REPLIES (json file: url -> text | {"hang": true})."""
import json, os, sys

assert "--mode" in sys.argv and "rpc" in sys.argv and "--no-tools" in sys.argv and "--no-session" in sys.argv, sys.argv
req = json.loads(sys.stdin.readline())
msg = req["message"]
item = json.loads(msg.split("ITEM (JSON):\n", 1)[1].strip().splitlines()[0])
reply = json.load(open(os.environ["FAKE_TRIAGE_REPLIES"])).get(item["url"], "not json")
print(json.dumps({"id": "q", "type": "response", "success": True}))
if not (isinstance(reply, dict) and reply.get("hang")):
    print(json.dumps({"type": "message_end", "message": {"role": "assistant", "usage": {"cost": {"total": 0.01}},
                                                          "content": [{"type": "text", "text": reply}]}}))
    print(json.dumps({"type": "agent_settled"}), flush=True)
else:
    sys.stdout.flush()
    import time; time.sleep(30)
