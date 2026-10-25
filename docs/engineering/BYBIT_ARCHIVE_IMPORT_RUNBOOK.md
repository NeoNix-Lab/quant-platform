# Bybit archive historical import - operator runbook

Authority: [ADR-0074](../decisions/ADR-0074-multi-instrument-bybit-linear-trades-v1.md) Amendment 1 (DG-N `T04`, #487). Every
step below is a manual operator step on the server; nothing here runs on its own, deploys or deletes data.

The historical source of every instrument of the allow-list (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `DOGEUSDT`) is the Bybit
public trade archive, one `.csv.gz` per UTC day. The import reads a **local** file, one day per invocation, and publishes it only
through S13 and S14. A day that already has a partition in the catalog (the SQLite `BTCUSDT` days, live days) is refused.

## 0. Preconditions

- The checkout is a clean, fast-forwarded commit of the integration branch (the import names `git rev-parse HEAD` as `code_ref` and
  refuses a dirty tree).
- `CATALOG_DSN` holds the writer DSN in the environment (never on the command line). The examples use the storage root `hot`.
- Free space on the hot root for the instrument being replaced (about 49 GB for `ETHUSDT`).

## 1. Download the days into a staging directory

```bash
python tools/fetch_bybit_trade_archive.py --instrument ETHUSDT --from 2020-10-21 --to 2026-10-19 --dest /srv/staging/bybit-archive/ETHUSDT
```

One JSON line per day: `fetched`, `present` (already there, kept), `absent` (HTTP 404: the archive has no file; that day stays an
explicit gap) or `error` (retry later; exit status 1).

## 2. Compare the old files with the archive (before anything is moved)

For the instruments whose old Parquet are being replaced, compare each day; this writes nothing.

```bash
python tools/compare_archive_day.py --instrument ETHUSDT --day 2023-06-15 --archive /srv/staging/bybit-archive/ETHUSDT/ETHUSDT2023-06-15.csv.gz --parquet /srv/marketdata/canonical/trades/bybit/ETHUSDT/trade-v1/dt=2023-06-15/part-001.parquet
```

`same_trades: true` means the same `trade_id` set with the same millisecond timestamp, side, price and size values;
`field_differences` tells text-only differences (`price_text`, a trailing zero) from different values. Keep the reports with the
change record. The same tool on a published `BTCUSDT` day (2024-01-15) is the equivalence proof of `T04`.

## 3. Move the old directory aside (same disk, no copy)

```bash
mv /srv/marketdata/canonical/trades/bybit/ETHUSDT /srv/marketdata/replaced-ETHUSDT
```

## 4. Import the days

```bash
for f in /srv/staging/bybit-archive/ETHUSDT/ETHUSDT*.csv.gz; do d=$(basename "$f" .csv.gz); d=${d#ETHUSDT}; python tools/import_bybit_archive_day.py --instrument ETHUSDT --day "$d" --archive "$f" || echo "REFUSED $d"; done
```

Each day prints one JSON line: the published partition (`state: valid`) or the refusing phase (`REQUEST`, `CATALOG`, `LOCK`, `TARGET`,
`SOURCE`, `MATERIALIZE`, `S13`, `S14`) with its reason. A refused day is not published; its files, if any were written before the
refusal, stay unpublished for inspection. A rerun skips nothing silently: a day already published is refused by `CATALOG`.

### Concurrent imports and locks

Several imports may run at once, for different days or by mistake for the same one. Each day is guarded by an exclusive lock file
`.import-dt=<day>.lock` in the dataset directory, created before anything is checked or written: a second import of the same day
is refused with `LOCK` before it touches a file, so the files on disk are always the ones the catalog certified. While an
instrument's dataset is not in the catalog yet, the seals of its first days are serialized (a short wait, at most 2 minutes,
guarded by `.import-dataset-registration.lock`); afterwards days run in parallel.

A lock left by an import that crashed is **never taken over**. The refusal names the lock file and the process that created it
(`pid`, `host`, time). Confirm that no import is running, then remove that one file and run the day again. A day refused after
its files were written (`S13`, `S14`) leaves them for inspection and is refused as `CATALOG` or `TARGET` on rerun until the
operator decides what to do with them.

## 5. Check and only then delete

```bash
python tools/check_trades_catalog.py
```

When the instrument shows the expected `valid` partitions, deleting `/srv/marketdata/replaced-ETHUSDT` is the maintainer's decision
and step.

## 6. Rebuild damaged legacy live days (DG-R, ADR-0074 Amendment 2)

Operator-run, one explicitly named set of days at a time, from a plan that was reviewed. The days come from you, never from the tool:
the 31 candidate dates of the amendment are an inventory, not a plan. Start with `2026-10-20`; further days only from a new plan.

**What it does, per day, in this order:** (1) *retire* the damaged live revision of `dt=<day>` with a superseding quality
reassessment (`fail`, reason `legacy_publication_incomplete_day`, naming the archive file), which the existing lifecycle maps to
`invalid`: nothing is deleted, its bytes, manifests, reports and admissions stay, and every read policy excludes it; (2) *import* the
whole archive day as revision 1 of `dt=<day>/seg=000000.000` through the ordinary S13/S14 path; (3) *verify* it against the archive
file field by field and by a strict `DataGateway` read (count, order, duplicates). The rebuilt rows carry the archive's null
`receive_ts` and `sequence`: the provider `sequence` of the rows the damaged revision served is lost in the served data (declared and
accepted; the old bytes are kept).

