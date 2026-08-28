# Scope: Data Access Foundation — DataGateway Contract v1

## Goal

Freeze the logical market-data read boundary between the canonical data plane and higher quantitative layers, without implementing DataGateway.

## In scope

DataGateway logical request/result contracts; UTC half-open temporal semantics; partition-state and schema rules; provenance requirements; physical-path isolation; historical/live boundary; first implementation slice; ADR-0019; and the minimum scope/capability/open-decision updates needed to govern implementation.

## Out of scope

DataGateway implementation or package code; candles; features; research; labels; API/client; live/stream transport; schema-v2; new L1/L2/L3 schemas; caching/distribution; and execution or ML/RL migration.

## Exit criteria

- The DataGateway v1 contract is documented and its ownership is explicit.
- Request identity, `[start, end)` UTC semantics, deterministic ordering,
  partition states and schema identity are explicit.
- Physical paths and storage-root locations are forbidden from upper-layer
  contracts, and the minimum provenance chain is identified.
- The first catalog-backed implementation slice is specified without being
  implemented.
- Unresolved choices are isolated in `OPEN_DECISIONS.md` and the contract is
  independently reviewed before implementation.
- All locally runnable canonical Python script tests pass through
  `python tools/run_tests.py`; environment-dependent database/integration
  tests are explicitly reported and remain unchanged.

## Next cycle

DataGateway v1 implementation, after independent contract review.
