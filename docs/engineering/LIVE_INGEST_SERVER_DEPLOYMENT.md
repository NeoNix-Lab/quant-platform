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

# The code is promoted first, by the procedure of DECK_HANDOFF_RUNBOOK.md (Promotion): infra/promote-main.sh, clean tree,
# fast-forward only, on the branch main. This runbook never switches or merges branches: live-ingest runs the same commit as
# the API and the worker (ADR-0068 section 8), whatever that promotion moved the checkout to.
git status --short --branch
test -z "$(git status --porcelain)"
git remote get-url origin
test "$(git symbolic-ref --short -q HEAD)" = main

DEPLOYED_SHA="$(git rev-parse HEAD)"
echo "DEPLOYED_SHA=$DEPLOYED_SHA"

# /etc/quant-platform is shared with the API and worker units (DECK_HANDOFF_RUNBOOK.md), which run as users outside
# marketdata: root:root 0751 lets every unit's user reach its own files without listing the directory; the files
# keep their own owner and mode. Do not narrow it to root:marketdata 0750.
sudo install -d -m 0751 -o root -g root /etc/quant-platform
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

## What The Server Publishes (ADR-0075 slices, DG-O `O01`)

Every publication is a **slice**: revision 1 of its own partition, never superseded. Under
`canonical/trades/bybit/<INSTRUMENT>/trade-v1/`:

```text
dt=<UTC day of the window start>/seg=<HHMMSS.mmm of the window start>/part-001.parquet
                                                                    /partition-manifest.json
                                                                    /coverage-manifest.json
```

- The window is half-open `[start, end)` in exchange time; the coverage assertion is `complete` over exactly that window. A
  window never crosses a UTC midnight. The trades of one millisecond are never split between two slices.
- A `DataGateway` read of a live day returns every published trade (`VALID_ONLY`). A read that spans the pause between two cycles is
  refused under `strict` coverage, because that pause is not covered (it ends with the continuous session, `O02`); under
  `allow-partial` it returns everything published.
