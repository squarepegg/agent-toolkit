"""Outcome task (flaw recall): does skills/doubt make an agent find MORE of the flaws in a plan?

plan.md is a migration plan with seven planted flaws of different kinds. The agent is asked to review it before it runs.
The score of a run is the number of planted flaws its answers name (each recognised by a fixed regex over everything
the agent said), or 0 if it changed plan.md or did not finish. Procedure (did it spawn a fresh reviewer, did it offer a
cross-model check) is recorded as secondary checks, NOT scored: following the skill's steps is not the claim; finding
more problems is.

  drop_before_deploy   the column is dropped before the code that stops using it is deployed, so rollback is impossible
  one_shot_backfill    one UPDATE over 40M rows: long lock, bloat, replication lag
  writes_lost          v1 keeps writing users.email after the backfill, so new rows/changes never reach contact_email
  index_blocks_writes  CREATE UNIQUE INDEX without CONCURRENTLY blocks writes on a live table
  uniqueness_contract  the contract says unique case-insensitively; a plain index on text is not (and duplicates may exist)
  warehouse_reads_email  a replica feeds a warehouse export that reads users.email, which the drop breaks
  big_bang_deploy      "all pods at once": no canary or rolling deploy, against the no-downtime contract

History: v1 had one flaw (drop before deploy) and procedure checks as primary (see evals/negative-results.md).
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HAB = os.path.join(os.path.dirname(HERE), "harness-ab")
sys.path.insert(0, HAB)
import tasks as T  # noqa: E402

PLAN = '''# Plan: move users.email to users.contact_email (tonight, 22:00)

Context: Postgres 15, `users` has 40M rows and takes writes all day. A read replica feeds the analytics warehouse, whose
nightly export reads `users.email`. App v1 reads and writes `users.email`. App v2 reads and writes
`users.contact_email` only. Both are ready to deploy.

Contract: no downtime for logged-in users; rolling back to v1 must stay possible until v2 has run for 24 hours; contact
emails are unique, ignoring case.

1. `ALTER TABLE users ADD COLUMN contact_email text;`
2. Backfill: `UPDATE users SET contact_email = email;`
3. `CREATE UNIQUE INDEX users_contact_email_idx ON users (contact_email);`
4. `ALTER TABLE users DROP COLUMN email;`
5. Deploy app v2 to all pods at once.
6. If anything breaks, roll back the deploy to v1.
'''

FLAWS = {
    "drop_before_deploy": r"drop[^\n]{0,220}(roll ?back|v1|irrevers|before (the |we |you )?(deploy|app|v2|code))|(roll ?back|v1)[^\n]{0,220}drop|drop[^\n]{0,60}(last|after)",
    "one_shot_backfill": r"batch|chunk|in pieces|incremental(ly)? backfill|long.running (transaction|update|statement)|bloat|\bwal\b|replication lag|single (update|statement|transaction)|one (big |huge |giant )?(update|statement|transaction)|40\s?m(illion)?[^\n]{0,80}(lock|rows? at once|one go)",
    "writes_lost": r"dual.?write|trigger|(writes?|updates?|inserts?|changes?|rows?|signups?)[^\n]{0,60}(during|between|after|while|since)[^\n]{0,60}(backfill|migration|deploy|cut.?over)|(backfill|migration)[^\n]{0,80}(miss|lose|lost|stale|drift|not captured|never reach)|v1[^\n]{0,100}(still |keeps? |continues? )?writ|catch.?up|re-?run the backfill",
    "index_blocks_writes": r"index[^\n]{0,160}(concurrent|lock|block)|(concurrent|lock|block)[^\n]{0,160}index",
    "uniqueness_contract": r"case.?insensitive|citext|lower\(|collat|duplicate|differ(s|ing)? only (in|by) case|unique[^\n]{0,80}(fail|violat|case)",
    "warehouse_reads_email": r"(warehouse|analytics|replica|export|downstream|etl|cdc|data team|reports?)[^\n]{0,160}(email|column|break|fail|schema)|(email|column)[^\n]{0,160}(warehouse|analytics|export|downstream|etl|cdc)",
    "big_bang_deploy": r"canary|gradual|staged|stagger|big.?bang|blue.?green|feature.?flag|rolling (deploy|update|out|restart)|one pod|small (percentage|fraction|slice)|incrementally (deploy|roll)",
}
FLAW_NAMES = list(FLAWS)
PREREG = {"n": 10, "alpha": 0.05, "ceiling": 0.8,
          "score": {"kills": [f"finds_{f}" for f in FLAW_NAMES], "gate": ["plan_unchanged"]},
          "primary": ["plan_unchanged", *[f"finds_{f}" for f in FLAW_NAMES]]}

SKILL_SENTENCE = "Use doubt-driven development on plan.md before I run it tonight. "
PROMPT_NONE = ("Review plan.md before I run it tonight. I'm at the keyboard and will answer your questions. "
               "Don't change plan.md; tell me what you found.")
PROMPT = ("Use doubt-driven development on plan.md before I run it tonight. I'm at the keyboard and will answer "
          "your questions. Don't change plan.md; tell me what you found.")   # skill and control arms; none: PROMPT_NONE (a missing skill stalls the agent)
REPLY = ("Yes, you may run a fresh-context reviewer with `pi`. Skip the cross-model (Gemini/Codex) review. "
         "For anything else, go with your recommendation and finish.")


def setup(r):
    T.init_repo(r, {"plan.md": PLAN})


def user(turn, text):
    if turn >= 4 or not re.search(r"\?|permission|approve|confirm|let me know|your call", text, re.I):
        return None
    return REPLY


def _without_plan(text):
    """drop what the agent merely quotes from plan.md (lines and sentences of 20+ chars), so quoting is not credited"""
    low = text.lower()
    pieces = [x.strip(" -*`#0123456789.").lower() for x in re.split(r"[\n]|(?<=[.;])\s", PLAN)]
    for piece in sorted({x for x in pieces if len(x) >= 20}, key=len, reverse=True):
        low = low.replace(piece, " ")
    return low


def found(text):
    low = _without_plan(text)
    return {f: bool(re.search(rx, low)) for f, rx in FLAWS.items()}


def grade(r, final_text, all_text, bash_cmds):
    revs = [c for c in bash_cmds if T.REVIEWER.search(c)]
    ext = [seg for c in bash_cmds for seg in T.external_invocations(c)]
    with open(os.path.join(r, "plan.md")) as f:
        unchanged = f.read() == PLAN
    checks = {"plan_unchanged": unchanged, **{f"finds_{k}": v for k, v in found(all_text).items()},
              # secondary (not scored): the procedure
              "fresh_reviewer_spawned": bool(revs), "no_unauthorized_external_cli": not ext}
    return checks, {"reviewer_calls": revs[:3], "external_calls": ext[:3], "flaws_found": [k for k, v in found(all_text).items() if v]}


# --------------------------------------------------------------------------- grader fixtures (CI pins the grader both ways)
FULL_ANSWER = """Findings on plan.md:
1. Step 4 drops users.email before app v2 is deployed in step 5, so rolling back to v1 (step 6) is impossible: v1 reads email.
2. Step 2 is one UPDATE over 40M rows: a long transaction, table bloat and replication lag. Backfill in batches instead.
3. v1 keeps writing users.email after the backfill, so new signups and changes never reach contact_email. Add dual-write or a trigger.
4. CREATE UNIQUE INDEX without CONCURRENTLY blocks writes on a live table for the whole build.
5. The contract says unique ignoring case; a plain index on text is case sensitive. Use lower(contact_email) or citext, and check for duplicates first.
6. The analytics warehouse export reads users.email from the replica; dropping the column breaks it.
7. Deploying v2 to all pods at once is a big-bang rollout; use a canary or rolling deploy."""
BLAND_ANSWER = "The plan looks reasonable overall. Consider adding monitoring and testing it on staging first."
QUOTE_ONLY_ANSWER = "Here is the plan again:\n" + PLAN + "\nI have read it."
PARTIAL_ANSWER = "Step 4 drops the column before the deploy, which makes rollback impossible. Also, the backfill should run in batches."


def write_solution(r, which):   # unused by the text-graded fixture tests; plan.md must stay as is
    pass


TASK = {"skill": "doubt", "prompt": PROMPT, "prompt_none": PROMPT_NONE, "setup": setup, "user": user, "grade": grade,
        "turn_timeout": 300, "serial": True,   # the skill writes /tmp/doubt-prompt.md
        "prereg": PREREG, "files": [__file__, os.path.join(HAB, "tasks.py")], "write_solution": write_solution,
        "fixtures": {"reference": FULL_ANSWER, "naive": BLAND_ANSWER, "partial": PARTIAL_ANSWER, "quote": QUOTE_ONLY_ANSWER}}
