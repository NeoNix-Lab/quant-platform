# ADR-0060 — Canonical replay I/O materialization profile v1

**Status:** ACCEPTED
**Date:** 2026-10-04

## Decision

The selected `trade-v1` profile for new replay/research materialization is the
existing behavior: Parquet v2.6, `zstd` compression, `65,536` rows per row
group, all canonical `trade-v1` columns, and sequential `DataGateway.scan()`
reads with a caller-selected logical batch default of `65,536`.  The physical
reader projects only canonical columns, uses one batch and fragment read-ahead,
and disables reader threads so physical scheduling cannot alter order or
multiply the logical batch-memory bound.

This profile changes no semantic record, canonical content hash, ordering, or
DataGateway contract.  Compression and row-group layout are physical artifact
choices: they change the exact file hash and require normal publication/
certification evidence for a replaced artifact, while identical ordered rows
retain their canonical content identity.  Do not change these defaults for a
large multi-year materialization without a new measured profile decision.

## Evidence and scope

The focused layout test proves zstd/default row grouping and that the same
ordered rows are returned through bounded scanning from a different physical
layout.  It is a fixture-level behavioral proof, not a throughput benchmark;
hardware-dependent performance measurements remain an operational follow-up.

No semantic recertification is required here because no artifact writer or
fixture artifact changes.  A future profile change must create replacement
artifacts and recertify their affected publication evidence.

## Out of scope

Representation materialization, feature precomputation, replay semantics,
source-acquired evidence rules, and changing existing artifacts.

## Related

Parent tracking: #232. Closes issue #235.
