# Scope: Consumer API Boundary Contract v1

## Goal

Freeze the semantic consumer-facing boundary of Quant Platform after the
DataGateway implementation slice, without implementing API transport, clients
or downstream quantitative engines.

## Roadmap position

The authoritative roadmap remains unchanged. The DataGateway implementation
milestone is complete at the base of this branch. The next product capability
in dependency order remains Representation foundation; the later Canonical
API and job runtime phase remains later. This scope is a contract-preparation
milestone and does not renumber or reorder those phases.

## In scope

- semantic Consumer API boundary for App UI, TUI, CLI and future consumers;
- distinction between consumer query, RepresentationDefinition and
  DatasetIdentity;
- consumer-visible coverage, deterministic result and provenance guarantees;
- stable API error translation policy;
- synchronous bounded reads versus future asynchronous Job semantics;
- versioning/evolution policy;
- `trades@1` backed by canonical Bybit `trade-v1` as a reference slice;
- documentation index, capability-map and ADR updates.

## Out of scope

- FastAPI, HTTP endpoints, DTOs, handlers or transport serialization;
- React, TUI, CLI commands or other client runtime code;
- candle, footprint, L1, L2, L3 or book builders;
- Feature, Research, Outcome, Label, Validation, Strategy or Execution
  runtimes;
- Job Runtime, authentication, deployment, pagination or streaming;
- DataGateway Contract v1, canonical schemas/DDL or physical storage changes;
- legacy CoreApp/API porting or compatibility promises.

## Exit criteria

- the semantic API boundary and client dependency direction are documented;
- consumer query, representation definition and dataset identity are
  explicitly separate;
- `trade-v1` is documented as a reference implementation, not the general
  market-data model;
- coverage, provenance, temporal semantics and error distinctions are
  preserved at the boundary;
- future representation and job extension points are explicit without
  claiming unsupported implementations;
- no runtime or client code is changed.

## Next cycle

Representation Foundation: CandleDefinition identity, closed/partial
semantics and equivalence tests, under the existing roadmap order.
