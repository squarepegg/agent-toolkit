---
name: doubt-driven-development
description: Subjects every non-trivial decision to a fresh-context adversarial review before it stands. Use when you want every assumption cross-examined before proceeding, when stress-testing a plan for hidden failure modes, when correctness matters more than speed, when working in unfamiliar code, when stakes are high (production auth, security-sensitive logic, a high-stakes migration, irreversible operations), or any time a confident output would be cheaper to verify now than to debug later.
---

# Doubt-Driven Development

## Overview

A confident answer is not a correct one. Long sessions accumulate context that quietly turns assumptions into "facts" without anyone noticing. Doubt-driven development is the discipline of materializing a fresh-context reviewer — biased to **disprove**, not approve — before any non-trivial output stands.

This is not `/review`. `/review` is a verdict on a finished artifact. This is an in-flight posture: non-trivial decisions get cross-examined while course-correction is still cheap.

Before putting a trade-off to the user, use `evidence-gated-decisions` (eval table, no recommendation).

## When to Use

A decision is **non-trivial** when at least one of these is true:

- It introduces or modifies branching logic
- It crosses a module or service boundary
- It asserts a property the type system or compiler cannot verify (thread safety, idempotence, ordering, invariants)
- Its correctness depends on context the future reader cannot see
- Its blast radius is irreversible (production deploy, data migration, public API change)

Apply the skill when:

- About to make an architectural decision under uncertainty
- About to commit non-trivial code
- About to claim a non-obvious fact ("this is safe", "this scales", "this matches the spec")
- Working in code you don't fully understand

**When NOT to use:**

- Mechanical operations (renaming, formatting, file moves)
- Following a clear, unambiguous user instruction
- Reading or summarizing existing code
- One-line changes with obvious correctness
- Pure tooling operations (running tests, listing files)
- The user has explicitly asked for speed over verification

If you doubt every keystroke, you ship nothing. The skill applies only to non-trivial decisions as defined above.

## The Process

Copy this checklist when applying the skill:

```
Doubt cycle:
- [ ] Step 1: CLAIM — wrote the claim + why-it-matters
- [ ] Step 2: EXTRACT — isolated artifact + contract, stripped reasoning
- [ ] Step 3: DOUBT — invoked fresh-context reviewer with adversarial prompt
- [ ] Step 4: RECONCILE — classified every finding against the artifact text
- [ ] Step 5: STOP — met stop condition (trivial findings, 3 cycles, or user override)
```

### Step 1: CLAIM — Surface what stands

Name the decision in two or three lines:

```
CLAIM: "The new caching layer is thread-safe under the
        read-heavy workload described in the spec."
WHY THIS MATTERS: a race here corrupts user data and is
                  hard to detect in QA.
```

If you can't write the claim that compactly, you have a vibe, not a decision. Surface it before scrutinizing it.

### Step 2: EXTRACT — Smallest reviewable unit

A fresh-context reviewer needs the **artifact** and the **contract**, not the journey.

