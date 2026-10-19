# Scope: DG-N — Multi-instrument Bybit linear trades v1

Status: **OPEN — design gate CLOSED, implementation of `T01`, `T04`, `T03` and `T02` AUTHORIZED (governance issue #483, 2026-10-19).** The design gate #377 produced ADR-0074 (PR #481), `ACCEPTED` by the maintainer on 2026-10-19 (PR #482). The integration branch is `implement/fid`, the current wave (the maintainer's choice, 2026-10-19): when the four atoms are merged and reconciled, the whole wave (DG-M and DG-N) is promoted to `main` by the operator and the first version of the product is declared concluded. No atom is implemented yet: **today only `BTCUSDT` is readable, deliverable or ingestable live.**

Scope kind: a design-gate sequencing scope that, after the gate closed, carries the Part B authorized inventory below.

---

## Historical scope archive

This file previously recorded **DG-M — Canonical feature identity for deck results v1** (CLOSED by governance issue #475 and the follow-on #479; atoms `M01`, `M02`, `M04`, `M05` merged into `implement/fid`), and before it DG-C / A14 (closeout #442) and DG-K2 (closeout #409). All are preserved in git history (`git log -- SCOPE.md`) and are not duplicated here, except for the facts this scope depends on. `SCOPE.md` is overwritten in place for each governance reconciliation.

---

## Why this scope exists

The platform ingests, publishes, serves and delivers Bybit linear trades for one instrument. The maintainer holds `trade-v1` Parquet files for other Bybit linear USDT instruments and wants them readable, deliverable to the deck and ingested live. On `implement/fid` the DataGateway refuses any non-BTC dataset before it reaches the catalog (`bybit_trade_v1_applies_to` is `BTCUSDT` only), the live adapter, the live server subject, the historical import and the default fees are bound to `BTCUSDT`, and ADR-0043 states a single-instrument invariant. ADR-0074 is the answer; this scope sequences its implementation.

---

## Fixed inputs (already decided; not reopened)

1. **ADR-0040, ADR-0041, ADR-0042, ADR-0043, ADR-0044:** the live trades acquisition, the checkpoint identity (it carries its `dataset_identity`), the single-process loop and the explicit-gap policy with no authoritative repair source. ADR-0074 amends only what its section 8 lists.
2. **ADR-0057, ADR-0064, ADR-0069, ADR-0072:** the server is the sole authority for canonical data; the deck receives verified bytes and has no storage mount; the wire and the K12/K13 contracts do not change.
3. **ADR-0074** (accepted): the decisions in the Part B acceptance below.
4. Every `BTCUSDT` identity, the ordering key `(exchange_ts, trade_id)` and `TradeKeyV1` are unchanged byte for byte (pinned by `tests/test_multi_instrument_trades_contract_v1.py`).
5. **Out of this scope:** a second venue (`A12`), spot and inverse markets, L2 for other instruments (its ordering policy is keyed by `(venue, instrument)`), a generic provider resolver, capacity measurements (#378), deployment to the server.

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
| `T01` | Read and delivery for the allow-list | `A06`, `A08`, `A09`, `K12` | startable |
| `T04` | Historical import `--instrument` | `A07`, `T01` | after `T01` is merged |
| `T03` | Explicit per-instrument fees | `H02`, `T01` | after `T01` is merged |
| `T02` | Live ingest per instrument | `A11`, `K10`, `T01`, `T03`, `T04` | after `T01`, `T04` and `T03` are merged |

```text
T01 ──> T04 ──> T03 ──> T02
```

Order: strictly `T01`, `T04`, `T03`, `T02`, one bounded mutation slice at a time.

### Acceptance per atom (from ADR-0074; an implementation issue adds no semantic decision)

- **`T01`** (ADR-0074 §1, §2, §5). The closed constant `BYBIT_LINEAR_TRADE_INSTRUMENTS_V1 = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")` in `source_adapters/bybit.py`, used by `bybit_trade_v1_applies_to` and the eligibility profile; every other symbol (and `BTCUSD`, `BTCUSDC`, empty) stays refused. A non-BTC dataset is read through the `DataGateway` and admitted and delivered through K12 `admit`/`deliver` end to end in a test (the J02 wire and the deck unchanged). A **read-only catalog check** of the allow-list reports which instruments are registered `valid`; what is missing is registered **only through the producer path** (materialize, certify, publish), a file that does not pass certification is not published, and no catalog row is written directly. The BTC identities, the ordering key and an existing BTC read are unchanged (regression). The `expectedFailure` markers of the guard test that this atom makes true are removed, and its baseline-fact tests are updated to the new facts. Mutation checks for the allow-list and the applicability.
- **`T04`** (§4). The historical import takes `--instrument` over the allow-list (any other value refused), keeps one UTC day per invocation and the producer path; the source evidence for the new instruments is stated in the issue before implementation; BTCUSDT behaviour unchanged; no repair of live gaps (ADR-0044 stands).
- **`T03`** (§7). Fee parameters are explicit per instrument: an execution or replay over a dataset whose instrument is not `BTCUSDT` and that names no fee schedule is refused (fail-closed), with a stable reason; `BTCUSDT` defaults and identities unchanged; tests with an allow-list instrument with and without a schedule.
- **`T02`** (§3, §6). The live adapter and server are parameterized by instrument from the allow-list (topic `publicTrade.<INSTRUMENT>`, symbol check, dataset identity, `subject_identity = bybit:<INSTRUMENT>:<storage_root_id>`, per-instance checkpoint path, gap orchestration bound to the instance's dataset); `tools/live_ingest_server.py` gets `--instrument`; a systemd template unit `quant-platform-live-ingest@<INSTRUMENT>.service` with per-instance environment files and the runbook; **a proof that two instances with different instruments do not collide** on checkpoints, partitions or catalog rows (stop condition otherwise); one hour of real provider traffic for `ETHUSDT` (handshake, `trade_id` deduplication, healthy signals, durable checkpoint) recorded as evidence; the BTC unit and its behaviour unchanged. Deployment to the server is a manual operator step and is not part of the acceptance; enabling more than `BTCUSDT` in production waits for the #378 measurements and the 365-day rule.

---

## Authority

- `AGENTS.md`
- governance issues #381, #409, #453, #458, #475, #479, #483; design-gate issue #377; evidence issue #378
- `docs/decisions/ADR-0074-multi-instrument-bybit-linear-trades-v1.md` (`ACCEPTED`)
- `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_DAG.md` (`T` atoms), `docs/architecture/OPEN_DECISIONS.md` (DG-N)
- ADR-0040, ADR-0041, ADR-0042, ADR-0043, ADR-0044, ADR-0054, ADR-0057, ADR-0064, ADR-0069, ADR-0072
- `tests/test_package_boundaries_v1.py`, `tests/test_multi_instrument_trades_contract_v1.py`

Accepted ADRs and frozen contracts remain normative. This reconciliation weakens none of them.

---

## Baseline and Credited State

```text
main @ ef14279 (DG-K P01-P06, DG-K2 S01-S07 and DG-C/A14 v1 COMPLETE within recorded bounds)
implement/fid: main + DG-M (M01, M02, M04, M05 COMPLETE, closeout #475, follow-on #477) + ADR-0074 (#481, #482) + this reconciliation
DG-N: T01, T04, T03, T02 FROZEN / MISSING (authorized by #483)
```

Credit, do not reimplement or re-prove absent invalidating evidence: every atom `COMPLETE` in `CAPABILITY_DAG.md`, within the bound its row records.

---

## Explicit non-claims

- No `T` atom is implemented; no non-BTC dataset is readable, deliverable or ingestable live.
- The maintainer's existing Parquet files are not known to be registered, readable or certifiable until `T01` checks them.
- No capacity is measured (#378): the daily volume, memory and disk figures offered for other instruments are not used, and nothing beyond the bounded `ETHUSDT` proof is enabled before the measurement and the 365-day rule.
- No deployment, no L2 for other instruments, no second venue, no spot or inverse market, no repair or backfill of live gaps.
- The generic provider `E07`, the FEI line and Omega are untouched.

---

## Mutation policy

- Only `governance/` or `admin/` branches may change `SCOPE.md`, `ROADMAP.md`, `CAPABILITY_MAP.md`, `CAPABILITY_DAG.md` or `OPEN_DECISIONS.md`. Implementation issues leave them untouched and report stale governance in the PR.
- Each atom changes only what its acceptance names. `T01` may also update the guard test of ADR-0074 as its acceptance says; an amendment to ADR-0043 or ADR-0040 is the one ADR-0074 section 8 describes and is appended, never a rewrite.
- No atom changes a `BTCUSDT` identity byte, touches the J02 wire or the K12/K13 contracts, or gives the deck a storage mount.

---

## Exit criteria for this scope

- `T01`, `T04`, `T03`, `T02` implemented and merged into `implement/fid` within the acceptance above, each with its mutation-checked tests.
- A governance closeout issue credits each atom with its PR and bound and records what is not claimed.
- The operator's promotion PR of `implement/fid` (DG-M and DG-N) into `main`, and the declaration that the first version of the product is concluded.
- The Phase A measurements (#378), the production enabling of further live instruments and the deployment are recorded separately; they are not claimed by this scope.

---

## Out of Scope

- a second venue, spot and inverse markets, symbols outside the allow-list, L2 for other instruments;
- measured capacity (#378) and enabling live ingest beyond `BTCUSDT` in production;
- deployment to the server and any change to the deployment units on the host;
- repair or backfill of live gaps (ADR-0044);
- the generic provider extension `E07`, the FEI line, Omega, `J11`.

---

## Stop / Escalation Conditions

Stop and report rather than implement or reconcile if:

- an atom needs a decision ADR-0074 does not state, or contradicts an accepted ADR;
- an atom would change a `BTCUSDT` identity byte, the J02 wire or the K12/K13 contracts;
- two live instances collide on a checkpoint, a partition or a catalog row;
- the atom order is not respected (a start gate above is not met);
- a certification of the maintainer's files fails and the only way forward is to publish without it.
