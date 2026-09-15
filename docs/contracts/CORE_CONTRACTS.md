# CORE_CONTRACTS.md

**Status:** DRAFT v0.1  
**Purpose:** semantic contracts for the canonical quantitative platform  
**Rule:** these contracts describe meaning before implementation details.

---

## 1. General contract rules

Every canonical domain entity must define, where applicable:

- identity;
- version;
- source/provenance;
- time semantics;
- deterministic parameters;
- validation invariants;
- serialization boundary;
- failure semantics.

No entity may silently infer critical semantics from a filename, column name, UI field, or runtime default.

---

## 2. DatasetIdentity

Represents one logically identifiable market dataset.

Required concepts:

- `dataset_id`
- `venue`
- `market_type`
- `instrument`
- `data_kind`
- `schema_version`
- `time_coverage`
- `provenance`
- `manifest_identity`

Invariants:

- identity must not depend on local mount path;
- logical dataset identity and physical storage location are separate;
- the same logical dataset may move across storage roots without becoming a different dataset.

---

## 3. PartitionIdentity

Represents one reproducible partition of a DatasetIdentity.

Required concepts:

- `dataset_id`
- `partition_id`
- `partition_key`
- `time_start`
- `time_end`
- `row_count`
- `content_hash`
- `schema_version`
- `storage_reference`

Invariants:

- content identity must be stable across physical relocation;
- partition boundaries must be explicit;
- overlapping partitions are either forbidden or explicitly modeled.

---

## 4. MarketEvent

Common semantic envelope for time-ordered market information.

Possible event families:

- Trade
- L1Quote
- L2Snapshot
- L2Update
- L3OrderEvent
- SessionEvent
- VenueStatusEvent

Required concepts:

- `event_time`
- `observation_time` when distinct
- `instrument`
- `venue`
- `sequence` when available
- source/provenance

Invariants:

- source timestamps must preserve their original precision;
- missing sequence/order identifiers must remain missing rather than fabricated.

---

## 5. Trade

Represents a canonical trade event.

Required concepts:

- event time;
- price;
- quantity;
- aggressor side when derivable/canonical;
- trade identifier when provided;
- sequence when provided;
- source provenance.

No synthetic receive timestamp is introduced unless it is genuinely observed.

---

## 6. L1Quote

Represents top-of-book state or update.

Required concepts:

- best bid price;
- best bid quantity;
- best ask price;
- best ask quantity;
- event time;
- update/sequence identity when available.

Derived values such as spread, mid, microprice and top-of-book imbalance are Features, not raw L1 fields unless explicitly supplied by the source.

---

## 7. L2Update / L2Snapshot

Represents aggregated book state by price level.

Required concepts:

- side;
- price level;
- aggregate quantity;
- event/update time;
- snapshot/update semantics;
- sequence/reconstruction metadata when available.

Invariants:

- snapshot and incremental update semantics are never conflated;
- reconstruction validity must be checkable;
- add/cancel/replenishment features are only claimed when the source semantics support them.

---

## 8. L3OrderEvent

Represents order-level book activity when provided by the venue/feed.

Possible actions:

- add;
- modify;
- cancel;
- execute;
- replace.

Required concepts depend on feed but may include:

- order identifier;
- side;
- price;
- quantity;
- queue-relevant ordering;
- event time;
- sequence.

Invariants:

- L3 features must not be reconstructed from L2 and labeled as native L3.

---

## 9. CandleDefinition

A reproducible definition of a candle representation.

Required concepts:

- `source_dataset`
- `interval`
- `alignment`
- `interval_semantics`
- `timestamp_label`
- `price_source`
- `volume_semantics`
- `empty_interval_policy`
- `late_event_policy`
- `partial_policy`

Recommended interval semantics:

```text
[start_time, end_time)
```

A CandleDefinition is part of identity.

---

## 10. Candle

Represents one interval produced by a CandleDefinition.

