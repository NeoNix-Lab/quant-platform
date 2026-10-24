# Scope: DG-N — Multi-instrument Bybit linear trades v1, and DG-O — Live publication v1

Status: **OPEN — two gates of one wave. DG-N: design gate CLOSED; `T01` and `T04` COMPLETE (reconciliation #489); `T03` merged (PR #492) and `T02` parts 1 to 5 merged (PR #494, #496, #498, #500, #502), both credited at the closeout, `T02` closing with `O03`. DG-O: design gate CLOSED (ADR-0075 `ACCEPTED` 2026-10-21, #504); `O01`, `O02` and `O03` AUTHORIZED (governance issue #507), not started.** Measured on the maintainer's server on 2026-10-21: the live publication supersedes each cycle with the next (a day reads as under 0.4% of its published trades) and about 27% of a day's trades are never acquired; DG-O fixes both, before the rest of the wave. The integration branch is `implement/fid`, the current wave (the maintainer's choice, 2026-10-19): when the atoms are merged and reconciled, the whole wave (DG-M, DG-N and DG-O) is promoted to `main` by the operator and the first version of the product is declared concluded. **Today `BTCUSDT` is the only instrument ingested live and registered on the server, and its live days are not fully readable or complete (DG-O); the other four instruments are readable once their partitions are `valid`.**

Scope kind: a sequencing scope for two gates of one wave: DG-N (design gate closed; Part B authorized inventory) and DG-O (design gate closed; Part C authorized inventory).

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
| `T03` | Explicit per-instrument fees | `H02`, `T01` | **merged** (#491, PR #492); credited at the closeout |
| `T02` | Live ingest per instrument | `A11`, `K10`, `T01`, `T03`, `T04`, `O03` | parts 1 to 5 merged (#493 / PR #494, #495 / PR #496, #497 / PR #498, #499 / PR #500, #501 / PR #502); closes with `O03` (its one-hour proof); credited at the closeout |

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
| `O01` | Append-only live slices | `A11`, `K10`, `T01` | startable |
| `O02` | Continuous live session | `O01` | after `O01` is merged |
| `O03` | Configuration, runbook and real-provider proof | `O02`, `T03`, `T04` | after `O02` is merged |

```text
O01 ──> O02 ──> O03 ──> T02
```

Order: strictly `O01`, `O02`, `O03`, one bounded mutation slice at a time; `T02` is credited when `O03` is merged.

### Acceptance per atom (from ADR-0075; an implementation issue adds no semantic decision)

- **`O01`** (§2 to §4, slice 9.1). A slice is the trades of one instrument whose `exchange_ts` lies in a half-open window; it closes after `slice_seconds` (300) or `slice_max_trades` (50 000), at a message boundary, with the boundary the greatest `exchange_ts` received and trades equal to it kept for the next slice; a window never crosses a UTC midnight and an empty window is not published. Each slice is **revision 1 of its own partition** `dt=<UTC day of the window start>/seg=<HHMMSS.mmm of the window start>` with `part-001.parquet` and manifests named by the window, and is never superseded; the windows of one session chain exactly and the coverage of a slice is `complete` over its window. The checkpoint invariants of ADR-0042 are unchanged; the checkpoint binds a slice partition and the window end comes from the catalog. The live certification profile accepts the key grammar and checks the chain. Over the existing bounded acquisition. Tests that fail on today's behaviour: a multi-cycle run leaves every trade readable through the `DataGateway` with no `superseded` partition (against a real PostgreSQL); two publications in one second do not collide; the `BTCUSDT` identities, the ordering key and `TradeKeyV1` are unchanged; mutation checks.
- **`O02`** (§1, §5, slice 9.2). One WebSocket session per instance, not closed between publications; a reader feeds a bounded buffer (20 000 messages) that the publisher drains; a buffer overflow, a real disconnect and a received trade whose `exchange_ts` is below the last boundary each end the session as an interruption, handled as ADR-0040 §7 and ADR-0042 §5 decide (bounded reconciliation from the last durable key, else an explicit gap; ADR-0044 unchanged) and counted; signals for the buffer high-water mark, the slice lag, the reconnects and the ordering violations. Tests with a scripted provider: no interval of the session is unlistened; an interruption is never a `complete` claim; the checkpoint never advances across one; mutation checks.
- **`O03`** (§9.3, slice 9.3). `slice_seconds`, `slice_max_trades` and the buffer bound in `LiveIngestServerConfigV1`, the CLI, the unit templates and the runbook; a real-provider run of **at least one hour** for `ETHUSDT` (the `T02` proof) that reports the trades captured against the Bybit archive of the same day, the trades readable through the `DataGateway`, the number of `exchange_ts` ordering violations and the health signals, recorded as evidence. Deployment of a changed server to the host stays a manual operator step.

---

## Authority

- `AGENTS.md`
- governance issues #381, #409, #453, #458, #475, #479, #483, #489, #507; design-gate issues #377, #487, #504; evidence issue #378; implementation issues #485, #487, #491, #493, #495, #497, #499, #501
- `docs/decisions/ADR-0074-multi-instrument-bybit-linear-trades-v1.md` (`ACCEPTED`, with Amendment 1 `ACCEPTED`)
- `docs/decisions/ADR-0075-live-trades-continuous-session-and-append-only-slices-v1.md` (`ACCEPTED`)
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_DAG.md` (`T` atoms), `docs/architecture/OPEN_DECISIONS.md` (DG-N)
- ADR-0040, ADR-0041, ADR-0042, ADR-0043, ADR-0044, ADR-0054, ADR-0057, ADR-0064, ADR-0069, ADR-0072
- `tests/test_package_boundaries_v1.py`, `tests/test_multi_instrument_trades_contract_v1.py`

Accepted ADRs and frozen contracts remain normative. This reconciliation weakens none of them.

---

## Baseline and Credited State

```text
main @ ef14279 (DG-K P01-P06, DG-K2 S01-S07 and DG-C/A14 v1 COMPLETE within recorded bounds)
implement/fid: main + DG-M (M01, M02, M04, M05 COMPLETE, closeout #475, follow-on #477) + ADR-0074 (#481, #482) + T01 (#486) + T04 and Amendment 1 (#488) + T03 (#492) + T02 parts 1 to 5 (#494, #496, #498, #500, #502) + ADR-0075 (#505, #506) + this reconciliation
DG-N: T01, T04 COMPLETE (reconciliation #489); T03, T02 FROZEN / MISSING (authorized by #483; merged, credited at the closeout)
DG-O: O01, O02, O03 FROZEN / MISSING (authorized by #507)
```

Credit, do not reimplement or re-prove absent invalidating evidence: every atom `COMPLETE` in `CAPABILITY_DAG.md`, within the bound its row records.

---

## Explicit non-claims

- `T03` and `T02` parts 1 to 5 are merged but **not credited** here: `T02` closes with `O03`, and nothing is deployed, so no non-BTC instrument is ingested live on the server.
- No non-BTC dataset is registered in the server catalog, so none is readable there yet: the maintainer's existing ETH/SOL/XRP/DOGE Parquet are not registered (they cannot be certified, Amendment 1) and are to be replaced by the archive import, an operator step not yet done. The server-side record-by-record proof of BTCUSDT 2024-01-15 and any real-host run are operator evidence and are not claimed.
- No capacity is measured (#378): the daily volume, memory and disk figures offered for other instruments are not used, and nothing beyond the bounded `ETHUSDT` proof is enabled before the measurement and the 365-day rule.
- DG-O: nothing is implemented. The live path still supersedes each cycle with the next and still closes its WebSocket between cycles. The existing live partitions and their superseded revisions are left as they are and are not repaired (recovery is paused: if a gap makes the data unusable, the history is re-ingested from scratch). Slices are not consolidated into a day partition. The provider's `exchange_ts` ordering is an assumption until `O03` measures it.
- No deployment, no L2 for other instruments, no second venue, no spot or inverse market, no repair or backfill of live gaps.
- The generic provider `E07`, the FEI line and Omega are untouched.

---

## Mutation policy

- Only `governance/` or `admin/` branches may change `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md`. Implementation issues leave them untouched and report stale governance in the PR.
- Each atom changes only what its acceptance names. `T01` may also update the guard test of ADR-0074 as its acceptance says; an amendment to ADR-0043 or ADR-0040 is the one ADR-0074 section 8 describes and is appended, never a rewrite.
- No atom changes a `BTCUSDT` identity byte, touches the J02 wire or the K12/K13 contracts, or gives the deck a storage mount.

---

## Exit criteria for this scope

- `T01`, `T04`, `T03`, `T02` and `O01`, `O02`, `O03` implemented and merged into `implement/fid` within the acceptance above (`T02` closes with `O03`), each with its mutation-checked tests.
- A governance closeout issue credits each atom with its PR and bound and records what is not claimed.
- The operator's promotion PR of `implement/fid` (DG-M, DG-N and DG-O) into `main`, and the declaration that the first version of the product is concluded.
- The Phase A measurements (#378), the production enabling of further live instruments and the deployment are recorded separately; they are not claimed by this scope.

---

## Out of Scope

- a second venue, spot and inverse markets, symbols outside the allow-list, L2 for other instruments;
- measured capacity (#378) and enabling live ingest beyond `BTCUSDT` in production;
- deployment to the server and any change to the deployment units on the host;
- repair or backfill of live gaps (ADR-0044);
- consolidation of slices into a day partition, and the repair or re-publication of the already damaged live days (ADR-0075 item 7);
- the generic provider extension `E07`, the FEI line, Omega, `J11`.

---

## Stop / Escalation Conditions

Stop and report rather than implement or reconcile if:

- an atom needs a decision ADR-0074 does not state, or contradicts an accepted ADR;
- an atom would change a `BTCUSDT` identity byte, the J02 wire or the K12/K13 contracts;
- two live instances collide on a checkpoint, a partition or a catalog row;
- the atom order is not respected (a start gate above is not met);
- a certification of the maintainer's files fails and the only way forward is to publish without it.
