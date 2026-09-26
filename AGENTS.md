# AGENTS.md

The `quant-platform` repository is the authoritative product codebase. Before architectural or domain-semantic work, read `README.md`, `SCOPE.md`, `docs/product/PRODUCT.md`, `docs/product/CAPABILITY_MAP.md`, `docs/product/ROADMAP.md`, `docs/architecture/TARGET_ARCHITECTURE.md`, `docs/contracts/CORE_CONTRACTS.md` and applicable ADRs.

## Rules

- Treat `ml_core` as read-only evidence; never copy modules wholesale.
- Do not create a parallel canonical architecture or silently reinterpret a contract version.
- Preserve frozen data-plane contracts and fixtures unless an explicit contract-evolution ADR authorizes change.
- Keep temporal availability, identity and provenance explicit.
- Keep quantitative business logic out of App UI, TUI and CLI clients.
- Upper layers must not bypass the future DataGateway to open canonical storage directly.
- Do not add bulk market data, databases, experiment artifacts or secrets to Git.
- GitHub `origin` is the authority for versioned code, contracts, schemas,
  fixtures and reviewed history. Server state is operational data/runtime
  state, not a competing source repository.
- Move code between machines through Git history only; do not mirror the
  repository with rsync, SMB/network copies, zips or arbitrary overwrites.
- Server promotion is explicit, clean-tree guarded and fast-forward-only. Do
  not automate stash, reset, clean, pull-on-push or deployment.
- Do not use `git add .` or `git add -A`; stage explicit paths only.
- If code conflicts with an accepted ADR or contract, stop and report the conflict.

## Branching & Staged Integration Strategy

- **Macro Waves**: Implementation cycles use a dedicated integration branch `implement/<wave>` (e.g. `implement/wave-4`) branched from `main`.
- **Atomic Slices**: Individual issues use branch name `agent/issue-<num>-<slug>` (or `codex/issue-<num>-<slug>`) branched from the active `implement/<wave>`.
- **PR Targeting**: Pull requests for atomic slices MUST target `implement/<wave>`, NOT `main`.
- **Workflow Automation**: Use `python tools/workflow.py start <issue>` to begin work and `python tools/workflow.py pr` to run preflight and open the PR targeting the active integration base.
- **Wave Promotion**: Only the final wave closeout PR promotes `implement/<wave>` into `main` after golden acceptance and governance reconciliation.

## Governance boundary

Implementation and semantic-materialization agents are **not responsible for repository governance reconciliation**.

They MAY read governance/planning documents as authority and context, but MUST NOT mutate them as part of ordinary implementation work. In particular, do not edit:

- `SCOPE.md`;
- `docs/product/ROADMAP.md`;
- `docs/product/CAPABILITY_MAP.md`;
- `docs/product/CAPABILITY_DAG.md`;
- `docs/architecture/OPEN_DECISIONS.md`;
- derived planning metrics, frontier lists, blocker projections, or capability-state counts.

If implementation reveals that governance is stale, inconsistent, or should change, report the exact finding in the issue/PR output and leave the governance files untouched.

Canonical semantic authority owned by the task is different from planning governance: an issue may still add or update its explicitly authorized ADR, normative contract, schema, code and tests when those are part of the task acceptance.

Only an issue explicitly designated as **governance-only / governance-reconciliation work** may authorize mutation of governance/planning files. That authority must be stated in the issue; it is never implied by implementation completion.

## Legacy adoption

For each candidate, identify the target contract, inspect implementation and tests, record semantic matches and mismatches, choose ADOPT/ADAPT/REVIEW/REJECT, and add canonical semantic and temporal tests.
