# Capability Map

Canonical State describes this repository. Legacy Evidence is reference evidence only.

| Domain | Capability | Canonical State | Legacy Evidence | Target owner | Notes |
|---|---|---|---|---|---|
| Data | `trade-v1` | FROZEN | NONE | Data Plane | Frozen contract. |
| Data | Dataset/partition manifests | FROZEN | NONE | Data Plane | Fixtures and validators exist. |
| Data | PostgreSQL catalog, lineage, storage roots | READY | NONE | Data Plane | DDL and provisioning exist. |
| Data | Historical Bybit import | PARTIAL | NONE | Data Plane | Importer and tests exist. |
| Data | Market Data Ingest Architecture | PARTIAL | NONE | Data Plane | Producer architecture documented; runtime remains unimplemented. |
| Data | Market Data Ingest Runtime | MISSING | NONE | Data Plane | No canonical collector/acquisition runtime. |
| Data | Source/Venue Adapter Contract | MISSING | NONE | Data Plane | Semantic contract documented; no adapter runtime. |
| Data | Historical Acquisition | PARTIAL | NONE | Data Plane | Narrow Bybit historical slice only. |
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
| Data | Canonical Partition Publication | PARTIAL | NONE | Data Plane | The automated first-vertical chain through materialization, manifests/coverage, S13 certification and S14 eligibility/verification is implemented; Human/Golden E2E evidence is still required before the full publication seam is treated as proven. |
| Gate | Producer–Consumer Conformity Gate — Contract Freeze Gate | READY | NONE | Data Plane/Application | PASSED: ADR-0023 and `PRODUCER_CONSUMER_CONFORMITY.md` are accepted; this documentation-level gate authorizes the conformity slices but does not certify runtime completion. |
| Gate | Producer–Consumer Conformity Gate — Conformity Implementation Gate | PARTIAL | NONE | Data Plane/Application | IN PROGRESS: Shared Semantic Primitives v1, Canonical Parquet Materializer v1, Manifest + Coverage Emission v1, Bounded DataGateway finite read v1, Golden Acceptance Support v1, Publication Certification v1 and Publication Eligibility Bridge v1 are on `main`; Human/Golden Bybit E2E and remaining acceptance are open. |
| Gate | Golden Conformity Acceptance Support | READY | NONE | Application/Data | Shared Golden fixture, incremental OPEN-only `DataScan` observer, bounded telemetry and lifecycle/reporting support are implemented. This is support evidence only, not Golden E2E or Conformity Gate PASS. |
| Data | Bybit First-Vertical Eligibility Profile | READY | NONE | Data Plane | Source-owned pre-check plus authoritative S13 certification are implemented for exactly `canonical/trades/bybit/BTCUSDT/trade-v1`; non-null, unique `trade_id` remains distinct from generic `trade-v1` nullability, and S14 consumes the resulting current certification evidence for publication eligibility. |
| Access | Bounded Historical DataGateway Read | READY | NONE | Application/Data | `DataGateway.scan()` provides lazy ordered batches with explicit open/reading/completed/aborted lifecycle and bounded-read tests; broader/live access remains open. |
| Data | Canonical trade-v1 Parquet Materialization | READY | NONE | Data Plane | `src/quant_platform/data/materializer.py` provides `ParquetWriter`-backed materialization and canonical round-trip tests; downstream automated publication now continues through manifests, S13 and S14, while Human/Golden E2E remains gate evidence. |
| Data | CanonicalContentHashV1 | READY | NONE | Data Plane/Application | Implemented in `src/quant_platform/data/models.py` and covered by pinned-vector tests; it remains additive to the existing physical `result_identity`. |
| Data | Canonical Publication Bridge | READY | NONE | Data Plane | S14 Publication Eligibility Bridge v1 is implemented: it selects current certification evidence, applies the frozen `valid`/`degraded` eligibility rules, establishes lineage idempotently and verifies post-write publication state fail-closed. |
| Data | Publication Certification | READY | NONE | Data Plane | Authoritative S13 runtime is implemented: Phase 1 SEAL registers durable `closed` catalog evidence, Phase 2 CERTIFY re-evaluates source/canonical/physical/manifests/coverage, and Phase 3 records durable `quality_reports` against the real partition UUID. PASS alone does not grant eligibility; S14 owns that transition. |
| Data | Multi-Venue Capability Model | MISSING | NONE | Data Plane | Capability dimensions documented; no venue matrix. |
| Storage | Storage Root Foundation | READY | NONE | Data Plane/Infrastructure | Hot, cold, and deep-cold roots are provisioned. |
| Storage | Storage Tiering | MISSING | NONE | Data Plane/Operations | No relocation runtime. |
| Storage | Partition Relocation | MISSING | NONE | Data Plane/Operations | Safety invariants documented only. |
| Storage | Storage Capacity Monitoring | MISSING | NONE | Operations | No capacity monitor. |
| Storage | Storage Health Monitoring | MISSING | NONE | Operations | No health monitor. |
| Storage | Storage Pressure Handling | MISSING | NONE | Operations | No pressure policy/runtime. |
| Storage | RAW / Source Data Protection | MISSING | NONE | Data Plane/Operations | Protection requirements documented only. |
| Storage | Backup / Restore | MISSING | NONE | Operations | Backup directories do not constitute backup capability. |
| Storage | Backup Verification | MISSING | NONE | Operations | No restore verification. |
| Storage | Retention Policy | MISSING | NONE | Operations | No retention or deletion policy. |
| Data | L1/L2/L3 contracts | MISSING | WEAK | Data Plane | Must follow ADR-0018. |
| Access | DataGateway | PARTIAL | PARTIAL | Application/Data | Narrow catalog-backed v1 implementation is present; broader live access remains open. |
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
| Interfaces | API/App/TUI/CLI | MISSING | PARTIAL | API/Clients | Consumer API Boundary v1 is frozen; API runtime and clients remain roadmap work. Clients cannot own quant logic. |
| Operations | Provisioning, fixtures, semantic tests | READY | NONE | Engineering/Infrastructure | CI and backup certification missing. |

## Evidence vocabulary

`NONE`, `WEAK`, `PARTIAL`, `STRONG`, `MULTIPLE`, `DEFECTIVE`, `REVIEW`, `ADOPT_CANDIDATE`, `ADAPT_CANDIDATE`.

See [ROADMAP.md](ROADMAP.md) for dependency order and implementation phases.