`PARTIAL` is a mutable incremental runtime representation and is not sealed.
`CLOSED` is immutable after closure. A reusable series of CLOSED candles may
be materialized as a canonical derived dataset with catalog, partition and
lineage semantics. Candle is a representation, not merely a Feature.

Required concepts:

- `start_time`
- `end_time`
- `state`: `PARTIAL` or `CLOSED`
- `open`
- `high`
- `low`
- `close`
- `volume`
- source/provenance

Optional canonical aggregates may include:

- trade count;
- VWAP.

Feature values derived at candle grain are not required to be embedded in the Candle object itself.

Temporal invariant:

- final values for a `CLOSED` candle cannot be treated as available before candle close plus any processing delay defined by the provider.

---

## 10.1 FootprintDefinition / Footprint

A footprint is a Representation-owned price-level aggregation of canonical
trades. It is not a FeatureDefinition and does not contain imbalance
thresholds, labels, strategy semantics or FeatureArtifact materialization.

`FootprintDefinition v1` is governed by
[ADR-0027](../decisions/ADR-0027-footprint-definition-v1.md).

Required definition concepts:

- source contract `trades@1` / `trade-v1`;
- fixed positive duration;
- UTC Unix-epoch alignment;
- half-open bucket support `[bucket_start,bucket_end)`;
- positive exact-decimal `tick_size`;
- zero-origin exact integer tick grid;
- sparse level policy;
- explicit aggression evidence requirement;
- finalized historical-only lifecycle for v1.

Required finalized Footprint concepts:

- definition identity;
- bucket support interval;
- integer `level_index`;
- canonical price `level_index * tick_size`;
- exact buy/sell volume sums by aggressor side;
- concrete source venue/instrument binding;
- authoritative source coverage/finality/provenance evidence.

Invariants:

- source price is valid only when `price / tick_size` is an exact integer;
- no rounding, snapping, epsilon tolerance or inferred side is allowed;
- `aggressor_side == "unknown"` fails closed for the affected bucket;
- missing grid levels remain absent and are never synthesized as zero rows;
- complete zero-trade bucket support produces no level rows and is distinct
  from missing source support;
- finalized Footprints require complete authoritative source support and are
  immutable for a fixed definition and source revision/content evidence.

---

## 11. FeatureDefinition

Canonical definition of a derived observable.

Required concepts:

- deterministic `feature_definition_id`
- governed canonical `feature_key`
- explicit `semantic_version`
- declared typed semantic-parameter schema
- canonical normalized semantic parameters
- exactly one versioned `InputContract`
- declarative support/reference semantics
- input maturity and availability/finality semantics
- initialization/history semantics when output-affecting
- minimal `OutputContract`

Provider families may include:

- Trades
- L1
- Footprint
- L2
- L3
- Technical
- Context
- Custom

Invariants:

- feature identity changes when semantic meaning, identity-bearing parameters,
  input contract, support, availability/finality, initialization/history or
  output-equivalence semantics change;
- feature identity does not depend on registry order, implementation
  build/SHA/backend, cache/materialization strategy, concrete dataset, venue,
  instrument, time range, physical locator, execution provenance or concrete
  representation grain unless that grain/duration is intrinsic feature
  semantics;
- temporal availability and finality are explicit and distinguish causal floors
  from nullable observed runtime evidence;
- undefined/insufficient support is a non-observation outcome, not a
  canonical null/NaN/zero sentinel value.

`FeatureDefinition` identifies one semantic observable, such as `delta@1` or
`vwap@1`, and is distinct from a bundle or materialization. Valid examples
include `trade -> candle`, `price_level -> candle`, `L1_update -> candle`,
`trade -> trade` and `candle -> candle`.

FeatureDefinition v1 is governed by
[ADR-0026](../decisions/ADR-0026-feature-definition-v1-semantic-foundation.md).

---

