#!/usr/bin/env bash
# One-shot wrapper: creates venv + installs Playwright on first run, then
# forwards all args to board_to_pdf.py.
#
# Usage:
#   ./run.sh login              # sign into Miro (one time)
#   ./run.sh pdf <BOARD_URL>    # capture a board to board.pdf

set -euo pipefail

cd "$(dirname "$0")"

VENV=".venv"
STAMP="$VENV/.installed"

if [[ ! -d "$VENV" ]]; then
  echo "==> Creating virtualenv in $VENV"
  python3 -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

if [[ ! -f "$STAMP" ]]; then
  echo "==> Installing Playwright (Python package + Chromium browser)"
  pip install --quiet --upgrade pip
  pip install --quiet playwright
  playwright install chromium
  touch "$STAMP"
fi

python board_to_pdf.py "$@"