**Between the retire and the import the day is served by nothing** (a strict read refuses, `allow-partial` reports the gap). There is
no atomic switch by design. The tool does one day at a time and holds no lock between days or while it reads and verifies.

### 6.1 Before you start

- The days are closed in UTC: the end of the day is at least 24 hours before the execution.
- The archive file of each day **and of every neighbouring day whose trades the damaged revision holds** (a day can hold rows of the
  day before or after it) is in one directory, fetched with `tools/fetch_bybit_trade_archive.py` (section 1).
- The live-ingest checkpoint path of the instrument, so the tool can refuse a day the checkpoint is bound to.
- The producing commit: the checkout is clean and its `HEAD` is what you want written in the manifests (or pass `--code-ref`).
- `CATALOG_DSN` (or the variable named by `--dsn-env`) holds the PostgreSQL DSN; it is never passed on the command line.

### 6.2 Plan (read-only)

```bash
python tools/rebuild_bybit_legacy_days.py --storage-root-id hot plan --days 2026-10-20 \
  --archive-dir /srv/marketdata/staging/BTCUSDT --checkpoint-path /srv/marketdata/canonical/_k10_checkpoints/live-ingest-server-v1.json \
  --out plan-2026-10-20.json
```

It prints the **digest** and the action per day (`retire+import`, `import`, `verify` or `refused` with the reasons) and writes the plan
file, which it never overwrites. Read the plan file before you go on: for each day the incumbent revision (partition UUID, key,
revision, state, hashes, coverage), the archive file (URL, SHA-256, size, observation time, rows), the key to create, and the
conservation summary (how many records the damaged revision holds and the days they belong to).

A day is **refused**, and nothing is changed for it, when: it is not closed in UTC; it has a live slice (`dt=<day>/seg=...`), a
checkpoint bound to one of its partitions or an open gap record that names it; its live revision is not `valid` (or `invalid`
retired by this operation for the same archive file); the incumbent's Parquet or durable manifests are missing, do not load, or are not
the ones the catalog and its quality reports certified (the manifests are looked for beside the Parquet and, for a day published by the
live publisher before the slices, in the root of the dataset as `partition-manifest-<HHMMSS>.json` and
`coverage-manifest-<HHMMSS>.json`, the time without the date: a root file that a later day overwrote in the same second is not this
revision's and the day is refused); the archive file is missing or fails the strict parsing; or **conservation
fails**: every record of the damaged revision, by `(exchange_ts, trade_id)`, must exist in the archive file of its own UTC day with
the same venue, instrument, exchange time (millisecond), side, price and size, and that day must be readable afterwards (rebuilt in
the same plan or already `valid`). A trade id that the damaged revision holds twice with different content refuses the day too. Any
difference refuses the day; there is no partial acceptance.

### 6.3 Execute

```bash
python tools/rebuild_bybit_legacy_days.py --storage-root-id hot execute --plan plan-2026-10-20.json --digest <DIGEST> \
  --archive-dir /srv/marketdata/staging/BTCUSDT --checkpoint-path /srv/marketdata/canonical/_k10_checkpoints/live-ingest-server-v1.json
```

The tool recomputes the plan from the catalog, the files and the clock and refuses (`STALE`, nothing done) if the digest differs: a
changed incumbent, archive file or catalog, or a day that closed in between. It never acquires another target and never widens the
set of days. It stops at the first day that fails; the later days are not attempted.

**Crashes and retries.** After a crash between the retire and the import the day is retired and not imported: run the same command
again and it imports. After a crash between the import and the verification, run it again: it finds the valid key it would create and
verifies. A crash while the import was writing files leaves them unpublished; the rerun is refused `TARGET` or `CATALOG` until you
decide what to do with them (nothing is deleted by the tool).

### 6.4 Verify, and the way back

```bash
python tools/rebuild_bybit_legacy_days.py --storage-root-id hot verify --days 2026-10-20 --archive-dir /srv/marketdata/staging/BTCUSDT
```

Read-only and repeatable. It passes only if the imported Parquet is identical to the archive file and a strict gateway read returns
exactly the archive's records, in `(exchange_ts, trade_id)` order, once each. A strict read can also be refused when a neighbouring
legacy day's coverage crosses midnight and overlaps the rebuilt day: that neighbour is retired and rebuilt by its own plan.

**Restore** is explicit, never automatic, and only for a day that was retired and **not** rebuilt (the import failed and you prefer
the damaged day to no day):

```bash
python tools/rebuild_bybit_legacy_days.py --storage-root-id hot restore --day 2026-10-20
```

It adds a superseding `pass` that returns the revision to `valid`; it is refused while the day has any partition under `dt=<day>/`
(two valid partitions over one day). The ordinary importer still refuses an occupied day, a retired one included: the rebuild is a
separate operation and never a `--force`.

### 6.5 What this does not do

It does not run itself, choose days, delete or clean retired or superseded revisions (the retention decision is the maintainer's), touch
live slices, checkpoints, gap evidence or healthy days, repair live gaps, or run on the server for you.
