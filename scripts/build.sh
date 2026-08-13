#!/usr/bin/env bash
# Builds a distributable package into dist/ - a .whl anyone can `pip install`
# and a .tar.gz of the source. Use this for the demo and the final handoff.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_python.sh"

rm -rf dist
"$PYTHON" -m build

echo
echo "Built:"
ls -1 dist
