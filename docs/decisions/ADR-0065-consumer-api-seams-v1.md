# ADR-0065 - Consumer API seams v1

**Status:** ACCEPTED
**Date:** 2026-10-04

## Context

ADR-0020 freezes one semantic Consumer API above DataGateway: clients select
capabilities semantically and receive deterministic result, provenance, and
error distinctions without storage paths or implementation exceptions. C02/C03
are its existing application precedent; ADR-0050 makes J02 a lossless carrier
and keeps quantitative logic in `application`/domain rather than clients or
transport.

The four G4 domains do not have the same presently executable surface.
`compose_decision`, Validation's finite operations, and
`train_evaluate_supervised_baseline` each accept immutable domain values and
return deterministic domain evidence. `HistoricalReplayRuntime.run()`, by
contrast, requires an injected `feature_provider` callable in addition to a
`ReplaySpec` and a DataGateway. `ReplaySpec` neither identifies nor serializes
that callable. Treating a path, import name, or client-provided executable as a
Replay request would invent a new semantic authority.

## Decision

### 1. Common seam discipline

J10-J13 are application-owned capability seams, not direct domain-object,
storage, or transport APIs. Every later operation has a versioned semantic
request whose request identity is derived from its canonical fields. A request
may carry existing immutable domain payloads and their content identities, but
never a catalog UUID, storage root, filesystem path, callable import path,
repository handle, or client-selected transport locator.

The application service normalizes the request, resolves only already-governed
definitions/evidence, invokes the one established domain operation, and returns
the operation's stable result plus its existing identities/provenance. It maps
invalid semantic input to `invalid_request`, unavailable accepted evidence to
`source_not_found` or `no_coverage` as applicable, and a failure to establish a
trustworthy result to `integrity_failure`; it never leak raw Python exceptions.
Clients only compose these requests and render responses. J02 serializes the
same request/result/error envelope without reinterpreting domain meaning.

An operation whose duration or resource use is not interactively bounded is
admitted through J03 before execution. This ADR defines no Job request, wire
message, remote scope, authorization, or client implementation. ADR-0063's
only current remote scope remains `j02.market_data.read`; a later implementation
must not infer a Strategy, Replay, Validation, or Training grant from it.

### 2. J10 - Strategy composition seam

`strategy-compose-v1` exposes exactly the existing `compose_decision`
operation. Its request carries the complete canonical `StrategySpec` payload
and `strategy_identity`, a set of canonical `StrategyInput` payloads, an
explicit decision time, and an instrument. `available_at`, input identity, and
provenance remain the StrategyInput-owned availability evidence; neither a
feature path nor a client-side signal calculation is admissible.

The result is the existing `StrategyCompositionResult`: exactly one canonical
`DecisionIntent` or `NoDecision`, including its content-derived identity,
strategy identity, policy identities, input identities, and no-decision reason
where applicable. The application does not change entry/exit, risk, sizing,
session, cooldown, or repeated-entry semantics.

### 3. J12 - Validation finite-operation seam

J12 exposes only the already accepted finite Validation operations, each under
its own semantic request identity: building expanding folds from a
`WalkForwardScheduleSpec`; classifying a `ValidationCandidate` with its
`WalkForwardFold` and `Embargo`; evaluating DSR with a
`ComparableTrialPanel` and `EffectiveTrialCountEvidence`; and evaluating PBO
with a panel and its existing block-count constraints. Results preserve the
returned folds, `CandidateClassificationResult`, `DSRResult`, or `PBOResult`
and their canonical payload/identity evidence.

J12 does not define a generic "run validation" operation, synthesize missing
availability, alter purge/embargo or lockbox rules, sample CSCV, or create a
new metric. A request that violates an existing constructor or evaluator
precondition is `invalid_request`; a non-evaluable existing result remains a
result with its domain status rather than a client-side pass/fail conversion.

### 4. J13 - supervised Training evaluation and registration seam

`supervised-train-evaluate-v1` carries an existing immutable
`SupervisedProjection`, `RunIdentity`, and `SupervisedTrainingPolicy`. It
invokes only `train_evaluate_supervised_baseline` and returns the existing
`SupervisedTrainingRunResult`: projection, policy, run, normalizer, model, and
artifact identities; prediction/metric evidence; and the `TrialAttemptResult`.
The request is semantic evidence, not a model file path, dataset path, database
connection, or caller-supplied repository.

