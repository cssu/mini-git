#!/usr/bin/env bash
# Shared helper. Not meant to be run directly - the other scripts source it.
#
# Sets two variables:
#   REPO_ROOT - the top of the repository
#   PYTHON    - the interpreter to use
#
# It prefers the project's own .venv so you never have to remember to activate
# it, and falls back to whatever python3 is on PATH (which is what CI uses).

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
elif command -v python3 > /dev/null 2>&1; then
  PYTHON="python3"
else
  echo "No Python interpreter found. Install Python 3.11+ and run scripts/init.sh." >&2
  exit 1
fi