## 12. FeatureSetDefinition

Defines a versioned bundle containing one or more `FeatureDefinition`
identities and stable materialization parameters. The existing canonical
`feature_set_definitions` catalog is retained as this identity foundation.
Feature-set identity does not replace per-feature identity.

## 13. FeatureArtifact

Represents computed feature values.

Required concepts:

- `feature_definition_identity`
- `source_dataset_identity`
- source partition identities;
- computation window;
- output grain;
- code/implementation identity;
- materialization/cache mode;
- content identity where persisted.

Lifecycle:

- `EPHEMERAL`
- `CACHEABLE`
- `MATERIALIZED`

---

## 14. HypothesisSpec

Represents a research hypothesis composed from Features.

Examples of semantics:

- conjunction/disjunction of conditions;
- regime constraints;
- threshold/percentile conditions;
- temporal context;
- feature interactions.

Invariants:

- strategy-specific compound logic belongs here or in StrategySpec, not in primitive FeatureDefinition;
- hypothesis semantics must be declarative enough to fingerprint and reproduce.

---

## 15. EventSpec

Defines how a hypothesis becomes discrete research events.

Required concepts may include:

- trigger condition;
- deduplication/cooldown;
- event timestamp;
- direction/context;
- minimum data/warmup requirements.

---

## 16. Event

Represents one detected occurrence.

Required concepts:

- `event_id`
- `event_time`
- `event_spec_identity`
- `instrument`
- relevant feature snapshot/provenance
- direction/context where applicable

An Event is not automatically a Signal.

---

## 17. OutcomeSpec

Defines what future/path information should be measured after an observation or Event.

Examples:

- multi-horizon return;
- MFE/MAE;
- barrier hits;
- time-to-event;
- path drawdown;
- realized volatility;
- cost-aware simulated outcome.

Invariants:

- horizon and temporal alignment are explicit;
- future information is never fed back into features or decision inputs.

---

## 18. Outcome

Represents the realized result of an OutcomeSpec for an Event/observation.

Required concepts:

- source event/observation identity;
- outcome-spec identity;
- measurement window;
- realized values;
- censoring/incomplete-path state where applicable.

Outcome is descriptive, not automatically a training label.

---

## 19. LabelDefinition

Defines how Outcomes or policy simulations become learning targets.

Families:

- outcome-derived;
- policy-derived.

Required concepts:

- source outcome/policy semantics;
- transformation;
- class/value schema;
- timing;
- censoring policy;
- implementation identity.

Policy-derived labels must include the StrategySpec / ExecutionSpec assumptions used in their creation.

---

## 20. ValidationSpec

Defines the evaluation schedule.

Required concepts may include:

- warmup;
- train;
- validation;
- test;
- lockbox;
- rolling/expanding mode;
- purge;
- embargo;
- fold alignment;
- minimum sample/event requirements.

Different domains may use different procedures while sharing these temporal primitives.

---

## 21. StrategySpec

Defines a stateful trading strategy independent from market replay/live source.

Possible components:

- EntryPolicy
- ExitPolicy
- PositionPolicy
- SizingPolicy
- RiskPolicy
- SessionPolicy
- SignalCombinationPolicy
- ExecutionPolicy

A StrategySpec is not reducible to one event trigger.

---

## 22. DecisionIntent

Canonical learner-agnostic strategy output.

Required concepts:

- `decision_time`
- `instrument`
- desired exposure or target position;
- direction where applicable;
- size/risk budget;
- confidence where applicable;
- urgency where applicable;
- policy/strategy provenance.

Invariants:

- every input used to produce the DecisionIntent must have been available by `decision_time`;
- DecisionIntent contains desired trading intent, not venue-specific fill results.

---

## 23. ExecutionSpec

Defines how DecisionIntent is translated into orders/fills.

Possible concepts:

