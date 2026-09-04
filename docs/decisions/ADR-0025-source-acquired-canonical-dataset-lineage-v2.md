# ADR-0025 — Source-Acquired Canonical Dataset Lineage v2

**Status:** ACCEPTED
**Date:** 2026-09-04

## Context

`dataset-manifest-v1` requires every non-raw dataset to declare at least one
`DatasetIdentity` parent and a transform. A source/archive acquisition is not a
`DatasetIdentity`, so a direct source/archive → canonical materialization cannot
be represented faithfully in v1. Creating a raw proxy, fake parent, or source
provenance subsystem would violate the frozen data-plane boundaries.

## Decision

Introduce the explicitly versioned `dataset-manifest-v2`. Version 1 remains
frozen and is never reinterpreted.

Dataset topology is:

```text
raw:
  origin absent, derived_from absent, transform absent

canonical + dataset_derived:
  origin=dataset_derived, derived_from >= 1, transform required

canonical + source_acquired:
  origin=source_acquired, derived_from absent, transform required

features:
  origin=dataset_derived, derived_from >= 1, transform required
```

`origin` describes dataset topology only. It does not identify a venue, source,
archive, storage location, source semantics, mapping, or coverage completeness,
and it is not part of `DatasetIdentity` or the catalog natural key.

For v2, `transform` means the semantic transformation that produced the
dataset. It is persisted on `catalog.dataset_lineage` only for datasets with
declared dataset parents. A source-acquired canonical dataset retains the
transform in its durable manifest and has exactly zero lineage rows; no
synthetic parent or catalog transform column is introduced.

Source provenance remains owned by `coverage-manifest-v1` acquisition,
assertions, and evidence. Coverage v1 is unchanged. S14 performs no additional
origin-to-coverage cross-check beyond the existing S13 quality-report and
coverage-hash binding.

Manifest validators and S14 use explicit schema-version dispatch. Unsupported
versions fail closed. V1 raw, canonical, features, and zero-parent rejection
retain their existing semantics.

S13 semantics, the catalog DDL, partition and trade contracts, DataGateway,
materializer, and Golden fixture remain unchanged. The existing catalog tables
represent a source-acquired canonical dataset as one `catalog.datasets` row and
zero `catalog.dataset_lineage` rows.

The v2 manifest is sufficient for a future semantic rebuild to reconstruct that
same zero-lineage topology. No catalog rebuild runtime is introduced by this
decision.

## Consequences

- Direct source/archive → canonical publication is representable without fake
  lineage.
- Existing v1 manifests remain compatible and retain their frozen meaning.
- Generic S14 parent resolution must understand v1 and v2 topology, while its
  exact-lineage and Phase-5 verification algorithms remain unchanged.
- Source-specific acquisition evidence remains outside the generic manifest and
  publication contracts.
- The implementation remains a narrow contract/runtime compatibility slice;
  source registries, artifact tables, provenance frameworks, new layers, and
  catalog migrations are out of scope.