If the operation is admitted for durable registration, the application resolves
its owned Experiment repository and invokes the existing
`record_supervised_training_run` boundary. The client cannot select the
repository or mutate Experiment/Artifact records directly. This does not add a
model registry, another learner, training-data construction rule, or a new
training lifecycle; unbounded execution still requires J03.

### 5. J11 - Replay seam is explicitly deferred

J11 has no accepted consumer request yet. A `ReplaySpec` identifies replay
configuration and input selection, and `ReplayResult` has deterministic result
and trace identities, but `HistoricalReplayRuntime.run()` also requires a
`feature_provider` callable. There is no accepted identity-bearing,
serializable, server-resolved definition for that callable. Exposing it as a
path, module name, opaque executable, or client callback would violate the
common discipline and alter Strategy/Replay authority.

J11 is therefore deferred, not implemented. Its evidence trigger is an
accepted ADR that defines a semantic, immutable feature/provider reference and
its provenance/availability contract, plus a bounded application composition
that resolves it without client domain logic. That ADR must also choose whether
the resulting operation is synchronously bounded or must be admitted as J03.
Only then may a J11 implementation define its request/result/error envelope.

## Consequences

- J10, J12, and J13 can be implemented as thin application adapters without
  reopening their domain semantics.
- J11 cannot be disguised as a generic RPC or a callable/file transport; its
  missing domain input remains visible and governed.
- J04/J05/J06 stay presentation clients: no quantitative calculation, storage
  resolution, Experiment persistence, or alternate request construction moves
  to them.

## Out of scope

- implementation of J10-J13, J02 wire messages, remote authorization scopes,
  J03 runtime, or any client;
- a universal RPC framework, generic training/replay runner, model registry,
  or new Strategy/Replay/Validation/Training behavior;
- changes to frozen DataGateway, domain, identity, Experiment, or Artifact
  contracts.

## Amendment 1 (issue #301) — bounded synchronous PBO

**Date:** 2026-10-05

Review of the J12 implementation found that the existing complete-CSCV
evaluator materializes every split.  Its cost therefore grows with both the
number of splits and the panel dimensions: `C(b, b/2) * trial_count *
observation_count`.  In the reviewed baseline, 30 blocks imply
`C(30,15) = 155117520` splits before iteration; a block-count limit alone
would not bound large panels.  Such requests require the separate J03
dispatch/result-materialization decision and are not synchronously executable
through J12.

Section 3 is amended only for the synchronous PBO request domain:

- `MAX_SYNC_PBO_BLOCK_COUNT = 16` (at most 12,870 CSCV splits);
- `MAX_SYNC_PBO_WORK = 5_000_000`, where work is
  `C(b, b/2) * trial_count * observation_count`.

After existing request type checks and before calling the evaluator, a request
outside either bound is refused as `invalid_request` with stable reason
`pbo_sync_budget_exceeded`.  No `ConsumerErrorCode`, sampling rule, PBO
formula, `PBOResult` payload, or identity changes.  A request within the bound
continues to invoke the existing evaluator unchanged and preserves its exact
result evidence.  The largest admitted result must remain below J02's 16 MiB
wire bound; the implementation proof serializes the 16-block boundary vector.
On the implementation environment, the 16-block/10-trial/16-observation
boundary vector produced 12,870 splits in 1.233 seconds with a 1,585,427-byte
canonical JSON result.  This is verification evidence, not a portable timing
guarantee; the semantic work bound is authoritative.

This amendment does not define J03 PBO dispatch, durable result
materialization/retrieval, or a new transport message.  Those remain a separate
design gate.  Until then, PBO outside the synchronous domain remains available
only as the in-process Validation library operation, not through the J12
Consumer-API seam.

## Acceptance evidence

`StrategySpec`, `StrategyInput`, `DecisionIntent`, and `NoDecision` provide the
J10 canonical inputs/outcomes. `WalkForwardScheduleSpec`,
`classify_candidate`, `evaluate_dsr_v1`, and `evaluate_pbo_v1` provide J12's
finite operations. `SupervisedProjection`, `RunIdentity`,
`SupervisedTrainingPolicy`, `train_evaluate_supervised_baseline`, and
`record_supervised_training_run` provide J13's existing boundary. The injected
`feature_provider` parameter of `HistoricalReplayRuntime` is the direct
evidence for J11's deferment.

`tests/test_consumer_api_seams_v1.py` guards these accepted boundaries and the
explicit no-callable/no-path rule. It is a design regression guard, not a
transport or end-to-end execution proof.

## Related

Parent tracking: #281. Closes issue #285.
