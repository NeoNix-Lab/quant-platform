# Scope: Representation Foundation — CandleDefinition Contract v1

## Goal

Produce an independently reviewable semantic contract candidate for
`CandleDefinition v1`, sufficient for a deterministic historical fixed-duration
candle runtime and compatible with future incremental/live construction.

This scope deliberately stops before the candle aggregation runtime. The
contract must be reviewed independently before any runtime implementation is
started.

## Roadmap position

The authoritative roadmap remains unchanged. DataGateway is the preceding
foundation and the later Feature, Research, Execution, API and client phases
remain later. This is the Representation foundation contract milestone; it does
not claim that the Representation runtime has started or reorder any phase.

## In scope

- `CandleDefinition v1` semantic identity and canonical serialization;
- canonical `trades@1` / `trade-v1` source dependency;
- fixed-duration UTC epoch alignment and half-open bucket semantics;
- exact decimal OHLCV and trade-count aggregation semantics;
- empty-bucket, coverage, support-interval and query-interval rules;
- `PARTIAL` / `CLOSED`, finalization, correction and late-event obligations;
- temporal availability and historical/live equivalence invariants;
- materialization, provenance and closed-record schema semantics;
- focused contract fixtures/tests that do not implement aggregation runtime;
- scope, capability-map, documentation-index and ADR updates.

## Out of scope

- candle aggregation runtime or `src/quant_platform/representations`;
- FastAPI, HTTP endpoints, DTOs, handlers or transport serialization;
- React, TUI, CLI commands or other client runtime code;
- footprint, L1, L2, L3 or book representations;
- Feature, Research, Outcome, Label, Validation, Strategy or Execution
  runtimes;
- Job Runtime, authentication, deployment, pagination or streaming;
- DataGateway Contract v1, canonical trade schema/DDL or physical storage changes;
- DataGateway, producer ingest, catalog migration or storage lifecycle changes;
- legacy CoreApp/API porting or compatibility promises.

## Exit criteria

- `CandleDefinition v1` identity, serialization and hashing are explicit;
- all mandatory candle semantic questions have a normative answer;
- source, query, dataset, materialization and runtime identities are separate;
- closed-record schema semantics are precise enough for independent runtime
  implementations;
- deterministic decimal, ordering, coverage, availability and equivalence
  obligations are testable;
- the ADR remains `PROPOSED — pending independent review`;
- no candle runtime, shared tooling or producer work is changed.

## Next cycle

After independent contract review, a separate mandate may implement the
historical fixed-duration candle runtime against this contract. That mandate
must not silently expand v1 or reinterpret unresolved source/data-plane
semantics.
