#!/usr/bin/env python3
"""Run the repository's executable local Python test scripts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    test_dir = root / "tests"
    tests = sorted(test_dir.glob("test_*.py"), key=lambda path: path.name)

    if not tests:
        print("LOCAL PYTHON TEST SUITE: discovered=0 passed=0 failed=0")
        return 1

    passed = 0
    failed = 0
    for test in tests:
        relative = test.relative_to(root)
        print(f"=== {relative} ===", flush=True)
        result = subprocess.run([sys.executable, str(test)], cwd=root)
        if result.returncode == 0:
            passed += 1
            print(f"PASS {relative}", flush=True)
        else:
            failed += 1
            print(f"FAIL {relative} (exit {result.returncode})", flush=True)

    print(
        f"LOCAL PYTHON TEST SUITE: discovered={len(tests)} "
        f"passed={passed} failed={failed}"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
