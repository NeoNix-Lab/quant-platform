# LIPR-06a Supervised Live-Ingest Deployment Evidence

Issue: https://github.com/NeoNix-Lab/quant-platform/issues/135

Status: PASS

## Intent

This document records the deployment-local proof for the supervised
live-ingest server operating path. The purpose is to close the remaining gap
between the already-proven `tools/live_ingest_server.py` process and a normal
production v1 operating path that is started, stopped, restarted, observed and
reconciled through a target-local supervisor.

This proof is inserted before the governance closeout issue (#129). It does not
change planning governance files and does not broaden the live-ingest scope into
B06 live consumer cursors, K07 tier relocation, K09 retention/delete authority,
J08 live product mode or multi-provider production.

## Target Boundary

- Target host: homelab.
- Runtime identity: `mkt-transform:marketdata`.
- Repository authority: GitHub `origin`.
- Base branch: `governance/ingest-server-production-v1-scope`.
- Server entrypoint: `tools/live_ingest_server.py`.
- Dataset: canonical Bybit BTCUSDT public trades, `trade-v1`.
- Storage root: hot canonical root, `/srv/marketdata`.
- Credential handling: deployment-local only; no secrets are committed.

## Implemented Operating-Path Artifacts

- `infra/systemd/quant-platform-live-ingest.service` installs the existing
  live-ingest CLI as a target-local supervised service.
- `infra/systemd/quant-platform-live-ingest.env.example` documents the
  non-secret deployment-local environment. It uses `PGPASSFILE` rather than
  committing or embedding a PostgreSQL password.
- `docs/engineering/LIVE_INGEST_SERVER_DEPLOYMENT.md` records the operator
  install/update, start, stop, restart, status and readback procedure.

The service file intentionally does not pass `--max-cycles`; bounded cycle
limits remain proof controls, not the normal operating path.

## Required Evidence

The completed proof must record:

1. the exact deployed commit and clean-tree / fast-forward promotion check;
2. the supervisor artifact installed on the target host;
3. `systemctl` or equivalent status showing the service is running;
4. runtime identity evidence proving the service process is not root;
5. operational signal evidence from the supervised path;
6. clean stop evidence;
7. restart evidence showing resume from the durable checkpoint;
8. checkpoint JSON after restart, including identity, generation, revision and
   last durable trade key;
9. DataGateway readback proving the checkpoint anchor is present and duplicate
   trade keys are absent;
10. explicit remaining exclusions, if any.

## Proof Log

Target proof executed on `homelab` on 2026-09-26. The supervised service path
started, published live Bybit BTCUSDT trades, advanced the durable checkpoint,
stopped cleanly, restarted from the checkpoint, reconciled the restart window,
continued live acquisition, and passed DataGateway readback.

The final proved commit was:

```text
d89335f1478984412673e7639874521fd1ac5ec7
```

The target evidence directory was:

```text
/tmp/lipr06a-supervised-pgpass-ok-20260926T065422Z-evidence
```

The service was intentionally left stopped after proof collection:

```text
Active: inactive (dead)
LIVE_INGEST_SERVER: STOPPED
```

### Findings Resolved During Proof

The proof found and resolved three target-only issues before PASS:

1. `BybitLiveSourceError: trade message observed before subscription was
   established`. Fixed in `afd1d57` by treating a real matching topic message
   after `CONNECTED` as implicit subscription evidence while still failing
   closed when no connection event exists.
2. Repeated `DEGRADED` cycles with no checkpoint because the bounded provider
   proof treated timeout after valid messages as pending unless
   `max_messages_per_cycle` was reached. Fixed in `d89335f`: valid messages
   collected inside the bounded window are publishable; zero-message windows
   remain pending.
3. `psycopg.OperationalError: fe_sendauth: no password supplied`. The target
   `/home/mkt-transform/.pgpass` was missing the exact
   `127.0.0.1:5433:market_catalog:market_catalog_writer` entry. Fixed
   deployment-locally without committing secrets.

### Start And Publish

The final start used the supervised systemd path and the exact deployed code
ref:

```text
QP_LIVE_INGEST_CODE_REF=d89335f1478984412673e7639874521fd1ac5ec7
Active: active (running)
user=mkt-transform
primary_group=marketdata
is_root=False
```

The first healthy acquisition cycle published through the normal operating
path and persisted a checkpoint:

```text
signal kind=HEALTH_SNAPSHOT ... checkpoint_identity=live-checkpoint-v1:sha256:3ab975f5b9d3bcf635454a142fc93ec7,cycle=1,health=HEALTHY,phase=acquire,pressure_new_writes_allowed=True,pressure_state=NORMAL,session_state=CONTINUOUS
```

The checkpoint file existed at the configured path:

```text
/srv/marketdata/canonical/_k10_checkpoints/live-ingest-server-v1.json
generation=1
revision=1
last_canonical_trade_id=a32b072d-bceb-566b-b4aa-a689bfea3575
```

### Clean Stop

The service accepted `SIGTERM`, finished the in-flight cycle, and exited cleanly:

```text
received signal 15; stopping after the current cycle completes
signal kind=LIFECYCLE_TRANSITION ... previous_state=RUNNING,resulting_state=STOPPED
LIVE_INGEST_SERVER: STOPPED
Deactivated successfully.
```

The stop proof included six healthy acquisition cycles before shutdown:

```text
cycle=6,health=HEALTHY,phase=acquire
```

### Restart And Reconcile

Before restart, the durable checkpoint was:

```text
checkpoint_identity=live-checkpoint-v1:sha256:2cea84bd0a9915261f7f42bea4f86cce1ae3a66f14542982ed7e881e2e4a8e14
generation=6
revision=6
last_canonical_trade_id=18bd701e-b313-55c3-90f8-acbf3e16a330
last_observed_sequence=816270009607
```

After supervised restart, the first cycle reconciled from the durable
checkpoint and the second cycle resumed live acquisition:

```text
cycle=1,health=HEALTHY,phase=reconcile,records=222,session_state=CONTINUOUS
cycle=2,health=HEALTHY,phase=acquire,session_state=CONTINUOUS
```

The checkpoint advanced after restart:

```text
checkpoint_identity=live-checkpoint-v1:sha256:ab760844b52cf1a784bfae12ca8ea1125ed0fbaa9de14f8363dd5dbff066f58f
generation=8
revision=8
last_canonical_trade_id=a3e4534c-558f-5d09-879c-94a8a788a720
last_observed_sequence=816270332356
```

### DataGateway Readback

Final DataGateway readback was executed against the last durable checkpoint
anchor and passed:

```json
{
  "checkpoint_generation": 20,
  "checkpoint_identity": "live-checkpoint-v1:sha256:f45a85d6783d522967d4edc483946a0948ef607d188db8124b08fd933b351223",
  "checkpoint_last_trade": {
    "exchange_ts": "2026-09-26T07:04:24.015Z",
    "partition_key": "dt=2026-09-26",
    "sequence": "816271176499",
    "trade_id": "e947c9c6-0c6d-52b5-9a1c-370e2757bf95"
  },
  "checkpoint_revision": 20,
  "contains_checkpoint_anchor": true,
  "coverage_complete": true,
  "coverage_gaps": [],
  "duplicate_trade_key_count": 0,
  "request_end": "2026-09-26T07:04:24.016Z",
  "request_start": "2026-09-26T07:04:24.015Z",
  "rows_read": 6,
  "status": "PASS"
}
```

### Final Stop

The final service stop also completed cleanly:

```text
cycle=19,health=HEALTHY,phase=acquire
signal kind=LIFECYCLE_TRANSITION ... previous_state=RUNNING,resulting_state=STOPPED
LIVE_INGEST_SERVER: STOPPED
Active: inactive (dead)
```

### Remaining Exclusions

The proof does not claim B06 live consumer cursor, K07 tier relocation, K09
retention/delete authority, J08 live product mode, strategy/execution authority,
orders, portfolio state, multi-provider production or generic job runtime.

## Target Preflight

Command:

```text
ssh -o BatchMode=yes -o ConnectTimeout=8 neonix@homelab.local "cd /opt/market-platform && hostname && git status --short --branch && git rev-parse --short HEAD && command -v systemctl && systemctl --version | head -1"
```

Observed output:

```text
homelab
## agent/issue-121-k10-real-server-execution...origin/agent/issue-121-k10-real-server-execution
?? src/quant_platform.egg-info/
c2504b8
/usr/bin/systemctl
systemd 257 (257.13-1~deb13u1)
```

Result: BLOCKED for supervised deployment. The target has systemd available,
but `/opt/market-platform` is not on the LIPR-06a branch and is not clean.
No repository promotion, service installation or service start was attempted.

## Local Verification

Command:

```text
python -m unittest tests.test_live_ingest_deployment_artifacts_v1 tests.test_live_ingest_server_cli_v1 tests.test_live_ingest_server_v1 tests.test_live_gap_orchestration_v1 tests.test_package_boundaries_v1
```

Observed output:

```text
Ran 38 tests in 9.149s
OK
```

Command:

```text
git diff --check
```

Observed result: no whitespace errors. Git emitted only the existing Windows
LF-to-CRLF working-copy warning for this document.
