# Architecture Decision Records

Accepted ADRs are historical records. New decisions supersede old ones; accepted ADRs are not rewritten to hide changed meaning.

## Amendment policy

An accepted ADR may receive a dated, append-only **Amendment** section recording
a narrower, later decision that does not contradict the original (e.g. ADR-0037
Amendment 1, ADR-0050 Amendment 1). The original Decision/Context/Consequences
prose is not rewritten to a new meaning by an amendment.

The one narrow exception: where an amendment changes a fact the original prose
states as a plain count or claim (for example, "the six frozen `ConsumerErrorCode`
values" after Amendment 1 added a seventh), the original text may be corrected
in place with an inline forward-pointer to the amendment that changed it
("six originally, seven as of Amendment 1"). This is a factual-consistency fix,
not a reinterpretation of what was decided, and must not be used to change the
substance of an original decision. A change in substance is a supersession
(a new ADR number) or a new design gate, never an in-place rewrite.

| ADR | Title | Status |
|---|---|---|
| 0001–0015 | Bootstrap architecture decisions | Accepted |
| 0016 | FeatureDefinition and FeatureSetDefinition are distinct identities | Accepted |
| 0017 | Candle runtime and materialization model | Accepted |
| 0018 | Frozen market-data contract evolution policy | Accepted |
| 0019 | DataGateway logical boundary | Accepted |
| 0020 | Consumer API semantic boundary | Accepted |
| 0021 | CandleDefinition v1 semantic contract | Accepted |
| 0022 | Declared coverage is a separate durable contract | Accepted |
| 0023 | Producer–Consumer Conformity Gate v1 | Accepted |
| 0024 | Package Boundary / Modular Monolith Foundation v1 | Accepted |
| 0025 | Source-Acquired Canonical Dataset Lineage v2 | Accepted |
| 0026 | FeatureDefinition v1 semantic foundation | Accepted |
| 0027 | FootprintDefinition v1 representation foundation | Accepted |
| 0028 | PressurePolicyDefinition v1 | Accepted |
| 0029 | Non-contiguous coverage reads v1 | Accepted |
| 0030 | General Quality Lifecycle v1 | Accepted |
| 0031 | Availability/purge/embargo v1 | Accepted |
| 0032 | RAW / source protection v1 | Accepted |
| 0033 | Backfill / repair v1 | Accepted |
| 0034 | FeatureArtifact v1 | Accepted |
| 0035 | H01 canonical integration v1 | Accepted |
| 0036 | Outcome-derived labels, censoring and lockbox v1 | Accepted |
| 0037 | DSR/PBO robust-comparison semantics v1 | Accepted |
| 0038 | F03 Outcome v1 semantic authority | Accepted |
| 0039 | Backup / restore v1 | Accepted |
| 0040 | Bybit live trades acquisition v1 | Accepted |
| 0041 | Live-ingest runtime identity v1 | Accepted |
| 0042 | Live-ingest checkpoint / recovery v1 | Accepted |
| 0043 | Live-ingest server v1 composition | Accepted |
| 0044 | Live-ingest long-gap remediation and explicit-gap state v1 | Accepted |
| 0045 | Session calendar and cooldown semantics v1 | Accepted |
| 0046 | Execution conflict and intra-bar fill model v1 | Accepted |
| 0047 | Live DataGateway cursor semantics v1 | Accepted |
| 0048 | Storage tier relocation v1 | Accepted |
| 0049 | Retention/deletion authority v1 | Accepted |
| 0050 | API transport and client boundary v1 | Accepted |
| 0051 | StrategySpec rule extension via execution policy v1 | Accepted |
| 0052 | StrategySpec two-sided exposure via spec pairs v1 | Accepted |
| 0053 | `translate_intent` caller-gated repeated entries v1 | Accepted |
| 0054 | A07 historical acquisition day-boundary v1 | Accepted |
| 0055 | Omega validation bridge phase 1 v1 | Accepted |
| 0056 | Golden proof and test-double boundary v1 | Accepted |
| 0057 | Server/deck runtime topology and artifact handoff v1 | Accepted |
| 0058 | Replay sweep orchestration boundary v1 | Accepted |
| 0059 | D05 materialized representation and replay input v1 | Accepted |
| 0060 | Canonical replay I/O materialization profile v1 | Accepted |
| 0061 | Replay summary output mode v1 | Accepted |
| 0062 | Durable job runtime v1 | Accepted |

See the individual ADR files for context and consequences.
