#!/usr/bin/env bash
# Runs the project's automated checks. This is what CI runs, so if it passes
# here it should pass on your pull request.
#
#   scripts/quality-check.sh                lint + formatting + tests
#   scripts/quality-check.sh --pre-commit   lint + formatting only (fast)
#
# The pre-commit hook uses the fast form so committing stays quick. Tests still
# run in CI, and you can run them yourself with scripts/test.sh.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_python.sh"

pre_commit=false
if [[ "${1:-}" == "--pre-commit" ]]; then
  pre_commit=true
fi

# Lint: catches unused imports, undefined names, and other common mistakes.
echo "Linting..."
"$PYTHON" -m ruff check .

# Formatting: checks the code matches the project's style without changing it.
# Run `scripts/format.sh` to fix anything this reports.
echo "Checking formatting..."
"$PYTHON" -m ruff format --check .

if [[ "$pre_commit" == true ]]; then
  echo "Quality checks completed (tests skipped for pre-commit)."
  exit 0
fi

echo "Running tests..."
"$PYTHON" -m pytest

echo "Quality checks completed."
