# Human Golden E2E — Bybit BTCUSDT 2024-01-15

- **Status:** PASS
- **Acceptance date:** 2026-09-05
- **Vertical:** `canonical/trades/bybit/BTCUSDT/trade-v1`
**Branch head at evidence capture:** `af0b5c328f028ac543d2c2f3faaa9fbff2193172`

This document records the first Human Golden end-to-end acceptance of the
frozen Bybit BTCUSDT first vertical. It is gate evidence, not a declaration
that the complete Conformity Implementation Gate has passed.

## Path exercised

```text
legacy SQLite historical source/archive
        ↓
Bybit historical source adapter
        ↓
TradeRecord
        ↓
canonical trade-v1 Parquet
        ↓
Dataset / Partition / Coverage manifests
        ↓
S13 certification
        ↓
S14 publication eligibility
        ↓
PostgreSQL catalog
        ↓
DataGateway.scan()
        ↓
Golden observer
        ↓
exact match
```

The legacy SQLite source at
`/cold/imports/legacy/bybit/sqlite/2326.sqlite` is legitimate historical
source/archive evidence. It is not a raw `DatasetIdentity`, a temporary proxy
dataset or a lineage parent.
The source-acquired canonical dataset uses `dataset-manifest-v2` with
`origin=source_acquired`, no `derived_from`, a required transform and exactly
zero catalog lineage edges. Source provenance remains in the CoverageManifest.

## Human execution and Golden observations

```text
PREFLIGHT                 PASS
RUN                       PASS
INSPECT                   PASS
GOLDEN E2E                PASS

row_count                 1,105,145
buy                         553,875
sell                        551,270
other_aggressor_side              0
first_exchange_ts         2024-01-15T00:00:00.492Z
last_exchange_ts          2024-01-15T23:59:59.931Z
```

## Publication and consumer evidence

```text
S13 certification         pass
S14 eligibility           valid
coverage_complete         true
coverage_gaps             0
catalog_partition_count   1
DataGateway lifecycle     OPEN → READING → COMPLETED
batches                    17
max_batch_size             65,536
Golden comparison         exact match
```

No direct Parquet consumer bypass was used for final verification.

## Durable content identities

```text
source fingerprint
  a707835c5ca7743ff8879383ca5127111b23bb041289c4c888acb2f294ac9609

physical artifact SHA256
  a9581327807fe2c3286bab8b2e6a2ce82dbb7d28a1724bc4d47bc66b9199ef6f

canonical-content-hash-v1
  sha256:e0a2bc287aef3b95f07d584b5203e3ddd1f8b9807347e609f69618525f41eedf
```

## Authorization boundary

The Human E2E used operator/bootstrap privileges sufficient to exercise
publication end to end. Those privileges are acceptance/bootstrap evidence
only. They do not define the production runtime authorization model.

Current and future access paths remain distinct:

```text
human administrator       → SSH
local production services → canonical service identities
future normal clients     → Canonical API → application services
                            → DataGateway → canonical data plane
```

The separate, non-blocking **Server Access & Runtime Identity Hardening v1**
follow-up must restore or audit canonical server access, service identities,
filesystem ACLs, database roles and credential disposition. It does not block
the Human E2E merge and does not open or close the Conformity Implementation
Gate.

## Gate and merge consequence

This run closes the Human Golden E2E milestone. It does not close adversarial
acceptance, Candle ordering compatibility or the final Conformity Gate review.
The complete Conformity Implementation Gate therefore remains **PARTIAL**.

The closeout sequence is:

```text
Human Golden E2E PASS
        ↓
governance closeout
        ↓
branch review / CI
        ↓
merge
```

After merge, the remaining gate path starts from authoritative `main`:

```text
Adversarial Acceptance + Candle Ordering Compatibility
        ↓
Final Conformity Gate Review
        ↓
Conformity Implementation Gate PASS
```

Package Boundary / Modular Monolith Foundation and Legacy Capability Harvest
remain post-gate work. Canonical API runtime, storage relocation and
backup/restore are not implemented by this closeout.
