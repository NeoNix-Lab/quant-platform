# Storage Lifecycle and Data Protection

**Status:** Architecture v1 — operational foundation; no storage runtime implemented

This document owns storage placement, tiering, protection, capacity, health,
and pressure concerns. It does not duplicate Data Plane identity, manifest,
catalog, or DataGateway semantics.

## 1. Storage is an operational Data Plane capability

Storage Lifecycle is inside the existing Data Plane. It changes physical
placement and operational protection, not the logical meaning of a dataset.

```text
logical DatasetIdentity / PartitionIdentity
                 ↓
       storage root and physical placement
                 ↓
          DataGateway resolution
```

The catalog, manifests, hashes, and lineage remain the authoritative shared
contracts. Consumers use logical requests through [DATA_GATEWAY.md](../contracts/DATA_GATEWAY.md);
they do not select disks, mounts, or paths.

## 2. Current storage-root foundation

The current provisioning and catalog model defines these operational roots:

| Root | `storage_root_id` | Catalog tier | Current role |
|---|---|---|---|
| Hot | `hot` | `hot` | Active write/read path on `/srv/marketdata` |
| Cold | `cold` | `cold` | Read-mostly storage on `/archive/marketdata-cold` |
| Deep-cold | `deepcold` | `cold` | Bulk read-mostly storage on `/cold/marketdata-deepcold` |

`deepcold` is a storage-root identifier, not a third semantic catalog tier.
The catalog tier remains `cold`, as defined by `db/init/001_catalog.sql` and
`infra/provision-marketdata.sh`.

## 3. Logical identity and physical placement

Dataset identity and partition identity are independent of storage device.
Moving a partition between hot, cold, and deep-cold must not create:

- a new DatasetIdentity;
- a new record schema;
- a new semantic dataset;
- a consumer-visible path contract.

Partitions of one logical dataset may occupy different roots simultaneously:

```text
recent partitions     → hot
older partitions      → cold
very old partitions   → deepcold
```

DataGateway may resolve the current location internally through catalog and
storage-root metadata. Physical location is diagnostic/operational metadata,
not application identity.

## 4. Tiering and relocation

The conceptual lifecycle is:

```text
actively written / recently used → HOT
sealed / older / less active     → COLD
large / old / rarely accessed    → DEEPCOLD
```

No age, utilization, schedule, or migration threshold is frozen.

A future relocation must ensure:

1. the original authoritative copy remains valid until the target is verified;
2. target content identity matches the source;
3. catalog location changes only after target validation;
4. failure before the switch leaves the original authoritative;
5. failure after the switch remains recoverable;
6. source removal occurs only after verification and state update.

Incomplete relocation must not become authoritative or create temporary
consumer disappearance. The exact relocation algorithm remains open.

## 5. Tiering is not backup

```text
STORAGE TIERING ≠ BACKUP
```

Moving the only copy from hot to cold or cold to deep-cold does not create an
independently recoverable copy. A backup or replica exists only when an
additional copy can be recovered and verified independently.

Deep-cold must not be described as a backup merely because it is slower, older,
or on a different device.

## 6. Data-protection classes

Retention and pressure decisions should account for reconstruction cost:

```text
cache / ephemeral
        ↓
derived / recomputable
        ↓
canonical
        ↓
unique RAW / source
```

This is a preservation-priority principle, not an automatic deletion policy.

Unique RAW and source archives may be non-reconstructible and receive the
highest preservation priority. Canonical data is normally reconstructible from
preserved source data and canonicalization contracts. Derived data and caches
are generally more reproducible.

## 7. RAW and source protection

Source protection is separate from normal tiering. It is especially important
for live L2/L3/MBO, feeds without complete downloadable history, and native
evidence required to reconstruct canonical data.

A future protection capability should cover:

- an independently recoverable second physical copy;
- source and archive certification;
- checksum and integrity verification;
- replication freshness and backlog;
- restore validation;
- immutable treatment of preserved migration sources where required.

Preserved SQLite or similar migration archives are separate from active
partitions. Their protection should include source certification, immutability,
checksums, preservation priority, and backup before the original is removed.
Migration-specific filenames must not become general architecture.

## 8. Backup and restore

The PostgreSQL catalog is intended to be reconstructible from durable manifests
where current repository semantics support it. Operational PostgreSQL backups
remain valuable and complementary.

Backup is a verified recovery capability, not merely a copied file:

```text
recoverable second copy
        + integrity verification
        + restore procedure
        + periodic restore validation
        = credible backup capability
```

Future backup capability should expose backup freshness, integrity status,
retention, restore results, and replication backlog. Schedule, software,
destination, cloud/NAS/object-storage choice, RAID, and replication topology
remain open. No backup implementation is included here.

Protect separately, as applicable:

- dataset and partition manifests;
- source/RAW data;
- canonical data;
- PostgreSQL catalog;
- Git repository and schema definitions;
- deployment configuration;
- checkpoint and operational state.

Market-data files alone are not a complete system-recovery plan.

## 9. Capacity management

Finite disk capacity is an operational constraint on ingest. Future monitoring
should report per storage root:

- total, used, and available bytes;
- percentage used;
- recent write rate;
- estimated time-to-full;
- writing/staging footprint;
- tiering backlog;
- backup/replication backlog;
- growth by dataset, venue, and data level where available.

Percentage used alone is insufficient. A future estimate should be equivalent
to:

```text
time_to_full = available_capacity / observed_write_rate
```

The estimator must handle changing rates, especially for L2, L3/MBO, and
multi-venue live acquisition. The forecasting algorithm and monitoring stack
are open.

Capacity accounting must include open partitions, downloads, canonicalization
staging, temporary files, and relocation copies, not only final artifact size.

## 10. Capacity states and pressure

The runtime may eventually expose configurable operational states:

| State | Meaning |
|---|---|
| `NORMAL` | Capacity is sufficient |
| `PRESSURE` | Projected exhaustion requires intervention |
| `CRITICAL` | Continued acquisition threatens safe persistence |
| `EXHAUSTED` | Safe persistence cannot be guaranteed |

Numeric thresholds are not frozen.

Potential pressure responses include tier migration, pausing optional
historical backfill, rejecting new bulk jobs, prioritizing live acquisition,
reclaiming cache, reclaiming reconstructible derived materializations, alerting,
or an emergency halt. These are future policy options, not automatic behavior.

Unique RAW/source data must never be silently deleted merely to free space.
Any destructive action must eventually be explicit, policy-based, auditable,
and provenance-aware.

## 11. Storage health

Future health monitoring may observe:

- mount availability and expected filesystem UUID;
- device remapping and read-only remounts;
- free space and I/O errors;
- checksum, write, and relocation failures;
- device-health evidence where available;
- backup freshness and replication lag/backlog.

No Prometheus, Grafana, Zabbix, exporter, or other monitoring stack is chosen.

## 12. Finite jobs and persistent services

- Historical/backfill acquisition may use asynchronous job semantics.
- Live collection is a persistent service with different recovery semantics.
- Tiering may be a finite operational workload.
- Backup may be a finite operational workload.
- Capacity and health monitoring may be persistent or scheduled.

No generic scheduler or DAG is required by this architecture.

## 13. Open storage decisions

- hot-to-cold and cold-to-deep-cold thresholds;
- relocation implementation and restart protocol;
- retention periods and deletion authority;
- backup destination, frequency, and topology;
- restore-validation procedure and evidence;
- capacity thresholds and forecasting method;
- pressure-state transitions and operator actions;
- checkpoint-state protection;
- monitoring and alerting technology.
