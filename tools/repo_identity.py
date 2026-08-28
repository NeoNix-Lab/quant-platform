#!/usr/bin/env python3
"""Report deterministic repository/deployment identity."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def branch_name() -> str:
    """Return the branch name, treating detached HEAD as a valid state."""
    result = subprocess.run(
        ["git", "symbolic-ref", "--short", "-q", "HEAD"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    if result.returncode == 1 and not result.stderr:
        return "DETACHED"
    raise subprocess.CalledProcessError(
        result.returncode,
        result.args,
        output=result.stdout,
        stderr=result.stderr,
    )


def main() -> int:
    try:
        branch = branch_name()
        sha = git("rev-parse", "HEAD")
        exact_tags = git("tag", "--points-at", "HEAD").splitlines()
        nearest = git("describe", "--tags", "--always", "HEAD")
        dirty = bool(git("status", "--porcelain"))
    except subprocess.CalledProcessError as exc:
        print(f"repository identity unavailable: {exc}", file=sys.stderr)
        return 1
    print(f"root={ROOT}")
    print(f"branch={branch}")
    print(f"sha={sha}")
    print(f"exact_tags={','.join(exact_tags) if exact_tags else '-'}")
    print(f"nearest_tag={nearest}")
    print(f"state={'dirty' if dirty else 'clean'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
