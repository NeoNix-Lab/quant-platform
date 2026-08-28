#!/usr/bin/env python3
"""Validate local Markdown links without requiring third-party tooling."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def main() -> int:
    failures = []
    files = sorted(
        path
        for path in ROOT.rglob("*.md")
        if ".git" not in path.parts
        and ".venv" not in path.parts
        and ".pytest_cache" not in path.parts
    )
    for source in files:
        text = source.read_text(encoding="utf-8")
        for match in LINK.finditer(text):
            target = match.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            path_text = target.split("#", 1)[0]
            if not path_text:
                continue
            target_path = (source.parent / path_text).resolve()
            try:
                target_path.relative_to(ROOT.resolve())
            except ValueError:
                failures.append(f"{source}: outside repository: {target}")
                continue
            if not target_path.exists():
                failures.append(f"{source}: missing target: {target}")
    if failures:
        for failure in failures:
            print(failure, file=sys.stderr)
        return 1
    print(f"Markdown links: PASS ({len(files)} files checked)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
