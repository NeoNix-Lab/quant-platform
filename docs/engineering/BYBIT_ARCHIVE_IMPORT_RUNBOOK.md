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