- order type policy;
- price policy;
- fee schedule;
- slippage model;
- latency model;
- partial-fill model;
- cancel/replace rules;
- participation constraints;
- venue-specific execution constraints.

Execution semantics must be shared between historical replay and live adapters as far as the real venue allows.

---

## 24. Order

Represents one executable order state.

Required concepts may include:

- order identity;
- instrument;
- side;
- type;
- quantity;
- limit/stop price where applicable;
- submission time;
- state;
- execution provenance.

---

## 25. Fill

Represents one realized fill.

Required concepts:

- order identity;
- fill time;
- price;
- quantity;
- fees;
- liquidity role when available;
- venue/source identity.

---

## 26. Position

Represents current instrument exposure.

Required concepts:

- quantity;
- average basis;
- realized/unrealized state;
- timestamps;
- strategy/run provenance.

---

## 27. Portfolio / Ledger

Canonical accounting state.

Responsibilities:

- cash/equity;
- positions;
- fills;
- realized/unrealized PnL;
- exposure;
- fees;
- reconciliation.

Ledger semantics must be deterministic under historical replay.

---

## 28. Study

A research program grouping related trials.

Example:

> BTCUSDT absorption-family study on trade/footprint features.

Study identity must not depend on run order.

---

## 29. Trial

One parameterized hypothesis/model/strategy evaluation within a Study.

Required concepts:

- complete parameterization;
- input identities;
- validation spec;
- result metrics;
- run/artifact references.

---

## 30. Run

One concrete execution of a reproducible Trial or operational job.

Required concepts:

- run identity;
- trial/study linkage where applicable;
- code identity;
- environment identity where applicable;
- status;
- timestamps;
- metrics;
- artifacts;
- failure metadata.

A single logical run must not acquire separate incompatible identities in different subsystems.

---

## 31. ArtifactIdentity

Represents persisted outputs such as:

- feature partitions;
- model files;
- prediction files;
- event tables;
- traces;
- backtest outputs;
- plots/reports.

Identity must include the semantic inputs needed to prove reproducibility.

---

## 32. Job

Represents long-running asynchronous work.

Required concepts:

- `job_id`
- operation type
- submitted time
- started/completed time
- status
- progress
- cancellation state
- result/artifact references
- failure/retry metadata

---

## 33. Shared temporal invariants

At minimum:

```text
feature_available_time <= decision_time
signal_available_time <= decision_time
decision_time <= order_submit_time
order_submit_time <= fill_time
```

For any derived value, the platform must be able to explain which source events were consumed and when the result became available.

---

## 34. PressurePolicyDefinition

Operational pressure policy is deterministic policy over explicit evidence, not
capacity observation. `PressurePolicyDefinition v1` is governed by
[ADR-0028](../decisions/ADR-0028-pressure-policy-v1.md).

Required concepts:

- immutable/versioned policy definition;
- deterministic content-derived policy identity;
- explicit UTC `as_of` evaluation instant;
- fresh K04 capacity evidence;
- optional caller-supplied write-rate evidence when time-to-full participates;
- `NORMAL`, `PRESSURE`, `CRITICAL` and `EXHAUSTED` states;
- explicit unavailable decisions for missing, stale, future-dated or malformed
  required evidence;
- deterministic decision evidence and identity;
- pressure restrictions as upper bounds only.

Invariants:

- K05 does not observe filesystems, collect telemetry history, mutate storage,
  schedule work or authorize deletion;
- the evaluator does not read an implicit host clock;
- `available_bytes` is consumed as reported by K04 and is not recomputed;
- zero write rate means unbounded time-to-full, not a finite sentinel;
- equality enters the more severe threshold state;
- `delete_authorized` is false for every successful K05 decision.

## 35. Contract evolution

Breaking semantic changes require:

- version increment;
- migration/adoption plan where persisted artifacts exist;
- ADR if the change affects architecture or domain meaning;
- updated semantic tests.

Silent reinterpretation of an existing version is forbidden.
