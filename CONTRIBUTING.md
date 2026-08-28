# Contributing

This is a private proprietary project. Work in a dedicated `codex/` branch or explicit worktree, keep one writer per capability, and document architectural changes before implementation. Preserve canonical contracts and tests, keep bulk data and secrets out of Git, and treat `ml_core` as read-only reference material.

Run the repository's existing tests before review. Stage explicit files only; do not use `git add .` or `git add -A`. Capability adoption requires a target contract, semantic comparison, tests and an adoption-ledger entry.
