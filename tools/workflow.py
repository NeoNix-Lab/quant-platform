#!/usr/bin/env python3
"""Canonical workflow helper for quant-platform.

Automates the Staged Integration Branch strategy:
  main -> implement/<name> -> agent/issue-<num>-<slug> -> PR into implement/<name> -> main

The integration branch is any ``implement/<name>`` (``implement/wave-7``,
``implement/omega``, ...). It is detected from the current HEAD's ancestry;
pass ``--base`` to override.

Commands:
  python tools/workflow.py status
  python tools/workflow.py start <issue_number> [--base <branch>]
  python tools/workflow.py preflight [--base <branch>]
  python tools/workflow.py pr [--draft] [--title <title>] [--base <branch>]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

PROTECTED_GOVERNANCE_FILES = {
    "SCOPE.md",
    "docs/product/ROADMAP.md",
    "docs/product/CAPABILITY_MAP.md",
    "docs/product/CAPABILITY_DAG.md",
    "docs/architecture/OPEN_DECISIONS.md",
}


def run_cmd(cmd: list[str], check: bool = False, capture: bool = True) -> subprocess.CompletedProcess:
    """Run an argv list command without a shell (#240): every caller already
    passes a list; this signature forces any future call site to keep doing
    so, rather than silently reintroducing shell string-interpretation."""
    return subprocess.run(cmd, capture_output=capture, text=True, cwd=REPO_ROOT, check=check)


def get_current_branch() -> str:
    res = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return res.stdout.strip()


INTEGRATION_BRANCH_PATTERN = re.compile(r"(?:remotes/origin/)?(implement/[A-Za-z0-9][A-Za-z0-9._-]*)")


def parse_integration_branches(branch_listing: str) -> list[str]:
    """Return the sorted unique ``implement/<name>`` branches in ``git branch -a`` output."""
    names: set[str] = set()
    for line in branch_listing.splitlines():
        cleaned = line.strip().lstrip("*+").strip()
        match = INTEGRATION_BRANCH_PATTERN.fullmatch(cleaned)
        if match:
            names.add(match.group(1))
    return sorted(names)


def choose_integration_branch(
    candidates: list[str],
    distance: dict[str, int | None],
    recency: dict[str, int],
) -> str:
    """Pick the integration branch the current HEAD was cut from.

    Closest by ancestry first (fewest commits on HEAD since the merge-base; a
    branch with no merge-base ranks last), then the most recently updated tip,
    then the name. ``main`` when there is no candidate.
    """
    if not candidates:
        return "main"

    def rank(name: str) -> tuple[bool, int, int, str]:
        steps = distance.get(name)
        return (steps is None, steps or 0, -recency.get(name, 0), name)

    return min(candidates, key=rank)


def resolve_ref(name: str) -> str | None:
    """Return the ref to compare against for a branch name.

    Both the local branch and ``origin/<name>`` may exist and either can be
    stale (a local ``main`` that was never fast-forwarded makes every
    governance file that landed since look like this branch's change). When
    both exist, use the one HEAD descends from most closely; ``origin/<name>``
    wins ties.
    """
    existing = [
        ref
        for ref in (f"origin/{name}", name)
        if run_cmd(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"]).returncode == 0
    ]
    if not existing:
        return None

    def commits_ahead(ref: str) -> int:
        count = run_cmd(["git", "rev-list", "--count", f"{ref}..HEAD"]).stdout.strip()
        return int(count) if count.isdigit() else 0

    return min(existing, key=commits_ahead)


def detect_active_integration_branch() -> str:
    """Detect the ``implement/<name>`` integration branch the current HEAD was cut from."""
    candidates = parse_integration_branches(run_cmd(["git", "branch", "-a"]).stdout)
    distance: dict[str, int | None] = {}
    recency: dict[str, int] = {}
    for name in candidates:
        ref = resolve_ref(name)
        if ref is None:
            continue
        stamp = run_cmd(["git", "log", "-1", "--format=%ct", ref]).stdout.strip()
        recency[name] = int(stamp) if stamp.isdigit() else 0
        merge_base = run_cmd(["git", "merge-base", ref, "HEAD"])
        if merge_base.returncode != 0 or not merge_base.stdout.strip():
            distance[name] = None
            continue
        count = run_cmd(["git", "rev-list", "--count", f"{merge_base.stdout.strip()}..HEAD"]).stdout.strip()
        distance[name] = int(count) if count.isdigit() else None
    return choose_integration_branch([name for name in candidates if name in recency], distance, recency)


def governance_violations(current_branch: str, base: str | None = None) -> set[str]:
    """Protected governance files this branch changed since it left its base.

    ``base`` defaults to the detected integration branch on an issue branch and
    to ``main`` otherwise.
    """
    if not base:
        base = detect_active_integration_branch() if re.search(r"issue-\d+", current_branch) else "main"
    ref = resolve_ref(base) or base

    # Compute merge-base so we only inspect commits introduced on this branch
    mb_res = run_cmd(["git", "merge-base", ref, "HEAD"])
    mb = mb_res.stdout.strip() if mb_res.returncode == 0 and mb_res.stdout.strip() else ref

    diff_res = run_cmd(["git", "diff", "--name-only", f"{mb}...HEAD"])
    if diff_res.returncode != 0:
        diff_res = run_cmd(["git", "diff", "--name-only", "HEAD~1"])

    changed = {line.strip().replace("\\", "/") for line in diff_res.stdout.splitlines() if line.strip()}
    return changed.intersection(PROTECTED_GOVERNANCE_FILES)


def slugify(text: str) -> str:
    """Generate a clean slug for branch names."""
    # Remove tags like [agent], [Epic], (ADR-xxx)
    text = re.sub(r'\[.*?\]', '', text)
    text = re.sub(r'\(.*?\)', '', text)
    text = re.sub(r'—|–', '-', text)
    # Remove non-alphanumeric except dashes
    text = re.sub(r'[^a-zA-Z0-9\s-]', '', text).strip().lower()
    # Replace spaces with single dash
    text = re.sub(r'[\s_-]+', '-', text).strip('-')
    words = text.split('-')[:4]
    return '-'.join(words)


def cmd_status(args: argparse.Namespace) -> int:
    current = get_current_branch()
    integration_base = detect_active_integration_branch()
    print("=" * 60)
    print("Quant Platform Workflow Status")
    print("=" * 60)
    print(f"Current Branch           : {current}")
    print(f"Active Integration Base  : {integration_base}")

    # Check if branch is an issue branch
    m = re.search(r'issue-(\d+)', current)
    if m:
        issue_num = m.group(1)
        print(f"Associated Issue         : #{issue_num}")
        # Fetch issue info via gh CLI
        res = run_cmd(["gh", "issue", "view", issue_num, "--json", "title,state,milestone"])
        if res.returncode == 0:
            try:
                data = json.loads(res.stdout)
                ms = data.get("milestone", {}).get("title") if data.get("milestone") else "None"
                print(f"Issue Title              : {data.get('title')}")
                print(f"Issue State              : {data.get('state')} (Milestone: {ms})")
            except Exception:
                pass
    print("=" * 60)
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    print("[PREFLIGHT] Running verification checks...")
    failed = False

    # 1. Syntax check
    print("  1/4 Checking Python syntax (compileall)...", end=" ", flush=True)
    res = run_cmd(["python", "-m", "compileall", "-q", "src", "tests", "tools"])
    if res.returncode == 0:
        print("OK")
    else:
        print("FAIL")
        print(res.stderr or res.stdout)
        failed = True

    # 2. git diff check
    print("  2/4 Checking git diff whitespace & conflicts...", end=" ", flush=True)
    res = run_cmd(["git", "diff", "--check"])
    if res.returncode == 0:
        print("OK")
    else:
        print("FAIL")
        print(res.stderr or res.stdout)
        failed = True

    # 3. Package boundaries
    print("  3/4 Checking package boundaries...", end=" ", flush=True)
    res = run_cmd(["python", "tests/test_package_boundaries_v1.py"])
    if res.returncode == 0:
        print("OK")
    else:
        print("FAIL")
        print(res.stderr or res.stdout)
        failed = True

    # 4. Governance boundary check per AGENTS.md
    print("  4/4 Checking governance boundary rules...", end=" ", flush=True)
    current_branch = get_current_branch()
    if current_branch in ("main", "master") or current_branch.startswith("governance/") or current_branch.startswith("admin/"):
        print(f"OK ({current_branch} branch)")
        return 0

    violated = governance_violations(current_branch, getattr(args, "base", "") or None)
    if violated:
        print("FAIL")
        print(f"      [VIOLATION] Branch '{current_branch}' modified protected governance files:")
        for f in sorted(violated):
            print(f"        - {f}")
        print("      Per AGENTS.md, ordinary implementation branches must not mutate governance files.")
        failed = True
    else:
        print("OK")

    if failed:
        print("[PREFLIGHT] FAILED: Please fix errors before pushing or creating a PR.")
        return 1

    print("[PREFLIGHT] ALL CHECKS PASSED.")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    issue_num = str(args.issue_number)
    base_branch = args.base or detect_active_integration_branch()

    print(f"Fetching issue #{issue_num} from GitHub...")
    res = run_cmd(["gh", "issue", "view", issue_num, "--json", "number,title,milestone"])
    if res.returncode != 0:
        print(f"Error fetching issue #{issue_num}: {res.stderr}", file=sys.stderr)
        return 1

    issue_data = json.loads(res.stdout)
    title = issue_data["title"]
    slug = slugify(title)
    branch_name = f"agent/issue-{issue_num}-{slug}"

    print(f"Issue Title : {title}")
    print(f"Target Base : {base_branch}")
    print(f"Branch Name : {branch_name}")

    # Ensure base branch is fetched and up to date
    print(f"Fetching base branch '{base_branch}'...")
    run_cmd(["git", "fetch", "origin", f"{base_branch}:{base_branch}"])

    # Check if branch exists
    check_local = run_cmd(["git", "rev-parse", "--verify", branch_name])
    if check_local.returncode == 0:
        print(f"Branch '{branch_name}' already exists. Switching to it...")
        run_cmd(["git", "checkout", branch_name], check=True)
    else:
        print(f"Creating '{branch_name}' from '{base_branch}'...")
        run_cmd(["git", "checkout", "-b", branch_name, base_branch], check=True)

    print(f"\n[OK] Switched to branch '{branch_name}'.")
    print("Ready for implementation. When finished, run:\n  python tools/workflow.py pr")
    return 0


def cmd_pr(args: argparse.Namespace) -> int:
    current_branch = get_current_branch()
    if current_branch in ("main", "master"):
        print("[ERROR] Cannot open a PR from 'main'. Please work on a feature branch.", file=sys.stderr)
        return 1

    # Extract issue number
    m = re.search(r'issue-(\d+)', current_branch)
    if not m:
        print(f"[ERROR] Branch '{current_branch}' does not contain an issue number (expected format: agent/issue-<num>-<slug>).", file=sys.stderr)
        return 1

    issue_num = m.group(1)
    base_branch = args.base or detect_active_integration_branch()

    print("=" * 60)
    print(f"Preparing Pull Request for Issue #{issue_num}")
    print(f"Head branch : {current_branch}")
    print(f"Base branch : {base_branch}")
    print("=" * 60)

    # 1. Run preflight checks
    if cmd_preflight(args) != 0:
        print("\n[ERROR] Preflight checks failed. Aborting PR creation.", file=sys.stderr)
        return 1

    # 2. Fetch issue metadata
    res = run_cmd(["gh", "issue", "view", issue_num, "--json", "number,title,milestone"])
    if res.returncode != 0:
        print(f"[ERROR] Could not fetch issue #{issue_num}: {res.stderr}", file=sys.stderr)
        return 1

    issue_data = json.loads(res.stdout)
    title = args.title or issue_data.get("title", f"Implement issue #{issue_num}")

    # 3. Push branch to origin
    print(f"Pushing branch '{current_branch}' to origin...")
    push_res = run_cmd(["git", "push", "-u", "origin", current_branch])
    if push_res.returncode != 0:
        print(f"[ERROR] Failed to push branch: {push_res.stderr}", file=sys.stderr)
        return 1

    # 4. Compose PR body
    body = f"""## Summary
- Implements issue #{issue_num}: {title}
- Linked Issue: Closes #{issue_num}
- Target Integration Branch: `{base_branch}`

## Verification
- `python -m compileall -q src tests tools` (PASS)
- `git diff --check` (PASS)
- `python tests/test_package_boundaries_v1.py` (PASS)

## Governance Note
- Standard implementation slice; no planning or governance authority files mutated.
"""

    # 5. Create PR
    cmd = [
        "gh", "pr", "create",
        "--base", base_branch,
        "--head", current_branch,
        "--title", title,
        "--body", body,
    ]
    if args.draft:
        cmd.append("--draft")

    print(f"Creating Pull Request targeting '{base_branch}'...")
    pr_res = run_cmd(cmd)
    if pr_res.returncode != 0:
        print(f"[ERROR] Failed to create PR: {pr_res.stderr}", file=sys.stderr)
        return 1

    pr_url = pr_res.stdout.strip()
    print("\n" + "=" * 60)
    print("[SUCCESS] Pull Request created successfully:")
    print(f"  {pr_url}")
    print("=" * 60)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Quant Platform canonical workflow helper.")
    subparsers = parser.add_subparsers(dest="command", help="Workflow command")

    # status
    subparsers.add_parser("status", help="Show current workflow, branch, and active base status")

    # preflight
    p_preflight = subparsers.add_parser("preflight", help="Run local preflight checks (syntax, boundaries, diff, governance)")
    p_preflight.add_argument("--base", type=str, default="", help="Base branch for the governance check (default: detected implement/*)")

    # start
    p_start = subparsers.add_parser("start", help="Start work on an issue (creates branch from active integration base)")
    p_start.add_argument("issue_number", type=int, help="Issue number to work on")
    p_start.add_argument("--base", type=str, default="", help="Override target base branch (default: active implement/*)")

    # pr
    p_pr = subparsers.add_parser("pr", help="Run preflight, push branch, and open PR targeting active integration base")
    p_pr.add_argument("--base", type=str, default="", help="Override target base branch (default: active implement/*)")
    p_pr.add_argument("--title", type=str, default="", help="Custom PR title (default: issue title)")
    p_pr.add_argument("--draft", action="store_true", help="Open PR as draft")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0

    if args.command == "status":
        return cmd_status(args)
    elif args.command == "preflight":
        return cmd_preflight(args)
    elif args.command == "start":
        return cmd_start(args)
    elif args.command == "pr":
        return cmd_pr(args)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
