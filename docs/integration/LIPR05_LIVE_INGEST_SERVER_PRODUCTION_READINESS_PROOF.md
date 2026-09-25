# LIPR-05 - Live Ingest Server Production-Readiness Proof

- **Status:** PARTIAL - target K02 execution blocked from this Codex session by interactive sudo.
- **Issue:** #128
- **Scope:** `SCOPE.md` / Live Ingest Server Production Readiness v1 / Active Path step 6.
- **Branch:** `codex/issue-128-lipr05-production-proof`
- **Evidence date:** 2026-09-25

This document records the proof work for the bounded Bybit BTCUSDT live-ingest
server. It separates executed evidence from blocked target-server evidence so
that the project does not claim production readiness from a local or hermetic
simulation alone.

## Authority

The proof is bounded to:

- ADR-0040 - Bybit live trades acquisition v1;
- ADR-0041 - live-ingest runtime identity v1;
- ADR-0042 - live-ingest checkpoint/recovery v1;
- ADR-0043 - live-ingest server v1 composition;
- ADR-0044 - live-ingest long-gap remediation and explicit-gap state v1;
- SCOPE acceptance items 1-11 for Live Ingest Server Production Readiness v1.

Explicit exclusions remain unchanged: B06 live-consumer cursor, K07 tier
relocation, K09 deletion authority, J08 live product mode, strategy/execution,
paper/shadow operation, second venues and generic schedulers.

## Implemented Proof Control

`tools/live_ingest_server.py` now accepts:

```text
--max-cycles N
```

Default behavior is unchanged. When supplied, the executable boundary requests
a clean stop after `N` completed `HEALTH_SNAPSHOT` cycles. This keeps bounded
production-readiness proofs deterministic without hard-killing an in-flight
publish and without adding acquisition, publication, checkpoint or repair
semantics outside the existing Application boundary.

## Executed Local Evidence

### CLI proof-control and server/gap tests

Command:

```text
python -m unittest tests.test_live_ingest_server_cli_v1 tests.test_live_ingest_server_v1 tests.test_live_gap_orchestration_v1
```

Observed output:

```text
Ran 20 tests in 0.210s
OK
```

Coverage from these tests:

- clean bounded stop through the CLI `--max-cycles` proof control;
- steady-state live acquisition cycles use the live publish path, not REST polling;
- startup with an existing checkpoint reconciles before steady-state acquisition;
- bounded reconcile resumes when continuity is provable;
- unresolved long gap records durable `known_gap` coverage with no partitions;
- long-gap state remains visible after subsequent restart/checkpoint advance;
- repeated sustained gap updates one open supersession chain;
- unparseable durable long-gap manifests emit `OBSERVATION_UNAVAILABLE`;
- corrupt checkpoint fails closed through a `FAILURE` signal.

### Package-boundary verification

Command:

```text
python -m unittest tests.test_package_boundaries_v1
```

Observed output:

```text
Ran 15 tests in 9.064s
OK
```

This confirms the new proof-control test and CLI change preserve the existing
executable-boundary rule: `tools/` reaches repository runtime code through
`quant_platform.application`.

### CLI help surface

Command:

```text
python tools/live_ingest_server.py --help
```

Material observed line:

```text
--max-cycles MAX_CYCLES
  Stop cleanly after this many completed health-snapshot cycles; intended for bounded proofs
```

## Target-Server Preflight Executed By Codex

Command:

```text
ssh -o BatchMode=yes -o ConnectTimeout=8 neonix@homelab.local \
  "cd /opt/market-platform && git fetch origin governance/ingest-server-production-v1-scope && git rev-parse --short FETCH_HEAD"
```

Observed output:

```text
bf4c5d6
```

Command:

```text
ssh -o BatchMode=yes -o ConnectTimeout=8 neonix@homelab.local \
  "sudo -n -u mkt-transform id"
```

Observed output:

```text
sudo: password is required
```

Result: Codex can reach the target server and fetch Git authority, but cannot
execute the K02-valid runtime identity from this non-interactive session.
Running as `neonix` would not satisfy ADR-0041/K02 because the proof must run
under the least-privileged `mkt-transform` identity.

## Target Proof Command Pack

The following command pack is the target-server proof still required before
this issue can be closed as production-ready evidence. It intentionally keeps
secrets out of Git and writes only bounded logs/JSON metadata, not bulk market
data.

