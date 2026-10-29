# Scope: DG-N — Multi-instrument Bybit linear trades v1, DG-O — Live publication v1, and DG-R — Rebuild of damaged legacy live days v1

Status: **DG-N and DG-O CLOSED within recorded bounds (closeout #527); DG-R OPEN, post-version.** DG-N: design gate CLOSED; `T01`, `T04`, `T03` and `T02` COMPLETE (reconciliations #489, #527; PR #486, #488, #492, #494, #496, #498, #500, #502, #526). DG-O: design gate CLOSED (ADR-0075 `ACCEPTED` 2026-10-21, #504); `O01`, `O02` and `O03` COMPLETE (PR #510, #524, #526; governance #507, #527). **The one-hour `ETHUSDT` real-provider run of `O03` (the `T02` proof) was not performed and is not claimed** (the maintainer's decision, 2026-10-25): the atoms are credited within the bound of their code, configuration, runbook, evidence tool and tests. DG-R: design gate CLOSED (ADR-0074 Amendment 2 `ACCEPTED` 2026-10-24, #515); `R01` and `R02` AUTHORIZED (governance issue #518; their code is merged, #519 and #520, and their credit is a closeout's, not given here). ADR-0074 Amendment 3 `ACCEPTED` 2026-10-29 (design gate #538, PR #539 and #541): `R03` and `R04` AUTHORIZED (governance issue #540), post-version, not started. They use the integration branch `implement/dgr-closure`, cut from `main` (the maintainer's scheme, 2026-10-29: main, then the slice's integration branch, then the issue branches). Measured on the maintainer's server on 2026-10-21: the live publication superseded each cycle with the next (a day read as under 0.4% of its published trades) and about 27% of a day's trades were never acquired; DG-O fixes both in the code, which is **not deployed** and was never run against the real provider. The integration branch is `implement/fid`, the current wave (the maintainer's choice, 2026-10-19). The operator promoted it to `main` once already (PR #514, 2026-10-24: DG-M, DG-N and `O01`); a later promotion carries the rest. The first version of the product is declared concluded by the maintainer, after this closeout. DG-R is post-version work and does not block that declaration. **Today `BTCUSDT` is the only instrument ingested live and registered on the server, and the server runs the code deployed before DG-O: its live days remain not fully readable or complete until the operator deploys and (for the old days) DG-R rebuilds them; the other four instruments are readable once their partitions are `valid`.**

Scope kind: a sequencing scope for three gates: DG-N (design gate closed; Part B authorized inventory), DG-O (design gate closed; Part C authorized inventory) and DG-R (design gate closed; Part D authorized inventory, post-version).

---

## Historical scope archive

This file previously recorded **DG-M — Canonical feature identity for deck results v1** (CLOSED by governance issue #475 and the follow-on #479; atoms `M01`, `M02`, `M04`, `M05` merged into `implement/fid`), and before it DG-C / A14 (closeout #442) and DG-K2 (closeout #409). All are preserved in git history (`git log -- SCOPE.md`) and are not duplicated here, except for the facts this scope depends on. `SCOPE.md` is overwritten in place for each governance reconciliation.

---

## Why this scope exists

The platform ingests, publishes, serves and delivers Bybit linear trades for one instrument. The maintainer holds `trade-v1` Parquet files for other Bybit linear USDT instruments and wants them readable, deliverable to the deck and ingested live. At the registration (#483) the DataGateway refused any non-BTC dataset before it reached the catalog (`bybit_trade_v1_applies_to` was `BTCUSDT` only), the live adapter, the live server subject, the historical import and the default fees were bound to `BTCUSDT`, and ADR-0043 states a single-instrument invariant. ADR-0074 is the answer; this scope sequences its implementation. `T01` (PR #486) and `T04` (PR #488) have since changed the read, delivery and historical import; the live adapter, the live subject and the default fees remain bound to `BTCUSDT` until `T02` and `T03`.

---

## Fixed inputs (already decided; not reopened)

1. **ADR-0040, ADR-0041, ADR-0042, ADR-0043, ADR-0044:** the live trades acquisition, the checkpoint identity (it carries its `dataset_identity`), the single-process loop and the explicit-gap policy with no authoritative repair source. ADR-0074 amends only what its section 8 lists.
2. **ADR-0057, ADR-0064, ADR-0069, ADR-0072:** the server is the sole authority for canonical data; the deck receives verified bytes and has no storage mount; the wire and the K12/K13 contracts do not change.
3. **ADR-0075** (accepted 2026-10-21; DG-O, Part C): one continuous session and append-only slices, ADR-0044 unchanged.
   **Amendment 1** (accepted 2026-10-23, PR #513): a bridge slice with no trade for a reconciliation across a UTC midnight, the next revision only over an attempt that never became valid, the floor as the end of the latest valid partition.
4. **ADR-0074** (accepted) and its **Amendment 1** (accepted 2026-10-20: the Bybit public trade archive is the historical source of every instrument): the decisions in the Part B acceptance below.
5. Every `BTCUSDT` identity, the ordering key `(exchange_ts, trade_id)` and `TradeKeyV1` are unchanged byte for byte (pinned by `tests/test_multi_instrument_trades_contract_v1.py`).
6. **Out of this scope:** a second venue (`A12`), spot and inverse markets, L2 for other instruments (its ordering policy is keyed by `(venue, instrument)`), a generic provider resolver, capacity measurements (#378), deployment to the server.

---

## Design gate (closed)

| Gate | Issue | Output | Result |
|---|---|---|---|
| **DG-N** | #377 (evidence input #378) | ADR-0074 (`docs/decisions/ADR-0074-multi-instrument-bybit-linear-trades-v1.md`) with its guard test | `ACCEPTED` 2026-10-19 (PR #481 proposed it, PR #482 accepted it) |

Maintainer-accepted values that were not derived from a measurement (ADR-0074 "Not claimed"): the five-instrument allow-list; one live process per instrument; the 365-day capacity rule; the one-hour `ETHUSDT` real-provider proof; no BTC fee defaults for other instruments.

## Part B — Authorized implementation inventory

Each atom is its own implementation issue (`[implementation]` template), opened after this reconciliation merges, citing its row here. Every one targets the integration branch below, never `main`.

**Integration branch:** `implement/fid` (exists; atom branches are `agent/issue-<n>-<slug>`).

| Atom | Result | Requires (besides ADR-0074) | Start gate |
|---|---|---|---|
| `T01` | Read and delivery for the allow-list | `A06`, `A08`, `A09`, `K12` | **COMPLETE** (#485, PR #486) |
| `T04` | Historical import `--instrument` from the Bybit public archive | `A07`, `T01` | **COMPLETE** (#487, PR #488) |
| `T03` | Explicit per-instrument fees | `H02`, `T01` | **COMPLETE** (#491, PR #492; closeout #527) |
| `T02` | Live ingest per instrument | `A11`, `K10`, `T01`, `T03`, `T04`, `O03` | **COMPLETE** (#493 / PR #494, #495 / PR #496, #497 / PR #498, #499 / PR #500, #501 / PR #502; closed by `O03`, PR #526; closeout #527) -- the one-hour real-provider run is not claimed |

```text
T01 ──> T04 ──> T03 ──> T02
```

Order: strictly `T01`, `T04`, `T03`, `T02`, one bounded mutation slice at a time.

### Acceptance per atom (from ADR-0074; an implementation issue adds no semantic decision)

- **`T01`** (ADR-0074 §1, §2, §5). The closed constant `BYBIT_LINEAR_TRADE_INSTRUMENTS_V1 = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")` in `source_adapters/bybit.py`, used by `bybit_trade_v1_applies_to` and the eligibility profile; every other symbol (and `BTCUSD`, `BTCUSDC`, empty) stays refused. A non-BTC dataset is read through the `DataGateway` and admitted and delivered through K12 `admit`/`deliver` end to end in a test (the J02 wire and the deck unchanged). A **read-only catalog check** of the allow-list reports which instruments are registered `valid`; what is missing enters the catalog **only through the producer path** (materialize, certify, publish) — for the maintainer's existing Parquet by the archive import of Amendment 1, not by registration —, a file that does not pass certification is not published, and no catalog row is written directly. The BTC identities, the ordering key and an existing BTC read are unchanged (regression). The `expectedFailure` markers of the guard test that this atom makes true are removed, and its baseline-fact tests are updated to the new facts. Mutation checks for the allow-list and the applicability. **Credited `COMPLETE` (reconciliation #489): #485 / PR #486.** The registration of the existing Parquet was superseded by Amendment 1: their coverage manifests declare `bybit-public-trades-csv-v1`, which no commit produced and S13 refuses, so they are replaced through the archive import (A1.2 item 8). Not claimed: that any non-BTC dataset is registered in the server catalog.
- **`T04`** (§4 and Amendment 1). The historical source of every instrument, `BTCUSDT` included, is the Bybit public trade archive (`<INSTRUMENT><YYYY-MM-DD>.csv.gz`, one file per UTC day, read from a local file; no network call in the producer path), with the versioned source profile `bybit-public-trade-archive-v1`; the import takes `--instrument` over the allow-list (any other value refused), keeps one UTC day per invocation and the producer path (materialize, manifests, S13, S14), refuses a day that already has a partition in the catalog and never overwrites a file; certification accepts the SQLite and the archive profile and refuses mixtures; BTCUSDT behaviour and the 2 118 existing `valid` days unchanged; no repair of live gaps (ADR-0044 stands). **Credited `COMPLETE` (reconciliation #489): #487 / PR #488.** Not claimed: the server-side record-by-record proof of BTCUSDT 2024-01-15 against the published day (`tools/compare_archive_day.py`), a real-host run, and any download, replacement or deletion of the existing files (operator steps, `docs/engineering/BYBIT_ARCHIVE_IMPORT_RUNBOOK.md`).
- **`T03`** (§7). Fee parameters are explicit per instrument: an execution or replay over a dataset whose instrument is not `BTCUSDT` and that names no fee schedule is refused (fail-closed), with a stable reason; `BTCUSDT` defaults and identities unchanged; tests with an allow-list instrument with and without a schedule.
- **`T02`** (§3, §6). The live adapter and server are parameterized by instrument from the allow-list (topic `publicTrade.<INSTRUMENT>`, symbol check, dataset identity, `subject_identity = bybit:<INSTRUMENT>:<storage_root_id>`, per-instance checkpoint path, gap orchestration bound to the instance's dataset); `tools/live_ingest_server.py` gets `--instrument`; a systemd template unit `quant-platform-live-ingest@<INSTRUMENT>.service` with per-instance environment files and the runbook; **a proof that two instances with different instruments do not collide** on checkpoints, partitions or catalog rows (stop condition otherwise); one hour of real provider traffic for `ETHUSDT` (handshake, `trade_id` deduplication, healthy signals, durable checkpoint) recorded as evidence; the BTC unit and its behaviour unchanged. Deployment to the server is a manual operator step and is not part of the acceptance; enabling more than `BTCUSDT` in production waits for the #378 measurements and the 365-day rule. **Its one-hour `ETHUSDT` proof is delivered by `O03` (Part C): `T02` is credited when `O03` is merged.** Parts 1 to 5 are merged and not yet credited.

## Part C — DG-O: live publication (authorized implementation inventory)

Registered by governance issue #507 after ADR-0075 (`ACCEPTED` 2026-10-21, design gate #504, PR #505 and #506). Each atom is its own implementation issue (`[implementation]` template), opened after this reconciliation merges, citing its row here. Every one targets `implement/fid`, never `main`.

Problem, measured on 2026-10-21 (ADR-0075 F1, F2): every cycle is a new revision of `dt=<day>` and the catalog supersedes the previous one, so a `VALID_ONLY` read of a live day returns under 0.4% of the trades published (ten days checked); and the loop closes its WebSocket between cycles, so for `dt=2026-10-20` the live path published 1 142 059 trades against 1 568 861 in the Bybit archive of that day (72.8%).

| Atom | Result | Requires (besides ADR-0075) | Start gate |
|---|---|---|---|
| `O01` | Append-only live slices | `A11`, `K10`, `T01` | **COMPLETE** (#509 / PR #510; closeout #527) |
| `O02` | Continuous live session | `O01` | **COMPLETE** (#523 / PR #524; closeout #527) |
| `O03` | Configuration, runbook and real-provider proof | `O02`, `T03`, `T04` | **COMPLETE within its bound** (#525 / PR #526; closeout #527) -- the real-provider run is not claimed |

```text
O01 ──> O02 ──> O03 ──> T02
```

Order: strictly `O01`, `O02`, `O03`, one bounded mutation slice at a time; `T02` was credited when `O03` merged (closeout #527).

### Acceptance per atom (from ADR-0075; an implementation issue adds no semantic decision)

- **`O01`** (§2 to §4, slice 9.1). A slice is the trades of one instrument whose `exchange_ts` lies in a half-open window; it closes after `slice_seconds` (300) or `slice_max_trades` (50 000), at a message boundary, with the boundary the greatest `exchange_ts` received and trades equal to it kept for the next slice; a window never crosses a UTC midnight and an empty window is not published (except the bridge slice of a reconciliation, Amendment 1 below). Each slice is **revision 1 of its own partition** `dt=<UTC day of the window start>/seg=<HHMMSS.mmm of the window start>` with `part-001.parquet` and manifests named by the window, and a valid slice is never superseded; the windows of one session chain exactly and the coverage of a slice is `complete` over its window. The checkpoint invariants of ADR-0042 are unchanged; the checkpoint binds a slice partition and the window end comes from the catalog. The live certification profile accepts the key grammar and checks the chain. Over the existing bounded acquisition. Tests that fail on today's behaviour: a multi-cycle run leaves every trade readable through the `DataGateway` with no `superseded` partition (against a real PostgreSQL); two publications in one second do not collide; the `BTCUSDT` identities, the ordering key and `TradeKeyV1` are unchanged; mutation checks. ADR-0075 Amendment 1 (accepted 2026-10-23, PR #513): a reconciliation whose proven span reaches a UTC midnight before its first new trade publishes a slice with no trade up to the midnight, and a slice is a revision later than 1 only over an attempt that never became valid (a valid slice is never superseded); the floor is the end of the latest valid partition. Merged (PR #510).
- **`O02`** (§1, §5, slice 9.2). One WebSocket session per instance, not closed between publications; a reader feeds a bounded buffer (20 000 messages) that the publisher drains; a buffer overflow, a real disconnect and a received trade whose `exchange_ts` is below the last boundary each end the session as an interruption, handled as ADR-0040 §7 and ADR-0042 §5 decide (bounded reconciliation from the last durable key, else an explicit gap; ADR-0044 unchanged) and counted; signals for the buffer high-water mark, the slice lag, the reconnects and the ordering violations. Tests with a scripted provider: no interval of the session is unlistened; an interruption is never a `complete` claim; the checkpoint never advances across one; mutation checks.
- **`O03`** (§9.3, slice 9.3). `slice_seconds`, `slice_max_trades` and the buffer bound in `LiveIngestServerConfigV1`, the CLI, the unit templates and the runbook; a real-provider run of **at least one hour** for `ETHUSDT` (the `T02` proof) that reports the trades captured against the Bybit archive of the same day, the trades readable through the `DataGateway`, the number of `exchange_ts` ordering violations and the health signals, recorded as evidence. Deployment of a changed server to the host stays a manual operator step.

## Part D — DG-R: rebuild of damaged legacy live days (authorized implementation inventory, post-version)

Registered by governance issue #518 after ADR-0074 Amendment 2 (`ACCEPTED` 2026-10-24, design gate #515, PR #516 and #521), and extended by governance issue #540 after ADR-0074 Amendment 3 (`ACCEPTED` 2026-10-29, design gate #538, PR #539 and #541). Each atom is its own implementation issue (`[implementation]` template): `R01` is #519 and `R02` is #520, citing their rows here; the issues of `R03` and `R04` are opened after this reconciliation merges. `R01` and `R02` targeted `implement/fid`; `R03` and `R04` target `implement/dgr-closure`; none targets `main`. These atoms are **post-version**: they do not block `O02`, `O03`, the closeout or the declaration that the first version is concluded.

Problem (operator-reported evidence, 2026-10-23/24, not an audit): the legacy live path published each cycle as a new revision of `dt=<day>` and superseded the previous one, so a closed day reads as its last batch; 31 candidate days, 2026-09-23 to 2026-10-23; for 2026-10-20 the archive has 1,568,861 trades, the local in-day unique IDs are 1,141,530 (all in the archive; 427,331 absent) and 529 local rows fall outside the day. The ID comparison does not establish equality of every field.

Amendment 3 problem (operator-reported on `homelab`, not an audit): in `cycles` mode the pause between two slices is neither covered nor reconciled; 2026-10-24 has 3,327 slices and 28,718 s (33.2%) uncovered, 2026-10-25 has 3,333 gaps and 29,681 s (34.4%); the share of trades actually lost on those days is not measured (no archive file was on the server). The maintainer's requirement (2026-10-27): every instrument of the allow-list, automatic, every day closed well, so that no day is repaired by hand again.

| Atom | Result | Requires (besides ADR-0074 Amendments 2 and 3) | Start gate |
|---|---|---|---|
| `R01` | Superseding reassessment writer | `A16`, `A08`, `A09` | startable after this reconciliation merges (and the acceptance record, PR #521) |
| `R02` | Legacy-day rebuild: plan, retire, import, verify | `R01`, `T04` | after `R01` is merged |
| `R03` | Slice-day closure from the archive | `R01`, `R02`, `O01` | startable after this reconciliation merges (`R01` and `R02` are merged; the acceptance record is PR #541) |
| `R04` | Unattended closure run | `R03` | after `R03` is merged |

```text
R01 ──> R02 ──> R03 ──> R04
```

Order: strictly `R01`, `R02`, `R03`, `R04`, one bounded mutation slice at a time.

### Acceptance per atom (from ADR-0074 Amendments 2 and 3; an implementation issue adds no semantic decision)

- **`R01`** (A2.3, issue #519). A writer, in the data plane next to `quality_lifecycle.py`, of a superseding reassessment report for the current live revision of one natural partition that is not `closed`: it copies the bound evidence of the assessment it supersedes unchanged, sets the status and a violation `legacy_publication_incomplete_day`, carries `metrics.supersedes_assessment_signature` and the producing commit; the lifecycle application then maps `fail` to `invalid` through the existing seam, and a superseding `pass` restores `valid`. Refuses a superseded, foreign or `writing` revision; idempotent; same lock order as the lifecycle application; ordinary `record_quality_report` is unchanged. Tests against a real PostgreSQL (state change both ways, supersession link, evidence copy, idempotence, refusals, a `VALID_ONLY` read excludes the retired revision, a concurrent S13 publication waits and proceeds, old bytes and reports unchanged); mutation checks. First check: who reacts to the state `invalid` (retention, eligibility, admitted inputs).
- **`R02`** (A2.2, A2.4 to A2.7, issue #520). An importer variant for which the `invalid` revision of the exact key `dt=<day>` does not occupy the day (everything else still refuses), the fixed key `dt=<day>/seg=000000.000`; a read-only plan with a digest (incumbent revision and hashes, archive file identity, producing commit, key to create, the result of the refusals); refusals with nothing changed: day not closed in UTC (less than 24 hours), live slices, checkpoint binding or open gap record naming the day, live revision not `valid`, failed **conservation** (every record of the incumbent live revision found, field for field, in the archive file of its own day, which must be readable afterwards), a differing common field, an archive that fails strict parsing or differs from the planned hash, a stale plan; one day at a time, no lock held across days; retire, import, verify (full-field comparison and a strict `DataGateway` read: count, order, duplicates); recovery of a crash between retire and import and between import and verify; restore by a superseding `pass`. Tests against a real PostgreSQL, including the pilot shape of 2026-10-20 (a strict read returns exactly 1,568,861 archive records, in order, without duplicates, equal field by field) and byte-identical non-target days and live checkpoint; mutation checks; CLI and runbook.
- **`R03`** (A3.2 to A3.7, A3.6; issue to be opened). For closed UTC days of `canonical/trades/bybit/<instrument>/trade-v1` made of live slices `dt=<day>/seg=...`: a read-only plan with a digest; retire every slice with the `R01` reassessment (`valid` -> `invalid`, nothing deleted); import the archive day as `dt=<day>/seg=000000.000` through S13/S14 (the `R02` importer variant widened to invalid slices); verify in full and through a strict `DataGateway` read (count, order, duplicates, full-field equality; null `receive_ts` and `sequence` expected). A slice day is closed when it has ended in UTC and the live checkpoint is bound to a partition of a later day (this replaces the 24-hour margin for slice days). Conservation by `(exchange_ts, trade_id)` with full-field equality; refusals with nothing changed: day not closed, a `writing` or `closed` slice, a bridge slice without its neighbour in the plan, a gap record not contained in the day, an overlapping `valid` partition, a day with nothing to repair, an archive or manifest hash that differs, a slice already at `seg=000000.000`, a stale plan; **and the overlap guard of A3.6 added to the `R02` plan**, with its own probe. Gap records are not modified and do not decide validity. Recovery of a crash between two retirements, between retire and import, and between import and verify; restore by the `R01` path. Tests against a real PostgreSQL, byte-identical non-target days, live checkpoint and gap record files; mutation checks of the conservation and overlap rules; measured duration of the retirements. An operator dry-run and execution on `2026-10-24` first, later days only from a new reviewed plan.
- **`R04`** (A3.12, A3.4; issue to be opened). A timer-started run, every hour, over every instrument of the allow-list, in a fixed order, selecting by rule the closed slice days that no `valid` archive key closes: a day is taken from 12:00 UTC of the following day; per day it only verifies (complete and conserved), replaces the whole day (incomplete, archive fetched and conserved, nothing overlapping), or marks the day invalid by retiring its slices and is retried by the next run; a failed verification also retires the imported partition. The 23rd attempt that does not close a day marks it invalid and abandons it; a `reopen` line makes the run take it again. The attempts and the signals are lines appended to one JSON-lines log (one line per attempt, never rewritten); a lost log gives more attempts and changes no data. The run is a separate process that never blocks the live service: a short `lock_timeout` on every lock, one object per transaction and no lock across a download, parse or verification, paced retirements, CPU and memory limits and a lower priority, a wall-clock limit, a run lock without waiting; it deletes nothing. Tests over a disposable PostgreSQL and a fake archive source: days in each outcome in one run for two instruments, a refusal that does not stop the others, a rerun after a crash, a stale plan, an archive that changes between two runs, the 23rd attempt, a lost log, a crash before the append, a second run that changes nothing, no day outside the rule changing by a byte; with the real service publishing, the publication latency before, during and after a closure stays within a stated bound, a hung or killed run leaves the service publishing, and the service runs without the unit installed.

### Decisions already taken by the maintainer (ADR-0074 Amendment 3; an implementation issue does not reopen them)

Every instrument of the allow-list; unattended; a day not closed after 23 hourly attempts is marked invalid and abandoned; the signals are lines appended to a JSON-lines log, nothing else for now; the validity of a day is decided only by the archive verification, and the long-gap records neither decide it nor are superseded (ADR-0044 is not amended); the slices retired by an archive closure carry no retention duty, and the run deletes nothing (ADR-0049 is unchanged; an automatic deletion would need its own amendment); the slices of an invalid or abandoned day are the only copy of that day's live data and are not covered by that decision.

### Open, none authorized (ADR-0074 A3.10)

Whether the Bybit archive file of the previous day exists at 12:00 UTC (not measured; the hourly retry absorbs a late file); the directories moved aside on 2026-10-26 and the catalog dump (who keeps them, ADR-0049); whether a complete live day may ever be replaced (proposed never); whether the explicit-gap state of the live service caused by an open long-gap record should ever end, and a rule for a record that crosses a midnight; the share of trades actually lost on 2026-10-24 and 2026-10-25.

---

## Authority

- `AGENTS.md`
- governance issues #381, #409, #453, #458, #475, #479, #483, #489, #507, #518, #527, #540; design-gate issues #377, #487, #504, #512, #515, #538; evidence issue #378; implementation issues #485, #487, #491, #493, #495, #497, #499, #501, #509, #523, #525, #519, #520 (the issues of `R03` and `R04` are opened after this reconciliation)
- `docs/decisions/ADR-0074-multi-instrument-bybit-linear-trades-v1.md` (`ACCEPTED`, with Amendments 1, 2 and 3 `ACCEPTED`)
- `docs/decisions/ADR-0075-live-trades-continuous-session-and-append-only-slices-v1.md` (`ACCEPTED`, with Amendment 1 `ACCEPTED`)
- `docs/decisions/ADR-0030-general-quality-lifecycle-v1.md` and `docs/decisions/ADR-0033-backfill-repair-v1.md` (unchanged; ADR-0030 is used by Part D)
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_DAG.md` (`T` atoms), `docs/architecture/OPEN_DECISIONS.md` (DG-N, DG-O, DG-R)
- ADR-0040, ADR-0041, ADR-0042, ADR-0043, ADR-0044, ADR-0054, ADR-0057, ADR-0064, ADR-0069, ADR-0072
- `tests/test_package_boundaries_v1.py`, `tests/test_multi_instrument_trades_contract_v1.py`

Accepted ADRs and frozen contracts remain normative. This reconciliation weakens none of them.

---

## Baseline and Credited State

```text
main @ 9cf0b5e (DG-K P01-P06, DG-K2 S01-S07 and DG-C/A14 v1 COMPLETE within recorded bounds; promotion PR #514 brought implement/fid @ fc69cc1 -- DG-M, DG-N, O01 -- into main on 2026-10-24, then ADR-0075 Amendment 1, PR #513)
implement/fid: main + DG-M (M01, M02, M04, M05 COMPLETE, closeout #475, follow-on #477) + ADR-0074 (#481, #482) + T01 (#486) + T04 and Amendment 1 (#488) + T03 (#492) + T02 parts 1 to 5 (#494, #496, #498, #500, #502) + ADR-0075 (#505, #506) + O01 (#510) + ADR-0075 Amendment 1 (#512 / PR #513, via main) + ADR-0074 Amendment 2 (#515 / PR #516, #521) + DG-R registration (#518 / PR #522) + O02 (#524) + O03 (#526) + this closeout (#527)
DG-N: T01, T04 COMPLETE (reconciliation #489); T03, T02 COMPLETE within bounds (closeout #527; the one-hour real-provider run is not claimed)
DG-O: O01, O02, O03 COMPLETE within bounds (closeout #527; PR #510, #524, #526)
DG-R: R01, R02 FROZEN / MISSING in CAPABILITY_DAG.md (authorized by #518; post-version; their code is merged on main, #519 / a05b91d and #520 / 207e446 with its review fixes, and their credit is a closeout's, not given here)
implement/dgr-closure: main @ 691974b + ADR-0074 Amendment 3 (#538; PR #539 into implement/fid, moved onto this branch with its acceptance record by #541) + this reconciliation (#540)
DG-R Amendment 3: R03, R04 FROZEN / MISSING (authorized by #540; post-version)
```

Credit, do not reimplement or re-prove absent invalidating evidence: every atom `COMPLETE` in `CAPABILITY_DAG.md`, within the bound its row records.

---

## Explicit non-claims

- **The one-hour `ETHUSDT` real-provider run of `O03` (the `T02` proof) was not performed.** `T02` and `O03` are credited within the bound of their code, configuration, runbook, evidence tool and tests (the maintainer's decision, 2026-10-25). What a real hour would report is not claimed: the captured share against the Bybit archive, the slice sizes, the health signals and the number of `exchange_ts` ordering violations. The ordering of the provider's `exchange_ts` stays an **assumption**; the 300 s / 50 000 trades / 20 000 messages bounds are accepted, not measured.
- **Nothing is deployed.** The server runs the code deployed before DG-O: it still supersedes each cycle and closes its WebSocket between cycles until the operator promotes and restarts it. The default of the CLI, the environment examples and the units is `cycles`; enabling the continuous mode is an operator step, and making it the default is the maintainer's decision after a real run.
- No non-BTC dataset is registered in the server catalog, so none is readable there yet: the maintainer's existing ETH/SOL/XRP/DOGE Parquet are not registered (they cannot be certified, Amendment 1) and are to be replaced by the archive import, an operator step not yet done. The server-side record-by-record proof of BTCUSDT 2024-01-15 and any real-host run are operator evidence and are not claimed.
- No capacity is measured (#378): the daily volume, memory and disk figures offered for other instruments are not used, and nothing beyond the bounded `ETHUSDT` proof is enabled before the measurement and the 365-day rule.
- DG-O: the existing live partitions and their superseded revisions are left as they are. Slices are not consolidated into a day partition. After a reconnect the session waits for the first trade of the new subscription before reconciling (a quiet interval delays it; a REST window that does not meet the subscription is an explicit gap): how often that happens is unmeasured. The `known_gap` span ends at the local detection instant; the evidence tool classifies missing trades against it.
- DG-R: `R03` and `R04` are not implemented, no slice day has been closed from the archive, and no timer, no first run and no deployment exist. `R01` and `R02` are merged and not credited here. The 2026-10-26 replacement of legacy days on the server was an operator one-off recorded as evidence in ADR-0074 A3.1; it is not a precedent and not the `R02` run. The gap figures of Amendment 3 are operator-reported, not an audit; the share of trades lost on 2026-10-24 and 2026-10-25 and the publication time of the archive file against 12:00 UTC are not measured; the live service remains in `cycles` mode and the switch to the continuous session is not part of this work. The 2026-10-20 evidence is operator-reported; the ID comparison does not establish field equality (the proof is `R02`'s). The 31 candidate dates are not an execution plan: the days come only from a reviewed plan. The atomic replacement is considered and not adopted (ADR-0074 A2.8). Nothing is deleted; retention of retired or superseded revisions is the maintainer's. Running the rebuild on the server is an operator step.
- No deployment, no L2 for other instruments, no second venue, no spot or inverse market, no repair or backfill of live gaps.
- The generic provider `E07`, the FEI line and Omega are untouched.

---

## Closeout result (#527)

`T03`, `T02` (parts 1 to 5 and the closing `O03`), `O01`, `O02` and `O03` are merged into `implement/fid` and credited `COMPLETE` within the bounds their rows in `CAPABILITY_DAG.md` record, each with mutation-checked tests, real-PostgreSQL integration where it publishes, and independent review findings fixed. The inventory is 110 of 132 (83.3%), recomputed from the DAG rows by script and equal across the four governance files; no decision-state change. DG-R (`R01`, `R02`) is untouched.

## Reopening triggers (none authorized)

Recorded in full in `docs/architecture/OPEN_DECISIONS.md` ("Closeout of DG-N and DG-O"): (1) the real-provider run and its report; (2) the provider's `exchange_ts` ordering; (3) the bounds; (4) the default mode and the deployment; (5) thin instruments and gaps after a reconnect; (6) the clock of the recorded gap span; (7) capacity (#378) and further instruments; (8) the damaged legacy days (DG-R); (9) consolidation of slices.

---

## Mutation policy

- Only `governance/` or `admin/` branches may change `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md`. Implementation issues leave them untouched and report stale governance in the PR.
- Each atom changes only what its acceptance names. `T01` may also update the guard test of ADR-0074 as its acceptance says; an amendment to ADR-0043 or ADR-0040 is the one ADR-0074 section 8 describes and is appended, never a rewrite.
- No atom changes a `BTCUSDT` identity byte, touches the J02 wire or the K12/K13 contracts, or gives the deck a storage mount.

---

## Exit criteria for this scope

- **Met:** `T01`, `T04`, `T03`, `T02` and `O01`, `O02`, `O03` implemented and merged into `implement/fid` within the acceptance above (`T02` closed with `O03`), each with its mutation-checked tests -- **except** the real-provider run of at least one hour of `O03`, which was not performed and is not claimed (the maintainer's decision, 2026-10-25). `R01`, `R02`, `R03` and `R04` (Part D) are post-version: they are not a condition of the declaration below.
- **Met:** a governance closeout issue (#527) credits each atom with its PR and bound and records what is not claimed.
- The promotion of `implement/fid` (DG-M, DG-N and DG-O) into `main`: PR #514 promoted DG-M, DG-N and `O01` on 2026-10-24, before `O02`, `O03` and the closeout; a promotion PR opened after #527 merges carries the rest. The declaration that the first version of the product is concluded is the maintainer's, after the closeout.
- The Phase A measurements (#378), the production enabling of further live instruments, the deployment and the real-provider run are recorded separately; they are not claimed by this scope.

---

## Out of Scope

- a second venue, spot and inverse markets, symbols outside the allow-list, L2 for other instruments;
- measured capacity (#378) and enabling live ingest beyond `BTCUSDT` in production;
- deployment to the server and any change to the deployment units on the host;
- repair or backfill of live gaps through the ADR-0044 path (the archive closure of Part D, ADR-0074 Amendments 2 and 3, is not that path: it does not touch the long-gap records);
- consolidation of slices into a day partition (ADR-0075 item 7); the rebuild of the already damaged live days is Part D;
- the atomic replacement of a live revision, automatic repair of live gaps through the ADR-0044 path, and retention or deletion of retired or superseded revisions (ADR-0074 Amendments 2 and 3: the unattended run deletes nothing);
- the generic provider extension `E07`, the FEI line, Omega, `J11`.

---

## Stop / Escalation Conditions

Stop and report rather than implement or reconcile if:

- an atom needs a decision ADR-0074 does not state, or contradicts an accepted ADR;
- an atom would change a `BTCUSDT` identity byte, the J02 wire or the K12/K13 contracts;
- two live instances collide on a checkpoint, a partition or a catalog row;
- the atom order is not respected (a start gate above is not met);
- a certification of the maintainer's files fails and the only way forward is to publish without it.