- The restart reconciliation publishes its records as a slice too, starting where the last published slice ended (continuity proven
  by the provider's recent-trades window). When that proven span reaches a UTC midnight before the first new trade, the span up to
  the midnight is a slice with no trade (a bridge), so the chain has no hole. A slice already published is never republished.
- "Already published" means valid: a slice that was sealed but never became valid (certification or eligibility failed) is not
  readable and is not a replay. After the restart its trades are published again; if the window is the same, the failed attempt is
  replaced by the next revision (`part-002.parquet`, ...). A valid slice is never superseded.
- A live trade before the end of what is already published is an ordering violation: the loop stops with the failure code
  `SLICE_ORDERING_VIOLATION`, publishes nothing, and the next start reconciles from the checkpoint.
- Partitions published before ADR-0075 (`dt=<day>`, many superseded revisions) are left as they are; there is no repair.

### Continuous session (ADR-0075 slices 9.2 and 9.3, `O02` and `O03`): opt-in per unit

`--mode continuous` (environment variable `QP_LIVE_INGEST_MODE`) makes the server run ONE WebSocket session instead of the bounded
cycles: the connection is not closed between publications, a bounded buffer decouples the reader from the publisher, and slices
close after `slice_seconds` of wall clock or `slice_max_trades` trades. The default is `cycles`, the loop described above, and
nothing changes for a unit whose environment file does not set the mode (an empty or unset value is `cycles`, and an environment
file that predates these variables still starts the unit). Enabling it is an operator step: edit the environment file of the
instance and restart the unit; the repository enables nothing, and deployment of a changed server stays manual.

| Variable | Flag | Default | Meaning |
|---|---|---|---|
| `QP_LIVE_INGEST_MODE` | `--mode` | `cycles` | `cycles` or `continuous` |
| `QP_LIVE_INGEST_SLICE_SECONDS` | `--slice-seconds` | 300 | continuous only: wall-clock seconds after which an open slice closes |
| `QP_LIVE_INGEST_SLICE_MAX_TRADES` | `--slice-max-trades` | 50000 | continuous only: trades after which an open slice closes |
| `QP_LIVE_INGEST_BUFFER_MESSAGES` | `--buffer-messages` | 20000 | continuous only: bound of the reader's buffer; a full buffer ends the session |

The three bounds are valid only with `continuous`: with `cycles` the server refuses to start (exit status 2) rather than ignore
them, so leave them empty (an empty value is the default). A non-positive or non-integer value is refused before any connection.
`--recent-limit` also applies to the reconciliation after an interruption. The ping interval, pong timeout and reconnect backoff are
not configuration. `--max-cycles`, when used for a bounded proof, counts health snapshots: one per published slice in this mode.

Example, an instance switched to the continuous session with the defaults (`/etc/quant-platform/live-ingest-server-ETHUSDT.env`):

```text
QP_LIVE_INGEST_MODE=continuous
QP_LIVE_INGEST_SLICE_SECONDS=
QP_LIVE_INGEST_SLICE_MAX_TRADES=
QP_LIVE_INGEST_BUFFER_MESSAGES=
```

What an operator sees:

- Startup prints `mode=continuous` and the three bounds in force.
- A disconnect, a missing pong, a full buffer or a trade below the open window **ends the session**; what was received is published,
  the interruption is signalled (`HEALTH_SNAPSHOT`, `DEGRADED`, `phase=interruption`, with the reason and the counters), the session
  reconnects with a bounded backoff, subscribes first and waits for the first trade of the new subscription. It then fetches the
  recent trades and reconciles from the last durable key; a subscription with no trade yet reconciles nothing and claims nothing.
- Continuity proven (a trade of the subscription is in the recent-trades window): the reconciled trades are published as slices and
  the chain stays exact. Anchor outside the window, or a window that does not meet the subscription: an explicit gap (ADR-0044),
  recorded, `DEGRADED`; a new window starts at the first trade after it and nothing is claimed across the gap.
- Every published slice signals `phase=slice` with `buffer_high_water` and `buffer_capacity`, `slice_lag_ms`, `reconnects` and
  `ordering_violations`. A UTC midnight with no trade is reported as an unclaimed span. A publication, catalog or checkpoint failure
  stops the loop (`FAILURE`), as in the cycle loop.
- A `DataGateway` read under `strict` coverage across an interruption's gap is refused; under `allow-partial` it returns everything
  published.

**Rollback to the cycles:** set `QP_LIVE_INGEST_MODE=cycles` (and the bounds empty), restart the unit. The checkpoint and the
published partitions are the same in both modes: the first cycle reconciles from the durable checkpoint.

### One-hour real-provider proof (`O03`; the `T02` proof for `ETHUSDT`)

Operator-executed; the repository starts nothing. It measures what ADR-0075 leaves as an assumption: the captured share against
the provider's archive, the ordering of the provider's `exchange_ts`, the slice sizes and the health signals. Use a **disposable
catalog and storage root** (or an `ETHUSDT`-only root the maintainer chooses): the proof never touches the `BTCUSDT` dataset or its
unit, and it enables nothing in production (the ADR-0074 section 6 capacity gate stands).

```bash
# 1. Run the continuous session for at least one hour, with the signals captured. Stop it cleanly with SIGTERM/SIGINT.
python tools/live_ingest_server.py --instrument ETHUSDT --mode continuous \
  --storage-root <ROOT> --storage-root-id <ID> --checkpoint-path <ROOT>/canonical/_k10_checkpoints/live-ingest-server-v1-ETHUSDT.json \
  --code-ref "$(git rev-parse HEAD)" 2>&1 | tee live-ethusdt-run.log

# 2. After the UTC day (or days) of the run has ended, fetch the archive of each day it touched (tools/fetch_bybit_trade_archive.py).
# 3. Produce the evidence (read-only; exit 0 = no stop condition, 1 = a stop condition, 2 = usage):
python tools/live_window_evidence.py --instrument ETHUSDT --archive ETHUSDT<YYYY-MM-DD>.csv.gz [more days] \
  --signals-log live-ethusdt-run.log --code-ref <SHA> --mode continuous > ethusdt-evidence.json
```

The report states, for the window the run claimed (the valid live slices): the archive trades in it, the trades a `strict` gateway
read returns (chunks of at most twelve slices, ending on slice boundaries), the captured ratio, the holes between exact chains (each
an interval the run declared unclaimed, with the archive trades it contains), the slices (count, rows and seconds: minimum, median,
maximum) and the counters of the session. **Stop conditions** (exit 1; do not read the proof as passed and report): an archive trade
inside a claimed window that is not read back (an undeclared loss); a strict read of a claimed window refused; a trade read twice or
out of order; a trade whose payload differs from the archive. A shortfall against the archive that falls inside an unclaimed span is
reported, not hidden, and is not a stop condition; an ordering violation is a measurement (it is an interruption by design), to be
explained in the evidence. Record the report, the log and the commit in the pull request of `O03`.

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

## Additional Bybit Linear Instruments (ADR-0074, DG-N `T02`)

Status: artifacts and operator steps only. Nothing here is installed or enabled by the repository, and nothing here proves
two instruments running side by side or a real-provider run (parts 5 and 6 of `T02`).

[ADR-0074](../decisions/ADR-0074-multi-instrument-bybit-linear-trades-v1.md) runs **one live-ingest process per instrument**.
`BTCUSDT` stays on `quant-platform-live-ingest.service`, with its own `live-ingest-server.env` and checkpoint, exactly as
above. The other instruments of the allow-list (`ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `DOGEUSDT`) run as instances of one template
unit:

| | `BTCUSDT` (existing) | any other instrument (template) |
|---|---|---|
| Unit | `quant-platform-live-ingest.service` | `quant-platform-live-ingest@<INSTRUMENT>.service` |
| Environment file | `/etc/quant-platform/live-ingest-server.env` | `/etc/quant-platform/live-ingest-server-<INSTRUMENT>.env` |
| Checkpoint | `QP_LIVE_INGEST_CHECKPOINT_PATH` of the file | `${QP_LIVE_INGEST_CHECKPOINT_DIR}/live-ingest-server-v1-<INSTRUMENT>.json`, built from the instance name |
| `--instrument` | omitted (defaults to `BTCUSDT`) | `--instrument %i`, always on the command line |

What the template makes impossible, by construction:

- **Forgetting the instrument.** The instance name is the instrument; the command line is not read from the environment file.
- **Two instances sharing a checkpoint.** The checkpoint file name contains the instance name.
- **Two writers on `BTCUSDT`.** `ExecStartPre` refuses the instance `BTCUSDT`; do not create `live-ingest-server-BTCUSDT.env`.
- **An instrument outside the allow-list.** `quant-platform-live-ingest@ADAUSDT.service` starts and is refused by the server's own
  `--instrument` option (exit status 2).
- **A checkpoint of another instrument.** If the file is there anyway, the loop ends with a `FAILURE` signal
  (`CheckpointDomainMismatch`) before it acquires or publishes anything.

### Capacity gate (ADR-0074 section 6)

Enabling live ingest for more than `BTCUSDT` in **production** requires the Phase A measurements of #378: the projected time for
the hot storage root to reach the critical-pressure threshold of the live-ingest unit, at the measured rate of all enabled
instruments, must be at least **365 days**. Until the measurement exists nothing beyond the bounded one-hour `ETHUSDT` proof
(part 6 of `T02`) is enabled. All instances share one storage root and one catalog; each one evaluates the pressure thresholds
of its own env file against the free space of the shared root, so the thresholds must be chosen for the sum of the enabled
instruments, not for one.

### Add An Instance

Run on `homelab` as the operator account, after the code was promoted as above (`DEPLOYED_SHA` is the deployed commit):

```bash
cd /opt/market-platform
sudo -v
set -euo pipefail

INSTRUMENT=ETHUSDT   # ETHUSDT | SOLUSDT | XRPUSDT | DOGEUSDT; never BTCUSDT
DEPLOYED_SHA="$(git rev-parse HEAD)"
test -z "$(git status --porcelain)"

sudo install -m 0644 -o root -g root \
  infra/systemd/quant-platform-live-ingest@.service \
  /etc/systemd/system/quant-platform-live-ingest@.service

if [ ! -f "/etc/quant-platform/live-ingest-server-${INSTRUMENT}.env" ]; then
  sudo install -m 0640 -o root -g marketdata \
    infra/systemd/quant-platform-live-ingest@.env.example \
    "/etc/quant-platform/live-ingest-server-${INSTRUMENT}.env"
fi
sudo sed -i "s/^QP_LIVE_INGEST_CODE_REF=.*/QP_LIVE_INGEST_CODE_REF=${DEPLOYED_SHA}/" \
  "/etc/quant-platform/live-ingest-server-${INSTRUMENT}.env"

sudo -u mkt-transform test -r /home/mkt-transform/.pgpass
sudo -u mkt-transform test -w /srv/marketdata/canonical/_k10_checkpoints
sudo systemctl daemon-reload
sudo systemctl cat "quant-platform-live-ingest@${INSTRUMENT}.service"
```

Inspect the instance's env file and correct only deployment-local values. Do not add `PGPASSWORD`. The hardening and
`ReadWritePaths=` notes above apply to the template unit as well.

### Start, Stop And Status Of An Instance

```bash
sudo systemctl start "quant-platform-live-ingest@${INSTRUMENT}.service"
sudo systemctl status --no-pager "quant-platform-live-ingest@${INSTRUMENT}.service"
sudo journalctl -u "quant-platform-live-ingest@${INSTRUMENT}.service" -n 80 --no-pager

sudo systemctl stop "quant-platform-live-ingest@${INSTRUMENT}.service"
```

Expected evidence, in addition to the list above:

- the banner prints `instrument=<INSTRUMENT>`;
- every signal names the subject `bybit:<INSTRUMENT>:hot`;
- the checkpoint is `/srv/marketdata/canonical/_k10_checkpoints/live-ingest-server-v1-<INSTRUMENT>.json`;
- the instrument's data lands under `canonical/trades/bybit/<INSTRUMENT>/trade-v1/`, and its explicit gap manifests in that
  directory only.

The instances are independent processes: stopping, restarting or failing one does not touch another. A history gap is handled
as ADR-0044 decides (explicit, no repair source); the period before an instance's first start is not acquired and is never
reported complete. Historical days come from the archive import (`BYBIT_ARCHIVE_IMPORT_RUNBOOK.md`), which refuses a day that
already has a partition.

### Remove An Instance

```bash
sudo systemctl stop "quant-platform-live-ingest@${INSTRUMENT}.service"
sudo systemctl disable "quant-platform-live-ingest@${INSTRUMENT}.service"
```

Data, checkpoint and env file are left in place; deleting them is the operator's decision, not this runbook's.
