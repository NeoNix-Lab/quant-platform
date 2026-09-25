# LIPR-06a Supervised Live-Ingest Deployment Evidence

Issue: https://github.com/NeoNix-Lab/quant-platform/issues/135

Status: NOT EXECUTED

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

Pending. The PR remains draft until the target proof is executed or the blocker
is reported exactly.
