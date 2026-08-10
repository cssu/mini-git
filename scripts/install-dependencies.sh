#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f package.json ]]; then
  exit 0
fi

if [[ -f pnpm-lock.yaml ]]; then
  corepack enable
  pnpm install --frozen-lockfile
elif [[ -f yarn.lock ]]; then
  corepack enable
  yarn install --immutable
elif [[ -f package-lock.json || -f npm-shrinkwrap.json ]]; then
  npm ci
else
  npm install
fi
