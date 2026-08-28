#!/usr/bin/env bash
# Guarded, explicit server promotion to reviewed origin/main.
set -euo pipefail

EXPECTED_ROOT="${QUANT_PLATFORM_REPO:-/opt/market-platform}"
EXPECTED_ORIGIN="${QUANT_PLATFORM_ORIGIN:-https://github.com/NeoNix-Lab/quant-platform.git}"
DEFAULT_SSH_ORIGIN="git@github.com:NeoNix-Lab/quant-platform.git"
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
origin=$(git remote get-url origin 2>/dev/null) || fail "remote origin is missing"
echo "origin=$origin"
echo "expected_origin=$EXPECTED_ORIGIN"
if [[ "${QUANT_PLATFORM_ORIGIN:-}" == "" ]]; then
  [[ "$origin" == "$EXPECTED_ORIGIN" || "$origin" == "$DEFAULT_SSH_ORIGIN" ]] || \
    fail "origin does not match the expected Quant Platform repository"
else
  [[ "$origin" == "$EXPECTED_ORIGIN" ]] || \
    fail "origin does not match QUANT_PLATFORM_ORIGIN"
fi

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
incoming=$(git log --oneline --decorate "$sha..$target")
if [[ -n "$incoming" ]]; then
  echo "incoming_commits:"
  printf '%s\n' "$incoming"
else
  echo "incoming_commits: none"
fi
git merge --ff-only origin/main

if ! python3 tools/run_tests.py; then
  echo "promote-main: repository promoted but validation failed; inspect manually; no rollback was attempted" >&2
  python3 tools/repo_identity.py || true
  exit 1
fi

echo "post_promotion_identity:"
python3 tools/repo_identity.py || {
  echo "promote-main: repository promoted but identity reporting failed; inspect manually" >&2
  exit 1
}
