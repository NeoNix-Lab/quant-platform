"""Proof-only Wave 5 Golden E2E supervised ML composition.

This module is an application-owned proof harness for issue #174.  It composes
the already-governed I04 supervised projection and I05 supervised training
capabilities over bounded canonical Bybit BTCUSDT fixture evidence, then runs
the same semantic inputs twice to prove deterministic identities end to end.
It is fixture-bound proof machinery, not a supported installed-package
dependency API (ADR-0056).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from typing import Any

from quant_platform.canonical import canonical_bytes
from quant_platform.data.models import CoverageInterval, DatasetIdentity, Instant
from quant_platform.experiments import (
    ComparisonProtocolIdentity,
    IdentityReference,
    RunIdentity,
    RunSpecIdentity,
    StudyIdentity,
    TrialIdentity,
)
from quant_platform.features.definitions import (
    FeatureDefinitionId,
    FeatureObservation,
    ObservationLifecycle,
    SupportIdentity,
    SupportReference,
)
from quant_platform.learning import (
    ProjectionSide,
    SupervisedFeatureInput,
    SupervisedProjection,
    SupervisedSampleCandidate,
    SupervisedSelectionPolicy,
    SupervisedTrainingPolicy,
    SupervisedTrainingRunResult,
    build_supervised_projection,
    train_evaluate_supervised_baseline,
)
from quant_platform.validation import (
    Embargo,
    LabelDefinition,
    LabelTransformKind,
    OutcomeEvidence,
    OutcomeState,
    WalkForwardFold,
    evaluate_label,
)


WAVE5_GOLDEN_SUPERVISED_PROOF_VERSION = "1"
DEFAULT_GOLDEN_FIXTURE = (
    Path(__file__).resolve().parents[3]
    / "fixtures"
    / "conformity"
    / "golden-bybit-btcusdt-2024-01-15.json"
)
CANONICAL_BTCUSDT_TRADES = DatasetIdentity(
    "canonical",
    "trades",
    "bybit",
    "BTCUSDT",
    "trade-v1",
)
PRICE_DELTA_FEATURE_ID = "feature-definition-v1:sha256:" + "5" * 64
OUTCOME_SPEC_ID = "outcome-spec-v1:sha256:" + "6" * 64
INPUT_CONTRACT_ID = "feature-input-contract-v1:sha256:" + "7" * 64


@dataclass(frozen=True, slots=True)
class Wave5GoldenSupervisedProof:
    """Deterministic proof output for the Wave 5 supervised Golden E2E."""

    source_fixture: str
    source_evidence_identity: str
    dataset_identity: DatasetIdentity
    projection: SupervisedProjection
    first: SupervisedTrainingRunResult
    second: SupervisedTrainingRunResult

    @property
    def deterministic(self) -> bool:
        return self.first.stable_dict() == self.second.stable_dict()

    def stable_dict(self) -> dict[str, Any]:
        return {
            "proof_version": WAVE5_GOLDEN_SUPERVISED_PROOF_VERSION,
            "source_fixture": self.source_fixture,
            "source_evidence_identity": self.source_evidence_identity,
            "dataset_identity": self.dataset_identity.stable_dict(),
            "projection_identity": self.projection.identity,
            "selection_policy_identity": self.projection.selection_policy.identity,
            "training_policy_identity": self.first.policy.identity,
            "run_identity": self.first.run_identity.stable_dict(),
            "run_spec_identity": self.first.run_identity.run_spec_identity.fingerprint,
            "sample_counts": {
                "train": len(self.projection.samples_for_side(ProjectionSide.TRAIN)),
                "test": len(self.projection.samples_for_side(ProjectionSide.TEST)),
                "rejected": len(self.projection.rejections),
            },
            "metrics": self.first.metrics.stable_dict(),
            "normalizer_identity": self.first.normalizer.identity,
            "model_identity": self.first.model.identity,
            "artifact_identities": [
                registration.identity.stable_dict()
                for registration in self.first.artifact_registrations
            ],
            "artifact_content_identities": {
                registration.identity.artifact_role: (
                    registration.identity.artifact_content_identity.content_identity
                )
                for registration in self.first.artifact_registrations
            },
            "first_run_equals_second_run": self.deterministic,
        }


def run_wave5_golden_supervised_proof(
    *,
    fixture_path: str | Path = DEFAULT_GOLDEN_FIXTURE,
    code_ref: str = "wave5-golden-supervised-e2e-v1",
    execution_id: str = "wave5-golden-supervised-e2e",
) -> Wave5GoldenSupervisedProof:
    """Run the bounded Wave 5 Golden E2E supervised proof twice.

    The function is intentionally hermetic: it reads only the checked-in
    canonical Bybit BTCUSDT fixture and performs no catalog, filesystem
    artifact-store or job-runtime writes.
    """

    fixture = Path(fixture_path)
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    source_identity = _source_evidence_identity(payload)
    projection = _build_projection(payload=payload, source_evidence_identity=source_identity, code_ref=code_ref)
    policy = SupervisedTrainingPolicy(
        "wave5.golden_supervised_training",
        1,
        code_ref,
        declared_class_labels=("0", "1"),
    )
    run = _run_identity(
        projection_identity=projection.identity,
        source_evidence_identity=source_identity,
        code_ref=code_ref,
        execution_id=execution_id,
    )
    first = train_evaluate_supervised_baseline(
        projection=projection,
        run_identity=run,
        policy=policy,
    )
    second = train_evaluate_supervised_baseline(
        projection=projection,
        run_identity=run,
        policy=policy,
    )
    return Wave5GoldenSupervisedProof(
        source_fixture=str(fixture),
        source_evidence_identity=source_identity,
        dataset_identity=CANONICAL_BTCUSDT_TRADES,
        projection=projection,
        first=first,
        second=second,
    )


def _build_projection(
    *,
    payload: dict[str, Any],
    source_evidence_identity: str,
    code_ref: str,
) -> SupervisedProjection:
    first = payload["candle"]["first_candle"]
    last = payload["candle"]["last_candle"]
    fold = WalkForwardFold(
        fold_index=0,
        train=_interval("2024-01-15T00:00:00Z", "2024-01-15T12:00:00Z"),
        test=_interval("2024-01-15T12:00:00Z", "2024-01-16T00:00:01Z"),
    )
    selection_policy = SupervisedSelectionPolicy(
        policy_key="wave5.golden_supervised_input",
        semantic_version=1,
        implementation_code_identity=code_ref,
        feature_definition_ids=(PRICE_DELTA_FEATURE_ID,),
        notes="bounded canonical Bybit BTCUSDT fixture projection",
    )
    candidates = (
        _candidate(
            sample_id="bybit-btcusdt-20240115-first-close-delta",
            decision_time=first["bucket_end"],
            support_start=first["bucket_start"],
            support_end=first["bucket_end"],
            source_evidence_identity=source_evidence_identity,
            value=_delta(first["close"], first["open"]),
            label_value="1",
        ),
        _candidate(
            sample_id="bybit-btcusdt-20240115-first-low-delta",
            decision_time=first["bucket_end"],
            support_start=first["bucket_start"],
            support_end=first["bucket_end"],
            source_evidence_identity=source_evidence_identity,
            value=_delta(first["low"], first["open"]),
            label_value="0",
        ),
        _candidate(
            sample_id="bybit-btcusdt-20240115-last-high-delta",
            decision_time=last["bucket_end"],
            support_start=last["bucket_start"],
            support_end=last["bucket_end"],
            source_evidence_identity=source_evidence_identity,
            value=_delta(last["high"], last["open"]),
            label_value="1",
        ),
        _candidate(
            sample_id="bybit-btcusdt-20240115-last-low-delta",
            decision_time=last["bucket_end"],
            support_start=last["bucket_start"],
            support_end=last["bucket_end"],
            source_evidence_identity=source_evidence_identity,
            value=_delta(last["low"], last["open"]),
            label_value="0",
        ),
    )
    return build_supervised_projection(
        fold=fold,
        embargo=Embargo(0),
        selection_policy=selection_policy,
        candidates=candidates,
    )


def _candidate(
    *,
    sample_id: str,
    decision_time: str,
    support_start: str,
    support_end: str,
    source_evidence_identity: str,
    value: str,
    label_value: str,
) -> SupervisedSampleCandidate:
    feature = _feature(
        sample_id=sample_id,
        support_start=support_start,
        support_end=support_end,
        decision_time=decision_time,
        value=value,
        source_evidence_identity=source_evidence_identity,
    )
    return SupervisedSampleCandidate(
        sample_id=sample_id,
        decision_time=decision_time,
        features=(feature,),
        label=_label(
            sample_id=sample_id,
            horizon_start=support_start,
            horizon_end=support_end,
            causal_available_at=decision_time,
            value=label_value,
        ),
        metadata={
            "dataset": CANONICAL_BTCUSDT_TRADES.stable_dict(),
            "source_evidence_identity": source_evidence_identity,
        },
    )


def _feature(
    *,
    sample_id: str,
    support_start: str,
    support_end: str,
    decision_time: str,
    value: str,
    source_evidence_identity: str,
) -> SupervisedFeatureInput:
    support_identity = SupportIdentity(
        input_contract_identity=INPUT_CONTRACT_ID,
        observation_identity=f"{source_evidence_identity}:{sample_id}",
        support_reference=SupportReference.current(),
    )
    observation = FeatureObservation(
        definition_id=FeatureDefinitionId(PRICE_DELTA_FEATURE_ID),
        support_identity=support_identity,
        value=value,
        lifecycle=ObservationLifecycle.FINAL,
        causal_available_at=decision_time,
        observed_available_at=decision_time,
        observed_finalized_at=decision_time,
    )
    return SupervisedFeatureInput(
        observation=observation,
        support=_interval(support_start, support_end),
        contemporaneous_version_proven=True,
    )


def _label(
    *,
    sample_id: str,
    horizon_start: str,
    horizon_end: str,
    causal_available_at: str,
    value: str,
):
    definition = LabelDefinition(
        label_key="wave5.golden.direction",
        semantic_version=1,
        source_outcome_spec_id=OUTCOME_SPEC_ID,
        transform_kind=LabelTransformKind.IDENTITY_VALUE,
    )
    outcome = OutcomeEvidence(
        outcome_id="outcome-v1:sha256:" + _sha256({"sample_id": sample_id, "value": value}),
        outcome_spec_id=OUTCOME_SPEC_ID,
        event_id=sample_id,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        causal_available_at=causal_available_at,
        state=OutcomeState.COMPLETE,
        realized_value=value,
    )
    return evaluate_label(definition, outcome)


def _run_identity(
    *,
    projection_identity: str,
    source_evidence_identity: str,
    code_ref: str,
    execution_id: str,
) -> RunIdentity:
    study = StudyIdentity(
        study_definition_version="wave5-golden-supervised-proof-v1",
        research_identity=_ref("golden-proof", "wave5-supervised-ml-v1"),
        evaluation_objective_identity=_ref("metric-set", "classification-accuracy-v1"),
        comparison_protocol_identity=ComparisonProtocolIdentity(
            _ref("comparison-protocol", "centroid-baseline-v1"),
            ("model_family",),
        ),
    )
    trial = TrialIdentity(study, {"model_family": "centroid_classifier_v1"})
    spec = RunSpecIdentity(
        trial_identity=trial,
        code_identity=_ref("code", code_ref),
        data_identities=(
            _ref("dataset", "canonical:trades:bybit:BTCUSDT:trade-v1"),
            _ref("fixture", source_evidence_identity),
            _ref("supervised-projection", projection_identity),
        ),
        feature_identities=(_ref("feature-definition", PRICE_DELTA_FEATURE_ID),),
        research_label_identities=(_ref("label-semantics", "wave5-golden-direction-v1"),),
        validation_identity=_ref("walk-forward-fold", "wave5-golden-fold-0"),
        environment_identity=_ref("environment", "stdlib-only-python"),
        run_configuration={
            "model_family": "centroid_classifier_v1",
            "proof_version": WAVE5_GOLDEN_SUPERVISED_PROOF_VERSION,
        },
    )
    return RunIdentity(spec, execution_id)


def _ref(kind: str, identity: str) -> IdentityReference:
    return IdentityReference(kind, identity)


def _interval(start: str, end: str) -> CoverageInterval:
    return CoverageInterval(Instant.parse(start), Instant.parse(end))


def _delta(value: str, base: str) -> str:
    return str((Decimal(value) - Decimal(base)).normalize())


def _source_evidence_identity(payload: dict[str, Any]) -> str:
    return "bybit-btcusdt-golden-fixture-v1:sha256:" + _sha256(payload)


def _sha256(payload: Any) -> str:
    encoded = canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=False)
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "CANONICAL_BTCUSDT_TRADES",
    "DEFAULT_GOLDEN_FIXTURE",
    "PRICE_DELTA_FEATURE_ID",
    "WAVE5_GOLDEN_SUPERVISED_PROOF_VERSION",
    "Wave5GoldenSupervisedProof",
    "run_wave5_golden_supervised_proof",
]
