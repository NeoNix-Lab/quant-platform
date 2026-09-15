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

## Governance mutation boundary

Governance documents are cumulative authority/current-state projections, not per-task scratchpads.

An implementation agent MAY mutate governance only when the change is a direct, mechanically supported projection of evidence produced or credited by its issue. Allowed examples are:

- mark the issue's own capability decision/implementation state to the exact level proven;
- add/index the accepted ADR or contract produced by the issue;
- update an already-defined blocker/unblock statement when the issue's proven result directly changes it;
- recompute derived planning counts from the actual repository state after those bounded changes;
- correct a stale fact for a directly affected prerequisite only when the issue explicitly authorizes reconciliation and merged evidence proves the correction.

An implementation agent MUST NOT, unless the issue explicitly grants that authority:

- rewrite `SCOPE.md`, roadmap, capability map/DAG or open-decision sections as a task-specific narrative;
- delete or weaken accepted historical foundations, gate states, invariants, ADR references or cumulative governance evidence;
- change capability ownership, dependency edges, priorities, architecture direction, accepted semantics or another capability's state;
- perform opportunistic governance cleanup/reconciliation outside the issue's directly affected surface.

`SCOPE.md` is cumulative project scope/state. When it must change, patch only the minimum current-state facts required by the issue and preserve accepted historical invariants (including completed conformity gates). Keep task-specific objective/scope/verification detail in the issue/PR rather than replacing cumulative project scope with it.

If a correct implementation appears to require governance mutation beyond this boundary, stop and report the exact inconsistency/proposed change instead of making it. Prefer reporting an unrelated stale fact as a finding over fixing it speculatively.

## Legacy adoption

For each candidate, identify the target contract, inspect implementation and tests, record semantic matches and mismatches, choose ADOPT/ADAPT/REVIEW/REJECT, and add canonical semantic and temporal tests.
