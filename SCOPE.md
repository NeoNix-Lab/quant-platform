# Scope: Producer–Consumer Conformity Gate v1 — Contract Correction

## Goal

Fix the contract blockers and precision defects an independent review
(`REQUEST CHANGES`) found in the first Producer–Consumer Conformity Gate v1
candidate, so the pair (`ADR-0023`,
`docs/contracts/PRODUCER_CONSUMER_CONFORMITY.md`) is ready for a final
re-review. This scope deliberately stops before any producer or consumer
runtime implementation.

## Roadmap position

Unchanged from the prior cycle: this work sits at the **Contract Freeze
Gate**, the first of the two gates `ADR-0023` now defines, between the
accepted foundation contracts (DataGateway v1, CandleDefinition v1, Declared
Coverage v1) and any conformity-slice implementation. It does not reorder any
roadmap phase.

## In scope

- resolving B9 (new): a Bybit BTCUSDT first-vertical eligibility profile
  requiring non-null, unique `trade_id`, explicitly distinct from generic
  `trade-v1` record-schema validity (which stays nullable, unchanged);
- resolving the B2 refinement: semantic catalog-rebuild equality (ignoring
  generated PostgreSQL UUIDs) and the durable certification-evidence
  persistence model (`quality_reports`, no schema/DDL change);
- resolving the B7 refinement: a fully specified, reproducible
  `CanonicalContentHashV1` byte-exact algorithm (domain separation, field
  order, canonical framing, hash function, empty-content and scope
  definition), and an identity matrix distinguishing it from
  `PhysicalArtifactHash` and the unchanged `result_identity`;
- removing the gate-lifecycle circularity by splitting the single "gate PASS"
  concept into two named, non-circular gates: **Contract Freeze Gate**
  (documentation-level exit, authorizes conformity-slice implementation) and
  **Conformity Implementation Gate** (runtime-level exit, reopens broader
  vertical expansion);
- correcting the ordering-non-overlap proof to attribute cross-partition
  safety to DataGateway's existing fail-closed `CatalogConflict` check
  (enforcement), not to declared-coverage contiguity alone (which only
  guarantees intra-partition contiguity);
- correcting ADR-0023's incorrect exit-criteria section reference;
- adding ADR-0021 to the frozen-ADR test guard, which omitted it;
- revising `docs/product/ROADMAP.md`, `docs/product/CAPABILITY_MAP.md`,
  `docs/architecture/OPEN_DECISIONS.md` and this file to use the two-gate
  terminology consistently;
- revising the contract test suite so it visibly distinguishes
  contract-consistency checks (executable now) from required future
  behavioral/runtime tests (enumerated, not implemented).

## Out of scope

Unchanged from the prior cycle:

- a canonical `trade-v1` Parquet writer/producer;
- a DataGateway streaming/bounded-read runtime implementation;
- a manifest/coverage-to-catalog publication bridge implementation;
- a producer certifier implementation;
- a golden ingest run against real source data;
- a catalog migration or any DDL change to `db/init/001_catalog.sql`;
- any change to `trade-v1`, `dataset-manifest-v1`, `partition-manifest-v1`,
  `coverage-manifest-v1`, `candle-v1`, or any accepted ADR's decision text
  (ADR-0019, ADR-0020, ADR-0021, ADR-0022);
- candle aggregation runtime, a Parquet writer, a certifier, or a bridge;
- FastAPI, HTTP endpoints, live collector, cursor or reconnect semantics;
- React, TUI, CLI commands or other client runtime code.

## Exit criteria

- B9 has an explicit, frozen resolution distinct from generic `trade-v1`
  nullability, with no `trade-v1.json` change;
- `CanonicalContentHashV1` is specified precisely enough that two independent
  implementations would produce byte-identical digests for the same ordered
  canonical records;
- the physical/semantic/result identity matrix states behavior under
  compression/layout variation without any wording implying a contradiction;
- `ADR-0023` and the conformity contract consistently use **Contract Freeze
  Gate** / **Conformity Implementation Gate** and never state that one gate's
  exit criteria require its own runtime;
- the durable certification-evidence model names its persistence target
  (`quality_reports`) and its frozen record shape, without a schema/DDL
  change;
- the certification/publication sequencing is fail-closed and non-circular
  (evidence commits before eligibility);
- catalog rebuild equality is defined field-by-field, excluding generated
  UUIDs;
- the ordering non-overlap proof correctly attributes cross-partition safety
  to the existing `CatalogConflict` enforcement;
- ADR-0023's section references are internally correct (tested);
- the frozen-ADR guard covers ADR-0019, ADR-0020, ADR-0021 and ADR-0022;
- the test suite's module docstring and structure make the
  contract-consistency-vs-behavioral-test distinction explicit;
- `python tools/run_tests.py`, `python tools/check_markdown_links.py` and
  `git diff --check` all pass;
- `ADR-0023` remains `PROPOSED — pending independent review`;
- no producer or consumer runtime, shared tooling, catalog migration, schema
  change or golden ingest is implemented by this cycle.

## Next cycle

After a final independent re-review accepts `ADR-0023` (Contract Freeze Gate
PASS), separate implementation mandates may proceed against
`PRODUCER_CONSUMER_CONFORMITY.md` §23's dependency-ordered slices. Only after
those slices are implemented and pass their required tests — Conformity
Implementation Gate PASS — do producer and consumer vertical development
resume as independent parallel tracks, as `ROADMAP.md` already intends.
