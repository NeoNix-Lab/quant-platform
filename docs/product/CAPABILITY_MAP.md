# Capability Map

Canonical State describes this repository. Legacy Evidence is reference evidence only.

| Domain | Capability | Canonical State | Legacy Evidence | Target owner | Notes |
|---|---|---|---|---|---|
| Data | `trade-v1` | FROZEN | NONE | Data Plane | Frozen contract. |
| Data | Dataset/partition manifests | FROZEN | NONE | Data Plane | Fixtures and validators exist. |
| Data | PostgreSQL catalog, lineage, storage roots | READY | NONE | Data Plane | DDL and provisioning exist. |
| Data | Historical Bybit import | PARTIAL | NONE | Data Plane | Importer and tests exist. |
| Data | L1/L2/L3 contracts | MISSING | WEAK | Data Plane | Must follow ADR-0018. |
| Access | DataGateway | MISSING | PARTIAL | Application/Data | Contract v1 defined; implementation next. |
| Representation | CandleDefinition/runtime | MISSING | PARTIAL | Representations | ADR-0017 distinguishes PARTIAL/CLOSED. |
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
| Interfaces | API/App/TUI/CLI | MISSING | PARTIAL | API/Clients | Clients cannot own quant logic. |
| Operations | Provisioning, fixtures, semantic tests | READY | NONE | Engineering/Infrastructure | CI and backup certification missing. |

## Evidence vocabulary

`NONE`, `WEAK`, `PARTIAL`, `STRONG`, `MULTIPLE`, `DEFECTIVE`, `REVIEW`, `ADOPT_CANDIDATE`, `ADAPT_CANDIDATE`.

See [ROADMAP.md](ROADMAP.md) for dependency order and implementation phases.