```bash
cd /opt/market-platform
sudo -v
set -euo pipefail

git fetch origin codex/issue-128-lipr05-production-proof

RUN_ID="lipr05-live-ingest-server-$(date -u +%Y%m%dT%H%M%SZ)"
WORKTREE="/tmp/${RUN_ID}-worktree"
CHECKPOINT="/srv/marketdata/canonical/_k10_checkpoints/${RUN_ID}.json"
EVIDENCE_DIR="/tmp/${RUN_ID}-evidence"
PGPASS="/tmp/${RUN_ID}.pgpass"

git worktree add --detach "$WORKTREE" FETCH_HEAD
mkdir -p "$EVIDENCE_DIR"

read -rsp "market_catalog_writer password: " K10_DB_PASSWORD
echo
sudo -u mkt-transform sh -c "umask 077; cat > '$PGPASS'" <<EOF
127.0.0.1:5433:market_catalog:market_catalog_writer:${K10_DB_PASSWORD}
EOF
unset K10_DB_PASSWORD

date -Is | tee "$EVIDENCE_DIR/process-a-start.txt"
sudo -u mkt-transform env \
  PGPASSFILE="$PGPASS" \
  PGHOST=127.0.0.1 \
  PGPORT=5433 \
  PGUSER=market_catalog_writer \
  PGDATABASE=market_catalog \
  /opt/market-platform/.venv/bin/python "$WORKTREE/tools/live_ingest_server.py" \
    --storage-root /srv/marketdata \
    --storage-root-id hot \
    --checkpoint-path "$CHECKPOINT" \
    --producer "lipr05-live-ingest-server" \
    --code-ref "issue-128-${RUN_ID}" \
    --max-messages-per-cycle 1 \
    --max-seconds-per-cycle 15 \
    --cycle-interval-seconds 0 \
    --max-cycles 1 \
  > "$EVIDENCE_DIR/process-a.stdout" 2>&1
date -Is | tee "$EVIDENCE_DIR/process-a-end.txt"

date -Is | tee "$EVIDENCE_DIR/process-b-start.txt"
sudo -u mkt-transform env \
  PGPASSFILE="$PGPASS" \
  PGHOST=127.0.0.1 \
  PGPORT=5433 \
  PGUSER=market_catalog_writer \
  PGDATABASE=market_catalog \
  /opt/market-platform/.venv/bin/python "$WORKTREE/tools/live_ingest_server.py" \
    --storage-root /srv/marketdata \
    --storage-root-id hot \
    --checkpoint-path "$CHECKPOINT" \
    --producer "lipr05-live-ingest-server" \
    --code-ref "issue-128-${RUN_ID}" \
    --max-messages-per-cycle 1 \
    --max-seconds-per-cycle 15 \
    --cycle-interval-seconds 0 \
    --max-cycles 1 \
  > "$EVIDENCE_DIR/process-b.stdout" 2>&1
date -Is | tee "$EVIDENCE_DIR/process-b-end.txt"

sudo -u mkt-transform ls -l "$CHECKPOINT" | tee "$EVIDENCE_DIR/checkpoint-ls.txt"
sudo -u mkt-transform cat "$CHECKPOINT" | tee "$EVIDENCE_DIR/checkpoint-after-b.json"

grep -E "effective runtime identity|user=mkt-transform|is_root=False|signal kind=|LIVE_INGEST_SERVER: STOPPED|max cycles reached" \
  "$EVIDENCE_DIR/process-a.stdout" "$EVIDENCE_DIR/process-b.stdout" \
  | tee "$EVIDENCE_DIR/material-summary.txt"

rm -f "$PGPASS"
```

Expected acceptance signals from the command pack:

- both processes print `user=mkt-transform` and `is_root=False`;
- Process A reaches `LIVE_INGEST_SERVER: STOPPED` after a bounded acquire
  cycle and persists a checkpoint;
- Process B starts from the same checkpoint, emits a reconcile health snapshot
  before any new steady-state acquisition, and exits cleanly;
- checkpoint generation and identity after Process B prove publication before
  checkpoint advance;
- pressure/capacity fields appear in `HEALTH_SNAPSHOT` payloads;
- no `known_gap` interval is marked complete unless a future A10 repair
  cutover evidence path proves it.

## Forced Long-Gap Evidence

Real forced long-gap execution was **NOT EXECUTED** by Codex on the target
server because valid K02 execution is blocked by the non-interactive sudo
boundary above.

Equivalent hermetic coverage is currently:

- `tests.test_live_ingest_server_v1.test_gap_detected_on_startup_records_non_complete_gap_and_starts_new_segment`;
- `tests.test_live_ingest_server_v1.test_open_gap_observability_survives_restart_after_checkpoint_advances`;
- `tests.test_live_ingest_server_v1.test_sustained_gap_updates_one_open_gap_chain_instead_of_appending_events`;
- `tests.test_live_gap_orchestration_v1.test_records_known_gap_without_partition_and_unproven_source_evaluation`;
- `tests.test_live_gap_orchestration_v1.test_inconclusive_source_evaluation_is_retryable_and_not_unproven`.

These tests are sufficient to prove repository behavior for an accepted
equivalent simulation, but they are not a substitute for the target-server
proof unless the reviewer/operator explicitly accepts that equivalence for
issue #128.

## Acceptance State

| Requirement | State | Evidence |
|---|---|---|
| Server runs under K02-compatible authority | BLOCKED | `sudo -n -u mkt-transform id` requires interactive password |
| Publication-before-checkpoint during normal running | PARTIAL | Hermetic tests pass; target Process A pending |
| Publication-before-checkpoint during restart/reconcile | PARTIAL | Hermetic tests pass; target Process B pending |
| Bounded reconcile resumes when continuity is provable | PARTIAL | Hermetic tests pass; target Process B pending |
| Long-gap remains explicit/non-complete | PASS for equivalent simulation | Hermetic tests listed above |
| Operator/runbook evidence covers session/publication/checkpoint/gap/repair/pressure/authority | PARTIAL | CLI emits K03/K04/K05 signals; target evidence pending |
| No secrets/bulk market data committed | PASS | This document records commands and bounded outputs only |

## Current Conclusion

Issue #128 is not yet production-ready closed by this branch alone. The code
now contains the bounded CLI proof control needed to run the proof cleanly,
and local/hermetic evidence is green. The remaining blocker is target-server
execution as `mkt-transform`, which requires an operator-provided interactive
sudo/password step that this Codex session cannot satisfy.
