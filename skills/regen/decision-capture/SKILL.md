---
name: decision-capture
description: Use when a change involves a trade-off, when choosing between options or rejecting an alternative, when the user says "we decided", "let's go with", "record why" or "write an ADR", or at PR time when a diff encodes a choice that is not written down. Writes a one-screen decision record (docs/decisions/NNNN-title.md, or the repo's existing ADR folder) with context, options, rejected alternatives, assumptions and what would make it wrong, and links it to the commit with a "Decision:" trailer. Optional commit-msg hook checks the link.
---

# Decision capture

The reasoning behind a change is the part git does not keep. Capture it while the conversation
still holds it, in one screen, linked to the commit.

Read `../_shared/conventions.md` first.

## When to write one

Write a record when the change has any of:
- two or more real options, and one was rejected for a reason;
- a constraint accepted on purpose (a deploy-order dependency, a dropped fallback, a cost);
- an assumption that could turn out false;
- something a future "cleanup" would plausibly undo.

Skip it for mechanical changes. If unsure, ask: *would someone removing this in a year need to
know why?*

## Steps

1. **Create:**
   `python3 <this-skill-dir>/scripts/new_decision.py new "<title>"`
   It picks `docs/decisions/`, or an existing `docs/adr/` (numbered or dated), and prints the
   path and the commit trailer.
2. **Fill it from the conversation and the diff.** Every section. Use `unknown` rather than
   inventing. Facts and links, not prose. Follow `examples/` for tone.
3. **Check:** `python3 <this-skill-dir>/scripts/new_decision.py check <file>`. At most 60
   lines, every section present and non-empty.
4. **Link** by putting the trailer in the commit message:
   ```text
   Decision: docs/decisions/0004-send-only-triggers-to-vendor.md
   ```
   A short id also works: `Decision: 0004`.
5. **Constraints.** If the decision should push back on future changes, fill the `Constraints`
   line and run `architecture-constraints` or `eval-tiering` to draft the guard. Its
   `Source:` header points back at this record.
6. **Superseding.** Never edit an accepted record's decision. Write a new one with
   `Status: Accepted, supersedes NNNN`, and set the old one to `Superseded by NNNN`.

## Hook (optional, per repo)

`sh <this-skill-dir>/scripts/install_hook.sh <repo>` installs a `commit-msg` hook.
- It warns if a `Decision:` trailer does not resolve, or if a new record is committed unlinked.
- `PHOENIX_DECISION_STRICT=1` makes those warnings block the commit.
- It never overwrites an existing hook or a `core.hooksPath` (husky); it prints the line to
  chain instead.

Find a record's commits with `new_decision.py list`, or
`git log --grep '^Decision: 0004' -E`.

## Example

See `examples/0004-send-only-triggers-to-vendor.md` (43 lines, passes `check`). It is a real
Offers decision whose reason lived only in a local plan doc and in a pull request.

That repo gitignores `docs/` (per its README), so `new` refuses `docs/adr/` there. A record in
an ignored folder never reaches teammates. Ask the owner for a tracked home: a `decisions/`
folder, or the repo's own convention (Offers summarises ADRs in its README).

## Known limitations

- The hook checks links, not content quality. `check` catches empty sections, not wrong ones.
- Squash merges keep the trailer only if it is in the PR description or the squash message.
  Put it in the PR body too.
- The record is only as honest as its author. "What would make this wrong" is the section most
  often skipped. Never leave it generic.
- Conversation transcripts are not stored. The record is the durable summary.
- Records written after the fact are reconstructions. Mark the date recorded, and label
  reasons that are inferred rather than stated.
