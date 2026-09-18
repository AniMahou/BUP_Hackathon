#!/usr/bin/env bash
set -euo pipefail

echo "Scanning tracked files for likely API keys..."

PATTERN='AIza[0-9A-Za-z_-]{35}|AQ\.[0-9A-Za-z_-]{30,}|sk-[A-Za-z0-9_-]{20,}'

if git grep -nE "$PATTERN" -- ':!*.md' ':!scripts/check_secrets.sh' 2>/dev/null; then
  echo "Potential secret found above. Remove it before committing/pushing." >&2
  exit 1
fi

echo "No obvious secrets found."
