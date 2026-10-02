# Live Ingest Server Deployment Runbook

Status: v1 deployment-local runbook for issue #135.

This runbook installs and proves the supervised Bybit BTCUSDT live-ingest
server operating path on `homelab`. It uses the already-owned
`tools/live_ingest_server.py` entrypoint and does not add ingestion,
publication, checkpoint, repair or trading semantics.

## Boundaries

- Runs as `mkt-transform:marketdata`.
- Uses GitHub `origin` as the code authority.
- Uses `/opt/market-platform` as the stable deployed checkout.
- Uses `/srv/marketdata` / storage root id `hot`.
- Uses the existing PostgreSQL catalog connection through libpq environment and
  `PGPASSFILE`; secrets stay deployment-local and out of Git.
- Does not imply B06 live consumer cursors, K07 tier relocation, K09 retention
  and deletion authority, J08 live product mode, generic job runtime, strategy
  execution, order placement or multi-provider production.

## Files

- Unit template: `infra/systemd/quant-platform-live-ingest.service`
- Non-secret env template:
  `infra/systemd/quant-platform-live-ingest.env.example`
- Target env file: `/etc/quant-platform/live-ingest-server.env`
- Target service file:
  `/etc/systemd/system/quant-platform-live-ingest.service`
- Target checkpoint:
  `/srv/marketdata/canonical/_k10_checkpoints/live-ingest-server-v1.json`

## Install Or Update

Run on `homelab` as the operator account:

```bash
cd /opt/market-platform
sudo -v
set -euo pipefail

git status --short --branch
test -z "$(git status --porcelain)"
git remote get-url origin

git fetch origin governance/ingest-server-production-v1-scope codex/issue-135-lipr06a-supervised-deployment
if git show-ref --verify --quiet refs/heads/codex/issue-135-lipr06a-supervised-deployment; then
  git switch codex/issue-135-lipr06a-supervised-deployment
else
  git switch --track -c codex/issue-135-lipr06a-supervised-deployment \
    origin/codex/issue-135-lipr06a-supervised-deployment
fi
git merge --ff-only origin/codex/issue-135-lipr06a-supervised-deployment

DEPLOYED_SHA="$(git rev-parse HEAD)"
echo "DEPLOYED_SHA=$DEPLOYED_SHA"

sudo install -d -m 0750 -o root -g marketdata /etc/quant-platform
sudo install -m 0644 -o root -g root \
  infra/systemd/quant-platform-live-ingest.service \
  /etc/systemd/system/quant-platform-live-ingest.service

if [ ! -f /etc/quant-platform/live-ingest-server.env ]; then
  sudo install -m 0640 -o root -g marketdata \
    infra/systemd/quant-platform-live-ingest.env.example \
    /etc/quant-platform/live-ingest-server.env
fi

sudo sed -i "s/^QP_LIVE_INGEST_CODE_REF=.*/QP_LIVE_INGEST_CODE_REF=${DEPLOYED_SHA}/" \
  /etc/quant-platform/live-ingest-server.env

sudo -u mkt-transform test -r /home/mkt-transform/.pgpass
sudo -u mkt-transform test -w /srv/marketdata/canonical/_k10_checkpoints
sudo systemctl daemon-reload
sudo systemctl cat quant-platform-live-ingest.service
```

Before start, inspect `/etc/quant-platform/live-ingest-server.env` and correct
only deployment-local values. Do not add `PGPASSWORD`.

The unit runs under `ProtectSystem=strict` (the whole filesystem read-only
except `ReadWritePaths=`). If this deployment's `QP_LIVE_INGEST_STORAGE_ROOT`
is not `/srv/marketdata`, update `ReadWritePaths=` in the installed unit to
match before starting the service, or every checkpoint/publish write will
fail with `EROFS`/permission-denied.

## Start, Stop, Restart And Status

```bash
sudo systemctl start quant-platform-live-ingest.service
sudo systemctl status --no-pager quant-platform-live-ingest.service
sudo journalctl -u quant-platform-live-ingest.service -n 80 --no-pager

sudo systemctl stop quant-platform-live-ingest.service
sudo systemctl status --no-pager quant-platform-live-ingest.service || true

sudo systemctl start quant-platform-live-ingest.service
sudo journalctl -u quant-platform-live-ingest.service -n 120 --no-pager
```

Expected material evidence:

- startup prints `=== effective runtime identity ===`;
- startup identity includes `user=mkt-transform` and `is_root=False`;
- logs include `signal kind=LIFECYCLE_TRANSITION`;
- logs include at least one `signal kind=HEALTH_SNAPSHOT`;
- after a restart, the first useful cycle reconciles from the durable
  checkpoint when one exists;
- `systemctl stop` sends SIGTERM and the process exits through
  `LIVE_INGEST_SERVER: STOPPED`.
- no `EROFS` / `Permission denied` entries in the journal: this unit runs
  under `ProtectSystem=strict`, so a storage-root or checkpoint-path mismatch
  with `ReadWritePaths=` surfaces here, not as a Python traceback.

## Readback Proof

After restart, read the checkpoint and run a narrow DataGateway check over the
last durable trade key. The check must prove the checkpoint anchor is present
and duplicate trade keys are absent. Record the JSON summary in
`docs/integration/LIPR06A_SUPERVISED_LIVE_INGEST_DEPLOYMENT.md`.

If readback reports partial coverage inside a half-open millisecond interval,
that is acceptable only when the checkpoint anchor is present and duplicate
keys are absent. Do not convert partial coverage into a false complete claim.

## Rollback

This runbook does not automate rollback. If the service fails:

```bash
sudo systemctl stop quant-platform-live-ingest.service
sudo systemctl status --no-pager quant-platform-live-ingest.service || true
sudo journalctl -u quant-platform-live-ingest.service -n 200 --no-pager
```

Then inspect the repository and service environment manually. Do not run
`git reset --hard`, `git clean`, automated stash or pull-on-push deployment as
part of this runbook.
