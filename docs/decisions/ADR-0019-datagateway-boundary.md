# ADR-0019: DataGateway logical boundary

**Status:** ACCEPTED

**Date:** 2026-08-28

## Context

The canonical repository already separates natural dataset identity from
partition location. `datasets` identify logical data, `partitions` carry
coverage, lifecycle and hashes, and `storage_roots` provide physical anchors.
The higher quantitative layers need one access boundary that cannot leak
mount paths or recreate competing loaders. Legacy `ml_core` has path- and
dataframe-oriented access patterns, but it is reference evidence and not a
canonical contract.

## Decision

1. Upper layers consume market data through the logical `DataGateway` contract
   in `docs/contracts/DATA_GATEWAY.md`.
2. Callers use logical dataset identity, schema and temporal requests. Physical
   roots, partition paths and catalog/file resolution remain internal.
3. Direct Parquet, arbitrary filesystem and direct catalog access by upper
   layers are forbidden as application contracts.
4. DataGateway reads and returns canonical records plus durable-grade
   provenance; it does not compute candles, features, research, labels,
   strategies or execution semantics.
5. Historical reads and future live/stream access share identity, time and
   provenance primitives, while read and stream interfaces may remain separate.
6. The first implementation is a narrow catalog-backed canonical Bybit
   `trade-v1` read. No implementation is part of this ADR task.

## Consequences

Logical results remain stable when data moves between hot, cold or deep-cold
roots, and downstream artifacts can retain a reproducible source chain. The
gateway implementation must handle catalog conflicts, path safety, partition
states, half-open intervals and schema identity explicitly. Existing legacy
loaders cannot be adopted as the canonical interface without an adapter and
semantic review. DatasetSnapshot representation, return representation, live
stream contract and schema-v2 evolution remain separately tracked decisions.
