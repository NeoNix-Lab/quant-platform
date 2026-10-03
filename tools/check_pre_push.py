"""Pre-push verification hook script.

Enforces:
1. Direct push to `main` branch is forbidden (all code must arrive via PR).
2. Code syntax integrity (python -m compileall).
3. Clean git diff (no whitespace/conflict markers).
4. Governance boundary check per AGENTS.md:
   Implementation branches must not mutate SCOPE.md, ROADMAP.md,
   CAPABILITY_DAG.md, CAPABILITY_MAP.md, or OPEN_DECISIONS.md.
"""

import os
import re
import sys
import subprocess
from pathlib import Path

# Reuse the same integration-base detection the staged-integration workflow
# helper already uses, so the two never drift again (see the incident this
# fix addresses: this hook always compared against origin/main, so any
# atomic-slice branch built on an active implement/<wave> branch was flagged
# as violating the governance boundary purely because that wave's own
# SCOPE.md legitimately differs from main's -- content the branch never
# touched itself).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow import detect_active_integration_branch  # noqa: E402

PROTECTED_GOVERNANCE_FILES = {
    "SCOPE.md",
    "docs/product/ROADMAP.md",
    "docs/product/CAPABILITY_MAP.md",
    "docs/product/CAPABILITY_DAG.md",
    "docs/architecture/OPEN_DECISIONS.md",
}


def run_command(cmd: list[str]) -> tuple[int, str, str]:
    res = subprocess.run(cmd, capture_output=True, text=True)
    return res.returncode, res.stdout, res.stderr


def check_direct_push_to_main(stdin_lines):
    allow_direct = os.environ.get("ALLOW_DIRECT_MAIN_PUSH", "0") == "1"
    for line in stdin_lines:
        parts = line.strip().split()
        if len(parts) >= 4:
            local_ref, local_sha, remote_ref, remote_sha = parts[:4]
            if remote_ref == "refs/heads/main":
                if not allow_direct:
                    print("=" * 72, file=sys.stderr)
                    print("[BLOCKED] Direct push to 'main' is forbidden by repository policy!", file=sys.stderr)
                    print("All changes must be submitted via a feature branch and Pull Request:", file=sys.stderr)
                    print("  1. git checkout -b <branch-name>", file=sys.stderr)
                    print("  2. git push -u origin <branch-name>", file=sys.stderr)
                    print("  3. gh pr create", file=sys.stderr)
                    print("=" * 72, file=sys.stderr)
                    return False
    return True


def check_syntax_and_formatting():
    # 1. compileall
    code, out, err = run_command(["python", "-m", "compileall", "-q", "src", "tests", "tools"])
    if code != 0:
        print("[PRE-PUSH ERROR] Python syntax check failed:", file=sys.stderr)
        print(err or out, file=sys.stderr)
        return False

    # 2. git diff --check
    code, out, err = run_command(["git", "diff", "--check"])
    if code != 0:
        print("[PRE-PUSH ERROR] git diff --check failed (whitespace/conflict markers):", file=sys.stderr)
        print(err or out, file=sys.stderr)
        return False

    return True


def check_governance_boundary():
    # Get current branch
    code, branch, _ = run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = branch.strip()

    # Governance branches are allowed to edit governance files
    if branch.startswith("governance/") or branch.startswith("admin/"):
        return True

    # Atomic-slice branches (agent/issue-N-... or codex/issue-N-...) are
    # branched from an active implement/<wave> integration branch, not from
    # main (AGENTS.md's Branching & Staged Integration Strategy) -- comparing
    # them against main would always show that wave's own SCOPE.md/etc. as
    # "changed", even though this branch never touched it. Compare against
    # the active integration base instead; only a plain chore/feature branch
    # off main should still be compared against main.
    if re.search(r"issue-\d+", branch):
        base = detect_active_integration_branch()
    else:
        base = "main"

    merge_base_code, merge_base_out, _ = run_command(["git", "merge-base", base, "HEAD"])
    diff_base = merge_base_out.strip() if merge_base_code == 0 and merge_base_out.strip() else base

    # Check changed files against the resolved base, falling back to HEAD~1
    # only if that ref can't be resolved at all (e.g. base not fetched).
    code, out, _ = run_command(["git", "diff", "--name-only", f"{diff_base}...HEAD"])
    if code != 0:
        code, out, _ = run_command(["git", "diff", "--name-only", "HEAD~1"])

    changed_files = {line.strip().replace("\\", "/") for line in out.splitlines() if line.strip()}
    mutated_protected = changed_files.intersection(PROTECTED_GOVERNANCE_FILES)

    if mutated_protected:
        print("=" * 72, file=sys.stderr)
        print("[PRE-PUSH ERROR] AGENTS.md Governance Boundary Violation!", file=sys.stderr)
        print(f"Implementation branch '{branch}' modified protected governance files:", file=sys.stderr)
        for f in sorted(mutated_protected):
            print(f"  - {f}", file=sys.stderr)
        print("\nPer AGENTS.md, ordinary implementation branches must NOT mutate governance files.", file=sys.stderr)
        print("Only an explicitly designated governance branch may update them.", file=sys.stderr)
        print("Please revert these files before pushing:", file=sys.stderr)
        print(f"  git checkout origin/{base} -- {' '.join(sorted(mutated_protected))}", file=sys.stderr)
        print("=" * 72, file=sys.stderr)
        return False

    return True


def main():
    # Read stdin lines passed by git pre-push
    stdin_lines = sys.stdin.readlines()

    # Check 1: Block direct push to main
    if not check_direct_push_to_main(stdin_lines):
        sys.exit(1)

    # Check 2: Syntax and formatting
    if not check_syntax_and_formatting():
        sys.exit(1)

    # Check 3: Governance boundary
    if not check_governance_boundary():
        sys.exit(1)

    print("[PRE-PUSH] All checks passed cleanly.")
    sys.exit(0)


if __name__ == "__main__":
    main()
