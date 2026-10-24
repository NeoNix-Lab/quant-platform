# ADR-0074 - Multi-instrument Bybit linear trades: read, delivery and live ingest v1

**Status:** ACCEPTED (design gate #377, PR #481; accepted by the maintainer 2026-10-19, recorded in issue #377). Amendment 1
(historical source, design gate #487): ACCEPTED 2026-10-20.
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

**Status:** PROPOSED (design gate [#515](https://github.com/NeoNix-Lab/quant-platform/issues/515))
**Date:** 2026-10-24

The accepted ADR and Amendment 1 above remain operative. This proposal is not an
implementation, a governance reconciliation, an acceptance record or authority to
change production data. The maintainer requested a reviewable reconstruction
proposal after the operator evidence below. Acceptance and the bounded
implementation authorization are separate steps.

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

The inventory is a candidate list, not permission to replace every date in it.
The 2026-10-23 sample contains both legacy revisions and new live slices; it is
excluded from the first replacement scope. Whether a day is closed is evaluated
in UTC at execution, not from a local calendar date or this document's date.

### A2.2 Proposed decision and authority boundary

Authorize an operator-requested reconstruction of explicitly selected, closed
UTC days of legacy `canonical/trades/bybit/BTCUSDT/trade-v1` from the whole-day
archive of Amendment 1. This is replacement of damaged historical publication,
not restoration of the original live session's continuity or sequence evidence.

The ordinary archive importer keeps its existing refusal for an occupied day.
Reconstruction is a separate application operation composed over the existing
data-plane repair owner, never a `--force` bypass in the ordinary importer.
Healthy historical/SQLite days, new live slice keys, other datasets and the
active ingest checkpoint are not replacement targets.

ADOPT the immutable candidate staging, evidence bindings, lock order,
compare-and-cutover, quality/eligibility owners and durable convergence principles
of [ADR-0033](ADR-0033-backfill-repair-v1.md). ADAPT their application composition
for this bounded defect. Do not introduce a parallel repair engine.

Current `CoverageGapTrigger` cannot replace an occupied natural key, and
`InvalidRevisionTrigger` requires an authoritative failed assessment and an
`invalid` predecessor. A legacy final batch can legitimately be `valid` over its
small interval; calling it invalid merely because it is not a whole day would
falsify its existing assessment. Neither v1 trigger authorizes this operation.

The proposed additive repair semantics are explicitly named
`quant-platform/a10-legacy-day-rebuild-v1`, distinct from
`quant-platform/a10-repair-v1`. Their predecessor-bound trigger records the
confirmed legacy publication defect, requested whole-day support, archive
identity and exact incumbent evidence. Existing v1 trigger eligibility, intent
IDs and outcomes keep their meaning. Implementation must specify and test the
new intent/plan serialization and dispatch; it must not reinterpret old intents.

### A2.3 Inspectable plan and temporal closure

Before materialization, a read-only plan captures:

- the authorized dataset, explicit UTC days and reason for reconstruction;
- each incumbent partition UUID, key, revision, lifecycle state, physical and
  manifest hashes, coverage documents/hashes, declared intervals and paths;
- the source URL, file SHA-256, byte size, source-observation time, expected
  count, mapping/profile, producing code commit and planned candidate revision;
- coverage, observed-record and adjacent-day effects; active checkpoint
  bindings; overlapping slice or healthy-history identities;
- immutable candidate locations, resource bounds, a canonical plan digest and
  per-partition repair obligations bound to that exact plan.

Re-read and compare this evidence under the repair owner's locks before any
visibility change. A changed predecessor, new overlap, changed source or changed
checkpoint relationship makes the plan stale; it cannot silently acquire a
different target or authorize another date.

Every incumbent effect must be contained in the explicitly authorized union of
closed days, using both declared coverage and actual record bounds. A legacy
batch spanning midnight can couple adjacent days. The plan reports that closure
and refuses execution if it reaches an unapproved date, healthy historical day,
live slice, checkpoint-bound partition or active/current UTC day. An adjacent day
can enter only through a newly approved plan; no automatic date-range expansion,
clipping of old manifests, or loss of the 529 out-of-day rows is authorized.

A connected component of approved adjacent legacy days is one atomic visibility
unit, while each candidate remains its own UTC-day partition with its own source
and certification. Independent components can execute separately and report
separate outcomes. This is explicit caller orchestration, not native aggregate
range completeness or a scheduler (ADR-0054).

The first 2026-10-20 pilot validates preparation and refusal/cutover semantics in
disposable PostgreSQL, including neighboring production-shaped intervals. It is
not permission to replace that day in production while its boundary closure is
unresolved. A plan whose closure cannot meet these restrictions remains blocked.

### A2.4 Candidate evidence and immutable storage

ADOPT Amendment 1's source profile, strict archive parsing, millisecond mapping,
unique non-empty trade IDs, canonical ordering and half-open UTC-day coverage.
Complete means the day as the archived source publishes it, not measured provider
infallibility or certified continuity of the old WebSocket session.

Materialize each candidate in a collision-safe immutable location distinct from
every incumbent artifact and manifest. Its final partition revision is the
contiguous successor captured by the plan, not revision 1 by convenience; its
files and coverage documents bind that revision. Dataset identity and existing
dataset-manifest bytes remain unchanged. Use paths representable by the existing
manifest schemas, with no overwrite or directory move of incumbent data.

Preparation validates file and canonical-content hashes, full source-to-candidate
canonical-record equality, ordering, uniqueness, identity and day ownership. It
produces authentic source-specific certification evidence for the candidate's
final identity using S13's verification semantics without calling its ordinary
predecessor-superseding seal. Fabricated quality reports or copied predecessor
reports cannot authorize replacement. Failed attempts retain their own evidence
for inspection but never alter predecessor visibility.

Archive reconstruction preserves the archive's null `receive_ts` and `sequence`;
it does not fabricate live-only fields. Live-to-archive comparison distinguishes
those expected provenance differences from changes in venue, instrument,
exchange time, side, price or size. ID membership alone is not a full-field gate.
Source observation, preparation and cutover times are recorded honestly; neither
availability nor the producing commit is backdated to the trade date.

### A2.5 Cutover, readers and restart

The repair owner provides a transaction-scoped composition for the new semantics:
all relevant dataset/lineage locks in the existing order, then affected partition
topologies in deterministic order, then under-lock reauthorization of the whole
plan. No component commits between its daily candidates.

Within that transaction, admit the final candidate revisions, apply the existing
A16 and S14 transaction-scoped decisions, supersede the exact approved incumbent
revisions, and persist predecessor/candidate/plan convergence provenance. Refusal
of any candidate rolls back the whole component, leaving all predecessors as
they were. Commit only when every candidate is eligible and the component's
coverage/identity/row-equality gates pass. Retain authentic failed-attempt evidence
outside the rolled-back visibility transaction.

This extends A10 composition only for the new version. It does not weaken S13's
ordinary durable phase boundaries or globally suppress its commits. A10's
existing cutover already reuses A16/S14 rather than calling ordinary S13 admission;
the bounded extension must retain that ownership and evidence discipline.

Readers resolving an affected component must use a coherent catalog generation
for partition selection and coverage evidence, or receive a typed retry/refusal.
One read may see all of the old component or all of the new component, never a
mixture. Immutable retained predecessor files let an already-resolved read finish
after cutover. Application composition owns DataGateway read-back; the data repair
package must not import Access to perform it (ADR-0019 and ADR-0033).

Persist a versioned plan/attempt/convergence record bound to the exact evidence;
do not overload existing v1 provenance rows or infer completion from matching
content hashes. A crash before commit leaves old visibility intact; prepared
immutable files remain unpublished evidence. After commit, an exact replay
returns durable convergence, not a second successor revision. A changed plan or
candidate is a distinct attempt and must be reauthorized. A lost response after
commit is resolved from provenance before any retry mutates data.

Existing gap evidence and live checkpoint bytes remain untouched. Reconstruction
does not mark historical live gaps repaired, rewind a checkpoint, claim missing
sequence continuity, or enable the archive as an automatic live repair source.
Already sealed admissions and in-flight deliveries retain their referenced bytes
and source identities; new admissions resolve the new eligible revision. No
retention or physical/catalog deletion is part of this operation (ADR-0049).

### A2.6 Exact effect if accepted

| Authority | Bounded amendment |
| --- | --- |
| This ADR A1.6 item 3 and occupied-day refusal | Remain the default for ordinary import; the separately authorized legacy-day reconstruction above is the explicit exception. |
| ADR-0075 section 7, delegation item 3 and acceptance item 3 | Lift the pause only for the explicitly approved historical legacy-day reconstruction; current slices, consolidation and live interruption evidence remain unchanged. |
| ADR-0033 decision and partition-bounded cutover | Add the distinctly versioned legacy-defect trigger and atomic composition of approved adjacent daily obligations; existing v1 triggers and cutover remain unchanged. |
| ADR-0044 | No amendment: no automatic live-gap repair or new proof of live continuity. |
| ADR-0018, frozen records/manifests, identity and ordering | No in-place changes. New repair control/provenance semantics need an explicit versioned contract and migration in their own authorized implementation slice. |
| S13, A16 and S14 ordinary publication | Existing phase boundaries and eligibility/quality authority remain; reconstruction composes the repair-owned transaction-scoped seams. |

This proposal does not accept ADR-0075 Amendment 1 (#512 / PR #513), change its
numbering, resolve #511, complete O02/O03 or grant new capability credit. It does
not change SCOPE, Roadmap, Capability Map/DAG or Open Decisions. Acceptance must be
recorded explicitly and followed by a separately authorized implementation issue
and governance reconciliation where the active scope requires one. Production
execution follows a reviewed, pinned implementation and a reviewed date plan.

### A2.7 Required implementation and operational proof

1. **Plan/refusal slice.** Versioned defect, plan and provenance contracts; read-only
   inventory and boundary closure. Tests reject healthy historical/SQLite days,
   slices, open UTC days, checkpoint targets, unapproved neighbors and stale plans.
2. **Candidate slice.** Immutable archive mapping/materialization and genuine
   final-identity certification evidence; prove full-field equality, malformed
   source refusal, hash/coverage binding and concurrent-attempt isolation. The
   2026-10-20 pilot must contain exactly 1,568,861 archive records, all in-day.
3. **Cutover slice.** Disposable PostgreSQL probes for S13-equivalent verification,
   A16/S14 refusal, failure after first candidate admission, process/connection
   loss before and after commit, stale predecessor evidence and exact retry.
   Every failed component leaves predecessor rows, bytes and readability intact.
   Real concurrent reader/writer interleavings prove coherent reads, loser
   non-mutation and lock ordering against ordinary S13/S14/A16. A two-day
   midnight-crossing fixture proves no adjacent-day record or coverage loss.
4. **Read-back/operator slice.** Strict DataGateway reads compare the full archive
   records, count, ordering and duplicate keys over each replaced day; verify
   unchanged non-target coverage, old admission bytes and live checkpoint. Repeat
   recovery from a lost successful response. Provide resource/time bounds and an
   inspect/prepare/execute/resume runbook. No automatic rollback or deletion.

The runtime proof is required after implementation; this design-only proposal
does not claim any of those probes passed. Only after the isolated pilot and
boundary-safe production plan succeed can the operator reconstruct further
explicitly approved closed days. The 31-date inventory is not that execution plan.
