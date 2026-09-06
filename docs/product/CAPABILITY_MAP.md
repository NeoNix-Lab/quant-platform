# Capability Map

Canonical State describes this repository. Legacy Evidence is reference evidence only.

| Domain | Capability | Canonical State | Legacy Evidence | Target owner | Notes |
|---|---|---|---|---|---|
| Data | `trade-v1` | FROZEN | NONE | Data Plane | Frozen contract. |
| Data | Dataset/partition manifests | FROZEN | NONE | Data Plane | `dataset-manifest-v1` remains frozen; accepted ADR-0025 adds versioned `dataset-manifest-v2` for source-acquired canonical topology without reinterpreting v1. |
| Data | Source-Acquired Canonical Dataset Lineage v2 | READY | NONE | Data Plane | `source_acquired` canonical datasets retain transform in the manifest, have no synthetic parent and exactly zero catalog lineage rows; source provenance remains in CoverageManifest evidence. |
| Data | PostgreSQL catalog, lineage, storage roots | READY | NONE | Data Plane | DDL and provisioning exist. |
| Data | Catalog Schema Registry Bootstrap | READY | NONE | Engineering/Data Plane | Repository schema bytes remain authoritative; bootstrap inserts absent registrations, accepts identical registrations and refuses conflicts without update/upsert/delete. |
| Data | Historical Bybit import | PARTIAL | NONE | Data Plane | The reference importer delegates source semantics to the production historical adapter; it is not the publication runtime. |
| Data | Market Data Ingest Architecture | PARTIAL | NONE | Data Plane | Producer architecture documented; runtime remains unimplemented. |
| Data | Market Data Ingest Runtime | MISSING | NONE | Data Plane | No canonical collector/acquisition runtime. |
| Data | Source/Venue Adapter Contract | PARTIAL | NONE | Data Plane | Semantic contract is documented and a narrow production Bybit historical source adapter exists for the first vertical. |
| Data | Historical Acquisition | PARTIAL | NONE | Data Plane | Narrow Bybit historical source slice implemented and exercised by the Human Golden E2E. |
| Data | Backfill / Repair | MISSING | NONE | Data Plane | Reconciliation and repair runtime are open. |
| Data | Live Collection | MISSING | NONE | Data Plane | Architectural target only. |
| Data | Trades Acquisition | PARTIAL | NONE | Data Plane | Bybit trade evidence exists. |
| Data | L1 Acquisition | MISSING | WEAK | Data Plane | Requires versioned contract and source capability. |
| Data | L2 Acquisition | MISSING | WEAK | Data Plane | Snapshot/incremental semantics remain future work. |
| Data | L3/MBO Acquisition | MISSING | WEAK | Data Plane | No schema or runtime; ADR-0018 applies. |
| Data | Checkpoint / Recovery | MISSING | NONE | Data Plane/Operations | No general implementation. |
| Data | Quality / Reconciliation | PARTIAL | NONE | Data Plane | Lifecycle, authoritative S13 certification, durable quality-report evidence and S14 eligibility verification exist for the first vertical; broader reconciliation remains open. |
| Data | Declared Coverage Contract | FROZEN | NONE | Data Plane | ADR-0022; schema, fixtures and semantic tests exist. |
| Data | Manifest + Coverage Emission | READY | NONE | Data Plane | Durable DatasetManifest, PartitionManifest and explicit CoverageManifest emission are implemented with deterministic persistence, source-owned evidence and fail-closed identity/artifact checks. |
| Data | Canonical Partition Publication | READY | NONE | Data Plane | The Bybit BTCUSDT `2024-01-15` first vertical completed materialization, manifests/coverage, S13 pass, S14 valid, catalog publication and DataGateway exact Golden verification. Broader generalization remains open. |
| Gate | Producer–Consumer Conformity Gate — Contract Freeze Gate | READY | NONE | Data Plane/Application | PASSED: ADR-0023 and `PRODUCER_CONSUMER_CONFORMITY.md` are accepted; this documentation-level gate authorizes the conformity slices but does not certify runtime completion. |
| Gate | Producer–Consumer Conformity Gate — Conformity Implementation Gate | READY | NONE | Data Plane/Application | PASSED: all ADR-0023 Decision §7 exit criteria are satisfied; Human Golden E2E, A1–A9 Adversarial Acceptance and Candle Ordering Compatibility are PASS; Final Gate Review APPROVE with no blockers or important findings. |
| Gate | Golden Conformity Acceptance Support | READY | NONE | Application/Data | Shared Golden fixture, incremental OPEN-only `DataScan` observer, bounded telemetry and lifecycle/reporting support are implemented. This is support evidence only, not Golden E2E or Conformity Gate PASS. |
| Gate | Human Golden Bybit BTCUSDT E2E | READY | NONE | Data Plane/Application/Engineering | PASS for `2024-01-15`: 1,105,145 rows, exact side counts and time bounds, S13 pass, S14 valid, complete gap-free coverage, one partition and DataGateway `OPEN → READING → COMPLETED` in 17 bounded batches. |
| Data | Bybit First-Vertical Eligibility Profile | READY | NONE | Data Plane | Source-owned pre-check plus authoritative S13 certification are implemented for exactly `canonical/trades/bybit/BTCUSDT/trade-v1`; non-null, unique `trade_id` remains distinct from generic `trade-v1` nullability, and S14 consumes the resulting current certification evidence for publication eligibility. |
| Access | Bounded Historical DataGateway Read | READY | NONE | Application/Data | `DataGateway.scan()` provides lazy ordered batches with explicit open/reading/completed/aborted lifecycle and bounded-read tests; broader/live access remains open. |
| Data | Canonical trade-v1 Parquet Materialization | READY | NONE | Data Plane | `src/quant_platform/data/materializer.py` provides canonical Parquet materialization and was exercised successfully by the Human Golden E2E. |
| Data | CanonicalContentHashV1 | READY | NONE | Data Plane/Application | Implemented in `src/quant_platform/data/models.py` and covered by pinned-vector tests; it remains additive to the existing physical `result_identity`. `DataSliceMetadata.canonical_content_hash` remains an additive future extension and was non-blocking for the Gate. |
| Data | Canonical Publication Bridge | READY | NONE | Data Plane | S14 Publication Eligibility Bridge v1 is implemented: it selects current certification evidence, applies the frozen `valid`/`degraded` eligibility rules, establishes lineage idempotently and verifies post-write publication state fail-closed. |
| Data | Publication Certification | READY | NONE | Data Plane | Authoritative S13 runtime is implemented: Phase 1 SEAL registers durable `closed` catalog evidence, Phase 2 CERTIFY re-evaluates source/canonical/physical/manifests/coverage, and Phase 3 records durable `quality_reports` against the real partition UUID. PASS alone does not grant eligibility; S14 owns that transition. |
| Data | Multi-Venue Capability Model | MISSING | NONE | Data Plane | Capability dimensions documented; no venue matrix. |
| Storage | Storage Root Foundation | READY | NONE | Data Plane/Infrastructure | Hot, cold, and deep-cold roots are provisioned. |
| Storage | Storage Tiering | MISSING | NONE | Data Plane/Operations | No relocation runtime. |
| Storage | Partition Relocation | MISSING | NONE | Data Plane/Operations | Semantic relocation invariance passed as Gate evidence; the operational relocation runtime remains an unimplemented post-Gate capability. |
| Storage | Storage Capacity Monitoring | MISSING | NONE | Operations | No capacity monitor. |
| Storage | Storage Health Monitoring | MISSING | NONE | Operations | No health monitor. |
| Storage | Storage Pressure Handling | MISSING | NONE | Operations | No pressure policy/runtime. |
| Storage | RAW / Source Data Protection | MISSING | NONE | Data Plane/Operations | Protection requirements documented only. |
| Storage | Backup / Restore | MISSING | NONE | Operations | Backup directories do not constitute backup capability. |
| Storage | Backup Verification | MISSING | NONE | Operations | No restore verification. |
| Storage | Retention Policy | MISSING | NONE | Operations | No retention or deletion policy. |
| Data | L1/L2/L3 contracts | MISSING | WEAK | Data Plane | Must follow ADR-0018. |
| Access | DataGateway | PARTIAL | PARTIAL | Application/Data | Narrow catalog-backed v1 implementation is proven for the first vertical; broader live access remains open. |
| Application | Application Service Ownership & Architecture Enforcement (ASS-01) | READY | NONE | Application | `quant_platform.application` is the canonical in-process composition owner. PR #27 merged as `066e7cd577104fb2c8f657430402b79cd58ba9aa`; executable orchestration under `tools/` is governed, with finite exact-edge ASS-03 debt. No application use-case runtime is introduced. |
| Application | First canonical application-service vertical (ASS-02) | MISSING | NONE | Application | No `MarketDataService` or equivalent canonical use case yet. Semantic-selector resolution, result-envelope construction and stable application error translation remain unimplemented. |
| Application | Executable orchestration / configuration convergence (ASS-03) | MISSING | NONE | Application/Engineering | Existing pre-ASS-01 tool bypasses and configuration divergence remain explicit bounded debt; no migration/convergence runtime has been implemented. |
| Representation | CandleDefinition/runtime | MISSING | PARTIAL | Representations | CandleDefinition v1 contract is frozen and accepted by ADR-0021; runtime remains missing. |
| Features | FeatureDefinition | MISSING | STRONG | Feature Engine | Legacy registry is evidence only. |
| Features | FeatureSetDefinition catalog | PARTIAL | PARTIAL | Data/Feature Engine | Existing catalog foundation retained. |
| Features | FeatureArtifact/materialization | MISSING | PARTIAL | Feature Engine/Data Plane | No canonical materializer. |
| Research | Hypothesis/Event/Event Study | MISSING | STRONG | Research Engine | Historical candidate. |
| Outcomes/labels | Outcome and Label engines | MISSING | STRONG | Outcome/Label Engine | Censoring defects require redesign. |
| Validation | Availability/walk-forward/purge/embargo | MISSING | STRONG | Validation Engine | Needs canonical temporal tests. |
| Strategy | StrategySpec/DecisionIntent | MISSING | PARTIAL | Policy/Strategy | No canonical runtime. |
| Execution | Orders/fills/portfolio/replay | MISSING | STRONG | Execution/Portfolio | Strong foundation only, not READY. |
| ML | Supervised learning/evaluation | MISSING | STRONG | Learning/Supervised | Bind to canonical provenance. |
| RL | Strategic and execution RL | MISSING | MULTIPLE/REVIEW | Learning/RL | Legacy runtimes are mixed. |
| Experiments | Study/Trial/Run/Artifact | MISSING | MULTIPLE | Experiment System | Avoid competing persistence. |
| Interfaces | Consumer API semantic boundary | FROZEN | PARTIAL | Application/API | ADR-0020 and `CONSUMER_API.md` freeze the semantic client boundary. This is not evidence of API transport/runtime implementation. |
| Interfaces | Canonical API runtime + Job runtime | MISSING | PARTIAL | API/Application | Transport, wire representation and Job implementation remain roadmap work; draft Job concepts are not promoted to frozen authority. |
| Interfaces | Clients (App UI, TUI, CLI) | MISSING | PARTIAL | Clients | Clients must consume the canonical API and cannot own quantitative logic. No canonical product client is implemented. |
| Operations | Provisioning, fixtures, semantic tests | READY | NONE | Engineering/Infrastructure | CI and backup certification missing. |
| Operations | Server Access & Runtime Identity Hardening v1 | MISSING | NONE | Operations | Non-blocking follow-up: audit/restore canonical server access, service identities, filesystem ACLs, database roles and credential disposition. Operator/bootstrap E2E privilege is not the production authorization model. |

## Evidence vocabulary

`NONE`, `WEAK`, `PARTIAL`, `STRONG`, `MULTIPLE`, `DEFECTIVE`, `REVIEW`, `ADOPT_CANDIDATE`, `ADAPT_CANDIDATE`.

See [ROADMAP.md](ROADMAP.md) for dependency order and implementation phases.
