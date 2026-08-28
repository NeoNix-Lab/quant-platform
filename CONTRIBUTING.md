# Contributing

This is a private proprietary project. Work in a dedicated `codex/` branch or explicit worktree, keep one writer per capability, and document architectural changes before implementation. Preserve canonical contracts and tests, keep bulk data and secrets out of Git, and treat `ml_core` as read-only reference material.

Run the repository's existing tests before review. Stage explicit files only; do not use `git add .` or `git add -A`. Capability adoption requires a target contract, semantic comparison, tests and an adoption-ledger entry.

## Repository flow

Develop on a dedicated feature branch, run local validation, and push a
review-ready branch to GitHub. After independent review, integrate to `main`
with a linear fast-forward where possible and wait for `main` CI to pass before
explicit server promotion.

The normal server update is: verify the expected repository, branch, SHA and
clean tree; `git fetch origin`; inspect incoming commits; then run
`git merge --ff-only origin/main` and report the resulting SHA. Do not use blind
`git pull`, automatic deployment, repository file-copy mirroring, stash, reset,
or clean automation. A dirty server is a stop condition requiring human
inspection.

Use `python tools/repo_identity.py` to report branch, exact SHA, tag and
dirty/clean state. See [Repository Synchronization](docs/engineering/REPOSITORY_SYNC.md)
for the full authority, test-tier and server-promotion policy.
