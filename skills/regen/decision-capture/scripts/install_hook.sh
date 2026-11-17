#!/usr/bin/env sh
# Install the decision-capture commit-msg hook into a repo. Opt-in, per repo.
# Usage: install_hook.sh [REPO_DIR]
# Never overwrites an existing hook and never changes core.hooksPath.
set -eu

repo="${1:-.}"
src="$(cd "$(dirname "$0")" && pwd)/commit-msg"
cd "$repo"
git rev-parse --git-dir >/dev/null 2>&1 || { echo "error: $repo is not a git repository" >&2; exit 2; }

hooks_path="$(git config --get core.hooksPath || true)"
if [ -n "$hooks_path" ]; then
  echo "core.hooksPath is set to '$hooks_path' (husky, lefthook or similar)."
  echo "Not installing. Add this line to that tool's commit-msg hook:"
  echo "  python3 \"$src\" \"\$1\""
  exit 0
fi

dest="$(git rev-parse --git-path hooks)/commit-msg"
if [ -e "$dest" ]; then
  echo "A commit-msg hook already exists at $dest. Not overwriting."
  echo "Chain it by adding this line to the existing hook:"
  echo "  python3 \"$src\" \"\$1\""
  exit 0
fi

cp "$src" "$dest"
chmod +x "$dest"
echo "Installed $dest (warn-only; export PHOENIX_DECISION_STRICT=1 to block commits)."
