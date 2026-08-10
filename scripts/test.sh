#!/usr/bin/env bash
# Runs the test suite. Extra arguments are passed straight through to pytest,
# so `scripts/test.sh -k objects` and `scripts/test.sh -x` work as expected.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_python.sh"

"$PYTHON" -m pytest "$@"
