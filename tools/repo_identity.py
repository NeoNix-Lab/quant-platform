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


def main() -> int:
    try:
        branch = git("symbolic-ref", "--short", "-q", "HEAD") or "DETACHED"
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