- Code: the diff or the function — not the whole file
- Decision: the proposal in 3–5 sentences plus the constraints it has to satisfy
- Assertion: the claim plus the evidence that supposedly supports it (kept distinct from the Step 1 CLAIM block, which is the orchestrator's hypothesis under scrutiny)

Strip your reasoning. If you hand over conclusions, you'll get back validation of your conclusions. The unit must be small enough that a reviewer can hold it in mind in one read — if it's a 500-line PR, decompose first.

### Step 3: DOUBT — Invoke the fresh-context reviewer

The reviewer's prompt **must be adversarial**. Framing decides the answer.

```
Adversarial review. Find what is wrong with this artifact.
Assume the author is overconfident. Look for:
- Unstated assumptions
- Edge cases not handled
- Hidden coupling or shared state
- Ways the contract could be violated
- Existing conventions this might break
- Failure modes under unexpected input

Do NOT validate. Do NOT summarize. Find issues, or state
explicitly that you cannot find any after thorough examination.

ARTIFACT: <paste artifact>
CONTRACT: <paste contract>
```

**Pass ARTIFACT + CONTRACT only. Do NOT pass the CLAIM.** Handing the reviewer your conclusion biases it toward agreement. The reviewer must independently determine whether the artifact satisfies the contract.

In Claude Code, the role-based reviewers in `agents/` start with isolated context by design and are usable here — see `agents/` for the roster and per-domain match.

**The adversarial prompt above takes precedence over the persona's default response shape.** Personas like `code-reviewer` are written to produce balanced verdicts with both strengths and weaknesses; doubt-driven needs issues-only output. Paste the adversarial prompt verbatim into the invocation so it overrides the persona's default. If a persona's response shape can't be overridden cleanly, fall back to a generic subagent with the adversarial prompt.

#### Cross-model escalation

A single-model reviewer shares blind spots with the original author — a colder, different-architecture model catches them. Doubt-driven is already opt-in for non-trivial decisions, so within that scope offering cross-model is part of the skill's value, not optional friction.

**Interactive sessions: always offer. Never silently skip.**

**Step 1: Ask the user**

After the single-model review in Step 3 above, but before RECONCILE, pause and ask:

> *"Single-model review complete. Want a cross-model second opinion? Options: Gemini CLI, Codex CLI, manual external review (you paste it elsewhere), or skip."*

This question is mandatory in every interactive doubt cycle — even on artifacts that feel low-stakes. The user — not the agent — decides whether the cost is worth it. The agent's job is to surface the choice.

**Step 2: If the user picks a CLI — verify, then invoke**

1. Check the tool is in PATH (`which gemini`, `which codex`).
2. Test it works (`gemini --version` or equivalent) before passing the full prompt — a stale or broken binary may pass `which` but fail on real input.
3. Confirm the exact invocation with the user, including required flags, auth, and env vars (e.g., API keys). Implementations vary; never assume.
4. Pass ARTIFACT + CONTRACT + the adversarial prompt **only**. No session context, no CLAIM.
5. Mind shell escaping. If the artifact contains quotes, `$(...)`, or backticks, prefer stdin (`echo … | gemini`) or a heredoc over inline `-p "…"`. When in doubt, ask the user to confirm the invocation before running it.
6. Take the output into Step 4 (RECONCILE).

**Never interpolate the artifact into a shell-quoted argument.** Code, markdown, and review prompts routinely contain backticks, `$(...)`, and quote characters that will either truncate the prompt or execute embedded shell. Write the full prompt to a file and pipe it through stdin.

Example shapes (verify flags against your installed tool — syntax differs across implementations and versions):

```bash
# Write the adversarial prompt + ARTIFACT + CONTRACT to a temp file first.
# Then pipe via stdin so shell metacharacters in the artifact stay inert.

# Codex (read-only sandbox keeps the CLI from writing to your workspace):
codex exec --sandbox read-only -C <repo-path> - < /tmp/doubt-prompt.md

# Gemini ('--approval-mode plan' is read-only; '-p ""' triggers non-interactive
# mode and the prompt is read from stdin):
gemini --approval-mode plan -p "" < /tmp/doubt-prompt.md
```

A read-only sandbox is the load-bearing detail: a doubt artifact may itself contain instructions (intentional or accidental prompt injection) that the cross-model CLI would otherwise execute against your workspace.

**Step 3: If the CLI is unavailable or fails**

Surface the failure explicitly. Offer: run it manually, try a different tool, or skip. Do not silently fall back to single-model — the user should know cross-model didn't happen.

**Step 4: If the user skips**

Acknowledge the skip in the output (*"Proceeding with single-model findings only"*) and continue to RECONCILE. Skipping is fine; silent skipping is not.

**Non-interactive contexts** (CI, `/loop`, autonomous-loop, scheduled runs):

- Cross-model is **skipped**, and the skip must be **announced** in the output: *"Cross-model skipped: non-interactive context."*
- **Never invoke an external CLI without explicit user authorization** — this is a load-bearing safety property.

Cross-model adds cost, latency, and tool fragility. The agent surfaces the choice every cycle; the user decides whether this artifact warrants it.

### Step 4: RECONCILE — Fold findings back

The reviewer's output is data, not verdict. **You are still the orchestrator.** Re-read the artifact text against each finding before classifying — rubber-stamping the reviewer is the same failure mode as ignoring it.

For each finding, classify in this **precedence order** (first matching class wins):

1. **Contract misread** — reviewer flagged something specifically because the CONTRACT you provided was unclear or incomplete. Fix the contract first, re-classify on the next cycle.
2. **Valid + actionable** — real issue requiring a change to the artifact. Change it, re-loop.
3. **Valid trade-off** — issue is real but cost of fixing exceeds cost of accepting. Document the trade-off explicitly so the user sees it.
4. **Noise** — reviewer flagged something that's actually correct under context the reviewer didn't have. Note it, move on, and ask: would adding that context to the contract have prevented the false flag?

A fresh reviewer can be wrong because it lacks context. Don't defer just because it's "fresh."

### Step 5: STOP — Bounded loop, not recursion

Stop when:

- Next iteration returns only trivial or already-considered findings, **or**
- 3 cycles completed (escalate to user, don't grind a fourth alone), **or**
- User explicitly says "ship it"

If after 3 cycles the reviewer still surfaces substantive issues, the artifact may not be ready. Surface this to the user — three unresolved cycles is information about the artifact, not a reason to keep looping.

If 3 cycles is "obviously insufficient" because the artifact is large: the artifact is too big — return to Step 2 and decompose. Do not lift the bound.


## Details (read the file when you reach that step)

- Loading Constraints: `reference/sections/03-loading-constraints.md`
- Common Rationalizations: `reference/sections/05-common-rationalizations.md`
- Red Flags: `reference/sections/06-red-flags.md`
- Interaction with Other Skills: `reference/sections/07-interaction-with-other-skills.md`
- Verification: `reference/sections/08-verification.md`
