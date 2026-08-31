# Scope: Producer–Consumer Conformity Implementation Gate v1

## Goal

Complete the executable conformity seam between the Data Plane producer output
and the DataGateway consumer input, using the accepted
`PRODUCER_CONSUMER_CONFORMITY.md` contract as the semantic authority. The
Contract Freeze Gate has passed. This cycle is the controlled convergence
period before the Conformity Implementation Gate; it is not a redesign of the
product roadmap or architecture.

## Current state and roadmap position

The repository is in **Phase A — Controlled Bidirectional Convergence**:

- Contract Freeze Gate: **PASSED**;
- Conformity Implementation Gate: **IN PROGRESS / PARTIAL**;
- Producer and Consumer may progress bidirectionally and concurrently only on
  work that directly advances the shared conformity seam and its gate evidence.

Completed conformity slices on authoritative `origin/main`:

1. Shared Semantic Primitives v1 — **DONE** (`RecordTimeBounds`,
   `CanonicalContentHashV1`, and provider-supplied ordering compatibility);
2. Canonical Parquet Materializer v1 — **DONE**;
3. Bounded DataGateway finite read v1 — **DONE**.

Remaining critical path to the Conformity Implementation Gate review:

4. Manifest + Coverage emission;
5. Publication Certifier;
6. Manifest/Coverage/Certification → Catalog Publication Bridge;
7. Human vertical / Golden Bybit E2E;
8. Adversarial acceptance;
9. Candle ordering compatibility acceptance;
10. Conformity Implementation Gate review.

The post-gate model is **Phase B — Full Bidirectional Expansion**. After the
Conformity Implementation Gate passes, the temporary Producer–Consumer
lockstep is removed and Producer and Consumer may resume independent,
concurrent development subject to normal contracts, ownership, dependency
direction, architecture gates and explicit scopes. This does not remove any
architecture constraint.

## In scope

- manifest and declared-coverage emission for the canonical Parquet artifact;
- the first-vertical publication certifier and durable evidence flow;
- the manifest/coverage/certification-to-catalog publication bridge;
- the human vertical and Golden Bybit BTCUSDT E2E proof;
- required adversarial acceptance, including zero-event coverage, gaps,
  supersession, overlap, relocation, rebuild, physical-layout variation and
  abort-before-completion;
- CandleDefinition ordering-compatibility acceptance;
- the evidence, tests and review needed for Conformity Implementation Gate
  PASS;
- documentation that records current status without changing frozen semantics.

The implementation dependency order and normative requirements are frozen in
`docs/contracts/PRODUCER_CONSUMER_CONFORMITY.md` §23. Shared/core remains
source-neutral; source-specific components depend toward generic/core, never
the inverse. Quantitative business logic remains outside App UI, TUI and CLI
clients, and upper layers do not bypass the DataGateway boundary.

## Controlled convergence rule

Until the Conformity Implementation Gate passes, broader vertical expansion is
blocked. Producer and Consumer work is allowed only when it directly advances
one of the conformity slices or its gate evidence. The completed slices do not
constitute Conformity Implementation Gate PASS by themselves.

## Explicitly blocked until Conformity Implementation Gate PASS

- full Candle runtime;
- Feature runtime and broader Representation expansion;
- broader producer verticals;
- live ingest and live collection;
- multi-venue runtime;
- L1, L2, L3/MBO runtime;
- unrelated consumer or producer vertical expansion;
- broader paper/live operational mechanics.

Maintenance, documentation and tests may proceed when they do not weaken a
frozen contract or bypass the current scope boundary.

## Package topology boundary

Current Conformity scope: broad package restructuring is **OUT OF SCOPE**.
The current single-repository, single-`src/` topology remains valid while the
Golden vertical and gate evidence are completed.

Immediately after Conformity Implementation Gate PASS, the next structural
scope is **Package Boundary / Modular Monolith Foundation v1**, before broad
independent Producer/Consumer expansion. This is a temporary cross-cutting
checkpoint, not a new product phase. Exact package names, hierarchy,
deployment split and migration mechanics remain open until that audit.

## Preserved future roadmap

The following work remains planned and is not deleted or made permanently
sequential by this scope.

### Producer roadmap after the conformity gate

- capacity monitoring;
- storage health and pressure behavior;
- RAW/source protection;
- safe storage placement and relocation;
- backup and restore;
- backup verification;
- retention/deletion authority;
- backfill and repair;
- live trades pilot;
- multi-venue trades;
- L1;
- L2;
- L3/MBO;
- advanced recovery and reconciliation.

### Consumer/Product roadmap after the conformity gate

- Candle runtime / Representation foundation;
- `FeatureDefinition` / `FeatureSetDefinition` / `FeatureArtifact`;
- Research / Hypothesis / Event / Outcome;
- Validation, labeling, warmup, walk-forward, purge, embargo, lockbox and
  censoring;
- Strategy / `DecisionIntent` / risk / sizing;
- Execution / replay / portfolio;
- Unified Experiment Orchestration and Persistence;
- Canonical API and job runtime;
- Supervised ML;
- Strategic RL;
- Execution RL;
- Clients: CLI / TUI / App UI;
- Paper / shadow;
- Live operation.

## Out of scope for this documentation synchronization

- changing `trade-v1`, dataset/partition/coverage contracts, CandleDefinition,
  DataGateway, Consumer API or any accepted ADR semantics;
- weakening or replacing frozen invariants;
- modifying runtime code, tests, schemas, DDL or fixtures as part of this
  documentation pass;
- implementing any remaining runtime slice while synchronizing these files;
- creating a parallel architecture or a generic framework;
- commit, push or branch creation/switching.

## Completion criteria

- this file describes the active Conformity Implementation scope and current
  gate state;
- the three completed slices and the remaining critical path are explicit;
- pre-gate controlled convergence and post-gate independent concurrency are
  explicit;
- blocked broader work and preserved future Producer/Consumer roadmap items
  are explicit;
- the allowed documentation set is factually aligned with `origin/main`;
- `python tools/check_markdown_links.py` and `git diff --check` pass;
- no runtime code, tests, schemas, DDL, fixtures or frozen normative semantics
  are changed by this synchronization.
