#!/usr/bin/env bash
# Push the current branch and open a pull request so GitHub Actions runs.
# Requires an authenticated gh CLI with repo and workflow scopes.
set -euo pipefail

cd "$(dirname "$0")/.."
BRANCH="$(git rev-parse --abbrev-ref HEAD)"

if [[ "$BRANCH" == "main" ]]; then
  echo "refusing to push from main; check out a feature branch first" >&2
  exit 1
fi
if [[ -n "$(git status --porcelain)" ]]; then
  echo "working tree is dirty; commit first" >&2
  exit 1
fi

echo "==> pushing $BRANCH"
git push -u origin "$BRANCH"

echo "==> opening pull request"
if gh pr view "$BRANCH" >/dev/null 2>&1; then
  echo "pull request already exists for $BRANCH"
else
  gh pr create --fill --base main --head "$BRANCH"
fi

echo "==> waiting for checks"
gh pr checks "$BRANCH" --watch || true

echo
echo "==> optional: run the engine-integration job (installs AIPerf, ~96 deps)"
echo "    gh workflow run ci.yml --ref $BRANCH"
