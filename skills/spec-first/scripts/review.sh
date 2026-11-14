#!/bin/bash
# spec-first: run the independent reviewer in a fresh process that sees only the spec, the evidence and the diff.
#   review.sh SPEC EVIDENCE_DIR
# Writes EVIDENCE_DIR/review-<n>.md (the reviewer's raw report) and EVIDENCE_DIR/brief-<n>.md (exactly what it saw).
# The reviewer runs with cwd = repository root, so it can open files, but nothing from the building session is passed.
# Refuses a 4th round (a failure that survives two fixes needs the user). Fails if the reviewer changed the repository.
# SPEC_REVIEW_RUNNER: the command that reads the brief on stdin and prints the report on stdout. Default: the first of
#   pi:     pi -p --no-session --no-context-files --no-skills --tools read
#   claude: claude -p --allowedTools Read Grep Glob --disallowedTools Bash Edit Write NotebookEdit
# SPEC_REVIEW_MAX_ROUNDS: default 3.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
[ $# -eq 2 ] || { sed -n 2,11p "$0"; exit 2; }
SPEC=$1; DIR=$2
[ -f "$DIR/evidence.json" ] || { echo "review: no $DIR/evidence.json; run evidence.py first"; exit 2; }
ROOT=$(git -C "$(dirname "$SPEC")" rev-parse --show-toplevel)
MAX=${SPEC_REVIEW_MAX_ROUNDS:-3}
n=$(find "$DIR" -maxdepth 1 -name 'review-*.md' | wc -l | tr -d ' '); n=$((n + 1))
[ "$n" -le "$MAX" ] || { echo "review: $MAX rounds done; stop and report what still fails to the user"; exit 3; }
if [ -n "${SPEC_REVIEW_RUNNER:-}" ]; then RUNNER=$SPEC_REVIEW_RUNNER
elif command -v pi >/dev/null 2>&1; then RUNNER="pi -p --no-session --no-context-files --no-skills --tools read"
elif command -v claude >/dev/null 2>&1; then RUNNER="claude -p --allowedTools Read Grep Glob --disallowedTools Bash Edit Write NotebookEdit"
else echo "review: no reviewer CLI (pi or claude) on PATH; set SPEC_REVIEW_RUNNER, or review by hand and say it was not independent"; exit 2
fi

BRIEF="$DIR/brief-$n.md"
{
  cat "$HERE/../templates/reviewer-brief.md"
  printf '\nRepository root (read-only for you): %s\n\n## Spec\n\n' "$ROOT"
  cat "$SPEC"
  printf '\n\n## Evidence (literal output, collected mechanically)\n\n'
  cat "$DIR/evidence.md"
  printf '\n\n## Diff\n\n```diff\n'
  cat "$DIR/diff.patch"
  printf '```\n'
} > "$BRIEF"
bytes=$(wc -c < "$BRIEF" | tr -d ' ')
echo "review: round $n, runner ${RUNNER%% *}, brief ${bytes} bytes (~$((bytes / 4)) input tokens before the reviewer reads any file)"

state() { (cd "$ROOT" && git status --porcelain --untracked-files=all && git diff HEAD | git hash-object --stdin); }
before=$(state)
( cd "$ROOT" && $RUNNER < "$BRIEF" ) > "$DIR/review-$n.md"
after=$(state)
[ "$before" = "$after" ] || { echo "review: the reviewer changed the repository; its report is void"; mv "$DIR/review-$n.md" "$DIR/void-review-$n.md"; exit 4; }
echo "review: $DIR/review-$n.md"
python3 "$HERE/verdict.py" "$DIR/review-$n.md" --evidence "$DIR" --out "$DIR/verdict-$n.md"
