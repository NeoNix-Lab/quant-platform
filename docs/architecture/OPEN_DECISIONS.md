# Open Decisions

This document records only decisions that are still live or intentionally deferred. Accepted ADRs/contracts remain semantic authority; [`../product/CAPABILITY_DAG.md`](../product/CAPABILITY_DAG.md) records which atoms each decision blocks and when a branch of a gate family is activated.

## Resolved foundation — reference only

The following are no longer open and must not be re-litigated without contradictory current authority:

```text
Contract Freeze Gate                                  PASSED
Conformity Implementation Gate                        PASSED
Human / Golden Bybit BTCUSDT E2E                     PASS
Package Boundary / Modular Monolith Foundation v1   COMPLETE
Legacy Capability Harvest Audit v1                   COMPLETE
ASS-01 Application ownership/enforcement             COMPLETE
ASS-02 in-process Application service                COMPLETE
DG-D Application configuration semantics             RESOLVED
DG-A FeatureDefinition v1 semantics                  FROZEN / COMPLETE
DG-A FootprintDefinition v1 semantics                FROZEN / COMPLETE
DG-A FeatureArtifact v1 (E04)                        FROZEN / COMPLETE
DG-A H01 canonical integration v1 (E06)              FROZEN / COMPLETE
DG-B B04 non-contiguous coverage reads v1            FROZEN / COMPLETE
DG-B A10 backfill / repair v1                        FROZEN / COMPLETE
DG-B A11 Bybit live acquisition semantics v1         FROZEN / COMPLETE
F03 Outcome v1 semantic authority                    FROZEN / COMPLETE
DG-E F07 labels/censoring/lockbox v1                 FROZEN / COMPLETE
DG-E F08 DSR/PBO robust comparison v1                FROZEN / COMPLETE
DG-H K02 live-ingest runtime identity v1             FROZEN / COMPLETE
DG-H K06 RAW / source protection v1                  FROZEN / COMPLETE
DG-H K08 backup / restore v1                         FROZEN / COMPLETE
DG-H K10 checkpoint / recovery v1                    FROZEN / COMPLETE
```

The completed two-stage Producer–Consumer Conformity Gate remains governed by [ADR-0023](../decisions/ADR-0023-producer-consumer-conformity-gate-v1.md). Its gate states above are historical accepted foundation, not live decisions.

Resolved architecture includes:

- canonical Data Access owner `quant_platform.access`;
- shared canonical primitives/Producer under `quant_platform.data`;
- deliberate shared physical seam `quant_platform.data.parquet`;
- source-specific ownership under `quant_platform.source_adapters`;
- application composition owner/package `application` / `quant_platform.application`;
- current runtime owner DAG and executable-orchestration enforcement;
- API-first direction: clients/API transport -> application services -> domain/DataGateway;
- Consumer API semantic selector/result/error boundary;
- completed ASS-02 in-process path: C02 semantic selector resolution + C03 result/error translation;
- `trade-v1`, Declared Coverage, CandleDefinition v1, FeatureDefinition v1, FootprintDefinition v1, B04 non-contiguous coverage reads v1, first-vertical conformity/publication semantics and source-acquired lineage v2;
- canonical F03 Outcome v1 semantics, including explicit end-of-data evidence, under [ADR-0038](../decisions/ADR-0038-outcome-v1-semantic-authority.md);
- K08 backup/restore semantics under [ADR-0039](../decisions/ADR-0039-backup-restore-v1.md);
- Bybit BTCUSDT live-trade acquisition/cutover/dedup/reconnect semantics under [ADR-0040](../decisions/ADR-0040-bybit-live-trades-v1.md);
- live-ingest runtime identity/least-privilege semantics under [ADR-0041](../decisions/ADR-0041-live-ingest-runtime-identity-v1.md);
- live-ingest checkpoint/recovery invariants under [ADR-0042](../decisions/ADR-0042-live-ingest-checkpoint-recovery-v1.md).

Legacy repositories remain evidence/reference only and are never runtime dependencies.

