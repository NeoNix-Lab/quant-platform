"""Install repository git hooks."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS_DIR = REPO_ROOT / ".git" / "hooks"
PRE_PUSH_HOOK = HOOKS_DIR / "pre-push"

HOOK_CONTENT = """#!/bin/sh
# Auto-generated pre-push hook for quant-platform
python tools/check_pre_push.py
"""


def install():
    if not HOOKS_DIR.exists():
        print(f"Error: .git hooks directory not found at {HOOKS_DIR}", file=sys.stderr)
        sys.exit(1)

    with open(PRE_PUSH_HOOK, "w", encoding="utf-8", newline="\n") as f:
        f.write(HOOK_CONTENT)

    # Make executable on Unix-like environments if possible
    try:
        PRE_PUSH_HOOK.chmod(0o755)
    except Exception:
        pass

    print(f"[OK] Pre-push hook successfully installed at: {PRE_PUSH_HOOK}")
    print("Direct push to 'main' is now protected locally.")


if __name__ == "__main__":
    install()
