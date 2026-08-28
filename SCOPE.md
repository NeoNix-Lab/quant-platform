# Scope: Repository Foundation v1

## Goal

Turn the server-born repository and reviewed bootstrap architecture into the version-controlled canonical foundation of Quant Platform.

## In scope

Documentation reorganization; README; proprietary LICENSE; agent governance; corrected capability map; roadmap; open decisions; ADR indexing; ADR-0016 through ADR-0018; legacy adoption records; `.gitignore` hardening; and documentation indexes.

## Out of scope

DataGateway, candle code, Feature Engine code, schema-v2 implementation, new L1/L2/L3 schemas, execution code, ML/RL migration, API/client implementation and live implementation.

## Exit criteria

- Canonical documentation is version-controlled and the temporary bootstrap nesting is gone.
- ADRs, capability map, roadmap, open decisions and scope are indexed.
- Root README, LICENSE and AGENTS exist.
- Frozen data-plane contracts, fixtures and import semantics are unchanged.
- All locally runnable canonical Python script tests pass through
  `python tools/run_tests.py`; environment-dependent database/integration
  tests are explicitly reported and remain unchanged.

## Next cycle

Data Access Foundation / DataGateway design.
