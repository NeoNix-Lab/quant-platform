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

# Validate the candidate before moving the operational checkout, in an
# isolated detached worktree -- a non-destructive, Git-only mechanism that
# never touches $ROOT. Only once this passes does the real checkout advance.
validate_dir=$(mktemp -d) || fail "could not create a temporary directory for candidate validation"
cleanup_validate_dir() {
  git worktree remove --force "$validate_dir" >/dev/null 2>&1 || rm -rf "$validate_dir"
}
trap cleanup_validate_dir EXIT

git worktree add --detach "$validate_dir" "$target" >/dev/null 2>&1 || \
  fail "could not create a validation worktree for $target"

echo "validating_candidate: $target (isolated worktree: $validate_dir)"
if ! (cd "$validate_dir" && python3 tools/run_tests.py); then
  echo "promote-main: FATAL: candidate $target failed validation in an isolated worktree." >&2
  echo "  operational checkout was NOT moved." >&2
  echo "  previous_sha (still checked out) = $sha" >&2
  echo "  target_sha (rejected candidate)  = $target" >&2
  echo "  next: inspect the candidate manually, e.g.:" >&2
  echo "    git worktree add --detach /tmp/promote-inspect $target" >&2
  echo "    cd /tmp/promote-inspect && python3 tools/run_tests.py" >&2
  exit 1
fi

cleanup_validate_dir
trap - EXIT

# Candidate validated; advance to the exact OID already validated above, not
# to the mutable origin/main ref -- a remote-tracking ref can still be moved
# by another process/operator/hook between validation and this merge even
# with no second fetch in this script, which would promote an unvalidated
# commit if we re-resolved the ref here instead of pinning to $target.
git merge --ff-only "$target"

echo "post_promotion_identity:"
python3 tools/repo_identity.py || {
  echo "promote-main: repository promoted but identity reporting failed; inspect manually" >&2
  exit 1
}
