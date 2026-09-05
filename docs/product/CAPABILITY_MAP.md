# Capability Map

Canonical State describes this repository/closeout branch. Legacy Evidence is reference evidence only.

| Domain | Capability | Canonical State | Legacy Evidence | Target owner | Notes |
|---|---|---|---|---|---|
| Data | `trade-v1` | FROZEN | NONE | Data Plane | Frozen contract. |
| Data | Dataset/partition manifests | FROZEN | NONE | Data Plane | `dataset-manifest-v1` remains frozen; ADR-0025 adds explicitly versioned `dataset-manifest-v2` for source-acquired canonical topology without reinterpreting v1. |
| Data | Source-Acquired Canonical Dataset Lineage v2 | READY | NONE | Data Plane | ADR-0025 accepted on the Human E2E branch; `source_acquired` canonical datasets retain transform in the durable manifest, have no synthetic parent and exactly zero catalog lineage rows. |
| Data | PostgreSQL catalog, lineage, storage roots | READY | NONE | Data Plane | DDL and provisioning exist; S14 verifies exact lineage including the zero-lineage `source_acquired` v2 case. |
| Data | Catalog Schema Registry Bootstrap | READY | NONE | Engineering/Data Plane | Repository schema bytes are the authority; bootstrap is create/verify/no-op/fail-closed and does not overwrite conflicting registrations. |
| Data | Historical Bybit import | PARTIAL | NONE | Data Plane | Reference importer now delegates source semantics to the production historical adapter; it is not the publication runtime. |
| Data | Market Data Ingest Architecture | PARTIAL | NONE | Data Plane | Producer architecture documented; broad collector/acquisition runtime remains unimplemented. |
| Data | Market Data Ingest Runtime | MISSING | NONE | Data Plane | No general canonical collector/acquisition runtime. |
| Data | Source/Venue Adapter Contract | PARTIAL | NONE | Data Plane | Contract documented; narrow production Bybit historical source adapter now exists for the first vertical. |
| Data | Historical Acquisition | PARTIAL | NONE | Data Plane | Narrow Bybit historical source slice implemented and exercised by Human E2E. |
| Data | Backfill / Repair | MISSING | NONE | Data Plane | Reconciliation and repair runtime are open. |
| Data | Live Collection | MISSING | NONE | Data Plane | Architectural target only. |
| Data | Trades Acquisition | PARTIAL | NONE | Data Plane | Bybit historical trade acquisition and source evidence exist for the first vertical. |
| Data | L1 Acquisition | MISSING | WEAK | Data Plane | Requires versioned contract and source capability. |
| Data | L2 Acquisition | MISSING | WEAK | Data Plane | Snapshot/incremental semantics remain future work. |
| Data | L3/MBO Acquisition | MISSING | WEAK | Data Plane | No schema or runtime; ADR-0018 applies. |
| Data | Checkpoint / Recovery | MISSING | NONE | Data Plane/Operations | No general implementation. |
| Data | Quality / Reconciliation | PARTIAL | NONE | Data Plane | Lifecycle, authoritative S13 certification, durable quality-report evidence and S14 eligibility verification exist for the first vertical; broader reconciliation remains open. |
| Data | Declared Coverage Contract | FROZEN | NONE | Data Plane | ADR-0022; schema, fixtures and semantic tests exist. |
| Data | Manifest + Coverage Emission | READY | NONE | Data Plane | Durable DatasetManifest, PartitionManifest and CoverageManifest emission are implemented with deterministic persistence, source-owned evidence and fail-closed identity/artifact checks. |
| Data | Canonical Partition Publication | READY (first vertical) | NONE | Data Plane | The real Bybit BTCUSDT `2024-01-15` chain completed materialization → manifests/coverage → S13 pass → S14 valid → catalog → DataGateway exact Golden match. Broader publication generalization remains out of scope. |
| Gate | Producer–Consumer Conformity Gate — Contract Freeze Gate | READY | NONE | Data Plane/Application | PASSED: ADR-0023 and `PRODUCER_CONSUMER_CONFORMITY.md` are accepted. |
| Gate | Producer–Consumer Conformity Gate — Conformity Implementation Gate | PARTIAL | NONE | Data Plane/Application | IN PROGRESS: Human/Golden Bybit E2E is now PASS. Adversarial acceptance, Candle ordering compatibility and final Gate review remain open. |
| Gate | Golden Conformity Acceptance Support | READY | NONE | Application/Data | Shared Golden fixture, incremental `DataScan` observer, bounded telemetry hooks and lifecycle/reporting support are implemented. |
| Gate | Human Golden Bybit BTCUSDT E2E | READY | NONE | Data Plane/Application/Engineering | PASS on `2024-01-15`: 1,105,145 rows; 553,875 buy; 551,270 sell; exact first/last bounds; S13 pass; S14 valid; one partition; coverage complete; DataGateway OPEN→READING→COMPLETED in 17 batches with max batch 65,536; exact Golden match. |
| Data | Bybit First-Vertical Eligibility Profile | READY | NONE | Data Plane | Source-owned pre-check plus authoritative S13 certification are implemented for exactly `canonical/trades/bybit/BTCUSDT/trade-v1`; S14 consumes current certification evidence. |
| Access | Bounded Historical DataGateway Read | READY | NONE | Application/Data | `DataGateway.scan()` provides lazy ordered batches with explicit open/reading/completed/aborted lifecycle and bounded-read tests; Human E2E observed 17 batches with max batch 65,536. |
| Data | Canonical trade-v1 Parquet Materialization | READY | NONE | Data Plane | Canonical Parquet materialization is implemented and exercised successfully by the real Human E2E. |
| Data | CanonicalContentHashV1 | READY | NONE | Data Plane/Application | Implemented with pinned vectors; Human E2E captured canonical payload `e0a2bc287aef3b95f07d584b5203e3ddd1f8b9807347e609f69618525f41eedf`. |
| Data | Canonical Publication Bridge | READY | NONE | Data Plane | S14 Publication Eligibility Bridge selects current certification evidence, applies the frozen first-vertical state rules and verifies post-write evidence/lineage fail-closed. |
| Data | Publication Certification | READY | NONE | Data Plane | S13 Phase 1 SEAL, Phase 2 CERTIFY and Phase 3 RECORD EVIDENCE are implemented; Human E2E certification result was `pass`. |
| Data | Multi-Venue Capability Model | MISSING | NONE | Data Plane | Capability dimensions documented; no venue matrix. |
| Storage | Storage Root Foundation | READY | NONE | Data Plane/Infrastructure | Hot, cold and deep-cold roots are provisioned. |
| Storage | Storage Tiering | MISSING | NONE | Data Plane/Operations | No relocation runtime. |
| Storage | Partition Relocation | MISSING | NONE | Data Plane/Operations | Safety invariants documented only. |
| Storage | Storage Capacity Monitoring | MISSING | NONE | Operations | No capacity monitor. |
| Storage | Storage Health Monitoring | MISSING | NONE | Operations | No health monitor. |
| Storage | Storage Pressure Handling | MISSING | NONE | Operations | No pressure policy/runtime. |
| Storage | RAW / Source Data Protection | MISSING | NONE | Data Plane/Operations | Protection requirements documented only; general legacy-source preservation remains a separate future capability. |
| Storage | Backup / Restore | MISSING | NONE | Operations | Backup directories do not constitute backup capability. |
| Storage | Backup Verification | MISSING | NONE | Operations | No restore verification. |
| Storage | Retention Policy | MISSING | NONE | Operations | No retention or deletion policy. |
| Data | L1/L2/L3 contracts | MISSING | WEAK | Data Plane | Must follow ADR-0018. |
| Access | DataGateway | PARTIAL | PARTIAL | Application/Data | Narrow catalog-backed v1 implementation is proven for the first vertical; broader/live access remains open. |
| Representation | CandleDefinition/runtime | MISSING | PARTIAL | Representations | CandleDefinition v1 contract is frozen; runtime remains blocked until complete Conformity Gate PASS. |
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
| Interfaces | API/App/TUI/CLI | MISSING | PARTIAL | API/Clients | Consumer API Boundary v1 is frozen; API runtime and clients remain roadmap work. |
| Operations | Provisioning, fixtures, semantic tests | READY | NONE | Engineering/Infrastructure | Schema-registry bootstrap is implemented; CI/release closeout for the Human branch remains mandatory before merge. |

## Evidence vocabulary

`NONE`, `WEAK`, `PARTIAL`, `STRONG`, `MULTIPLE`, `DEFECTIVE`, `REVIEW`, `ADOPT_CANDIDATE`, `ADAPT_CANDIDATE`.

See [ROADMAP.md](ROADMAP.md) for dependency order and implementation phases.
