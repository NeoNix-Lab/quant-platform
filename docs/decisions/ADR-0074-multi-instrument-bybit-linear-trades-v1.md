# ADR-0074 - Multi-instrument Bybit linear trades: read, delivery and live ingest v1

**Status:** ACCEPTED (design gate #377, PR #481; accepted by the maintainer 2026-10-19, recorded in issue #377). Amendment 1
(historical source, design gate #487): ACCEPTED 2026-10-20. Amendment 2 (rebuild of damaged legacy live days, design gate #515):
ACCEPTED 2026-10-24.
**Date:** 2026-10-19

Design gate #377; evidence issue #378. Amends ADR-0040, ADR-0043 and the applicability of the Bybit `trade-v1` ordering and
eligibility profile (`conformity` §10) only where section 8 says so, and only when this ADR is `ACCEPTED`. It leaves ADR-0042 and
ADR-0044 as they are.

## Context

The platform ingests, publishes, serves and delivers Bybit linear trades for one instrument, `BTCUSDT`. The maintainer holds
Parquet `trade-v1` files for other Bybit linear USDT instruments (ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT and others) and wants to read them through the
platform, deliver them to the deck and ingest the same instruments live. #377 asks whether that is possible "without weakening the
existing fail-closed identity guarantees". Verified against `implement/fid` @ `ca7c3f3`:

| Where | What is fixed to `BTCUSDT` |
| --- | --- |
| `source_adapters/bybit.py`, `bybit_trade_v1_applies_to` | the ordering provider and the publication eligibility profile apply only to `instrument == "BTCUSDT"` |
| `access/gateway.py`, `DataGateway._validate_support` | refuses a dataset to which the provider does not apply (`UnsupportedDatasetKind`) **before the catalog is reached** |
| `source_adapters/bybit_live.py` | `SUPPORTED_SYMBOL`, `SUPPORTED_TOPIC = "publicTrade.BTCUSDT"`, checks on topic and symbol, the dataset identity |
| `application/live_ingest_server.py` | `subject_identity = f"bybit:BTCUSDT:{storage_root_id}"` |
| `application/live_gap_orchestration.py` | gap remediation bound to `bybit_live_dataset_identity()` |
| `application/bybit_import.py`, `source_adapters/bybit_historical.py` | historical import supports one instrument (`SUPPORTED_INSTRUMENT`, `SUPPORTED_SYMBOL`) |
| `execution/__init__.py` | default fee rates are the BTCUSDT VIP0 rates |
| `tools/live_ingest_server.py`, `infra/systemd/quant-platform-live-ingest.service` | no `--instrument`; one unit with fixed environment names |
| ADR-0040, ADR-0043 §5 | the live loop is "a single bounded process for exactly one venue, instrument and schema" |

Reproduced on this commit: a `DataGateway.scan` for `bybit`/`trades@1` reaches the catalog for `BTCUSDT` and is refused for
`ETHUSDT` and `SOLUSDT` with `UnsupportedDatasetKind` ("ordering provider is not applicable to the requested dataset"). So a
dataset that exists in the catalog cannot be read, admitted or delivered today whatever its content.

What already fits: the ordering key `(exchange_ts, trade_id)`, `TradeKeyV1 = (venue, instrument, exchange_ts, trade_id)`, the
dataset identity (which includes the instrument), the K12 identity texts, the J02 wire and the checkpoint identity
(ADR-0042 §4: a checkpoint carries its `dataset_identity`, so one file per instrument is a distinct recovery domain by
construction).

Reported by the maintainer, **not verified here**: the instruments' Parquet files follow the BTCUSDT structure and are already
registered. A query of the server's catalog reported in the same session lists only `BTCUSDT` (2 118 valid partitions). The two
statements conflict; the ADR does not depend on either (section 5). The storage and resource figures offered for the extra
instruments (daily volume, RSS, free space) are the kind #378 shows to be inconsistent and are not used.

## Decision

### 1. A closed allow-list of instruments

The venue is `bybit`, the market is the **linear USDT-settled perpetual** category, the dataset is canonical `trades` /
`trade-v1`. The admitted instruments are a **closed constant in code**, not a free string:

```text
BYBIT_LINEAR_TRADE_INSTRUMENTS_V1 = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")
```

One definition is used by the ordering provider's applicability, the eligibility profile, the historical import and the live
adapter. Spot, inverse, other venues and any other symbol (for example `ADAUSDT`) are not admitted. A new instrument is admitted by an amendment appended to this ADR, a change of the constant and the
capacity rule of section 6. L2 is not covered: its ordering policy is keyed by `(venue, instrument)` and stays as ADR-0070 has it.

### 2. Identity

- Dataset identity: `DatasetIdentity("canonical", "trades", "bybit", <INSTRUMENT>, "trade-v1")` for each admitted instrument.
- Live subject identity: `bybit:<INSTRUMENT>:<storage_root_id>`; checkpoint path, process and health signals are per instrument.
- Ordering applicability: `bybit_trade_v1_applies_to` is true for the allow-list. The ordering key, `TradeKeyV1`, the canonical
  record and every existing `BTCUSDT` identity are **unchanged byte for byte** (pinned by the guard test).
- Reads: nothing in the J02 wire, K12 or the DataGateway contract changes. That a non-BTC dataset is read, admitted and delivered
  end to end is **not verified yet** beyond the ordering gate; the first implementation slice proves it (section 9).

### 3. Process model: one process per instrument

Live ingest runs **one process per instrument** (ADR-0043's loop, unchanged): a systemd template unit
`quant-platform-live-ingest@<INSTRUMENT>.service` with a per-instance environment file (`QP_LIVE_INGEST_CHECKPOINT_PATH`,
subject, instrument). Rejected: one connection with several topics, because a stall or a desynchronization of one instrument would
stop the others and the checkpoint, the subject and the health signals would have to be multiplexed. The processes share one
storage root and one catalog; that concurrent instances with different instruments neither collide on checkpoints, partitions or
catalog rows is **not verified yet** and is part of the live slice's acceptance (a stop condition if they collide).

### 4. Gaps and history

ADR-0044 stands: a gap is explicit and no authoritative repair source is proven; it is not weakened here. For a new instrument:

- The live stream starts when its process starts. The period before is **not acquired**, never "complete" and never inferred.
- History for an instrument comes only from the **historical import** (the one-UTC-day import generalized to the allow-list with an
  `--instrument` argument, its source evidence to be stated by its slice) or from files the maintainer already holds, which enter
  **only through the producer path** (materialize, certify, publish), never by a direct catalog write.
- Junction between history and live carries no overlap guarantee; coverage evidence is the only claim.

### 5. Existing Parquet of the maintainer

Whether the maintainer's files are already registered is the open fact above. The decision is independent of it: an instrument's
partitions are readable only when they are in the catalog as `valid` through the producer path with the eligibility profile of
section 2. The first slice starts with a **read-only check of the catalog** for the allow-list and registers what is missing through
that path; a file that does not pass certification is not published.

### 6. Capacity gate

Enabling live ingest for more than `BTCUSDT` in production requires the Phase A measurements of #378 (disk growth, compaction,
bytes per trade, trades per second, CPU and RSS of the existing service). The rule, not a number from this ADR: the projected time
for the hot storage root to reach the **critical-pressure threshold** of the live-ingest unit at the measured rate of all enabled
instruments must be at least **365 days** (a maintainer-accepted value, to be confirmed at acceptance). Until the measurement
exists nothing beyond a bounded proof is enabled. The proof the maintainer asked for is **one hour of real provider traffic for
`ETHUSDT`** (handshake, deduplication of `trade_id`, healthy signals, durable checkpoint), executed once; `SOLUSDT`, `XRPUSDT` and `DOGEUSDT` are validated by
the same contract tests and enabled only under the capacity rule.

### 7. Fees and execution

Fee parameters are explicit per instrument. The BTCUSDT VIP0 defaults of `execution` are not applied silently to another
instrument: a replay or an execution over a dataset whose instrument is not `BTCUSDT` and that names no fee schedule is refused
(fail-closed). `BTCUSDT` behaviour is unchanged.

### 8. What this amends

| Document | Statement | After this ADR is `ACCEPTED` |
| --- | --- | --- |
| ADR-0043 §5 | "exactly one venue, instrument and schema (`bybit`/`linear`/`BTCUSDT`/`trade-v1`)" | exactly one venue, **one instrument of the allow-list per process** and one schema |
| ADR-0040 | live source `publicTrade.BTCUSDT`, `BTCUSDT` dataset | `publicTrade.<INSTRUMENT>` for each admitted instrument; semantics of `TradeKeyV1`, dedup and ordering unchanged |
| `conformity` §10 profile | applies to `BTCUSDT` | applies to the allow-list; the profile's checks are unchanged |

ADR-0044's disposition, ADR-0042 and the single-venue limit (second venue, `A12`) are untouched.

### 9. Slices this ADR implies (to be authorized by a governance reconciliation after acceptance)

1. **Read and delivery** for the allow-list: the constant, the applicability change, an end-to-end test over a non-BTC dataset
   (`DataGateway`, K12 `admit`/`deliver`), the catalog check and registration of section 5, BTC regression.
2. **Live ingest per instrument**: adapter and server parameterized by instrument, per-instance checkpoint and subject, template
   unit, runbook, concurrency proof, the one-hour `ETHUSDT` proof. Deployment stays a manual operator step.
3. **Explicit fees** per instrument (section 7).
4. **Historical import `--instrument`** over the allow-list.

## Consequences

- A dataset of any admitted instrument that is published `valid` becomes readable and deliverable like `BTCUSDT`, with no change to
  the deck, the wire or the K12/K13 contracts; the deck pins no instrument.
- Every other consumer of the BTC literals (conformity fixtures, golden proofs) is unchanged: they name `BTCUSDT` on purpose.
- Live processes multiply with the instruments (one unit each); the cost is operational and bounded by the capacity rule.
- The allow-list is one place to change, and changing it is an ADR amendment.

## Not claimed

- That the maintainer's files are registered, readable or certifiable (section 5 checks it).
- Any measured capacity, compaction behaviour or per-instrument volume (#378).
- Spot, inverse, other venues, other symbols, L2 for other instruments, a generic provider resolver.
- A repair source for gaps (ADR-0044) or a backfill of live gaps.
- A deployment: enabling the units on the server is a manual, separate operator step.
- That concurrent live processes against one catalog and storage root are safe (to be proved by slice 2).

## Acceptance record

Accepted by the maintainer on 2026-10-19 (merge of the proposal, PR #481, and the instruction to implement). The four open items of
the proposal were resolved as follows:

1. **Allow-list.** `BTCUSDT`, `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `DOGEUSDT`, as stated in section 1.
2. **Capacity.** The rule of section 6 (at least 365 days to the critical-pressure threshold at the measured rate) is accepted as
   written. The ADR is accepted **without** the Phase A numbers of #378 because the rule gates the *production enabling* of live
   ingest beyond `BTCUSDT`, not the implementation: nothing beyond the bounded `ETHUSDT` proof is enabled before the measurement
   exists, so the numbers are an input to that enabling decision, not to this contract.
3. **Existing Parquet.** Their location and registration state remain an open fact. Section 5 makes the contract independent of it;
   slice 1 begins with the read-only catalog check and registers what is missing through the producer path.
4. **Historical source.** Stated by the issue of slice 4 with its own evidence; this ADR does not choose it.

## Amendment 1 - Historical source: the Bybit public trade archive

**Status:** ACCEPTED (design gate #487, PR #488; accepted by the maintainer on 2026-10-20, see A1.6).

### A1.1 Facts

- **No other source exists.** The only historical adapter (`source_adapters/bybit_historical.py`) reads the maintainer's SQLite
  (`bybit-public-trades-sqlite-v1` / `bybit-sqlite-day-extract-v1`). The maintainer stated on 2026-10-20 that those files were made
  by the maintainer and hold only `BTCUSDT`, and that for the other instruments only Parquet files exist. The maintainer's decision
  is that every historical import, for every instrument including `BTCUSDT`, comes from Bybit.
- **The existing Parquet cannot be certified.** Reported by the server agent on 2026-10-20, not verified here: the days of `ETHUSDT`,
  `SOLUSDT`, `XRPUSDT` and `DOGEUSDT` on the hot root (8 065 days, about 130 GB) declare `source_semantics:
  bybit-public-trades-csv-v1` and `mapping: bybit-csv-day-extract-v1`. No commit on any branch of the repository has produced that
  mapping, the `producer`/`code_ref` they declare (`human-e2e-operator-harness-v1`) emits only the SQLite mapping, and
  `BybitTradeV1CertificationProfile.validate_source` accepts only the SQLite profile. Under section 5 none of them is published.
- **The archive.** `https://public.bybit.com/trading/<INSTRUMENT>/<INSTRUMENT><YYYY-MM-DD>.csv.gz`, one gzip CSV per UTC day, with a
  directory for each of the five instruments (for `ETHUSDT` the listing runs from 2020-10-21 to 2026-10-19). Four files were read on
  2026-10-20:

  | File | SHA-256 of the `.csv.gz` | Rows | Header | Row order |
  | --- | --- | --- | --- | --- |
  | `ETHUSDT2020-10-21.csv.gz` | `5400527c466922a7e982f9113d709f2803260f9481f935ebbc04a8e45fdb707d` | 11 145 | 10 columns | descending |
  | `ETHUSDT2023-06-15.csv.gz` | `7a955d4f1dd920b3d3f69ea77c823eb98a340a3f4acf8708622966abc0b3628a` | 294 839 | 10 columns | ascending |
  | `ETHUSDT2026-10-19.csv.gz` | `197fc403f19807cbfe0b10610f4d7a60719f56d34f71d0dd5ee1e5553d9d8287` | 1 428 981 | 11 columns (`RPI`) | ascending |
  | `BTCUSDT2024-01-15.csv.gz` | `b126988e2d9f0226138c0273d14a427e316b42b10a9a884ff9f520bcf44a30b9` | 1 105 145 | 10 columns | ascending |

  The 10-column header is `timestamp,symbol,side,size,price,tickDirection,trdMatchID,grossValue,homeNotional,foreignNotional`; the
  recent one appends `RPI` (every sampled value `0`). `timestamp` is decimal seconds with 0 to 4 fraction digits (100 µs at most).
  In every sample: one `symbol` equal to the instrument, `side` only `Buy`/`Sell`, `trdMatchID` non-empty and unique, every timestamp
  inside the file's UTC day. `ETHUSDT` 2020-10-21 is the listing day and starts at 09:23:20Z.
- **Equivalence with the BTC source.** `BTCUSDT` 2024-01-15 from the archive, with timestamps truncated to milliseconds, equals the
  SQLite golden fixture `fixtures/conformity/golden-bybit-btcusdt-2024-01-15.json` on row count (1 105 145), buy (553 875), sell
  (551 270), first and last `exchange_ts` (`00:00:00.492Z`, `23:59:59.931Z`) and the first and last one-minute candle (open, high,
  low, close, volume, trade count). The SQLite source is the archive at millisecond precision; the live source (`publicTrade`,
  field `T`) is milliseconds too.

### A1.2 Decision

1. **Source.** The historical source of every instrument of the allow-list, `BTCUSDT` included, is the Bybit public trade archive,
   one file per UTC day. The importer reads a **local** file that the operator downloaded; the producer path makes no network call.
   The download helper `tools/fetch_bybit_trade_archive.py` is an operator tool outside the producer path.
2. **Source profile.** New versioned names: `source_semantics = "bybit-public-trade-archive-v1"`, `mapping =
   "bybit-archive-day-extract-v1"`. The unversioned `bybit-public-trades-csv-v1` / `bybit-csv-day-extract-v1` are not adopted.
3. **Header.** Exactly one of the two headers of A1.1; any other header refuses the day. A new archive format is a new amendment.
4. **Mapping to `trade-v1`** (the record, the ordering key and `TradeKeyV1` unchanged):
   - `exchange_ts`: `timestamp` parsed as a decimal string (digits, optional point, 0 to 9 fraction digits; anything else refuses
     the day) and **truncated to milliseconds**, the precision of the SQLite source and of the live source. Proposed so that one
     instrument's history and live share one precision and an archive day of `BTCUSDT` equals its SQLite day (open item 1).
   - `instrument`: the `symbol` column, which must equal the requested instrument.
   - `aggressor_side`: `Buy` to `buy`, `Sell` to `sell`; any other value refuses the day.
   - `price`, `size`: the column text unchanged, validated as a canonical positive decimal as for the SQLite source.
   - `trade_id`: `trdMatchID`, non-empty and unique within the day; a duplicate refuses the day.
   - `receive_ts`, `sequence`: null (the archive has neither).
   - `tickDirection`, `grossValue`, `homeNotional`, `foreignNotional` and `RPI` are not carried (`trade-v1` has no field for them).
     Every row is imported whatever its `RPI` value.
5. **Day bounds and order.** Every row must fall in `[day 00:00Z, next day 00:00Z)`; one outside refuses the day. The file's row
   order is not relied on: the importer orders by the canonical key `(exchange_ts, trade_id)`.
6. **Coverage.** One assertion `complete` for the UTC day, with evidence `deterministic_source_extract` naming the archive URL, the
   file name, the SHA-256 and size of the `.csv.gz` and the row count. "Complete" means the whole day as Bybit publishes it,
   including a listing day. A day without an archive file is not acquired: no coverage, an explicit gap (section 4, ADR-0044).
7. **Certification.** The Bybit `trade-v1` certification profile accepts this source profile **in addition** to the SQLite one; its
   other checks are unchanged. A partition already `valid` from the SQLite source is untouched.
8. **Existing Parquet.** The files of A1.1 are **not registered**. Per instrument they are replaced through the producer path: the
   operator moves the instrument's directory aside on the same disk, the archive import writes and publishes each day, a comparison
   per day against the old file (`tools/compare_archive_day.py`: `trade_id` set, millisecond timestamp, side, price and size) is
   reported, and only then does the maintainer delete the old copy. Removing data stays the maintainer's step.
9. **BTCUSDT.** Any future historical import of `BTCUSDT` uses the archive. The 2 118 `valid` days from the SQLite source stay as
   they are; this amendment does not re-import them.

### A1.3 What it amends

| Place | Before | After acceptance |
| --- | --- | --- |
| Section 4, second bullet | "its source evidence to be stated by its slice" | the archive of A1.2 is that source |
| Section 5 | registers what is missing through the producer path | the files of A1.1 are not registered; they are replaced as in A1.2 item 8 |
| Acceptance record, item 4 | "this ADR does not choose it" | chosen by this amendment |
| `BybitTradeV1CertificationProfile.validate_source` | only the SQLite profile | the SQLite profile or the archive profile |

The `trade-v1` contract, the ordering key, `TradeKeyV1`, the J02 wire, K12/K13, the deck and every existing `BTCUSDT` identity are
unchanged. `T04` is re-scoped by a governance reconciliation after acceptance (archive adapter, profile extension, `--instrument`,
and a server-side proof that the archive import of `BTCUSDT` 2024-01-15 equals the existing `valid` day record by record). The
registration part of `T01` (#485) is closed by the same reconciliation: there is nothing to register.

### A1.4 Not claimed

- That every archive day has the format of the samples (A1.2 item 3 refuses the others), or that the listing has no missing days.
- That archive and SQLite agree on every `BTCUSDT` day: one day is verified on aggregates; `T04` proves one day record by record.
- That the archive is an authoritative repair source for live gaps (ADR-0044 is unchanged).
- Any download, deployment or deletion: those are operator steps.

### A1.5 Open items for the maintainer

1. **Precision.** Truncate to milliseconds (proposed) or keep the archive's 100 µs. Keeping it preserves digits that `trade-v1`
   could carry, but splits one instrument's history between two precisions (SQLite and live are milliseconds) and makes a
   `BTCUSDT` archive day differ from its SQLite day.
2. **Download.** A helper in `tools/` that fetches one day into a staging directory, or the operator's own command.
3. **Re-import of the 2 118 `BTCUSDT` days.** Proposed: no.

### A1.6 Acceptance record

Accepted by the maintainer on 2026-10-20 (the instruction to implement the amendment on PR #488). The open items of A1.5 are
resolved as proposed:

1. **Precision.** Milliseconds, truncated (A1.2 item 4).
2. **Download.** `tools/fetch_bybit_trade_archive.py` fetches days into a staging directory; the import reads the local file.
3. **Re-import of the 2 118 `BTCUSDT` days.** No; an archive import of a day that already has a catalog partition is refused.

---

## Amendment 2 - Rebuild damaged legacy BTCUSDT live days from the archive

**Status:** ACCEPTED (design gate [#515](https://github.com/NeoNix-Lab/quant-platform/issues/515), PR #516; accepted by the maintainer 2026-10-24, see A2.9)
**Date:** 2026-10-24

The accepted ADR and Amendment 1 above remain operative. This amendment is not an
implementation, a governance reconciliation or authority to change production data. The maintainer requested a reviewable reconstruction
proposal after the operator evidence below, and chose the reduced form of it
(2026-10-24): **retire** the damaged legacy day from reads with an ordinary
quality reassessment, then **import** the archive day under a free key. It does
not replace a partition atomically (see A2.8). The bounded implementation is
authorized separately (governance issue #518).

### A2.1 Problem and evidence

On `homelab`, the legacy live publisher wrote a new revision of `dt=<day>` for
each batch, superseding the preceding batch rather than accumulating the day.
The archive can supply trades never acquired between cycles; re-enabling the
superseded batches alone cannot restore them.

Operator-reported checks in the deployment conversation, on producing commit
`fc69cc17e8f8aeee1326d1b26287b8db1e61b73d`, establish the following bounded facts.
These are supplied operational evidence, not a new independent server audit.

| Check | Result |
| --- | --- |
| Catalog inventory | 31 candidate dates, 2026-09-23 through 2026-10-23; 85,056 superseded legacy revisions, 24,160,325 declared rows; 9,908 declared rows in valid legacy revisions. Totals are not unique-trade counts. |
| 2026-10-20 local files | 3,326 distinct readable Parquet footers; all row counts match the catalog; 1,142,059 rows; 41,257,944 bytes. No full physical-hash check was performed by this inventory. |
| Archive | `BTCUSDT2026-10-20.csv.gz`, 57,387,589 bytes, SHA-256 `d45890283c23973ee061364ff2faf5835dee6dc9c1fee1cbd648375377c4447d`; 1,568,861 rows and unique trade IDs. |
| In-day ID comparison | 1,141,530 local unique IDs, all in the archive; no in-day duplicate or null IDs; 427,331 archive IDs absent locally. Equality of other canonical fields was not tested. |
| Day ownership | 529 local rows fall outside 2026-10-20. Legacy catalog coverage intervals also cross UTC midnight. A partition-key date alone does not delimit the replacement's effects. |

The inventory is a candidate list, not permission to rebuild every date in it.
The 2026-10-23 sample contains both legacy revisions and new live slices; it is
excluded from the first scope. Whether a day is closed is evaluated in UTC at
execution, not from a local calendar date or this document's date.

### A2.2 Decision

Authorize an operator-requested rebuild of explicitly selected, closed UTC days of
`canonical/trades/bybit/BTCUSDT/trade-v1` whose publication is a legacy
`dt=<day>` family (many superseded revisions and one live revision that holds only
the day's last batch). Per selected day, in this order:

1. **Retire.** Record a superseding quality reassessment of the day's current live
   revision, `fail` on completeness, and apply [ADR-0030](ADR-0030-general-quality-lifecycle-v1.md)'s
   existing mapping `fail -> invalid`. Nothing is deleted or rewritten: the
   revision stays the live (non-superseded) row of its key, now `invalid`, with its
   bytes, manifests, quality reports and admissions intact. Every read policy
   excludes `invalid`, so the day is no longer served from the damaged batch.
2. **Import.** Import the whole-day archive file of Amendment 1 (same download
   identity, strict parsing, millisecond mapping, unique non-empty trade IDs,
   half-open UTC-day coverage) through the ordinary S13/S14 path as **revision 1 of
   the key `dt=<day>/seg=000000.000`**, `part-001.parquet`: the key grammar of
   ADR-0075 section 3 for a window that starts at the day's first instant. The legacy
   key stays as it is; the new key is free, so no revision is raised and no
   predecessor is superseded.
3. **Verify.** Compare the imported records with the archive file in full
   (`tools/compare_archive_day.py`), then read the day through the `DataGateway`
   strictly and check count, order and duplicates.

The ordinary importer keeps its refusal of an occupied day (A1.6 item 3). The
rebuild is a separate operation that treats the retired (`invalid`) legacy revision
of that exact day as not occupying it, and nothing else: a `valid` or `closed`
partition of the day, or any `dt=<day>/seg=...` partition other than the one it
creates, still refuses. It is never a `--force` of the ordinary importer.

### A2.3 The reassessment writer

ADR-0030 already defines reassessment: a new, target-bound, immutable quality
report that carries `metrics.supersedes_assessment_signature` of the assessment it
supersedes, and an application step that maps its status to a state. The state
transition exists (`valid` is an allowed source state for `fail`). What does not
exist is a writer of such a report for a revision that is not `closed`:
`record_quality_report` refuses any other state, and the repair path writes reports
only for candidates inside its own cutover.

The slice adds that writer, in the data plane next to the lifecycle seam, with these
properties:

- it targets the current live revision of one natural partition, locked in the same
  order as the lifecycle application, and refuses a superseded, foreign or
  `writing` revision;
- it copies the bound evidence of the assessment it supersedes unchanged (profile,
  natural identity, dataset, partition and content hashes, coverage manifest and
  assertion identities) and sets the status, the coverage category and a
  violation that names the reason (`legacy_publication_incomplete_day`) and the
  archive identity that shows it; it carries the producing commit;
- the reassessment is honest: the partition is not corrupt, it is incomplete for
  its day, and the report says so; it is idempotent (the same semantic signature
  adds no second leaf);
- the lifecycle application then runs through the existing seam, in its own short
  transaction (a few milliseconds of the dataset row lock, per partition).

**Restore.** A later reassessment `pass` that supersedes the `fail` returns the
revision to `valid` by the same seam. It is the documented way back if the import
or its verification fails and the operator prefers the damaged day to no day.

### A2.4 Plan, preconditions and refusals

A read-only plan precedes any change and is bound by a digest the executing command
must present. For each selected day it records: the incumbent revision (partition
UUID, key, revision, state, physical and manifest hashes, coverage identities), the
archive file (URL, SHA-256, size, source-observation time, row count), the producing
commit, the key to create and the result of the checks below. A changed incumbent,
source or catalog makes the plan stale; the executor never acquires another target
and never widens the date set.

A day is refused, and nothing is changed for it, if any of the following holds:

- the day is not closed in UTC: its end is less than 24 hours before execution;
- the day has a live slice (`dt=<day>/seg=...` other than the one to create), a
  checkpoint binding to any of its partitions, or an open gap record that names it;
- its current live revision is not `valid`;
- **conservation fails.** Every record of the incumbent live revision, by
  `(exchange_ts, trade_id)`, must be found with equal venue, instrument, exchange
  time, side, price and size in the archive file of its own UTC day, and that day
  must be readable after the operation (rebuilt in the same plan, or already valid
  in the catalog). A record outside the day (the 529 rows of the evidence) is
  checked against its own day, not dropped. Any difference in a common field refuses
  the day; there is no partial acceptance;
- the archive file fails Amendment 1's strict parsing or its hash differs from the
  plan.

The incumbent live revision of a legacy day holds only the day's last batch; the
superseded revisions are not read today and are not part of the conservation
check. The rebuilt day carries the archive's null `receive_ts` and `sequence`. The
rows that the incumbent revision serves lose the provider `sequence` they have now
(ADR-0040 keeps it as live evidence): this is an accepted, declared loss of
provenance on those rows, recoverable from the retained bytes, and not a claim of the
original session's continuity.

### A2.5 Readers, concurrency and failure

- **No atomic switch, by design.** Between *Retire* and *Import* the day is served
  by nothing: a strict read refuses with `NoCoverage`; `allow-partial` reports the
  gap. The order is chosen so that this is an honest absence and never a state in
  which two valid partitions cover the same day (the gateway refuses that overlap).
  The days are closed and have no consumer yet (usage tests start after the first
  version); the plan states the window per day and the executor does one day at a time.
- **Coherent reads.** `Catalog.select_partitions` selects partitions in one SQL
  statement filtered by lifecycle state, so each lifecycle commit is seen whole or
  not at all by a reader; the proofs below use that mechanism.
- **Crash and retry.** After a crash between *Retire* and *Import* the day is
  retired and unimported; rerunning the plan imports it. After a crash between the
  import and the verification the executor finds the valid key it would create
  and goes to the verification, which is read-only and repeatable. The attempt is
  identified by the plan digest.
- **Concurrency.** The lifecycle application and the import take the same dataset
  row lock as the live publication, each for a short transaction. The executor does
  not hold a lock across days, the files' reading or the verification.
- **Untouched.** Live checkpoint files, gap evidence, healthy historical days, live
  slices, other datasets and all old bytes. No deletion, catalog reset or retention
  is part of the operation ([ADR-0049](ADR-0049-retention-deletion-authority-v1.md)). Cleaning
  retired or superseded legacy revisions is the maintainer's retention decision.

### A2.6 Exact effect if accepted

| Authority | Bounded amendment |
| --- | --- |
| This ADR A1.6 item 3 and occupied-day refusal | Remains the default for ordinary import. The rebuild treats the retired legacy revision of the selected day as not occupying it and imports under `dt=<day>/seg=000000.000`; nothing else is let through. |
| ADR-0075 section 7, delegation item 3 and acceptance item 3 | Lift the pause only for the explicitly selected, closed legacy days; current slices, consolidation and live interruption evidence remain unchanged. |
| ADR-0030 (A16) | No change of meaning. Adds the writer of a reassessment report for a revision that is not `closed` (A2.3). |
| ADR-0033 (A10) | Not used. The first scope does not need an atomic replacement (A2.8); A10 and its triggers are unchanged. |
| ADR-0044 | No amendment: no automatic live-gap repair, no new proof of live continuity. |
| ADR-0018, frozen records and manifests, identity, ordering | Unchanged. The new key uses the accepted two-level grammar; no schema, migration or new control table. |
| S13, S14 | Unchanged; the import uses them as they are. |

This proposal does not accept ADR-0075 Amendment 1 (#512 / PR #513), resolve #511,
complete O02/O03 or grant capability credit. It changes no governance file.
Acceptance must be recorded explicitly and followed by a separately authorized
implementation issue and, where the active scope requires it, a governance
reconciliation. Production execution follows a reviewed, pinned implementation and a
reviewed date plan. The 31-date inventory is not that plan.

### A2.7 Required implementation and operational proof

1. **Writer slice.** The reassessment writer (A2.3) over a disposable PostgreSQL:
   `valid -> invalid` and back by a superseding `pass`; the supersession link; the
   bound evidence copied unchanged; idempotence; refusal of a superseded, foreign or
   stale target; a concurrent publication on the same dataset waits and then
   proceeds; a `VALID_ONLY` read excludes the retired revision; the old bytes,
   manifests and reports unchanged.
2. **Rebuild slice.** The importer variant, the plan and its digest, the
   conservation check, the runbook. Disposable PostgreSQL probes: a retired day is
   imported under the new key; a `valid` or `closed` incumbent, a live slice, a
   checkpoint binding, an open day, a stale plan and a differing common field each
   refuse without changing anything; a crash between *Retire* and *Import* and
   another between *Import* and the verification are recovered by rerunning; the
   restore path; a neighbouring day with its own retired revision whose legacy
   coverage crossed midnight reads correctly. Strict `DataGateway` read of the pilot
   day returns exactly 1,568,861 archive records, in order, without duplicates,
   with full-field equality to the archive (null `receive_ts` and `sequence`
   expected); non-target days and the live checkpoint are byte-identical.
3. **Operation.** Dry-run, execute and verify on `2026-10-20` first; further days
   only from a new reviewed plan. Nothing in the production run is claimed by this
   proposal.

The runtime proof is required after implementation; this design-only proposal does
not claim any of those probes passed.

### A2.8 Considered and not adopted

An atomic replacement of the live revision (retire and admit the successor in one
transaction, with the predecessor readable until the switch), built on ADR-0033's
cutover and extended to a component of adjacent days with new versioned repair
triggers, provenance and migration, a prospective certification seam in S13, and a
bounded lock hold against the live service. Its cost is several implementation
slices and a lock on the dataset row that the live publication needs, for a benefit
(no read gap) that closed days without consumers do not require. It stays available
as a later amendment if a consumer of these days appears; this proposal does not
close that door.

### A2.9 Acceptance record

Accepted by the maintainer on 2026-10-24 ("accetto la #516"), after the maintainer chose the reduced form (retire, then import)
over the atomic replacement of the first draft on 2026-10-24. The text of A2.1 to A2.8 stands as written; the
operator-reported evidence of A2.1 is accepted as evidence, not as an audit.

Not done by this acceptance: the implementation (`R01`, issue #519; `R02`, issue #520); the governance registration of those
atoms (issue #518); any change of production data; the choice of the days to rebuild, which comes only from a reviewed plan
(A2.4). Cleaning retired or superseded legacy revisions remains the maintainer's retention decision.
