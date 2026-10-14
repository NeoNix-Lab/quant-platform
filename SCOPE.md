# Scope: DG-C / A14 — L2 contract, continuity and publication v1 (design gate)

Status: **OPEN — design-gate scope on integration branch `implement/l2` (cut from `implement/dgk`).**

Scope kind: this document opens the **L2 design gate** for capability **`A14`** (L2
contract/acquisition) and registers the atoms that implement it. It decides no L2
semantics: those belong to `ADR-0070`, which does not exist yet. Registered by governance
issue #364.

---

## Historical scope archive

This file previously recorded **DG-K — Process Topology v1** (Part A closed via
ADR-0068/ADR-0069; Part B implemented on `implement/dgk`), and earlier **Wave 8 — Full
Platform API & Remote Service Topology v1** (closed #327/PR #328). Both are preserved in
git history (`git log -- SCOPE.md`). `SCOPE.md` is rewritten in place on each integration
line; the DG-K scope remains authoritative on `implement/dgk` until its closeout, and the
two versions are reconciled by hand at promotion to `main` (see "Promotion").

---

## Why this scope exists

1. PR #317 / issue #316 added L2 groundwork only: `schemas/l2-book-event-v1.json`,
   `L2BookEvent` / `L2LevelChange` (`quant_platform.data.models`) and
   `normalize_bybit_l2_message` (`quant_platform.source_adapters.l2`).
2. `A14` is nevertheless `OPEN_BLOCKING` / `MISSING`. No accepted ADR resolves snapshot /
   delta reconciliation, sequence continuity, gap detection and resync; no canonical
   materializer, ordering provider, publication path, ingest harness or consumer read exists
   for L2. `CAPABILITY_DAG.md` requires the L2 branch decision to ratify the #317 schema by ADR
   or supersede it.
3. The maintainer selected L2 on 2026-10-14. This scope turns that selection into a design gate
   and a registered, not yet authorized, atom inventory.

---

## Owner decisions (fixed inputs of the design gate)

Taken on 2026-10-14 and recorded by #364:

1. **Aggregated price levels only.** No order-level, queue-position or L3/MBO semantics; no
   synthetic book from trades.
2. **`A13` (L1) is not a prerequisite of `A14`.** "L1 does not activate L2" is unchanged.
3. **Two batches.** `v1` is historical ingest; `v2` is live ingest.
4. **Consumer read is in scope of `v1`.** An L2 that can be written but not read does not close `A14`.
5. **`implement/l2` stays cut from `implement/dgk`**; promotion to `main` follows DG-K closeout.
6. **Venue: Bybit only.** No second venue.

## Proposed inputs — NOT decisions

Drafted before this scope (PR #360) and carried only as proposals for `ADR-0070` to settle or
reject:

- ordering key `(exchange_ts, native_sequence, native_update_id)` and provider name
  `bybit-l2-exchange-ts-seq-v1`;
- physical path `canonical/l2/{venue}/{instrument}/l2-book-event-v1/dt=YYYY-MM-DD/part-001.parquet`,
  Zstandard compression and the Arrow schema of `bids`/`asks`;
- topics `orderbook.50` / `orderbook.500`;
- certification profile and check-suite names for `kind="l2"`.

---

## Part A — Design gate

| Gate | Target ADR | Questions to resolve | Status |
|---|---|---|---|
| **DG-C / A14** | `ADR-0070` | The eight questions of `OPEN_DECISIONS.md` (DG-C, L2 branch): total order given the update-id reset on snapshots; reconciliation, gap and resync rule and the partition-boundary state; events versus reconstructed book; Parquet layout and manifests; certification and publication eligibility; a read surface that does not change frozen J02 v1 or representation contracts; the fate of `l2-book-event-v1`; (`v2`) live supervision. | OPEN |

`ADR-0070` is a separate design-gate issue (opened after #364). No atom below is authorized
before it is accepted.

---

## Part B — Registered atom inventory

Registered as `OPEN_BLOCKING` / `MISSING`; **not authorized** until `ADR-0070` is accepted and the
atoms are re-stated by a reconciliation. Names are provisional; final names are the design gate's call.

| Atom | Batch | Capability | Requires |
|---|---|---|---|
| `A14a` | v1 | L2 ordering provider and certification profile | `A14`, `ADR-0070` |
| `A14b` | v1 | Canonical L2 Parquet materializer | `A14a` |
| `A14c` | v1 | L2 publication and catalog bridge for `kind="l2"` | `A14a`, `A14b`, `A08`, `A09` |
| `A14d` | v1 | Bybit historical L2 ingest with per-interval gap/outcome tracking | `A14b`, `A14c`, `A07` |
| `A14e` | v1 | L2 consumer read through DataGateway / Consumer API | `A14c`, `B02` |
| `A14f` | v2 | Bybit live L2 ingest with gap detection and resync | `A14d`, `A11` |

`A14` closes when `A14a`-`A14e` are `COMPLETE` and the `v1` golden end-to-end proof is accepted
(historical ingest -> materialization -> catalog publication -> consumer read). The golden proof is
the closeout criterion of the batch, not an atom. `A14f` follows and is outside that closure.

---

## Non-claims and out of scope

- No order-level, queue-position, MBO or L3 semantics; no synthetic book from trades.
- No L1 (`A13`), no second venue, no execution or trading on L2 data.
- No change to the frozen `trade-v1` or `candle-v1` contracts, to J02 v1, or to existing
  representation contracts unless an accepted ADR amendment allows it.
- No DG-K work (`P01`-`P06`, issues #339, #342, #343, #357) and no other parallel line.
- No implementation, no ADR text and no bulk market data in Git under this scope.

## Promotion

Each integration line rewrites `SCOPE.md` in place. DG-K promotes first; the L2 line then rebases and
reconciles `SCOPE.md` by hand at its own merge. The order is recorded in `ROADMAP.md` and is not
decided by an implementation issue.

## Stop conditions

Stop and report if registering or resolving DG-C / A14 would require changing an accepted ADR or a
frozen contract.
