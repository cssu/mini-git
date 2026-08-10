#!/usr/bin/env bash
# One-time setup for a fresh clone: virtual environment, dependencies, Git hooks.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v python3 > /dev/null 2>&1; then
  echo "Python 3 is not installed. Install Python 3.11 or newer, then run this again." >&2
  exit 1
fi

# A virtual environment keeps this project's packages out of your system Python,
# so nothing you install here can break another project.
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11 or newer is required. Found: $(python3 --version)" >&2
  exit 1
fi

if [[ ! -d .venv ]]; then
  echo "Creating virtual environment in .venv/"
  python3 -m venv .venv
fi

scripts/install-dependencies.sh
scripts/install-git-hooks.sh

cat << 'EOF'

Setup complete.

  Activate the virtual environment:  source .venv/bin/activate
  Run the CLI:                       minigit --help
  Run the checks:                    scripts/quality-check.sh

The scripts in scripts/ find .venv on their own, so activating is optional.
EOF
