#!/usr/bin/env bash
# Installs the project and its development tools.
set -euo pipefail

source "$(dirname "${BASH_SOURCE[0]}")/_python.sh"

# --editable means the installed `minigit` command runs your working copy, so
# your edits take effect without reinstalling.
# [dev] pulls in the tools listed under optional-dependencies in pyproject.toml.
"$PYTHON" -m pip install --quiet --upgrade pip
"$PYTHON" -m pip install --editable ".[dev]"
