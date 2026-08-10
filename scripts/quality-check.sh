#!/usr/bin/env bash
set -euo pipefail

has_command() {
  command -v "$1" > /dev/null 2>&1
}

run_node_script_if_present() {
  local script_name="$1"

  if [[ ! -f package.json ]]; then
    return 0
  fi

  if ! node -e "const p=require('./package.json'); process.exit(p.scripts && p.scripts['$script_name'] ? 0 : 1)" 2> /dev/null; then
    return 0
  fi

  if [[ -f pnpm-lock.yaml ]]; then
    if ! has_command pnpm && has_command corepack; then
      corepack enable
    fi
    if ! has_command pnpm; then
      echo "pnpm-lock.yaml found, but pnpm is not installed." >&2
      exit 1
    fi
    pnpm run "$script_name"
  elif [[ -f yarn.lock ]]; then
    if ! has_command yarn && has_command corepack; then
      corepack enable
    fi
    if ! has_command yarn; then
      echo "yarn.lock found, but yarn is not installed." >&2
      exit 1
    fi
    yarn "$script_name"
  else
    npm run "$script_name"
  fi
}

run_node_script_if_present lint
run_node_script_if_present test

echo "Quality checks completed."
