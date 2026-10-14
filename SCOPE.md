# Scope: DG-C / Capability A14 — Canonical L2 Market Depth Ingest & Materialization v1

Status: **OPEN — Design Gate and Implementation Scope on integration branch `implement/l2`.**

Scope kind: this document defines the governance and implementation boundary to resolve capability **`A14` (L2 contract/acquisition)** and render aggregated L2 orderbook market depth fully ingestible, canonically materializable as Parquet, and publishable into `market_catalog`.

---

## Historical scope archive

This file previously recorded **DG-K — Process Topology v1** (Part A closed via ADR-0068/ADR-0069; Part B implemented on `implement/dgk`), and earlier **Wave 8 — Full Platform API & Remote Service Topology v1** (closed #327/PR #328). Both are preserved in git history (`git log -- SCOPE.md`). Per repository governance, `SCOPE.md` is updated in place on active integration branches.

---

## Why this scope exists

1. Groundwork for L2 market depth was introduced in PR #317 / issue #316:
   - Frozen schema: `schemas/l2-book-event-v1.json`
   - Canonical models: `L2BookEvent` and `L2LevelChange` in `quant_platform.data.models`
   - Normalizer: `normalize_bybit_l2_message()` in `quant_platform.source_adapters.l2`
2. However, capability **`A14`** remained recorded as `OPEN_BLOCKING` and `MISSING` in `CAPABILITY_DAG.md` and `OPEN_DECISIONS.md`:
   - No accepted ADR resolves snapshot / delta reconciliation, sequence continuity, gap detection, and resync semantics.
   - No canonical Parquet materializer exists for `l2-book-event-v1` (`materialize_l2_book_event_v1` is missing from `quant_platform.data.materializer`).
   - No deterministic `OrderingProvider` is ratified for `L2BookEvent`.
   - No catalog publication eligibility bridge or S13/S14 certification suite exists for the `l2` dataset kind.
   - No operational ingestion harness (historical dump reader / live websocket supervisor) is wired to acquire and store canonical L2 partitions.
3. This scope resolves `A14` end-to-end, providing a production-grade, reproducible pipeline for L2 market depth ingest, Parquet partition materialization, and catalog publication.

---

## Owner decisions (inputs governing this scope)

1. **Ratify `l2-book-event-v1`**: Build strictly upon `schemas/l2-book-event-v1.json` and existing domain models `L2BookEvent` and `L2LevelChange`. No breaking changes to existing data-plane contracts.
2. **Aggregated Price-Level Only**: L2 semantics stop at aggregated price levels (`bids`/`asks` with exact decimal strings; `action="delete"` with `size="0"`, `action="upsert"` with `size > 0`). Never infer order queue position, order IDs, or native MBO/L3 mechanics.
3. **Deterministic Total Order**: Ratify an explicit canonical ordering policy for L2 partitions based on `(exchange_ts, native_sequence, native_update_id)`.
4. **Physical Canonical Storage**: Parquet partitions placed under `canonical/l2/{venue}/{instrument}/l2-book-event-v1/dt=YYYY-MM-DD/part-001.parquet` using Zstandard compression and dictionary encoding.
5. **Catalog Publication**: Support `kind="l2"` in `PublicationCertification` and `PublicationEligibilityBridge` with full manifest emission (`dataset-manifest.json`, `partition-manifest.json`, `coverage-manifest.json`).
6. **Strict Continuity & Gap Semantics**: Bybit L2 delta streams must be validated against sequence identifiers (`seq` and `u`); any undetected gap invalidates the partition interval unless bridged by a valid authoritative snapshot.

---

## Part A — Design Gate (ADR-0070)

| Gate | Target ADR | Scope & Questions Resolved | Status |
|---|---|---|---|
| **DG-C / A14** | `ADR-0070` | **Canonical L2 Orderbook Ingest, Continuity, and Materialization v1**:<br>1. Snapshot & delta reconciliation rules and gap detection policy.<br>2. Canonical ordering provider definition (`bybit-l2-exchange-ts-seq-v1`).<br>3. Physical Parquet layout (PyArrow schema for bids/asks level changes).<br>4. Partition and coverage manifest schemas for L2.<br>5. Catalog publication eligibility rules (`L2_CHECK_SUITE` & `L2_CERTIFICATION_PROFILE`). | OPEN |

---

## Part B — Authorized Implementation Inventory

Each atom below is implemented on an atomic branch targeting `implement/l2`:

| Atom | Capability | Scope | Dependencies | Sequencing Note |
|---|---|---|---|---|
| `A14a` | L2 Ordering & Certification Profile | Ratify `OrderingProvider` and certification profile for `l2-book-event-v1` in `quant_platform.ordering` and `quant_platform.source_adapters.l2`. | ADR-0070 | Can start immediately upon ADR-0070 acceptance. |
| `A14b` | Canonical L2 Parquet Materializer | Implement `materialize_l2_book_event_v1()` in `quant_platform.data.materializer` with PyArrow Zstd compression and atomic file emission. | `A14a` | Requires `A14a` ordering provider. |
| `A14c` | L2 Publication & Catalog Bridge | Extend `PublicationCertification` and `PublicationEligibilityBridge` to support `kind="l2"` in `quant_platform.data.publication`. | `A14a`, `A14b` | Enables sealed partition cataloging. |
| `A14d` | Bybit L2 Ingest & Stream Harness | Implement Bybit L2 historical reader & live websocket subscriber with gap detection in `quant_platform.source_adapters.bybit_l2`. | `A14b`, `A14c` | Operational ingestion pipeline. |
| `A14e` | Golden E2E Verification & Integration | Hermetic test suite and deterministic golden proof covering ingest -> materialization -> catalog publication -> consumer read. | `A14d` | Closeout milestone for `A14`. |

```text
ADR-0070 ──> A14a ──> A14b ──> A14c ──> A14d ──> A14e (Golden Proof)
```

---

## Integration branch

- Active integration branch: **`implement/l2`** (branched from `origin/implement/dgk` @ `9db9637`).
- Atomic slices use `agent/issue-<N>-<slug>` (or `codex/issue-<N>-<slug>`) branched from `implement/l2` and targeting `implement/l2`.
- Promotion to `main` occurs only upon successful completion of `A14e` golden acceptance.

---

## Authority

- `AGENTS.md`
- `docs/contracts/CORE_CONTRACTS.md` (§7 `L2Update / L2Snapshot`)
- `schemas/l2-book-event-v1.json`
- `docs/product/CAPABILITY_DAG.md` (§DG-C `A14`)
- `docs/architecture/OPEN_DECISIONS.md` (`A14`)
- `ADR-0004` (market data levels), `ADR-0018` (contract evolution), `ADR-0024` (modular monolith)

---

## Baseline and Credited State

```text
implement/l2 @ 9db9637 (branched from origin/implement/dgk)
Credited data plane: trade-v1, candle-v1, footprint-v1, l2-book-event-v1 schema (PR #317)
Credited platform: J02 message families, K12/K13 runtime store, process topology v1
```

Credit, do not rebuild:
- Existing `trade-v1`, `candle-v1`, and `footprint-v1` materializers and datasets.
- Schema `schemas/l2-book-event-v1.json` and domain models `L2BookEvent`, `L2LevelChange`.
- Venue normalizer `normalize_bybit_l2_message()`.

---

## Explicit non-claims

- No native L3/MBO order queue simulation or individual order tracking.
- No modification of frozen `trade-v1` or `candle-v1` contracts.
- No synthetic generation of L2 depth from trade flow.
- No direct bypass of the DataGateway / storage abstraction layer.

---

## Exit Criteria for this Scope

1. `ADR-0070` accepted and recorded in `docs/decisions/`.
2. Atoms `A14a` through `A14e` fully implemented with passing unit, contract, and property tests.
3. Hermetic golden roundtrip test proving lossless raw L2 wire -> canonical Parquet -> catalog partition seal -> consumer read.
4. Capability `A14` promoted from `OPEN_BLOCKING` / `MISSING` to `COMPLETE` in governance reconciliation.
