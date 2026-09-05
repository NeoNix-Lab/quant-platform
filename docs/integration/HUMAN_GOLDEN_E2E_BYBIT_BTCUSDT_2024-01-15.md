# Human Golden E2E — Bybit BTCUSDT 2024-01-15

**Status:** PASS  
**Acceptance date:** 2026-09-05  
**Vertical:** `canonical/trades/bybit/BTCUSDT/trade-v1`  
**Closeout branch head at evidence capture:** `af0b5c328f028ac543d2c2f3faaa9fbff2193172`

This document records the first Human Golden end-to-end acceptance of the
frozen Bybit BTCUSDT first vertical. It is gate evidence, not a declaration that
the complete Conformity Implementation Gate has passed.

## Production path exercised

```text
legacy SQLite historical source
        ↓
Bybit historical source adapter
        ↓
source-specific translation
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

No direct Parquet consumer bypass was used for final verification.

## Golden observations

| Observation | Expected | Observed | Result |
|---|---:|---:|---|
| Rows | 1,105,145 | 1,105,145 | PASS |
| Buy | 553,875 | 553,875 | PASS |
| Sell | 551,270 | 551,270 | PASS |
| Other side | 0 | 0 | PASS |
| First exchange timestamp | `2024-01-15T00:00:00.492Z` | `2024-01-15T00:00:00.492Z` | PASS |
| Last exchange timestamp | `2024-01-15T23:59:59.931Z` | `2024-01-15T23:59:59.931Z` | PASS |

## Publication and consumer evidence

```text
S13 certification         pass
S14 eligibility           valid
coverage_complete         true
coverage_gaps             0
catalog partitions        1
DataGateway lifecycle     OPEN → READING → COMPLETED
completed metadata        present
batches                   17
max_batch_size            65,536
Golden comparison         exact match
```

## Durable content identities

### Historical source extract fingerprint

```text
a707835c5ca7743ff8879383ca5127111b23bb041289c4c888acb2f294ac9609
```

### Physical canonical artifact SHA256

```text
a9581327807fe2c3286bab8b2e6a2ce82dbb7d28a1724bc4d47bc66b9199ef6f
```

### CanonicalContentHashV1 payload

```text
e0a2bc287aef3b95f07d584b5203e3ddd1f8b9807347e609f69618525f41eedf
```

The canonical value above is the SHA-256 payload of the
`CanonicalContentHashV1` identity captured by the run.

## Bounded-read note

The operator run did not pass an explicit memory sampler to the Golden observer,
so memory telemetry was reported as unavailable. This is not a Golden mismatch:
`golden_field_mismatches()` does not use optional memory telemetry as an
acceptance field.

The run nevertheless observed the full 1,105,145-row result through 17 bounded
batches with `max_batch_size=65,536`. The stronger BR1 property — memory bounded
independently of requested row count — remains an implementation/test invariant
of the Bounded DataGateway finite read slice rather than a claim derived solely
from these batch counters.

## Evidence retention

The successful canonical artifact, durable manifests, quality evidence and
catalog rows are acceptance evidence. Closeout must not silently delete,
overwrite, truncate or reset them. Any future retention or archival procedure
belongs to explicit storage/operations governance.

## Gate consequence

This run closes:

```text
Human vertical / Golden Bybit BTCUSDT E2E — PASS
First-vertical Human acceptance             — COMPLETE
```

It does **not** close:

```text
Adversarial acceptance
Candle ordering compatibility acceptance
Final Conformity Implementation Gate review
```

Therefore the complete Conformity Implementation Gate remains
**IN PROGRESS / PARTIAL** until those remaining acceptance items and final gate
review pass.
