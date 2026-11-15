#!/usr/bin/env bash
# Commit triage records + draft candidates to a branch and open (or update) one PR. No-op when nothing changed.
#   scripts/triage_pr.sh <YYYY-MM-DD> <report.md>      env: BASE (default main), REMOTE (default origin)
# Adds only evals/triage and evals/candidates, so a stray file in the workspace can never ride along.
set -euo pipefail
date="${1:?date}"; report="${2:?report file}"
base="${BASE:-main}"; remote="${REMOTE:-origin}"
[[ "$date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || { echo "bad date: $date" >&2; exit 2; }
paths=(); for d in evals/triage evals/candidates; do [ -d "$d" ] && paths+=("$d"); done
[ "${#paths[@]}" -gt 0 ] || { echo "triage: nothing to record"; exit 0; }
git add -- "${paths[@]}"
if git diff --cached --quiet; then echo "triage: nothing to record"; exit 0; fi
branch="triage/$date"
git checkout -q -B "$branch"
git -c user.name="triage-bot" -c user.email="triage-bot@users.noreply.github.com" commit -q -m "triage: scout candidates $date"
git push -q --force "$remote" "$branch"
gh label create triage --color 5319e7 --description "model triage of scout candidates" 2>/dev/null || true
if gh pr view "$branch" >/dev/null 2>&1; then
  gh pr edit "$branch" --body-file "$report"
else
  gh pr create --base "$base" --head "$branch" --label triage --title "Triage: scout candidates $date" --body-file "$report"
fi
