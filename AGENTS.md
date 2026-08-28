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
- Do not use `git add .` or `git add -A`; stage explicit paths only.
- If code conflicts with an accepted ADR or contract, stop and report the conflict.

## Legacy adoption

For each candidate, identify the target contract, inspect implementation and tests, record semantic matches and mismatches, choose ADOPT/ADAPT/REVIEW/REJECT, and add canonical semantic and temporal tests.
