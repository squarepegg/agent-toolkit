# Tracing a site to its origin

`scar_scan.py` does steps 1–4 automatically. Do them by hand for sites it missed, and to check
any origin that looks wrong.

## 1. Origin: pickaxe, not blame

```bash
git log -S '<distinctive text>' --format='%h %ad %s' --date=short -- <file>   # oldest is last
git log -S '<distinctive text>' --format='%h %ad %s' --date=short --follow -- <file>
```

- Run both forms. `-S` with `--follow` silently misses some matches.
- Choose text that is unique in the file: the whole line, a string literal, a rare identifier.
- If the text occurs several times in the file, the oldest hit is the *first* occurrence, which
  may not be this one. Check with `git log -L<start>,<end>:<file> -s --format='%h %s'`.
- Value changes (`200` → `500`) do not change the count of an identifier. Use `-G '<regex>'`
  or `git log -L` to see them.

## 2. Last touch: blame, for contrast only

```bash
git blame -L <start>,<end> --porcelain <file> | grep -E '^(summary|author) '
```

When blame differs from the origin, say so. Comment rewrites (e.g. "rewrite comments to describe
the finished system, not its history") and CI or formatting commits hide the real author.

## 3. Moves and splits

If the origin is the commit that created the file, check whether that commit deleted the same
text elsewhere:

```bash
git show <sha> --format= --unified=0 | grep -F -- '-<distinctive text>'
git log -S '<distinctive text>' <sha>~1 --format='%h %s'      # history before the move
```

## 4. PR and incident

```bash
git log --merges --ancestry-path --reverse --format='%h %s' <sha>..HEAD | head -1   # merge PR
gh pr view <N> -R <owner>/<repo> --json title,body,url                              # read-only; -R needed in worktrees
```

Search the commit body, PR body and `docs/` (ADRs, TDDs, PRDs, runbooks) for: incident and
ticket ids, "observed in prod", "fail open", "p99", "SLO", "outage", "regression", vendor
names, and the identifier itself.

Also read the PR's inline review threads, resolved and bot threads included:

```bash
gh api repos/<owner>/<repo>/pulls/<N>/comments --paginate \
  --jq '.[] | select(.in_reply_to_id == null) | "\(.user.login) \(.path):\(.original_line)\n\(.body)"'
```

A guard often exists because a reviewer found the edge case: a concrete input, then a fix
commit. The PR body rarely repeats it. A thread that was resolved by a later commit is the
lesson and its origin in one place. A thread that is still open, or was resolved as "won't
fix", is a known gap. Report it as `unknown, do not remove` or under Tensions.

## 5. Judge the evidence

| Evidence | Weight |
|---|---|
| PR or commit text that names an observed failure and a number | strong |
| Review thread with a concrete input, resolved by a named commit | strong for that edge case. Check the fix commit is still in history |
| ADR, TDD or design doc that states the rule | strong, check it is not superseded |
| Code comment that explains why | medium. Comments drift; check the commit that wrote it |
| Commit body that only lists *what* changed | weak |
| Initial-import or mega-PR (PR-A) | weak. Origin is "since the start", reason usually unknown |
| Your own reasoning about why it might exist | `inferred:`. Never `stated:` |

## 6. Common traps

- An AI-authored commit body (Co-Authored-By: Claude) can sound certain. It records what the
  agent believed at the time. Quote it; do not upgrade it to fact.
- A test that *mentions* a symbol does not pin it. Open the test and check the assertion.
- A doc rule at one layer (e.g. "client: no retries") is not a contradiction of a rule at
  another layer (e.g. "server → upstream: retry resets once"). Record it as a tension only if
  both apply to the same call.
