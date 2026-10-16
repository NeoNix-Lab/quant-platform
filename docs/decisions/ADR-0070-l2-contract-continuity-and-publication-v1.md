# ADR-0070 - L2 contract, continuity and publication v1 (DG-C / A14)

**Status:** ACCEPTED (design gate #367, PR #370, 2026-10-15; amended by Amendment 1, issue #402)
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
  feed. Pre-2025-09 history is admitted by an explicit, deterministic top-200 projection (§9),
  not by changing the depth of the dataset.

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
  out of `v1`. The one derived canonical content is the top-N *event* projection of §9, which
  carries explicit lineage and is certified by recomputation; nothing else derived is written to
  the canonical layer.
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
- **One effective depth per dataset.** Every event of a partition has the same `source_channel`
  and `source_depth_limit`, and the dataset has one *effective book depth*, 200: a partition is
  either source-acquired (`orderbook.200.BTCUSDT`, `source_depth_limit = 200`) or a §9
  projection (`source_depth_limit = 500`, manifest transform `top_n = 200`). A second
  *effective* depth cannot share the identity; it needs a new identity dimension, a separate
  contract evolution under ADR-0018 that is **not** made here.
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
  2. single stream per partition: constant `source_channel` and `source_depth_limit`, and an
     effective depth equal to 200 (source depth 200, or the recorded projection `top_n = 200`);
  3. strictly increasing `native_sequence` in physical order, no duplicates;
  4. `exchange_ts` non-decreasing along `native_sequence`;
  5. segment accounting: `u + 1` continuity per segment, discontinuities enumerated
     (non-fatal: they set state `degraded` and produce non-complete coverage);
  6. replay sanity per segment: no crossed book after any event; side depth within
     `source_depth_limit`; deletes of absent levels counted (policy value fixed from the Open
     evidence item below, default **0 allowed**);
  7. coverage fold equals the sealed catalog row (ADR-0022, S13);
  8. physical artifact size and SHA-256 equal manifest evidence; row count and observed bounds
     equal the partition manifest;
  9. a projected partition (§9) equals the recomputation of its projection from the raw
     partition it declares in `derived_from`.
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
- **Depth 200 is the only effective depth.** Source-acquired events are
  `orderbook.200.BTCUSDT`. Reasons: it is the only depth in both the recent archive (`ob200`,
  2025-09 onward) and the live feed (`orderbook.200`), so `v2` can extend `v1` without an
  identity change; and `orderbook.500` is not a valid live topic.
- **`ob500` history (up to 2025-08) is admitted by projection (§9), after `A14d`.** It is not
  converted silently and not mixed as depth 500: it is a derived partition with lineage. Until
  `A14g` exists, an `ob500` day is refused with `source_depth_unsupported`. Any other depth
  (`ob50`, `ob1000`, `orderbook.1000`) is refused the same way; only 200 and the §9 projection
  from 500 are admitted.
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

### 9. Projection of deeper history, follow-on `A14g` (outside the `A14` closure)

Pre-2025-09 history exists only as depth 500. It is brought into the depth-200 dataset by a
**deterministic top-N projection** of the source's own events, declared as such.

- **Premise, tested.** A book of depth N is the top-N of a deeper book at the same `seq`. In the
  capture, the top-50 of the replayed depth-200 book equalled the depth-50 book at all 479
  shared `seq` values (Appendix D). The 500 -> 200 case is the same principle but cannot be
  tested directly: there is no live depth 500, and depth 1000 shares no `seq` with depth 200.
- **Raw first.** The `ob500` day is stored in the raw layer as normalized `L2BookEvent` rows at
  depth 500, lossless (`raw`, `l2`, `bybit`, `BTCUSDT`, `l2-book-event-v1`; kind `l2` is
  already in the raw vocabulary; ADR-0032). Consumers never read raw.
- **Projection.** Replay the raw events per continuity segment (§2) from a snapshot. After each
  source event `k`, let `T_k` be the top 200 levels per side (bids by descending price, asks by
  ascending price). The emitted changes are the symmetric difference of `T_{k-1}` and `T_k`:
  `upsert` for a level new to the window or with a changed size, `delete` (size `"0"`) for a
  level that left the window, whether removed from the book or pushed below rank 200. A
  difference-free event emits nothing. A source `snapshot` emits a full top-200 `snapshot`. This
  is the same behaviour the 200-level stream shows: its book size stayed exactly 200 per side.
- **Event fields.** `exchange_ts` = source `cts`, `provider_ts` = source `ts` (an event
  without `cts` is refused); `native_sequence` = source `seq`; `source_channel` and
  `source_depth_limit` stay the true source (`orderbook.500.BTCUSDT`, `500`) so that provenance
  is not falsified; `native_update_id` is the *source stream's* `u` and is not comparable to the
  200 stream's `u`.
- **Continuity.** The `u + 1` rule is checked on the *raw* segments before projection. Because
  projected events skip source events, `u + 1` is not a property of the projected rows; the
  segment boundaries and the discontinuity intervals are recorded in the coverage evidence
  `detail`, and the same `known_gap` / `degraded` outcomes apply (§2).
- **Lineage.** The manifest carries `derived_from` (the raw partition) and
  `transform = project-top-n-v1` with `top_n = 200`. Lineage is per partition, so source-acquired
  and projected days coexist in one dataset and a read exposes each partition's origin.
- **Certification by recomputation** (check 9, §5): re-projecting the raw partition must
  reproduce the projected partition's `canonical_content_hash_v1`. A projected partition whose
  raw partition is missing or altered is ineligible.
- **What it does not claim.** A projected day is not byte-comparable with a genuine `ob200`
  day: it has fewer events (changes confined to ranks 201-500 emit nothing) and different
  `native_update_id`. The consumer-visible book state at each retained `seq` is the claim.
- **No synthetic data.** Every level and size comes from a venue message; nothing is inferred
  from trades; a book is never invented across a discontinuity.

`A14g` has its own golden (the A07 day 2024-01-15, an `ob500` day) and is not part of the
`A14` closure, like `A14f`.

## Atoms (the set `A14a`-`A14g`, now statable)

| Atom | Final name | In | Out | Requires |
|---|---|---|---|---|
| `A14a` | Bybit L2 source semantics, ordering provider and certification profile | `L2BookEvent`; ADR-0070 | `bybit-orderbook-l2-v2` adapter mapping; provider `bybit-l2-book-event-v1-seq-v1`; profile and check suite | `A14`, ADR-0070 |
| `A14b` | Canonical L2 Parquet materializer and scanner | ordered `L2BookEvent` rows | sealed `part-001.parquet` + physical/semantic hashes | `A14a` |
| `A14c` | L2 certification, publication and catalog bridge (`kind="l2"`) | sealed partition, manifests, coverage | `valid`/`degraded` catalog row | `A14a`, `A14b`, `A08`, `A09` |
| `A14d` | Bybit historical L2 ingest (`ob200` day) with per-day outcome | archive day file | published partition + coverage, or typed refusal | `A14b`, `A14c`, `A07` |
| `A14e` | `l2_book_events@1` consumer read | `DataRequest` / Consumer query | ordered events, coverage, gaps, `anchored` flag | `A14c`, `B02` |
| `A14f` | Bybit live L2 ingest and resync (`v2`) | `orderbook.200` stream | durable partitions, checkpoints, explicit gaps | `A14d`, `A11` |
| `A14g` | Bybit `ob500` raw ingest and top-200 projection (follow-on) | `ob500` archive day | raw partition + projected canonical partition with lineage, or typed refusal | `A14d`, `A14b`, `A14c` |

Dependency order for `v1`: `A14a` -> `A14b` -> `A14c` -> (`A14d` and `A14e`, independent).
`A14g` follows `A14d` and, like `A14f`, is outside the `A14` closure; `A14g` is not registered in
`CAPABILITY_DAG.md` yet and is added by the reconciliation that follows acceptance.

### v1 golden proof

Historical ingest -> materialization -> certification -> catalog publication -> consumer read of
one pinned UTC day of the `ob200` archive.

- **Fixtures.** A pinned day (candidate: a day in the `ob200` range such as 2025-12-01; the
  A07 golden day 2024-01-15 is an `ob500` day, usable only by the `A14g` golden of §9). The
  archive's SHA-256 is
  recorded; a committed excerpt is limited to what is needed to exercise snapshot, delta,
  delete and one discontinuity, under the repository's no-bulk-data rule.
- **Hermetic/real split.** Hermetic: synthetic and excerpt fixtures prove every check and
  outcome. Real: one full day, run by the maintainer on their machine, produces the evidence
  recorded in the closeout.
- **Acceptance.** The published partition's `canonical_content_hash_v1` and physical SHA-256 are
  reproducible; the consumer read of an interval returns the same events as the materializer
  input in `native_sequence` order, with coverage and `anchored` flags; an unsupported depth and
  a conflicting duplicate are refused with their typed reasons (an `ob500` day is refused until
  `A14g`).

## Open evidence (does not block acceptance; each has a fixed rule)

These are measurements `A14a` must make before the check-suite parameters are frozen. The rules
above hold whatever the outcome; the outcome only fixes how much data is eligible.

| # | Question | Rule that already applies |
|---|---|---|
| E1 | Format of an `ob200` and of an `ob500` day file (fields, presence of `cts`/`u`/`seq`, first record type) | An event without `cts` is refused (§1); a day not starting with a snapshot has an unanchored head and is `degraded` (§2) |
| E2 | Does `seq` survive a provider restart | A regression makes the partition ineligible (§2) |
| E3 | Rate of deletes of absent levels in the archive | Default 0 allowed (§5 check 6); a measured non-zero floor is a profile parameter, set by the maintainer |
| E4 | Does the archive ever contain re-snapshots mid-day | Each starts a new segment (§2) |
| E5 | Is the top-200 projection of an `ob500` day indistinguishable from a genuine depth-200 book | Only the 50 vs 200 case is proven (Appendix D); `A14g` acceptance adds a parity test on a real day pair, and the claim stays "book state at retained `seq`" (§9) |

## Consequences

- `A14a`-`A14e` can be implemented without further semantic decisions. `A14f` has its supervision
  rule; its soak evidence remains to be produced.
- No accepted ADR is weakened; no frozen contract changes; no existing identity changes.
- Adding a second *effective* depth or a second venue requires an identity dimension (ADR-0018
  evolution), decided separately. Pre-2025-09 history needs none: it enters through §9.
- One canonical dataset can hold source-acquired and projected partitions; consumers see the
  origin per partition and must not assume byte-equal event streams across the 2025-09 boundary.
- The `trade-v1` code paths are untouched; L2 is a sibling set of modules.
- The consumer read is bounded by bytes, so a day is read in slices; in-process consumers are
  not bounded by J02.
- `OPEN_DECISIONS.md` DG-C Q1-Q8 are answered by this ADR; the governance reconciliation after
  acceptance re-states `A14a`-`A14f` as authorized and registers `A14g` (as DG-K did with #319).

## Decisions for the maintainer

Acceptance is the merge of this PR. These are the choices that change scope most; reject or
amend any of them in review:

1. **Effective depth 200**; `ob500` history enters only by the §9 projection (`A14g`), never as
   depth 500 in the dataset (§7, §9).
2. **`exchange_ts` = `cts`**, `provider_ts` = `ts`, refuse events without `cts` (§1).
3. **Order by `seq` alone**; `exchange_ts` is a certified property, not a key (§1).
4. **A discontinuity degrades, a `seq` regression or `exchange_ts` regression rejects** (§2, §5).
5. **A byte-based result bound** for the L2 representation (§6).
6. **Sibling L2 modules; no change to the trade path** (§4).
7. **A declared exception to "no derived canonical content"** for the §9 projection, certified by
   recomputation and carried with per-partition lineage (§3, §9).

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

### D. Projection principle test, 2026-10-14 (same capture as B)

The depth-200 and depth-50 streams were each replayed (snapshot, then upsert/delete). At every
`seq` present in both (479), the top 50 levels per side of the depth-200 book were compared with
the depth-50 book by price and size text. **479 of 479 were identical**; the depth-50 book held
exactly 50 levels per side after warm-up (3,805 of 3,805 events). This supports the §9 premise
for N = 50 vs 200. It is not a test of 500 vs 200.

## Related

Governance #364 (PR #360). Design gate #367. Consumes ADR-0004, ADR-0018, ADR-0022, ADR-0029,
ADR-0030, ADR-0032, ADR-0040, ADR-0041-0044, ADR-0050, ADR-0054, ADR-0057, ADR-0063, ADR-0066,
ADR-0068. Groundwork: PR #317 / #316.

## Amendment 1 (issue #402) — partitioning at continuity discontinuities

**Date:** 2026-10-16
**Amends:** §2 (discontinuity and partition boundary), §3 (lossless wording), §4 (partition key and path), §5 (outcomes), Consequences. **Does not amend:** §1, §6-§9, the atom set, the evidence items, or any other ADR.

### Why

`A14c` (PR #401) found that §2/§5 cannot be realized on the frozen coverage fold. §2 split a day with a `u` discontinuity into `complete` and `known_gap` intervals *inside one partition* and §5 published it as `degraded`. The fold (`data/coverage.py`, ADR-0022/ADR-0029) requires one contiguous `complete` interval per partition (`COVERAGE_NOT_CONTIGUOUS`), forbids it to overlap a non-complete assertion (`COVERAGE_CONTRADICTION`) and requires every observed record to lie inside it (`OBSERVED_OUTSIDE_DECLARED`); the quality lifecycle accepts a `warn` only when the `source` category warns and the partition still seals. The maintainer chose, on 2026-10-16, to keep the frozen contracts and cut the day instead (**owner decision 7**). A gap *between* partitions is already representable: the fold attributes complete coverage per partition and ADR-0029 defines non-contiguous reads.

### Decision

1. **Publishable run.** A *run* is a maximal sequence of events that starts at an in-band `snapshot` and ends immediately before the delta that opens a discontinuity (the A14a definition: `u` is not `previous u + 1` with no snapshot in between), or at the end of the source day. Several snapshots inside one run are not cuts (a snapshot without a `u` gap loses nothing). The cut points are exactly the discontinuities reported by the A14a certifier over the whole source day; no second definition exists.
2. **What is excluded.** Events inside a `book_state_unknown` interval (from the delta that opened it to the event before the next snapshot, or to the end of the day) and events before the first snapshot of the day carry no usable book state. They are **not written to the canonical layer**. They remain in the retained source archive (ADR-0032) and are counted, with the source archive's SHA-256, in the gap evidence. Every canonical L2 event is therefore anchored.
3. **Partition key and path.** Uniform for every day, content-independent in form and deterministic in value: partition key `dt=YYYY-MM-DD/seg=NNN`, where `NNN` is the 1-based ordinal of the run within the UTC day of its first event, zero-padded to at least three digits; artifact path `canonical/l2/bybit/BTCUSDT/l2-book-event-v1/dt=YYYY-MM-DD/seg=NNN/part-001.parquet`. A day with no discontinuity is exactly one partition, `seg=001`. Every event of a run must lie inside the UTC day of its source file (item 4 refuses the day otherwise); the live case is item 9.
4. **Declared coverage.** One coverage document **per run** and one **per gap**, all with `basis = source_archive` and intent `[day start, day end)`. The documents are separate because S13 evidence is partition-scoped: `seal` folds the supplied documents against the one partition being sealed and any complete reference to another partition is `PARTITION_UNKNOWN`, so a document used as evidence names exactly one partition. Coverage timestamps carry at most six fractional digits (`coverage-manifest-v1`), so the unit is **1 µs**: for a time `t`, `ceil_us(t)` is the least µs-aligned instant strictly greater than `t`, and `floor_us(t)` the greatest µs-aligned instant not greater than `t`.
   - the run's document carries one `complete` assertion, naming exactly that partition and revision. `start` is the day's intent start for the first run when the day's first event is a snapshot, and `floor_us(first event time)` otherwise; `end` is `ceil_us(last event time of the run)` when a gap follows, or the intent end for the last run of a day that ends cleanly. Evidence: `archive_completeness` and `sequence_continuity` whose `detail` is `bybit_l2_continuity_evidence_detail` of that partition's own events.
   - each gap document carries one `known_gap` assertion (never a partition reference) for an excluded interval: `[ceil_us(last event time of the previous run), floor_us(first event time of the next run))`, or `[ceil_us(last event time of the last run), intent end)` when the day ends inside a discontinuity, or `[intent start, floor_us(first snapshot time))` for events before the first snapshot. Evidence: `sequence_discontinuity` whose `detail` is `expected_u:<n>|observed_u:<n>|excluded_events:<n>|source_sha256:<hex>`, or `unanchored_head|excluded_events:<n>|source_sha256:<hex>` for the head.
   - If a gap interval is empty or negative (`ceil_us(last) >= floor_us(next first)`, for example two events in the same microsecond), the day is refused with `discontinuity_not_representable`. A day with no run (empty, or no snapshot at all) is refused with `no_anchored_run`, and a day with an event whose `exchange_ts` lies outside the UTC day of its source file is refused with `event_outside_day`. Nothing is published for a refused day.
5. **Certification.** Each run is a partition certified `eligible` by A14a and published `valid` through the unchanged S13/S14 path. A partition that contains a discontinuity or an unanchored head never exists in the canonical layer, so the `A14c` rule that fails such a partition stays as the guard against a producer defect. The `degraded` outcome of A14a is a *producer-side signal* used to find the cut points; it is never a published lifecycle state for L2 in `v1`.
6. **Binding of continuity evidence.** The `sequence_continuity` evidence of a `complete` assertion binds to the bytes of the partition the assertion names. Because the evidence documents of one S13 run name exactly one partition (item 4), the `A14c` binding check (`validate_bybit_l2_coverage_against_events`) needs no change.
7. **Feasibility, checked on the merged code.** A source day with one `u` discontinuity (two runs, two excluded events) was cut as above with `materialize_l2_book_event_v1`, `emit_partition_manifest` and `emit_coverage_manifest` (one document per run, one per gap). `reconstruct_catalog_coverage` over all documents and both partitions returned no violation and the folded intervals `seg=001 = [day start, ceil_us(last event of run 1))` and `seg=002 = [floor_us(first event of run 2), day end)`; each run, run through `L2PublicationCertification` with its own document, certified `pass`. No frozen code was modified.
8. **Re-acquisition.** A source day is acquired once. A repeated acquisition must reproduce byte-identical partitions (same keys and `canonical_content_hash_v1`); any difference in the set of runs or in a run's content is refused (`day_already_published_differently`) and is outside `v1` (repair is ADR-0033's explicit-defect flow, not an acquisition loop).
9. **Live day roll (`v2`), principle only.** No synthetic snapshot is ever written. A run that crosses UTC midnight is split at the day boundary; the continuation partition (`dt=<next day>/seg=001`) starts with a delta and is anchored only by evidence that links its predecessor partition (key, revision, last `native_sequence` and `native_update_id`) and that the certifier verifies against the catalog. The mechanics (a certifier parameter and the catalog lookup) are fixed in the `A14f` issue; this is the one item the amendment defers, because it depends on measured live behaviour.
10. **Effect on the atoms.** `A14d` finds the cut points with the A14a certifier, writes one partition per run with `materialize_l2_book_event_v1`, emits the partition manifests and the per-run and per-gap coverage documents above, and refuses a day as stated; `A14c` and `A14a` are unchanged; `A14e` reports each `known_gap` as a consumer gap and never fills it. `A14b` must accept the `raw` layer when `A14g` is implemented (already recorded for that atom).

### Superseded text

| Where | Original | Replaced by |
|---|---|---|
| §2, third bullet | "…so a day with a discontinuity is split into `complete` and `known_gap` intervals. Events inside that interval are preserved as received (lossless) but flagged `unanchored` by the certification report." | Items 1, 2 and 4: the day is cut into runs; excluded events are not in the canonical layer. |
| §2, "Partition boundary" | "Partitions are natural UTC days by `exchange_ts` (`dt=YYYY-MM-DD`, `part-001.parquet`, as for `trade-v1`)… A day that does not start with a snapshot and does not continue its predecessor has an *unanchored head*… the partition state is `degraded`." | Items 3, 4 and 9: partitions are runs, `dt=YYYY-MM-DD/seg=NNN`; events before the first snapshot are excluded and declared as a `known_gap`; the live continuation head is item 9. |
| §3, opening sentence | "The canonical layer stores **source events**: snapshots and deltas exactly as normalized by the adapter…" | Unchanged in meaning for anchored events; unanchored events are excluded (item 2). |
| §4, "Path" | `canonical/l2/…/dt=YYYY-MM-DD/part-001.parquet` | Item 3. |
| §4, "Declared coverage" | "A day is `complete` only with attributable `archive_completeness` evidence and no unresolved discontinuity…" | Item 4: a run is `complete`; the discontinuity is a `known_gap` between partitions. |
| §5, "Outcomes" | "Check 5 findings and an unanchored head make it `degraded`… A `degraded` partition is published with its non-complete coverage…" | Item 5: a partition is `eligible` or it does not exist; `degraded` is a producer-side signal. |
| Consequences | "…the governance reconciliation after acceptance re-states `A14a`-`A14f` as authorized…" | Unchanged; a further reconciliation re-states `A14d` and `A14f` after this amendment. |

### Consequences

- The frozen coverage fold, the manifests, the catalog DDL, J02 v1 and `trade-v1` are unchanged. No accepted ADR other than ADR-0070 §2/§3/§4/§5 as listed is weakened.
- Events in a `book_state_unknown` interval are not in the canonical dataset. They are not lost: the source archive is retained and the gap evidence names it by SHA-256 and counts the events.
- A day with many discontinuities yields many small partitions; the number is bounded by the source's re-snapshot behaviour, which E4 measures.
- The partition-key shape `dt=…/seg=…` is a two-level key already accepted by the manifest key pattern; the catalog's one-live-revision-per-key rule applies per run.
- `A14d` and `A14f` need a governance reconciliation (a separate `governance/` issue) to restate their sequencing notes; `A14c` needs no change.
- The evidence items E2-E5 are unchanged. A measured discontinuity rate changes how many partitions a day yields, not the rule.
