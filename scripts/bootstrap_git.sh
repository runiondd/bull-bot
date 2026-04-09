#!/usr/bin/env bash
#
# One-time git bootstrap for Bull-Bot.
#
# Run this ONCE from your Mac terminal, after confirming:
#   1. The GitHub repo https://github.com/runiondd/bull-bot exists and is empty
#      (or delete any README/license GitHub auto-created)
#   2. You have either an SSH key or a personal access token configured
#      for github.com
#
# Usage:
#   cd ~/Bull-Bot
#   bash scripts/bootstrap_git.sh
#
# This is idempotent up to the initial commit — if a .git dir already exists
# it blows it away first. Safe because we have no history to lose.

set -euo pipefail

cd "$(dirname "$0")/.."

echo "==> Project: $(pwd)"

# 0. Python version check — must be 3.11+
if command -v python3 >/dev/null 2>&1; then
  PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
  PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
  PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
  if [[ "$PY_MAJOR" -lt 3 ]] || { [[ "$PY_MAJOR" -eq 3 ]] && [[ "$PY_MINOR" -lt 11 ]]; }; then
    echo "WARNING: python3 is $PY_VER. Bull-Bot requires 3.11+."
    echo "         Your venv will not work. Install with: brew install python@3.11"
  else
    echo "==> python3 version: $PY_VER"
  fi
fi

# 1. Clean any partial .git state
if [[ -d .git ]]; then
  echo "==> Removing existing .git/"
  rm -rf .git
fi

# 2. Sanity: .env must exist and must NOT be about to get committed
if [[ ! -f .env ]]; then
  echo "ERROR: .env is missing. Refusing to bootstrap without it."
  exit 1
fi
if [[ ! -f .gitignore ]]; then
  echo "ERROR: .gitignore is missing. Refusing to bootstrap — .env would leak."
  exit 1
fi
if ! grep -q '^\.env$' .gitignore; then
  echo "ERROR: .gitignore does not exclude .env. Refusing to bootstrap."
  exit 1
fi

# 3. Init
git init -q
git branch -m main
git config user.email "runiondd@gmail.com"
git config user.name  "Dan Runion"

# 4. Stage and paranoid-check that .env is NOT in the index
git add -A
if git ls-files --error-unmatch .env >/dev/null 2>&1; then
  echo "FATAL: .env is staged. Aborting before commit."
  git rm --cached .env
  exit 1
fi
echo "==> .env confirmed NOT staged"

# 5. Commit
git commit -q -m "Initial commit: Bull-Bot scaffold

- Docs: ARCHITECTURE.md v2.0, WORK_PLAN.md v2.1, reviews
- Schemas: Pydantic module for signals/decisions/trading/performance/
  evolver/config/backtest/regime (T1.0a)
- Utils: JSON-structured logging with thread-local context (T1.0b)
- Tests: pytest harness + conftest fixtures + schema smoke tests (T1.0c)
- Config: wired ANTHROPIC_API_KEY; requirements.txt upgraded for
  pydantic/anthropic/pytest/pandas-market-calendars"

# 6. Add remote (ignore if already present)
git remote add origin https://github.com/runiondd/bull-bot.git 2>/dev/null || true

# 7. Push
echo "==> Pushing to origin/main ..."
git push -u origin main

echo ""
echo "==> DONE. Repo is live at https://github.com/runiondd/bull-bot"
echo "==> Verify .env is NOT visible on GitHub before continuing."
