#!/usr/bin/env bash
# Reformats the code and fixes the lint problems that can be fixed
# automatically. Run this when quality-check.sh complains about formatting.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_python.sh"

"$PYTHON" -m ruff check --fix .
"$PYTHON" -m ruff format .
