#!/usr/bin/env bash
# Guarded, explicit server promotion to reviewed origin/main.
set -euo pipefail

EXPECTED_ROOT="${QUANT_PLATFORM_REPO:-/opt/market-platform}"
INSPECT_ONLY=0
if [[ "${1:-}" == "--inspect-only" && "$#" -eq 1 ]]; then
  INSPECT_ONLY=1
elif [[ "$#" -ne 0 ]]; then
  echo "usage: $0 [--inspect-only]" >&2
  exit 2
fi

fail() { echo "promote-main: FATAL: $*" >&2; exit 1; }

ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || fail "not inside a Git repository"
[[ "$ROOT" == "$EXPECTED_ROOT" ]] || fail "repository is $ROOT, expected $EXPECTED_ROOT"
git remote get-url origin >/dev/null 2>&1 || fail "remote origin is missing"

branch=$(git symbolic-ref --short -q HEAD || printf 'DETACHED')
sha=$(git rev-parse HEAD)
echo "repository=$ROOT"
echo "branch=$branch"
echo "current_sha=$sha"
echo "state=$(if [[ -n "$(git status --porcelain)" ]]; then echo dirty; else echo clean; fi)"

if (( INSPECT_ONLY )); then
  exit 0
fi

[[ "$branch" == "main" ]] || fail "current branch is $branch, expected main"
[[ -z "$(git status --porcelain)" ]] || fail "working tree is dirty; inspect manually"

git fetch origin
target=$(git rev-parse origin/main)
echo "target_sha=$target"
git merge-base --is-ancestor "$sha" "$target" || fail "origin/main is not a fast-forward target"
git merge --ff-only origin/main

final_sha=$(git rev-parse HEAD)
echo "final_sha=$final_sha"
[[ -z "$(git status --porcelain)" ]] || fail "promotion left a dirty working tree"
echo "state=clean"
