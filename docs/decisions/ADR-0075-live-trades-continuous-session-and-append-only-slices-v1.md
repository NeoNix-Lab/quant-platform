# ADR-0075 - Live trades: one continuous session and append-only slices v1

**Status:** PROPOSED (design gate #504)
**Date:** 2026-10-21

Amends ADR-0040 (§6, §8), ADR-0042 (§1) and ADR-0043 (§1) only where section 8 says so, and only when this ADR is `ACCEPTED`. It
leaves ADR-0044, ADR-0074 and the frozen `trade-v1` record, the ordering key and `TradeKeyV1` as they are.

## Context

The live-ingest server (ADR-0043) runs on the maintainer's server for `BTCUSDT`. Two defects of how it publishes were measured on
2026-10-21 with a read-only query on the production catalog and with the Bybit public archive. Both are structural: they follow
from the code, not from the host.

### F1. A live day's readable data is only the last cycle

`live_ingest_server` publishes every cycle through `run_real_server_publish_proof`, as a new revision of one partition per UTC
day, `dt=<day>`. `publication_catalog.seal_partition` allows exactly one live revision per partition key: sealing revision `N+1`
sets revision `N` to `superseded`. A cycle's file holds only that cycle's trades, so each publication replaces all earlier ones.
Catalog partitions of `canonical`/`trades`/`BTCUSDT` with more than one revision, most recent days:

| partition key | revisions | rows `valid` | rows published (all revisions) |
| --- | --- | --- | --- |
| `dt=2026-10-21` | 952 | 643 | 184 001 |
| `dt=2026-10-20` | 3 326 | 548 | 1 142 059 |
| `dt=2026-10-19` | 1 826 | 58 | 497 234 |
| `dt=2026-10-18` | 3 150 | 116 | 646 370 |
| `dt=2026-10-17` | 3 325 | 36 | 331 672 |
| `dt=2026-10-16` | 3 317 | 249 | 1 032 031 |
| `dt=2026-10-15` | 3 324 | 307 | 1 507 772 |
| `dt=2026-10-14` | 3 318 | 564 | 1 223 528 |
| `dt=2026-10-13` | 3 317 | 61 | 1 063 378 |
| `dt=2026-10-12` | 3 316 | 588 | 909 174 |

A `DataGateway` read with `VALID_ONLY` returns under 0.4% of what was published on each of these days. Reproduced hermetically by
`tests/integration_live_two_instruments_postgres.py` (PR #502): of 6 published trades per instrument a read returns the 2 of the
last revision. The superseded revisions' files are still on disk, and `operations/retention.py` classifies `superseded`
partitions as retention candidates by age.

### F2. About 27% of the trades are never acquired

For `dt=2026-10-20` the live path published 1 142 059 trades. The Bybit public archive file of that day,
`BTCUSDT2026-10-20.csv.gz` (SHA-256 `d45890283c23973ee061364ff2faf5835dee6dc9c1fee1cbd648375377c4447d`, 57 387 589 bytes, read
2026-10-21), holds **1 568 861** rows with 1 568 861 distinct `trdMatchID`. At least 426 802 trades, 27.2%, were not acquired (more
if cycles re-delivered trades, which the published total would then include).

The cause is the loop. Each cycle opens a WebSocket, listens for at most `max_seconds_per_cycle` (20 s), closes it, publishes
(S13, S14, a read-back) and waits `cycle_interval_seconds` (5 s) before reconnecting: 3 326 cycles that day, one per 26 s, so the
process listened for 77% of the time and captured 72.8%. ADR-0040 §7 and ADR-0042 §5 require after every disconnect a bounded
reconciliation from the last durable key or an explicit gap; the steady state of `live_ingest_server` reconciles only at start,
at restart or after a cycle that proved nothing (its own docstring says so), so the pause between two ordinary cycles is neither
reconciled nor recorded as a gap, and the checkpoint advances across it (ADR-0042 §4 forbids bypassing an interruption).

### Constraints that frame the answer

- The partition-key grammar already accepts a two-level key (`dt=YYYY-MM-DD/seg=<token>`, `data/manifests._PARTITION_KEY`), and
  ADR-0070 (item 3 and its consequences) uses it for L2: one partition per run, with the catalog's one-live-revision-per-key rule
  applying per run. The read path selects partitions by interval; many disjoint partitions per dataset are ordinary.
- K12 admission carries at most 16 members and 4 MiB per request (`DEFAULT_MAX_MEMBERS`, `DEFAULT_MAX_TOTAL_BYTES`). At the
  archive day's busiest hour (14:00 UTC, 309 847 trades) five minutes are about 26 000 trades; at the 28 to 37 bytes a trade
  that the reported `BTCUSDT` storage (58.5 GB over 2 118 days) and the archive's gzip size imply, about 1 MB. This is an
  estimate, not a measurement of the slice files.
- `trade-v1`, the ordering key `(exchange_ts, trade_id)` and `TradeKeyV1` are frozen; so is the per-instrument identity of
  ADR-0074.

## Decision

### 1. One continuous session per instance

The WebSocket session is not closed between publications. A reader keeps receiving while a slice is being published; received
messages go into a **bounded in-memory buffer** (default 20 000 messages), and the publisher drains it. There is no interval during which the process is
not listening unless the session itself is interrupted. A buffer that would overflow is an **interruption** (item 5), never a
silent drop. Instance, subject, checkpoint and dataset stay per instrument (ADR-0074).

### 2. The slice

The unit of publication is a **slice**: the trades of one instrument whose `exchange_ts` lies in a half-open window
`[B(k-1), B(k))`, published as one partition. Windows of one session **chain exactly**: the end of a window is the start of the
next. Boundaries are defined in exchange time only, from the stream itself:

- a slice closes after `slice_seconds` of wall clock (default 300) or `slice_max_trades` trades (default 50 000), whichever comes first, at a message
  boundary;
- the boundary `B(k)` is the greatest `exchange_ts` received so far; trades with `exchange_ts == B(k)` stay in the buffer for the
  next slice, so a slice never splits a millisecond between two partitions;
- a window never crosses a UTC midnight: the boundary is forced to the midnight instant and the later trades open the next slice;
- a window with no trades is not published; the next window then simply starts at the last published boundary;
- the first window of a recovery domain starts at the first trade received; the first window after a restart starts at the last
  published boundary (item 5).

The stream is assumed **non-decreasing in `exchange_ts`**, apart from ties. This is an assumption, not a proven provider
guarantee: a received trade with `exchange_ts` below the last boundary is a violation, handled as an interruption (item 5), and
its count is reported.

### 3. Partition identity: append-only, never superseded

Each slice is **revision 1 of its own partition**: partition key `dt=<UTC day of the window start>/seg=<HHMMSS.mmm of the window
start>`, artifact `part-001.parquet`, partition and coverage manifests named by the same key. The key is derived from the window,
so it is deterministic and two slices never share a key or a file name (today's `part-HHMMSS` wall-clock names can collide when
two publications fall in one second). No publication supersedes another; the one-live-revision rule applies per slice. The
coverage of a slice is `complete` over its window, backed by the session-continuity evidence of ADR-0040 §8; consecutive slices
chain by construction.

### 4. Checkpoint

The invariants of ADR-0042 do not change: publication is durable before the checkpoint advances; the key is monotonic; the
domain is the instrument. The checkpoint now binds a **slice partition** (its key, revision 1 and manifest hash) instead of a
revision of the day. The window end `B(k)` is read from the catalog coverage of the bound partition, so no new checkpoint field
is required. A crash between publication and checkpoint advance is replayed and deduplicated as ADR-0042 §3 says.

### 5. Interruption and reconnect

A real disconnect, a buffer overflow or an ordering violation ends the session and is handled as ADR-0040 §7 and ADR-0042 §5
already decide: reconnect, bounded reconciliation from the last durable key through the recent-trades window, and the
interval is either proven continuous (the next slice starts at the last published boundary and carries the reconciled trades) or
recorded as an explicit non-complete gap (ADR-0044, unchanged) before a new window starts after it. The interruption is evidence
(a signal and, when unproven, a `known_gap` manifest); it is never turned into a `complete` claim.

### 6. Readers

`DataGateway` reads slices as ordinary disjoint partitions: no change to the read contract. A K12 admission window of `N` minutes
carries about `N / 5 + 1` members at the default, within 16 for windows up to about an hour; its byte bound (4 MiB) is a property
of the admission, not of this ADR, and is unchanged.

### 7. Not decided here

- **Day consolidation.** With slices a closed day is stored as about 288 small partitions. Consolidation would later replace
  them with one partition per day (merging the slices, or taking the day from the Bybit archive), so that a read of a whole day
  opens one file instead of 288. It is an optimization of reads, not a condition of correctness: with slices every trade is
  already readable and nothing is superseded. It is therefore not part of this decision, and it is opened only if a measured read
  or admission cost asks for it.
- **The already damaged days** (the supersession of F1 and the loss of F2 in the existing catalog) are **not repaired**.
  Maintainer's instruction of 2026-10-21: recovery is paused; if a gap makes the data unusable the history is re-ingested from
  scratch. Existing live partitions stay as they are.
- Retention and deletion authority (K09).

### 8. What this amends

| Document | Statement | After this ADR is `ACCEPTED` |
| --- | --- | --- |
| ADR-0043 §1 | the loop "acquire one bounded batch / publish / advance / if the session dropped: loop back" | acquisition is one continuous session decoupled from publication by a bounded buffer; the per-cycle `max_messages`/`max_seconds` become slice bounds; a drop is item 5 |
| ADR-0040 §6, §8 | session lifecycle and coverage per acquisition | the session persists across publications; coverage is per slice window and chains |
| ADR-0042 §1 | the checkpoint binds the publication of the day's partition revision | it binds a slice partition (revision 1) |
| Live certification profile (`BybitLiveTradeV1CertificationProfile`) | one `dt=<day>` key | the key grammar `dt=<day>/seg=<token>` and the chain of windows |

ADR-0040 §4, §5, §7, §9, §10, ADR-0042 §2 to §7 and ADR-0044 are untouched.

### 9. Slices this ADR implies (to be authorized by a governance reconciliation after acceptance)

1. **Append-only slices:** the window, the partition key, revision 1, coverage chain, the certification profile and the checkpoint
   binding (items 2 to 4), over the existing bounded acquisition. Ends F1 on its own.
2. **Continuous session:** the reader and the bounded buffer, the slice bounds, the interruption handling and the signals
   (items 1 and 5). Ends F2.
3. **Deployment and proof:** the configuration (`slice_seconds`, `slice_max_trades`, the buffer bound) in the unit templates and the
   runbook, and a real-provider run of at least one hour that reports captured trades against the archive of the same day and
   the number of ordering violations.

## Consequences

- A read of a live day sees every trade acquired, in order; nothing is superseded by a later publication.
- The server listens continuously; the only losses are interruptions, and each one is explicit evidence.
- About 288 partitions per instrument per day at the default (one per five minutes), against about 3 300 revisions per day today:
  fewer files and fewer catalog rows, and none of them superseded.
- The publication path of a historical day (one partition, revision 1) and the archive import are unchanged.
- `T02` part 6 (the one-hour `ETHUSDT` proof) is best done once item 9.3 exists, so that its evidence covers capture and ordering.

## Not claimed

- That Bybit's stream is non-decreasing in `exchange_ts`; the proof of item 9.3 measures it, and a violation is fail-closed.
- That the existing damaged days are recoverable or repaired; they are left as they are.
- Any measured read latency over many slices, or a choice of the consolidation policy.
- That 300 s, 50 000 trades and 20 000 messages are the best bounds; they are chosen by the estimates of the bounds paragraph
  of item 2 and are checked, not proven, by the proof of item 9.3.
- A deployment: enabling a changed server on the host is a manual operator step.

## Choices made under the maintainer's delegation (2026-10-21)

The maintainer delegated the bounds and the order of work to the author of this proposal and asked to test as early as possible.

1. **Bounds** (configurable; these are the defaults):
   - `slice_seconds` **300**: at the busiest hour of the archive day five minutes are about 26 000 trades, about 1 MB (estimate),
     and a K12 window of up to an hour is at most 13 members, within the 16 allowed. One minute would give 1 440 partitions a day
     and 61 members an hour, over the limit.
   - `slice_max_trades` **50 000**: about 1.9 MB at 37 bytes a trade (estimate), under the 3 MiB per member that K12 carries; the
     bound only binds on a burst, since five minutes of the busiest hour is 26 000.
   - **Buffer 20 000 messages**: the busiest hour is at most 86 trades a second, hence at most 86 messages a second (one trade
     per message at worst); 20 000 messages cover at least about four minutes of stalled publication, against the second or two a
     publication takes in the runs of PR #502. A larger stall is an interruption (item 5).
2. **Order of work: 9.1, then 9.2, then 9.3.** Slice 9.1 is small and makes every day readable at once (ends F1); slice 9.2 ends F2;
   slice 9.3 holds the configuration and the one-hour real-provider run, which then also covers `T02` part 6.
3. **The existing live partitions.** Left as they are; recovery is paused (item 7). Nothing in the repository runs retention or
   deletion by itself: the only callers of `operations/retention.py` and `application/retention_deletion.py` are
   `tools/dispose_source_copy.py` (disposal of a source copy) and the wave-6 golden proof, both started by an operator, and
   neither is pointed at canonical `superseded` partitions. The policy code does classify such partitions as candidates by age,
   so a deletion that an operator builds over them would remove them. The cleanup code is the maintainer's and stays untouched
   by this ADR.
4. **Consolidation:** not now (item 7).
