# ADR-0070 - L2 contract, continuity and publication v1 (DG-C / A14)

**Status:** PROPOSED (design gate #367; accepted when the maintainer merges the PR that carries it)
**Date:** 2026-10-14

The ADR number is provisional until acceptance (the FEI gate #363 also assigns its number at
acceptance).

## Context

`A14` (L2 contract/acquisition) is `OPEN_BLOCKING` / `MISSING`. PR #317 added groundwork only:
`schemas/l2-book-event-v1.json`, `L2BookEvent` / `L2LevelChange` and
`normalize_bybit_l2_message`. No accepted ADR resolves L2 ordering, continuity, materialization,
publication or reading. Governance #364 registered the atoms `A14a`-`A14f` and fixed six owner
decisions (2026-10-14): aggregated price levels only; `A13` is not a prerequisite; `v1` is
historical ingest and `v2` is live ingest; consumer read is in `v1`; `implement/l2` stays cut
from `implement/dgk`; Bybit only.

Constraints already accepted and respected here:

- `trade-v1`, `candle-v1`, `dataset-manifest-v1`, `partition-manifest-v1` and J02 v1 stay frozen
  (ADR-0018, ADR-0050, ADR-0066 §1). Every change below is additive.
- `DATA_GATEWAY.md` §4: a new dataset kind needs usable native sequence to fully order
  equal-event-time records, or a stable tie-break primitive, before the DataGateway supports it.
- ADR-0040 sets the live convention for Bybit: `exchange_ts` is the provider *event* time (`T`),
  message `ts` is provider data-generation time and is not `receive_ts`; a disconnect is never
  reinterpreted as complete coverage; no synthetic resume cursor.
- ADR-0044: an interval that cannot be proven stays explicit non-complete.
- ADR-0066 §1: transport framing may not be used to avoid an application-level result bound.
- "No synthetic book from trades" (owner decision 1) extends to any state the venue did not send.

### Evidence

This ADR rests on four evidence sets, summarised in the Appendix. Their method is reproducible
and none of the raw data is committed.

1. **Provider documentation** (Bybit v5 public orderbook stream), read 2026-10-14.
2. **A live capture** of `wss://stream.bybit.com/v5/public/linear`, `BTCUSDT`, 150 s, depths
   50/200/1000 on one connection, 6,074 messages.
3. **Archive availability probes** (HTTP `HEAD` only; no archive file was downloaded) against
   Bybit's public order-book archive for `BTCUSDT` linear.
4. **The repository**: how `trade-v1` is materialized, certified, published and read today.

Two findings reshape the earlier proposals and are stated up front:

- **`orderbook.500` does not exist on the live feed.** The subscribe request for
  `orderbook.500.BTCUSDT` was rejected (`handler not found`). Valid live depths are 1, 50, 200
  and 1000.
- **The historical archive changed depth inside the history.** `..._ob500.data.zip` is present on probed
  dates through 2025-08-01 and absent from 2025-09-01; `..._ob200.data.zip` is present on every probed date from 2025-09 to the previous
  day. Depth 200 is therefore the only depth present in both the recent archive and the live
  feed.

## Decision

Numbers follow the DG-C question set (`OPEN_DECISIONS.md`).

### 1. Total order (Q1)

**Order is the numeric `native_sequence` (`seq`), within one stream.**

- A *stream* is one `(venue, instrument, source_channel)`. A canonical L2 dataset holds exactly
  one stream (§4).
- `seq` is compared numerically, never lexicographically (`DATA_GATEWAY.md` §4). Provider
  documentation defines a smaller `seq` as generated earlier. In the capture, `seq` was strictly
  increasing and unique on every depth (0 duplicates in 6,074 messages).
- The ordering policy is `bybit-l2-book-event-v1-seq-v1` (name: a proposal of this ADR, now
  fixed). Its key is the single integer `native_sequence`. No tie-break exists because
  duplicates are not tolerated: two events with the same `native_sequence` are an idempotent
  duplicate if their `l2_book_event_identity_v1` are equal and a fail-closed integrity conflict
  otherwise (the ADR-0040 §4 rule, last-writer-wins forbidden).
- **`exchange_ts` is not part of the key.** It is a *certified monotonic property* of the order:
  `exchange_ts` must be non-decreasing along `seq`. If it regresses, the partition is not
  eligible (`event_time_regression`). Reason: book replay is defined by the engine order; ordering
  by time first would let a clock regression reorder deltas and corrupt the book.
- **`u` (`native_update_id`) is not an ordering key.** A snapshot resets it (`u=1` after a
  service restart), so it cannot totally order a day. It is the *continuity* signal (§2).
- **`exchange_ts` is the matching-engine time `cts`; `provider_ts` is the message `ts`.** This
  follows ADR-0040 §2 and the provider documentation, which says `cts` correlates with the trade
  channel's `T`. The groundwork adapter maps `exchange_ts = ts`; `A14a` corrects it under the
  source-semantics id `bybit-orderbook-l2-v2`. In the capture `ts - cts` was 0-102 ms, so the two
  differ materially. If a source message has no `cts`, the event is refused
  (`missing_engine_time`); no fallback to `ts` is allowed, because a mixed clock inside one
  dataset would silently break the monotonicity check.
- **Rejected proposal:** `(exchange_ts, native_sequence, native_update_id)` with provider
  `bybit-l2-exchange-ts-seq-v1`. Time-first ordering is unsafe for replay (above) and
  `native_update_id` adds nothing to a key already unique by `seq`.

### 2. Reconciliation, continuity and the partition boundary (Q2)

Reconciliation is **never performed to repair data**: the platform records what the venue sent
and states, explicitly, which intervals support a book and which do not.

- **Segment.** A *continuity segment* starts at a `snapshot` event and continues while every
  following `delta` has `u == previous u + 1`. In the capture, 100% of consecutive messages
  satisfied `+1` on every depth; each stream had one snapshot (the first message) and no
  re-snapshot in 150 s.
- **A snapshot always starts a new segment**, whatever its `u` (including `u=1`). The platform
  never infers that a snapshot is "the same book".
- **A discontinuity** is a delta whose `u` is not `previous u + 1` without an intervening
  snapshot. The interval from the last event of the previous segment to the next snapshot is
  `book_state_unknown`: it is recorded as a `known_gap` coverage assertion with
  `sequence_discontinuity` evidence (both already exist in declared coverage v1) and is never
  filled, never interpolated and never reconstructed. Coverage assertions may not overlap and a
  `complete` assertion names exactly one partition, so a day with a discontinuity is split into
  `complete` and `known_gap` intervals. Events inside that interval are preserved as received
  (lossless) but flagged `unanchored` by the certification report.
- **A `seq` regression** (a smaller `seq` after a larger one in source order) is not a
  discontinuity: it makes the partition ineligible (`sequence_regression`). Whether Bybit's
  `seq` survives a service restart is *not* established by the evidence (a restart did not
  occur during the capture); the rule is therefore fail-closed regardless.
- **Snapshots are in-band.** An archive or live feed contains its own snapshots. No snapshot is
  ever fabricated to anchor a partition (owner decision 1; ADR-0044).
- **Partition boundary.** Partitions are natural UTC days by `exchange_ts` (`dt=YYYY-MM-DD`,
  `part-001.parquet`, as for `trade-v1`). **No book state is carried in a partition.** Continuity
  across the boundary is carried as *evidence*, not as state: the `detail` text of the coverage
  evidence records the first and last event of the interval (`native_sequence`,
  `native_update_id`, `event_type`); the coverage and partition manifests gain no field.
  Whether day D+1 continues day D is a derived check (`first.u == last.u + 1` with no snapshot in
  between) recorded as `sequence_continuity` evidence. A day that does not start with a snapshot
  and does not continue its predecessor has an *unanchored head*: its events before the first
  snapshot are `unanchored`, and the partition state is `degraded`, not ineligible.
- **Replay needs an anchor.** A book *as of t* is the replay of events from the latest snapshot
  at or before `t`; it can cross a day boundary. That is a property of the read (§6), not of
  storage.

### 3. Events, not a reconstructed book (Q3)

The canonical layer stores **source events**: snapshots and deltas exactly as normalized by the
adapter, with level order preserved (the identity hash already refuses to sort levels).

- A reconstructed or resampled book is a *derived* representation with its own identity and is
  out of `v1`. Nothing derived is written to the canonical layer.
- The replay semantic is fixed by the events: apply `upsert` (insert or overwrite size) and
  `delete` (size `"0"`, remove the level); a `snapshot` replaces the whole book. A `delete` of an
  absent level is a source-semantics violation counted by certification (0 occurrences in the
  capture; the archive's behaviour is an evidence item, see Open evidence).
- Depth is bounded by `source_depth_limit`; a replayed book with more levels than that limit
  on a side is a certification violation.

### 4. Layout, identity and manifests (Q4)

- **Identity.** `(canonical, l2, bybit, BTCUSDT, l2-book-event-v1)`. `l2` is already a valid
  `dataset_kind` for raw and canonical in the frozen manifest vocabulary, and `_derive_rel_root`
  yields `canonical/l2/bybit/BTCUSDT/l2-book-event-v1`. **No manifest or `DatasetIdentity`
  evolution is needed for `v1`.**
- **One stream per dataset.** Every event of the dataset has the same `source_channel` and
  `source_depth_limit`. `v1` fixes them to `orderbook.200.BTCUSDT` and `200` (see §7). A second
  depth cannot share the identity; it needs a new identity dimension, which is a separate
  contract evolution under ADR-0018 and is **not** made here.
- **Path.** `canonical/l2/bybit/BTCUSDT/l2-book-event-v1/dt=YYYY-MM-DD/part-001.parquet`,
  Zstandard, row-group size 65,536 (the `trade-v1` convention, accepted as proposed).
- **Columns** (one row per event, schema identity `l2-book-event-v1`, optional fields nullable):
  `exchange_ts` and `provider_ts` as UTC nanosecond timestamps; `receive_ts` nullable;
  `event_type` dictionary string; `native_sequence` and `native_update_id` as **unsigned 64-bit
  integers** so that the order is numeric; `source_depth_limit` int32; `continuity_token` string;
  `bids` and `asks` as `list<struct<price: string, size: string, action: string,
  order_count: string?>>` with exact decimal text as in `trade-v1`. Exact `price` text
  (for example `"81887.80"`) is preserved, never renormalized.
- **Declared coverage** reuses ADR-0022/ADR-0029: bases `source_archive` (historical) and
  `live_stream` (v2); evidence kinds `archive_completeness`, `sequence_continuity`,
  `sequence_discontinuity`, all existing. A day is `complete` only with attributable
  `archive_completeness` evidence and no unresolved discontinuity; absence of events is neither
  completeness nor a gap (ADR-0040 §8).
- **The `trade-v1` code paths are not modified.** `materialize_trade_v1`, the Parquet scanner,
  `PublicationCertification` and the DataGateway support check are each bound to `TradeRecord`
  or `trade-v1` today. `A14b`/`A14c`/`A14e` add L2 siblings; a shared helper may be extracted
  only where the existing trade test suite proves byte-identical behaviour.

### 5. Certification and publication eligibility (Q5)

- **Profile** `producer-consumer-conformity-v1/bybit-l2-book-event-v1` (S13/S14 vocabulary,
  as for `trade-v1`); check suite `producer-consumer-conformity-v1/bybit-l2-book-event-v1-checks`.
- **Checks**, all fail-closed unless marked:
  1. every record validates against `l2-book-event-v1` and matches the dataset identity;
  2. single stream: constant `source_channel` and `source_depth_limit`;
  3. strictly increasing `native_sequence` in physical order, no duplicates;
  4. `exchange_ts` non-decreasing along `native_sequence`;
  5. segment accounting: `u + 1` continuity per segment, discontinuities enumerated
     (non-fatal: they set state `degraded` and produce non-complete coverage);
  6. replay sanity per segment: no crossed book after any event; side depth within
     `source_depth_limit`; deletes of absent levels counted (policy value fixed from the Open
     evidence item below, default **0 allowed**);
  7. coverage fold equals the sealed catalog row (ADR-0022, S13);
  8. physical artifact size and SHA-256 equal manifest evidence; row count and observed bounds
     equal the partition manifest.
- **Outcomes.** Checks 1-4, 6 (violation) and 7-8 make the partition ineligible. Check 5
  findings and an unanchored head make it `degraded`, which is an eligible lifecycle state
  already (`closed`/`valid`/`degraded`). A `degraded` partition is published with its
  non-complete coverage; consumers see `complete: false` and the gaps.
- **Catalog.** `market_catalog` rows carry `kind="l2"` through the existing identity columns;
  the catalog DDL needs no change (to be confirmed by the `A14c` slice with a PostgreSQL
  integration test, not assumed).

### 6. Read surface (Q6)

- **A new representation, additive.** `representation = { kind: l2_book_events, version: 1 }`,
  resolved by the application layer to `(canonical, l2, bybit, BTCUSDT, l2-book-event-v1)` and
  ordering policy `bybit-l2-book-event-v1-seq-v1`. The consumer never names a dataset, schema or
  policy (`CONSUMER_API.md`). J02 v1 carries `representation.kind` and `.version` as data, so
  the wire contract is unchanged. The scope stays `j02.market_data.read` (ADR-0063); **no
  amendment to an accepted ADR is required.**
- **Gateway.** DataGateway gains an L2 read path under the same `DataRequest` shape. The
  `trade-v1` path and its `UnsupportedDatasetKind`/`UnsupportedSchema` behaviour are unchanged;
  an L2 request to the trades path is still refused.
- **Temporal coordinate.** Selection is `[start, end)` over `exchange_ts`; results are in
  `native_sequence` order. A result that starts mid-segment is returned as stored and marked
  `anchored: false` until its first `snapshot`; **the read never fabricates a snapshot** and
  never replays a book. A pure `replay` helper over events is a library function with explicit
  anchor rules; it is not a stored artifact and not a J02 operation in `v1`.
- **Bound.** `DEFAULT_MAX_RESULT_ROWS = 50,000` is calibrated to ~263 B per trade and would be
  ~53 MB for depth-200 deltas (about 1,064 B per delta and 9.4 KB per snapshot in the capture),
  above the 16 MiB J02 ceiling. The L2 representation therefore owns a **byte-based** bound on
  encoded result size, refused as the existing `result_too_large` with `max_result_bytes` in the
  context. Per ADR-0066 §1 framing is not used to avoid it: a caller walks the interval in
  slices, as for A07. In-process consumers (deck, ADR-0057) read through DataGateway directly.

### 7. Fate of `l2-book-event-v1`, depth and the proposals (Q7)

- **`l2-book-event-v1` is ratified as is.** The JSON schema bytes do not change and no field is
  added. This ADR is the ADR-0018 step that `CAPABILITY_DAG.md` required ("ratify or supersede").
  The normative field semantics are the ones in §1: `exchange_ts` = `cts`, `provider_ts` = `ts`,
  `native_sequence` = `seq`, `native_update_id` = `u`, `continuity_token` derived.
  No L2 data has been persisted yet, so correcting the adapter mapping (§1) changes no stored
  identity.
- **Depth 200 is the only `v1` depth.** `source_channel = orderbook.200.BTCUSDT`. Reasons:
  it is the only depth in both the recent archive (`ob200`, 2025-09 onward) and the live feed
  (`orderbook.200`), so `v2` can extend `v1` without an identity change; `orderbook.500` is
  not a valid live topic; and a 500-level delta stream cannot be truncated to 200 without
  reconstructing the book, which would be synthetic.
- **`ob500` history (up to 2025-08) is out of `v1`.** An `ob500` day is refused with
  `source_depth_unsupported`, not converted.
- **Rejected proposals:** topics `orderbook.50`/`orderbook.500`; `exchange_ts`-first ordering key
  and `bybit-l2-exchange-ts-seq-v1`. **Accepted proposals:** the `dt=` path and Zstandard,
  with the typed column set in §4 (the Arrow schema is now fixed, not left open).
- **Retention of raw bytes.** The raw source archive is `RAW` under ADR-0032; the canonical
  layer is derived from it and a single day is on the order of 0.2 GB zipped and ~1 GB of
  JSON lines. Bulk data stays outside Git.

### 8. Live supervision, v2 (Q8)

`A14f` extends the `v1` dataset; it does not define a second one. The rules fixed now:

- **Reuse the live-ingest host** (ADR-0041/ADR-0042/ADR-0043): one supervised process, durable
  publication before checkpoint advance, restart from the last durable checkpoint.
- **One WebSocket subscription**, `orderbook.200.BTCUSDT`. Message loss, reconnect or a
  provider-initiated re-snapshot always opens a new segment; there is no REST history for
  books, so **no repair path exists** and ADR-0044's `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN`
  applies unchanged: an interval across a disconnect is explicit non-complete.
- **Resync means waiting for the next in-band `snapshot`** (a re-subscribe elicits one), never
  inventing one. The interval between the last durable event and that snapshot is
  `book_state_unknown` (§2).
- **Snapshot and delta rates are not assumed.** The capture shows ~10 messages/s at depth 200
  (~0.87 M events/day); buffering, batching and day-roll behaviour are `A14f` implementation
  evidence, to be proven in a bounded soak, not asserted here.
- **Cutover.** Historical and live meet at a `native_sequence` cut: `seq <= cut` historical
  authority, `seq > cut` live authority, with the ADR-0040 §5 duplicate/conflict rule on any
  overlap.
- **Out of `v1`:** everything in this section is `v2`; `A14f` is not authorized by this ADR's
  acceptance alone (it stays registered, not implemented).

## Atoms (the set `A14a`-`A14f`, now statable)

| Atom | Final name | In | Out | Requires |
|---|---|---|---|---|
| `A14a` | Bybit L2 source semantics, ordering provider and certification profile | `L2BookEvent`; ADR-0070 | `bybit-orderbook-l2-v2` adapter mapping; provider `bybit-l2-book-event-v1-seq-v1`; profile and check suite | `A14`, ADR-0070 |
| `A14b` | Canonical L2 Parquet materializer and scanner | ordered `L2BookEvent` rows | sealed `part-001.parquet` + physical/semantic hashes | `A14a` |
| `A14c` | L2 certification, publication and catalog bridge (`kind="l2"`) | sealed partition, manifests, coverage | `valid`/`degraded` catalog row | `A14a`, `A14b`, `A08`, `A09` |
| `A14d` | Bybit historical L2 ingest (`ob200` day) with per-day outcome | archive day file | published partition + coverage, or typed refusal | `A14b`, `A14c`, `A07` |
| `A14e` | `l2_book_events@1` consumer read | `DataRequest` / Consumer query | ordered events, coverage, gaps, `anchored` flag | `A14c`, `B02` |
| `A14f` | Bybit live L2 ingest and resync (`v2`) | `orderbook.200` stream | durable partitions, checkpoints, explicit gaps | `A14d`, `A11` |

Dependency order for `v1`: `A14a` -> `A14b` -> `A14c` -> (`A14d` and `A14e`, independent).

### v1 golden proof

Historical ingest -> materialization -> certification -> catalog publication -> consumer read of
one pinned UTC day of the `ob200` archive.

- **Fixtures.** A pinned day (candidate: a day in the `ob200` range such as 2025-12-01; the
  A07 golden day 2024-01-15 is an `ob500` day and cannot be reused). The archive's SHA-256 is
  recorded; a committed excerpt is limited to what is needed to exercise snapshot, delta,
  delete and one discontinuity, under the repository's no-bulk-data rule.
- **Hermetic/real split.** Hermetic: synthetic and excerpt fixtures prove every check and
  outcome. Real: one full day, run by the maintainer on their machine, produces the evidence
  recorded in the closeout.
- **Acceptance.** The published partition's `canonical_content_hash_v1` and physical SHA-256 are
  reproducible; the consumer read of an interval returns the same events as the materializer
  input in `native_sequence` order, with coverage and `anchored` flags; an `ob500` day and a
  conflicting duplicate are refused with their typed reasons.

## Open evidence (does not block acceptance; each has a fixed rule)

These are measurements `A14a` must make before the check-suite parameters are frozen. The rules
above hold whatever the outcome; the outcome only fixes how much data is eligible.

| # | Question | Rule that already applies |
|---|---|---|
| E1 | Format of an `ob200` day file (fields, presence of `cts`/`u`/`seq`, first record type) | An event without `cts` is refused (§1); a day not starting with a snapshot has an unanchored head and is `degraded` (§2) |
| E2 | Does `seq` survive a provider restart | A regression makes the partition ineligible (§2) |
| E3 | Rate of deletes of absent levels in the archive | Default 0 allowed (§5 check 6); a measured non-zero floor is a profile parameter, set by the maintainer |
| E4 | Does the archive ever contain re-snapshots mid-day | Each starts a new segment (§2) |

## Consequences

- `A14a`-`A14e` can be implemented without further semantic decisions. `A14f` has its supervision
  rule; its soak evidence remains to be produced.
- No accepted ADR is weakened; no frozen contract changes; no existing identity changes.
- Adding a second depth or a second venue requires an identity dimension (ADR-0018 evolution),
  decided separately.
- The `trade-v1` code paths are untouched; L2 is a sibling set of modules.
- The consumer read is bounded by bytes, so a day is read in slices; in-process consumers are
  not bounded by J02.
- `OPEN_DECISIONS.md` DG-C Q1-Q8 are answered by this ADR; the governance reconciliation after
  acceptance re-states `A14a`-`A14f` as authorized (as DG-K did with #319).

## Decisions for the maintainer

Acceptance is the merge of this PR. These are the choices that change scope most; reject or
amend any of them in review:

1. **Depth 200 only in `v1`** and `ob500` history excluded (§7).
2. **`exchange_ts` = `cts`**, `provider_ts` = `ts`, refuse events without `cts` (§1).
3. **Order by `seq` alone**; `exchange_ts` is a certified property, not a key (§1).
4. **A discontinuity degrades, a `seq` regression or `exchange_ts` regression rejects** (§2, §5).
5. **A byte-based result bound** for the L2 representation (§6).
6. **Sibling L2 modules; no change to the trade path** (§4).

## Appendix - evidence

### A. Provider documentation (Bybit v5, public orderbook), 2026-10-14

Linear/inverse and spot: L1 10 ms, L50 20 ms, L200 100 ms, L1000 200 ms. First message after
subscribe is a `snapshot`; deltas follow; a new `snapshot` resets the local book; `u=1` can occur
after a service restart; size `0` deletes; `seq` is the cross sequence, comparable across depths,
smaller means earlier; `cts` is the matching-engine timestamp and correlates with `T` in the
public trade channel. L1 is snapshot-only with repeated `u`. The documentation does not describe
out-of-order delivery or detecting gaps beyond the snapshot reset rule.

### B. Live capture, 2026-10-14, BTCUSDT linear, one connection, 150 s

| Topic | Messages | Rate | Snapshots | `u` step | `seq` | `ts` ties | `cts` | Mean delta |
|---|---|---|---|---|---|---|---|---|
| `orderbook.50` | 3,815 | 25.4/s | 1 | always +1 | strict, unique | 0 | monotone | 321 B, 5.1 levels |
| `orderbook.200` | 1,506 | 10.1/s | 1 | always +1 | strict, unique | 0 | monotone | 1,064 B, 38.3 levels |
| `orderbook.1000` | 753 | 5.0/s | 1 | always +1 | strict, unique | 0 | monotone | 2,292 B, 93.7 levels |

Also: `orderbook.500` refused; `ts - cts` 0-11 ms (depth 50), 0-102 ms (depth 200), 0-23 ms
(depth 1000); 479 `seq` values shared between depth 50
and depth 200, none with depth 1000; zero sizes were always the string `"0"`; no delete of an
absent level and no crossed book in any replay; the existing adapter normalized 6,074 of 6,074
messages. One snapshot is 2.5 KB (50), 9.4 KB (200), 46 KB (1000). Estimated 0.87 M events/day
at depth 200.

The capture is 150 s on one connection and shows no restart. It supports the rules; it does not
prove them across restarts or days (Open evidence E2, E4).

### C. Archive availability probes, 2026-10-14 (HEAD requests, no download)

`BTCUSDT` linear, `https://quote-saver.bycsi.com/orderbook/linear/BTCUSDT/<date>_BTCUSDT_<obN>.data.zip`:
`ob500` present 2024-01-15, 2025-06-01, 2025-07-01, 2025-08-01 and absent from 2025-09-01;
`ob200` absent on every probed date up to 2025-08-01 and present on every probed date from
2025-09-01 to 2026-10-13 (monthly probes, plus 2025-06-01 and 2026-10-10/13); the 2026-10-13
file is published at about 00:11 UTC the next day. A day is about 210-250 MB zipped. File
contents were **not** inspected.

## Related

Governance #364 (PR #360). Design gate #367. Consumes ADR-0004, ADR-0018, ADR-0022, ADR-0029,
ADR-0030, ADR-0032, ADR-0040, ADR-0041-0044, ADR-0050, ADR-0054, ADR-0057, ADR-0063, ADR-0066,
ADR-0068. Groundwork: PR #317 / #316.
