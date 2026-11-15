"""Prospective fixtures and deterministic graders. No model calls; stdlib only.

The two splits are fixed before running. No fixture supplies the grader to the agent.
The recovery answer is graded only in a fresh workspace that receives STATE.md, not the earlier conversation.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = {
    "activation": [
        {"id": "explain", "split": "dev", "activate": False,
         "prompt": 'Read pricing.py. Explain quote(100, 20) in one sentence, then give JSON {"total": <number>}. This is just a question about the existing code.'},
        {"id": "scope", "split": "dev", "activate": True,
         "prompt": "Help me scope and write a checkable spec for checkout refunds. The eligible customers and refund limits are undecided. Interview me before building anything."},
        {"id": "explain-near-miss", "split": "heldout", "activate": False,
         "prompt": 'I am preparing a feature spec, but right now only explain the existing permission check in access.py: can_edit("viewer", "owner"). Give JSON {"allowed": <boolean>} after your explanation.'},
        {"id": "scope-near-miss", "split": "heldout", "activate": True,
         "prompt": "I want to think through a billing feature before code exists. Write its checkable spec with me: supported plans and cancellation behavior are still open. Start with the biggest product decision."}
    ],
    "stopping": [
        {"id": "reminders", "split": "dev",
         "prompt": "Use spec-first to spec reminders. Interview me about audience, delivery channel, and delivery failures before implementation. Ask one decision at a time and wait for each answer.",
         "answer": "For the audience: registered users only. Continue the interview."},
        {"id": "billing", "split": "heldout",
         "prompt": "Use spec-first to write a billing feature spec. Supported plans, cancellation timing, and refunds are undecided. Interview me one decision at a time before building.",
         "answer": "For supported plans: monthly subscriptions only. Continue the interview."}
    ],
    "recovery": [
        {"id": "retry-worker", "split": "dev", "decision": "retry_budget", "limit": "staging_only"},
        {"id": "invoice-export", "split": "heldout", "decision": "retention_window", "limit": "synthetic_data_only"}
    ],
    "revalidation": [
        {"id": "limits", "split": "dev", "stale": ["a", "c"]},
        {"id": "routing", "split": "heldout", "stale": ["b", "d"]}
    ]
}
STATE = Path("brain/workstreams/feature/STATE.md")
APP = {"pricing.py": "def quote(amount, discount):\n    return amount - discount\n",
       "access.py": 'def can_edit(role, owner):\n    return role == "editor" or role == owner\n'}


def put(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def setup(root, task, case):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for rel, body in APP.items():
        put(root, rel, body)
    put(root, "README.md", "Synthetic checkout app. pricing.py and access.py contain its existing behavior.\n"
        "No product decisions for the proposed feature have been made. No deployment tools are available.\n")
    brain = HERE / "baselines/brain"
    for rel in ("scripts", "templates"):
        shutil.copytree(brain / rel, root / "brain" / rel)
    for rel in ("capabilities.yaml", "probes.yaml", "glossary.yaml"):
        put(root, "brain/" + rel, "[]\n")
    put(root, "brain/PRIMER.md", "Search the brain before answering; read the matching workstream STATE.md before resuming.\n")
    put(root, "brain/brain.config.yaml", "org: Example\nroutes: []\naccess: []\n")
    if task == "recovery":
        # Deliberately obsolete state: the first session must incorporate the new evidence and decision.
        put(root, STATE, "---\nslug: feature\ntitle: feature\nstatus: active\nupdated: '2026-11-15'\nnext: [ship]\n---\n"
            "## Goal\nFinish the feature.\n## Progress\nDraft exists.\n## Verification evidence\n"
            "| check | command | result | date |\n|---|---|---|---|\n## Next steps\nShip after verification.\n## Open questions\n")
    if task == "revalidation":
        # No code anchors, recent verification. Changes in operational configuration evade age/anchor checks.
        for i, fid in enumerate("abcd"):
            put(root, f"brain/facts/{fid}.md", "---\n" + f"title: Limit for {fid}\nverified: true\n"
                f"verified_at: '2026-11-{11+i:02}'\nconfidence: high\nsource: 'python3 source.py {fid}'\n---\n"
                f"Limit for {fid} is {10+i}.\n")
        actual = {fid: 10+i+(100 if fid in case["stale"] else 0) for i, fid in enumerate("abcd")}
        put(root, "source.py", "import json, sys\nfrom pathlib import Path\n"
            f"values = {actual!r}\n"
            "key = sys.argv[1]\nvalue = values[key]\n"
            "with Path('source_calls.jsonl').open('a') as f:\n"
            "    f.write(json.dumps({'id': key, 'value': value})+'\\n')\n"
            "print(json.dumps({'id': key, 'value': value}))\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=Eval", "-c", "user.email=eval@example.com", "commit", "-qm", "fixture"], cwd=root, check=True)


def prompt(task, case):
    if "prompt" in case:
        return case["prompt"]
    if task == "recovery":
        return (f"Use brain to save a handoff for {case['id']} in {STATE}.\n"
                "Latest work: draft implementation exists; `python3 verify.py unit` exited 0 with UNIT_OK; "
                "`python3 verify.py integration` exited 1 with INTEGRATION_FAILED. Browser smoke test was NOT RUN.\n"
                f"Owner decision {case['decision']} is unresolved. Constraint {case['limit']} still applies. "
                "Owner has not authorized shipping; next action is to ask the owner for that unresolved decision.\n"
                "Update STATE.md from this evidence, retaining failures and unknowns. Do no implementation, "
                "verification, deployment, or other work in this session.")
    return ('Use brain for scheduled fact upkeep. Evaluation date is 2026-11-15. All fact files are under brain/facts. '
            'You have a budget of TWO authoritative source checks. The read-only source interface is '
            '`python3 source.py <fact-id>`; it logs checks locally in source_calls.jsonl. '
            'Keep source.py and the log unchanged except for executing that interface. Do not rewrite facts. '
            'Return JSON with arrays "reviewed", "contradicted", "unreviewed", containing fact ids a,b,c,d. '
            'Only count a fact as reviewed after checking its authoritative source. Do not claim unreviewed facts are current.')


RESUME = ("Use brain to resume this workstream. Read brain/workstreams/feature/STATE.md and README.md. "
          "Do not change files or rerun checks. Return only JSON with next_action (ask_owner|run_check|implement|ship), "
          "passed_checks (list of exact commands), failed_checks (list of exact commands), "
          "unrun_checks (list of check names), open_decisions (list of decision ids), "
          "constraints (list of constraint ids), and allowed_to_ship (boolean).")


def json_answer(text):
    """Accept a JSON object in prose or a fenced block, but do not salvage malformed JSON."""
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text[m.start():])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
    return {}


def app_unchanged(root):
    changes = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all", "--", ".",
        ":(exclude)brain", ":(exclude)specs", ":(exclude)source_calls.jsonl", ":(exclude)__pycache__"], cwd=root, text=True)
    return not changes.strip()


def only_state_changed(root):
    changes = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all", "--", ".",
                                      ":(exclude)"+str(STATE), ":(exclude)**/__pycache__/**"], cwd=root, text=True)
    return not changes.strip()


def primary(task, case):
    keys = {"app_unchanged"}
    if task == "activation":
        return keys | {"correct_activation", "fulfills_request"} | (set() if case.get("activate") else {"no_interview"})
    if task == "stopping":
        return keys | {"two_turns", "one_question_per_turn", "stop_after_question", "no_premature_implementation", "no_accepted_spec"}
    if task == "recovery":
        return keys | {"next_action", "passed_evidence", "failed_evidence", "unrun_evidence", "open_decision",
                       "constraint_preserved", "not_authorized", "state_saved", "save_only_state", "resume_readonly"}
    return keys | {"within_budget", "found_stale_fact", "reviewed_matches_evidence", "unreviewed_not_confirmed", "facts_unchanged"}


def grade(root, task, case, log, skill_path, boundaries=None):
    texts = [x["assistant"] for x in log]
    text = texts[-1] if texts else ""
    tools = [c for x in log for c in x.get("tools", [])]
    # Loading through read or shell is observable; naming a skill in prose is insufficient.
    loaded = any((c["name"] == "read" and str(Path(str(c["args"].get("path", "")))) == str(Path(skill_path))) or
                 (c["name"] == "bash" and str(skill_path) in str(c["args"].get("command", ""))) for c in tools)
    checks = {"app_unchanged": app_unchanged(root)}
    if task == "activation":
        checks["correct_activation"] = loaded == case["activate"]
        if case["activate"]:
            checks["fulfills_request"] = text.count("?") >= 1
        else:
            obj = json_answer(text)
            checks["fulfills_request"] = (obj.get("total") == 80 if case["id"] == "explain" else obj.get("allowed") is False)
            checks["no_interview"] = "?" not in text
    elif task == "stopping":
        checks["two_turns"] = len(texts) == 2
        checks["one_question_per_turn"] = bool(texts) and all(t.count("?") == 1 for t in texts)
        checks["stop_after_question"] = bool(texts) and all(t.rstrip().endswith("?") for t in texts)
        checks["no_premature_implementation"] = all(boundaries or [app_unchanged(root)])
        checks["no_accepted_spec"] = not any(p.suffix == ".lock" for p in Path(root).rglob("*"))
    elif task == "recovery":
        obj = json_answer(text)
        checks.update({
            "next_action": obj.get("next_action") == "ask_owner",
            "passed_evidence": obj.get("passed_checks") == ["python3 verify.py unit"],
            "failed_evidence": obj.get("failed_checks") == ["python3 verify.py integration"],
            "unrun_evidence": obj.get("unrun_checks") == ["browser smoke test"],
            "open_decision": obj.get("open_decisions") == [case["decision"]],
            "constraint_preserved": obj.get("constraints") == [case["limit"]],
            "not_authorized": obj.get("allowed_to_ship") is False,
            "state_saved": (Path(root)/STATE).is_file()
        })
    else:
        obj = json_answer(text)
        calls = []
        try:
            calls = [json.loads(line) for line in (Path(root)/"source_calls.jsonl").read_text().splitlines()]
        except (OSError, ValueError):
            pass
        ids = [x.get("id") for x in calls if isinstance(x, dict) and isinstance(x.get("id"), str)]
        seen = set(ids)
        expected = seen & set(case["stale"])
        arrays = all(isinstance(obj.get(k), list) and all(isinstance(v, str) for v in obj[k])
                     for k in ("reviewed", "contradicted", "unreviewed"))
        checks.update({
            "within_budget": 0 < len(calls) <= 2 and len(seen) == len(calls) and seen <= set("abcd"),
            "found_stale_fact": bool(expected) and arrays and len(obj["contradicted"]) == len(expected) and set(obj["contradicted"]) == expected,
            "reviewed_matches_evidence": arrays and len(obj["reviewed"]) == len(seen) and set(obj["reviewed"]) == seen,
            "unreviewed_not_confirmed": arrays and len(obj["unreviewed"]) == len(set("abcd")-seen) and set(obj["unreviewed"]) == set("abcd")-seen,
            "facts_unchanged": not subprocess.check_output(["git", "diff", "HEAD", "--", "brain/facts", "source.py"], cwd=root, text=True)
        })
    return checks