### Resolved DG-D / C05 configuration semantics

The Application configuration decision is frozen as:

```text
CLI / environment
      ↓
executable boundary: acquire + resolve input only
      ↓
typed immutable capability-specific Application config
      ↓
quant_platform.application: concrete composition owner
      ↓
Catalog / DataGateway / source / producer capabilities
```

Rules:

- executable tooling owns CLI/environment acquisition and parsing;
- precedence is **explicit CLI > environment > declared default > explicit failure**;
- Application receives resolved values only and does not know their acquisition source;
- Application-facing config is typed, immutable and capability-specific;
- `quant_platform.application` owns concrete composition and does not read process arguments/environment directly;
- no generic DI container, service locator, provider registry or plugin/config framework is introduced.

This resolves the C05 decision state. Both C05 and C04/ASS-03 implementation are complete (C04 via issue #40/PR #44, 2026-09-14).

## Decision-gate policy

An `OPEN_BLOCKING` decision is not automatically a current project. It becomes active only when a selected dependent atom reaches that proposition on its transitive dependency path.

A gate-family name groups related decisions for navigation; it is **not** a requirement to resolve every sibling proposition in the family.

An `OPEN_DEFERABLE` decision remains deliberately unresolved until its stated real-world evidence trigger exists. No decision is frozen merely to improve a completeness percentage.

## DG-A — Representation / Feature integration

This family has independent branches.

### Candle materialization (`D05`)

Activate only when persisted Candle results are selected. Resolve how a persisted Candle binds CandleDefinition/Representation identity, source dataset/partition evidence, temporal support and implementation identity.

`D05` is not a prerequisite of canonical H01 integration.

### FeatureArtifact (`E04`) — RESOLVED

Artifact identity/provenance and equivalence of cached vs recomputed output
are frozen and complete under [ADR-0034](../decisions/ADR-0034-feature-artifact-v1.md).
`FeatureArtifactIdentity`, portable `FeatureSetDefinitionIdentity`,
`SupportShape` non-contiguous support, FINAL-only sealing and the
attributable-evidence caller-discipline obligations on E06/F02/I04 are the
accepted authority; do not re-litigate them without contradictory current
authority.

### Footprint + canonical H01 (`D06`,`E06`) — RESOLVED

The canonical H01 path is:

```text
D06 footprint representation (FROZEN / COMPLETE by ADR-0027)
E02 FeatureDefinition (FROZEN / COMPLETE by ADR-0026)
E04 FeatureArtifact (FROZEN / COMPLETE by ADR-0034)
E05 pure H01 kernel (RESOLVED / COMPLETE)
E06 canonical H01 integration (FROZEN / COMPLETE by ADR-0035; issue #83/PR #88)
```

[ADR-0035](../decisions/ADR-0035-h01-canonical-integration-v1.md) freezes the first concrete H01 Feature vertical:

- `quant_platform.application` owns D06/Feature cross-owner composition while H01 meaning remains Feature-owned;
- Diagonal Imbalance and Stacked Imbalance are independent `FeatureDefinition`s, both consuming the canonical FINAL D06 Footprint directly;
- both use `footprint.price_level@1`, `current()` support and `FINAL_ONLY` maturity;
- Diagonal owns `imbalance_ratio`; Stacked owns `imbalance_ratio` plus `stacked_min_levels`;
- one eligible FINAL Footprint bucket yields one structured per-level observation for each constituent feature;
- covered-empty buckets yield FINAL empty observations; missing support remains non-observation;
- canonical Diagonal and Stacked RECORD outputs are fixed, including `NaN -> null` normalization for absent-neighbor diagonal ratios;
- deterministic D06 exact-decimal -> E05 float calculation semantics are accepted for H01 v1 without weakening D06 evidence authority;
- E06 derives the true expected H01 observation universe required by ADR-0034;
- E04 binds exact immutable D06 result/binding evidence rather than a duplicated trade-lineage model;
- one `h01_imbalance@1` FeatureSetDefinition contains the two independent observables.

E06 therefore has no remaining semantic decision blocker. Its implementation is `COMPLETE` (issue #83/PR #88).

Do not activate Candle materialization merely because it is in DG-A. Do not create a generic provider/plugin framework. FeatureDefinition v1, FootprintDefinition v1, FeatureArtifact v1 and H01 canonical integration semantics are accepted authority under ADR-0026, ADR-0027, ADR-0034 and ADR-0035.

## DG-B — Historical / Live data convergence

This family also has separate repair and live branches.

### Repair branch (`A16`,`A10`,`B04` resolved) — RESOLVED for the accepted first vertical

B04 explicit non-contiguous coverage reads are frozen and complete under
[ADR-0029](../decisions/ADR-0029-non-contiguous-coverage-reads-v1.md). B04
reports requested support, eligible support, exact gaps and returned rows; it
does not decide repair triggering, retry, replacement revision or live cursor
policy.

A10 backfill/repair -- repair triggering (coverage-gap and invalid-revision
triggers), isolated candidate attempts, and one atomic compare-and-cutover
transaction reusing the A16/S14 seams -- is frozen and complete under
[ADR-0033](../decisions/ADR-0033-backfill-repair-v1.md). **Accepted known
limitations** (see ADR-0033 Consequences): coverage-trigger
*re-verification* is architecturally unsatisfiable inside A10's own package
boundary (attributable-evidence principle, same as ADR-0032/ADR-0034); candidate
identity binding for `coverage_start`/`coverage_end` and full provenance
persistence remain partial, deferred to a future pass.

Historical repair does not activate live-cursor semantics by default. Any
repair proposition beyond the accepted A10/A16/B04 foundation -- general
quality-report -> lifecycle mapping beyond the accepted first vertical,
duplicate resolution beyond what A10 already resolves -- remains live and
should only be activated by the selected repair slice actually needing it.

### Live acquisition branch (`A11`) — RESOLVED / IMPLEMENTED

A11 semantics for the selected first vertical are frozen under [ADR-0040](../decisions/ADR-0040-bybit-live-trades-v1.md).

Accepted A11 v1 decisions include:

- Bybit public linear `publicTrade.BTCUSDT` is the selected source;
- canonical mapping preserves provider trade time/ID/price/size/taker-side/cross-sequence while `receive_ts` remains null in v1 rather than inventing semantics;
- `TradeKeyV1 = (venue, instrument, exchange_ts, trade_id)` and canonical total order remains `(exchange_ts, trade_id)`;
- provider `seq` is source evidence, not a gap-free resume cursor;
- same key + equivalent payload is idempotent; same key + conflicting payload fails closed;
- historical authority owns keys through the explicit historical cutover key and live authority owns keys after it;
- disconnect/reconnect uses only bounded provider evidence capable of proving continuity; inability to recover the last durable key produces explicit non-complete coverage, never fabricated completeness;
- transport is at-least-once while the canonical economic effect is idempotent.

A11 implementation is `COMPLETE`: PR #112 implements the source/application/certification path and real-provider smoke proof; PR #113 proves the same path on the target server through canonical materialization, S13/S14 publication/catalog and historical DataGateway read-back.

### Live gap remediation beyond bounded reconciliation — **DISPOSED, `NO_AUTHORITATIVE_REPAIR_PATH_PROVEN`**

Investigated in issue #110 (2026-09-24; full evidence in the issue's closing comment). A real, attributable Bybit historical archive exists for BTCUSDT (`public.bybit.com/trading/BTCUSDT/...csv.gz`), but at investigation time the archive directory stopped one day short of the live recent-trade window, so no current archive/recent-trade overlap could be compared, and no provider completeness attestation was found for any requested interval. The disposition below remains the accepted rule going forward; this is not a new decision, it is confirmation that the trigger condition has not yet been proven satisfiable and the gap stays explicit. A future issue may reopen this once archive/live overlap exists and completeness evidence can actually be produced for a bounded interval.

ADR-0040 deliberately does not invent a provider capability for interruptions that exceed the bounded recent-public-trades reconciliation window.

Trigger: after an A11 disconnect/restart, the last durable canonical `TradeKeyV1` cannot be recovered from the provider evidence available to the bounded reconciliation path, leaving an explicit non-complete interval.

Before such an interval may be declared filled/complete, resolve and prove:

- the authoritative source capable of reconstructing the exact missing interval (for example a provider historical/archive source only if its semantics and availability are actually observed and attributable);
- how that source binds to the existing Bybit `TradeKeyV1`, ordering and `trade-v1` semantics without inventing sequence continuity;
- how the existing A10 repair-intent/candidate/cutover path consumes that evidence;
- how the repaired interval is re-verified strongly enough to replace the prior `transport_interruption` / non-complete coverage evidence;
- what happens when no authoritative source can prove the missing interval (the gap must remain explicit; no guessed completion).

This open block does **not** prevent A11/K10 from running, recording an explicit gap and continuing with a new governed live segment. It **does** prevent the project from claiming that such a gap has been colmato/completed, and prevents Live Ingest Vertical closeout from claiming lossless continuity across that interval, until the missing-evidence proposition is actually satisfied.

Issue #110's disposition (above) is the current record. A negative or temporarily unprovable archive result must not be reinterpreted as proof that a gap is complete.

Do not solve this by treating missing `seq` values, absence of trades, wall-clock time or a finite local buffer as proof of completeness.

### Live consumer cursor (`B06`) — still OPEN / OUTSIDE CURRENT SCOPE

B06 deterministic consumer resume/replay semantics remain unresolved until a real live consumer is selected. ADR-0040 freezes A11 acquisition/reconciliation state only; it does not silently freeze B06.

## DG-C — Market-data depth

### L1 branch (`A13`)

Activate when a concrete L1 feed is selected. Resolve versioned L1 schema, identity, ordering, coverage and provenance from observed source evidence.

Selecting L1 does **not** activate L2.

### L2 branch (`A14`)

Activate only when L2 is selected, after the declared L1 dependency. Resolve snapshot/increment semantics, deterministic reconstruction ordering, gap handling and duplicate rules.

Exact L3/MBO semantics remain separately deferable until a real L3 feed exists.

## DG-E — Validation semantics — RESOLVED / IMPLEMENTED

DG-E has no remaining open semantic or implementation branch through F07/F08.

### Validation / labeling branch (`F06`,`F07`) — COMPLETE

F06 availability/purge/embargo is frozen and complete under
[ADR-0031](../decisions/ADR-0031-availability-purge-embargo-v1.md).

F07 label/censoring/lockbox semantics are frozen under
[ADR-0036](../decisions/ADR-0036-labels-censoring-lockbox-v1.md) (issue #95), and the bounded runtime is integrated via issue #96 / PR #99.

Accepted F07 v1 decisions include:

- outcome-derived labels only; policy-derived labels wait for explicit Strategy/Execution authority;
- only `COMPLETE` F03 Outcomes may produce a label value; source-censored and insufficient-coverage Outcomes remain explicit no-value states;
- exact identity-value and ordered exact-rational threshold transforms only;
- label causal availability equals source Outcome causal availability;
- target support includes the consumed F03 boundary at `horizon_end` and is projected into F06 without shrinking that endpoint;
- training targets are fold-completion dependencies; test/lockbox targets are evaluation outputs, not decision-time inputs;
- lockbox v1 is one terminal holdout interval with explicit hidden-evaluation isolation and irreversible reveal semantics;
- F07 freezes semantic isolation, not physical ACL/vault infrastructure;
- Validation consumes a narrow Validation-owned projection of F03 evidence rather than importing/redefining Research runtime semantics.

### DSR/PBO branch (`F08`) — COMPLETE

F08 robust-comparison semantics are frozen under
[ADR-0037](../decisions/ADR-0037-dsr-pbo-robust-comparison-v1.md) (issue #97), and the bounded Validation-owned runtime is integrated via issue #98 / PR #104.

Accepted F08 v1 decisions include:

- one complete same-frequency excess-return panel over a declared comparable trial population;
- non-annualized canonical Sharpe using sample standard deviation (`ddof=1`);
- deterministic raw standardized moment formulas for DSR;
- DSR v1 is the location-only `DSR-L` variant; DSR-LS/full-search variants are future extensions;
- effective trial count `K_eff` is explicit caller evidence, not estimated by F08;
- search adjustment uses the Bailey/López de Prado expected-maximum location approximation and PSR formula;
- sampling assumption is explicitly `IID_V1`; no serial-correlation correction is implied;
- PBO v1 is full CSCV with all symmetric half-block combinations, deterministic IS selection, OOS ranking, average exact-tie rank and strict `lambda < 0` overfit event;
- non-evaluable required Sharpe values fail the complete metric rather than silently dropping a trial/split;
- F05 walk-forward folds are not reinterpreted as CSCV partitions;
- binary64 numerical policy and pinned DSR/PBO reference vectors are part of the contract;
- Validation remains the runtime owner and consumes opaque cross-owner evidence without loosening the package DAG.

DG-E is therefore complete through F08. No future scope should reopen these semantics absent contradictory accepted authority.

## DG-F — Strategy / Execution semantics

Blocks `G04,H03` when their Strategy/Replay path is selected.

Resolve with pinned/adversarial vectors:

- session calendars, DST and session-boundary behavior;
- cooldown/eligibility semantics;
- stop/target/bracket/OCO behavior;
- same-bar/intrabar conflicts;
- partial fills and conflict ordering.

Keep Strategy upstream of Execution. Do not let execution simulation redefine strategy semantics.

## DG-G — Experiment / RL / Jobs

Independent branches:

### Experiment persistence (`I02`)

Resolve one canonical Study/Trial/Run/Artifact persistence model. Restart/query/resume must preserve identity and idempotency; avoid competing persistence stores/models.

### Strategic RL (`I06`)

Freeze StrategicState/StrategicAction/StrategicReward only when the strategic-RL runtime is selected. It must not absorb execution-control variables/objectives.

### Execution RL (`I07`)

Freeze ExecutionState/ExecutionAction/ExecutionReward only when execution RL is selected. It must remain structurally separate from the strategic task.

### Job runtime (`J03`)

Before durable long-running operations, freeze submission identity, status/lifecycle, retry/idempotency, result identity and failure semantics. Do not infer transport/process topology from the Job contract.

## DG-H — Operational safety

This family is progressive and non-monolithic.

### Runtime identity (`K02`) — RESOLVED / IMPLEMENTED for Live Ingest v1

Live-ingest runtime identity semantics are frozen under [ADR-0041](../decisions/ADR-0041-live-ingest-runtime-identity-v1.md).

PR #113 proves the real target deployment under the existing non-root `mkt-transform` identity and minimum catalog role, and verifies physical storage topology plus backup-authority separation. The real-server proof reused the existing governed identity boundary and applied only the explicitly authorized ACL correction needed to remove ingest write authority from the independent K08 recovery copy.

For public Bybit trades v1 there is no provider secret. Filesystem/database authority remains restricted to ingest/publication/checkpoint responsibilities; normal ingest authority does not imply backup-destruction authority.

### Observability (`K03`)

Before paper/live claims or checkpoint/recovery runtime, define the minimum externally observable health/provenance/failure transitions required by the selected runtime. Exact technology/SLOs remain local until needed.

### Pressure (`K05`)

After observational capacity `K04`, resolve thresholds/time-to-full and explicit safe actions. No silent deletion.

### Source protection (`K06`) — RESOLVED

Protection authority and reconstruction guarantees are frozen and complete
under [ADR-0032](../decisions/ADR-0032-raw-source-protection-v1.md): a pure
protection-identity/assessment seam (`ProtectionState`,
`ProtectionAssessment`, `assess_protection()`, `SafetyRelevanceAssertion`,
`ProtectionWriteAuthorization`) that never crawls a filesystem or authorizes
deletion itself. K07 and K08 may depend on its output. **Accepted known
limitation:** `ProtectionObligationEvidence`'s obligation kind is not yet
type-restricted to K06-owned concerns, tracked for a future narrowing pass.

### Tier relocation (`K07`)

If relocation is selected, freeze crash-safe old-or-new-valid placement semantics. This is a sibling downstream use of source protection, not a prerequisite of backup/restore.

### Backup/restore (`K08`) — RESOLVED / IMPLEMENTED

K08 semantics are frozen under [ADR-0039](../decisions/ADR-0039-backup-restore-v1.md): protect finalized published canonical state as an identity-bound recovery set; prove restore from storage independent of the tested primary boundary into an empty isolated target; reproduce canonical identities/coverage/catalog resolution and historical DataGateway-visible data. RPO v1 is the last finalized recovery set; no numeric RTO/HA/off-site claim is implied.

PR #111 implements the identity-bound recovery set, export/restore and isolated Postgres/DataGateway proof. PR #113 supplies the previously pending deployment-independence evidence on the real topology and removes ingest write authority over the independent recovery tier. K08 is therefore implementation `COMPLETE` for v1.

### Retention/deletion (`K09`)

Restore proof must precede deletion authority. No protected or sole recoverable evidence may be deleted.

### Checkpoint/recovery (`K10`) — RESOLVED / IMPLEMENTED

K10 semantics are frozen under [ADR-0042](../decisions/ADR-0042-live-ingest-checkpoint-recovery-v1.md).

A checkpoint means the last canonical progress point already durably published; publication must become durable before checkpoint advance. Replay after crash is allowed and relies on ADR-0040 idempotent deduplication. Invalid checkpoints fail closed. Restart uses bounded provider reconciliation and must record an explicit gap when continuity cannot be proven; it never guesses a cursor/completeness state.

PR #114 implements persisted checkpoint state, binding/monotonicity/refusal semantics, A11 restart composition and the required hermetic proof matrix. PR #122 (issues #109, #121) closes the previously pending `REAL_RESTART_PROOF_PENDING` handoff: under the K02 identity, Process A publishes and persists a checkpoint, Process B restarts, runs bounded reconciliation, durably publishes the accepted reconciliation records under explicit `reconciliation` coverage, and only then advances the checkpoint to a new monotonic generation (`K10_REAL_RESTART_PROOF: PASS`, run `k10-restart-fixed-fast-20260924T193502Z`). K10 is therefore implementation `COMPLETE` for v1.

## Explicitly deferable decisions

The following are classified and therefore not roadmap unknowns:

### Second provider / multi-capability resolution (`A12`,`C06`)

Wait for a real second venue or second representation. Do not freeze a generic resolver from first-provider symmetry.

### L3/MBO (`A15`)

Wait for a real L3/MBO feed and map its semantics without invention.

### Durable DatasetSnapshot (`B05`)

Wait for a concrete durable replay/reproduction requirement. Catalog-query replay alone remains insufficient; reference-vs-artifact shape is not frozen now.

### Schema evolution beyond accepted versions (`B08`)

Wait for a real next schema/version requirement.

### Feature provider extension (`E07`)

Wait for a real second provider/capability need; no generic plugin framework now.

### Multi-asset execution (`H06`)

Wait for explicit multi-asset product scope.

### Canonical API transport (`J02`)

Consumer API semantics are already frozen. Concrete HTTP/gRPC/Arrow Flight/WebSocket/other transport, serialization, pagination/streaming and runtime host remain deferred until a real remote/client need exists.

## Implementation-local choices — not governance blockers

Unless a future accepted contract says otherwise, these remain local choices:

- internal helper/class names;
- concrete bounded-batch Python class/method naming where semantics are frozen;
- Parquet writer library/compression/row-group/page/dictionary settings;
- client presentation details;
- observability implementation technology after required signals are known;
- local reversible code organization within established ownership/dependency boundaries.
