# MARKET_PLATFORM_INTEGRATION.md

**Status:** DRAFT v0.1  
**Purpose:** define how the Quant Research/Trading platform consumes the canonical server-side market-data plane.

---

## 1. Integration principle

The canonical market-data plane owns market datasets, storage, manifests, partition identity and lineage.

The research/trading layers consume data through `DataGateway`.

Do not duplicate authoritative data contracts in the research layer.

---

## 2. Existing server contracts to treat as authoritative candidates

The current server implementation already contains important contracts and infrastructure that must be reviewed before defining new equivalents.

Known authoritative/near-authoritative areas include:

- canonical trade schema (`trade-v1`);
- dataset identity;
- partition manifests;
- dataset manifests;
- lineage;
- storage roots;
- PostgreSQL catalog;
- canonical Parquet representation work;
- historical Bybit import/reference fixtures.

The exact current files and schema versions must be audited from the repository before freezing this document.

---

## 3. Storage identity vs physical location

The research layer must consume logical identity.

It must not use physical storage path as dataset identity.

Conceptually:

```text
DatasetIdentity
      ↓
PartitionIdentity
      ↓
StorageReference
      ↓
physical path / root
```

Moving data between hot/cold/deep-cold storage must not alter the logical quantitative identity.

---

## 4. DataGateway boundary

Upper quantitative layers should request data semantically.

Examples:

```text
read trades for:
- dataset identity
- instrument
- time range
- projected columns
```

or:

```text
open replay stream for:
- dataset identity
- time range
- event families
```

The DataGateway resolves catalog/partition/storage details.

---

## 5. Server-side feature computation

Primitive derived features should be computable close to the canonical data.

Target server flow:

```text
canonical partitions
       ↓
Feature Job
       ↓
Feature Engine
       ↓
derived feature partitions
       ↓
catalog + lineage registration
```

Early candidate primitive trade features:

- aggressive buy volume;
- aggressive sell volume;
- delta;
- total volume;
- trade count;
- VWAP;
- buy/sell ratio;
- imbalance;
- intensity;
- returns;
- candle descriptors.

---

## 6. Derived feature persistence

Materialized feature outputs must preserve:

- source dataset identity;
- source partition identities;
- FeatureDefinition identity;
- implementation/code identity;
- computation time range;
- output grain;
- content identity;
- storage reference.

Do not create an untracked feature file merely because computation succeeded.

---

## 7. L1/L2/L3 contract rule

Before designing research-layer L1/L2/L3 schemas:

1. inspect current server contracts;
2. identify what is already represented;
3. determine whether extension belongs in the data plane;
4. define quantitative features only above the raw/canonical data contract.

Raw market-data levels and derived feature levels must remain separate concepts.

---

## 8. Candles

Candles may be computed from canonical source data on the server.

The CandleDefinition must be reproducible and catalogable.

Potential source examples:

- canonical trades;
- canonical L1 stream;
- combined canonical event stream, if explicitly defined.

Closed historical and incremental live candle implementations must converge to identical final semantics.

---

## 9. Research access to materialized features

The Research Engine should not care whether a feature was:

- computed ephemerally;
- loaded from cache;
- loaded from a materialized feature dataset.

It consumes a FeatureArtifact with valid identity/provenance.

---

## 10. Git boundary

Source control contains:

- contracts;
- feature implementations;
- manifests/schemas definitions;
- tests;
- small fixtures;
- infrastructure code;
- catalog migrations.

Source control does not contain:

- bulk market data;
- large derived feature partitions;
- experiment caches;
- large model artifacts;
- secrets.

---

## 11. Integration tests

At minimum:

- canonical partition → DataGateway read;
- canonical trades → candle reference;
- canonical trades → primitive feature reference;
- materialized feature → catalog lineage;
- relocated physical partition → unchanged logical identity;
- historical replay ordering;
- temporal availability enforcement.

---

## 12. Open decisions to freeze after repository audit

- exact L1 raw contract;
- exact L2 snapshot/update contract;
- exact L3 event contract;
- feature partition schema;
- whether candles are a distinct dataset family or a specialized feature/representation artifact;
- unified manifest schema for derived datasets;
- feature catalog tables vs reuse/extension of dataset catalog;
- live stream catalog/identity semantics.
