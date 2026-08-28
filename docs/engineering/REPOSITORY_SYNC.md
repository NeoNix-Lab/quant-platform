# Repository Synchronization & Integrity

**Status:** Foundation v1 policy

This document governs versioned code synchronization and repository integrity.
It does not define replication of canonical market data.

## Authority model

| Environment | Authority and responsibility |
|---|---|
| GitHub `origin` | Authoritative versioned software, contracts, schemas, migrations, fixtures, reviewed source and Git history. It is not the authority for canonical market-data contents. |
| Desktop | Primary development workstation and source of review-ready branches. |
| Server | Operational canonical data-plane and PostgreSQL/runtime environment; a working copy of versioned code. |

Desktop and server are not competing repositories. The exact deployed source
revision must always be knowable.

## Normal development and promotion flow

```text
feature branch on desktop
        -> local validation
        -> push review-ready branch to GitHub
        -> independent review
        -> merge/fast-forward into main
        -> main CI green
        -> explicit server promotion
```

Review-ready branches should be pushed to GitHub rather than existing only on
one workstation. `main` means reviewed, coherent and green on deterministic /
lightweight checks, and suitable for controlled server integration. It does
not mean live-trading production certification.

The normal server repository state tracks `main`. A feature branch may be
checked out on the server only for an explicitly authorized integration
experiment and must not become silent permanent server state.

## Code sync versus data sync

Code moves between machines through Git history: fetch, review, merge and
explicit fast-forward promotion. Do not use rsync, SMB/network-copy mirroring,
zip/manual copies or arbitrary desktop-to-server overwrites as normal
synchronization.

This is separate from data/runtime state, which includes canonical Parquet,
PostgreSQL catalog state, bulk SQLite source data, model artifacts, caches and
large experiment outputs. Those are not synchronized through Git in this
policy, and this document does not design their replication or backup.

## Safe server update procedure

Run the repository-owned `infra/promote-main.sh` directly on the server, or
follow this equivalent guarded procedure:

1. Confirm the expected repository path and Git remote.
2. Inspect the current branch and exact SHA.
3. Require a clean working tree; a dirty server is a stop condition.
4. Fetch `origin` explicitly.
5. Inspect current and incoming commits and confirm the intended reviewed
   `origin/main` target.
6. Require the target to descend from the current SHA.
7. Run `git merge --ff-only origin/main`.
8. Run designated lightweight/server smoke validation.
9. Report the final branch, exact SHA, tag and clean/dirty state.

The helper never stashes, resets, cleans, force-checks out, deletes files,
auto-pulls, runs periodically, deploys automatically or alters bulk market
data. It refuses non-fast-forward updates. Use `--inspect-only` to report the
state without requiring `main` or changing the repository.

## Repository identity

`python tools/repo_identity.py` reports the repository root, branch, exact Git
SHA, exact/nearest tag when available, and dirty/clean state. The same facts
are printed by the server promotion helper. These outputs are intended to be
reusable as later provenance inputs; no deployment database or release service
is introduced.

## Test tiers

### Tier 1 — GitHub / lightweight deterministic

Runs without private mounts or bulk historical data:

- `python tools/run_tests.py`;
- Markdown local-link and repository-path validation;
- schema and semantic fixture validation through the local suite;
- disposable PostgreSQL catalog DDL validation when the workflow job runs;
- future pure contract tests.

### Tier 2 — server integration

Explicitly invoked on the server and allowed to use canonical infrastructure:

- actual PostgreSQL/catalog integration;
- canonical filesystem/storage roots and real Parquet reads;
- the Bybit real-data integration test;
- future DataGateway integration and catalog/filesystem consistency checks;
- physical relocation semantics.

### Tier 3 — heavy/certification

Not run on every commit:

- full catalog rebuild and bulk content verification;
- large historical equivalence and full replay regressions;
- large backtests and future ML/RL, paper or live certification.

No nightly schedule is assumed here.

## Current real-data integration

`tests/integration_bybit_trades_2024_01_15.py` is a Tier 2 server/real-data
integration test. It requires the external historical Bybit SQLite database
passed through its CLI argument. The large SQLite source is not placed in
GitHub CI, and the small semantic fixtures remain the Tier 1 reference.

`tests/test_catalog_ddl.sql` is a disposable PostgreSQL catalog validation
test. The GitHub workflow initializes it using the repository's
`db/init/001_catalog.sql` and `db/init/002_roles.sh`, then runs the test against
the disposable PostgreSQL 17 service.

## Emergency server-side code fix

If an emergency server-side code fix is unavoidable, it follows:

```text
server branch -> commit -> push to GitHub -> review -> merge to main
```

No durable code fix may remain server-only. Direct commits to `main` on the
server are not the normal path.

## Integrity contract

Machine-verifiable checks should validate claims such as local Markdown links,
referenced schemas and repository paths, and the existence of canonical test
commands. Checks must not depend on exact prose wording. CI status is reported
as pending until GitHub has actually executed the workflow; a local pass is not
presented as CI green.
