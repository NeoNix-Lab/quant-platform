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
| Data | Quality / Reconciliation | PARTIAL | NONE | Data Plane | Lifecycle and quality-report primitives exist. |
| Data | Declared Coverage Contract | FROZEN | NONE | Data Plane | ADR-0022; schema, fixtures and semantic tests exist. |
| Data | Canonical Partition Publication | PARTIAL | NONE | Data Plane | Manifests/catalog and coverage semantics exist; producer bridge is missing. |
| Gate | Producer–Consumer Conformity Gate — Contract Freeze Gate | PARTIAL | NONE | Data Plane/Application | ADR-0023 and `PRODUCER_CONSUMER_CONFORMITY.md` freeze the seam contract; not yet independently reviewed/accepted. Documentation-level only; passing it does not by itself unlock runtime. |
| Gate | Producer–Consumer Conformity Gate — Conformity Implementation Gate | MISSING | NONE | Data Plane/Application | Runtime-level gate; requires the conformity slices below to be implemented and pass their tests, including the golden Bybit BTCUSDT vertical. Cannot start before Contract Freeze Gate PASS. |
| Data | Bybit First-Vertical Eligibility Profile | MISSING | NONE | Data Plane | No certifier exists to enforce it yet. Profile (non-null, unique `trade_id`) is frozen (`PRODUCER_CONSUMER_CONFORMITY.md` §10), explicitly distinct from generic `trade-v1` nullability, which is unchanged. |
| Access | Bounded Historical DataGateway Read | MISSING | NONE | Application/Data | `DataGateway.read()` buffers the full requested row set and performs one global sort (O(total rows) memory); bounded `scan()` contract is frozen (`PRODUCER_CONSUMER_CONFORMITY.md` §5) but unimplemented. |
| Data | Canonical trade-v1 Parquet Materialization | MISSING | NONE | Data Plane | `tools/import_bybit_trades.py` writes canonical JSONL only; no Parquet writer exists. Physical contract is frozen (`PRODUCER_CONSUMER_CONFORMITY.md` §9). |
| Data | CanonicalContentHashV1 | MISSING | NONE | Data Plane/Application | No implementation exists. Byte-exact algorithm (domain separation, field order, framing, hash) is frozen (`PRODUCER_CONSUMER_CONFORMITY.md` §11). |
| Data | Canonical Publication Bridge | MISSING | NONE | Data Plane | No manifest+coverage-to-catalog bridge exists in `src/` or `tools/`. Mapping and semantic rebuild equality are frozen (`PRODUCER_CONSUMER_CONFORMITY.md` §14). |
| Data | Publication Certification | MISSING | NONE | Data Plane | No certifier exists; `state=valid` evidence rule, durable evidence model (`quality_reports`) and publication sequencing are frozen (`PRODUCER_CONSUMER_CONFORMITY.md` §13). |
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
