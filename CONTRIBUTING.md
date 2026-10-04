# Contributing to Quant Platform

Thank you for your interest in `quant-platform`. This repository is a source-available, highly disciplined quantitative research and execution codebase. Contributions must adhere to the architectural rules, frozen contracts, and workflow invariants outlined in [`AGENTS.md`](AGENTS.md) and [`docs/architecture/TARGET_ARCHITECTURE.md`](docs/architecture/TARGET_ARCHITECTURE.md).

---

## Branching & Staged Integration Strategy

The repository follows a strict staged integration workflow:

1. **Macro Waves**:
   - Major development cycles integrate into a dedicated integration branch `implement/<wave>` (e.g. `implement/wave-4`), branched from `main`.
2. **Atomic Slices**:
   - Individual issues are developed on dedicated feature branches named `agent/issue-<num>-<slug>` (or `codex/issue-<num>-<slug>`), branched from the active `implement/<wave>`.
3. **Pull Request Targeting**:
   - Pull requests for atomic slices **MUST target `implement/<wave>`**, NOT `main`.
4. **Workflow Automation**:
   - Use `python tools/workflow.py start <issue>` to start work on an assigned issue.
   - Use `python tools/workflow.py pr` to run preflight and open/target the PR against the active integration base.
5. **Wave Promotion**:
   - Only the final wave closeout PR promotes `implement/<wave>` into `main` after golden acceptance and governance reconciliation.

---

## Non-Negotiable Engineering Rules

- **Zero Secrets & Bulk Data**:
  - Never commit API keys, `.env` files, database passwords, or credentials.
  - Do not commit bulk market data, Parquet files, SQLite/PostgreSQL databases, or model binaries to Git.
- **Explicit Git Staging**:
  - **Never use `git add .` or `git add -A`**. Stage explicit file paths only (`git add <path>`).
- **Precision Monetary Arithmetic**:
  - All currency balances, prices, quantities, sizing calculations, fee schedules, and PnL must use `Decimal`. Floating-point arithmetic (`float`) is strictly prohibited in financial paths.
- **Temporal Causality & Monotonicity**:
  - Respect the temporal chain across all event processing:
    $$t_{\text{event}} \le t_{\text{decision}} \le t_{\text{order}} \le t_{\text{fill}} \le t_{\text{ledger}}$$
  - Features must strictly satisfy availability floors ($t_{\text{available}} \le t_{\text{decision}}$).
- **DataGateway Boundary**:
  - Upper layers (Strategy, Execution, Portfolio, Replay) must consume data exclusively through `DataGateway.scan()` and `DataGateway.read()`. Never open storage files or catalog tables directly (ADR-0019).
- **Package Boundaries**:
  - Respect the modular monolith package boundaries enforced by `tests/test_package_boundaries_v1.py` (ADR-0024).

---

## Governance Boundary

Implementation agents and contributors are **not authorized to mutate repository governance files** unless explicitly designated as a governance-only task.

**Do not edit**:
- `SCOPE.md`
- `docs/product/ROADMAP.md`
- `docs/product/CAPABILITY_MAP.md`
- `docs/product/CAPABILITY_DAG.md`
- `docs/architecture/OPEN_DECISIONS.md`
- Derived planning metrics, frontier projections, or capability counts.

If implementation reveals that governance documentation is stale, report the finding in the Pull Request description and leave governance files untouched.

---

## Preflight Verification Checklist

Before submitting a Pull Request, verify that all local checks pass:

```bash
# 1. Preflight integrity gate (syntax, whitespace, boundaries, governance)
python tools/workflow.py preflight

# 2. Complete repository unit test suite
python tools/run_tests.py

# 3. Markdown reference and link validation
python tools/check_markdown_links.py

# 4. Lint baseline (requires `pip install -e ".[dev]"`)
ruff check src tools

# 5. Whitespace and merge conflict markers check
git diff --check origin/<integration-branch>...HEAD
```

All GitHub Actions CI checks must be green before a Pull Request is eligible for review and merge.

### Lint and type baseline

The lint gate is deliberately small and explicit: `ruff check src tools` with the rule families
selected in `pyproject.toml` (`E4`, `E7`, `E9`, `F`). The count is **zero** and CI keeps it there.
ruff's built-in default rule set changes between releases, so it is not used as a gate and the
ruff version is bounded in `pyproject.toml`.

Baselines recorded when the gate was introduced (not enforced; they may only go down):

| Check | Command | Baseline |
|---|---|---|
| Same ruff rule set on tests | `ruff check tests` | 150 findings (mostly `E702`, `E402`) |
| Type check | `python -m mypy src --ignore-missing-imports` | 303 errors in 37 files |

Ratchet plan: widen `select` one rule family at a time, only after its count in `src tools` is zero;
land the mechanical auto-fixes for a family as their own behavior-neutral change. Type-checking is
not in CI yet; it needs its own slice because most findings are in proof code and test doubles
shipped inside `src` (see the golden proof-code boundary audit).

---

## Server Promotion & Deployment

Server promotion is explicit, clean-tree guarded, and fast-forward-only:
- Inspect incoming commits: `git fetch origin`.
- Fast-forward merge: `git merge --ff-only origin/main`.
- See [Repository Synchronization](docs/engineering/REPOSITORY_SYNC.md) for the full operational sync and server authority policies.
