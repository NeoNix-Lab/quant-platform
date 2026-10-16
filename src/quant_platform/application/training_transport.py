"""Application-owned J13 canonical codec and opt-in authenticated J02 route (issue #339).

``j02-training-v1``, operation ``train_evaluate`` (ADR-0069 section 3). The wire
request is the ADR-0065 section 4 ``supervised-train-evaluate-request-v1``
canonical payload, byte for byte, plus two transport-only members that are
never part of any domain canonical payload or identity:

- ``register``: a boolean. ``true`` additionally needs ``j02.training.register``,
  which the server authorizes *before* the request is decoded;
- ``reconstruction`` (``j13-reconstruction-v1``): the evidence the canonical
  payload cannot carry, so that the owned constructors can rebuild the request.
  The canonical payload hashes the Study/Trial/RunSpec preimages, the
  ``SupportIdentity`` preimage and the original type of each feature value;
  the reconstruction carries exactly those, and nothing else (the label payload
  is the existing ``LabelResult.canonical_payload``).

Nothing is resolved, looked up or invented by the server. Every rebuilt object
is checked against the canonical payload it came with (identities, hashes and
canonical text), so a mutated or mismatched piece of evidence is
``invalid_request`` before training or registration. The server never imports
anything named by the request.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any

from quant_platform.canonical import canonical_bytes

from ..data.models import CoverageInterval, Instant
from ..experiments import (
    ComparisonProtocolIdentity,
    ExperimentRepository,
    IdentityReference,
    RunIdentity,
    RunSpecIdentity,
    StudyIdentity,
    TrialIdentity,
)
from ..features.definitions import (
    FeatureObservation,
    ObservationLifecycle,
    SupportIdentity,
    SupportReference,
    SupportSelectorKind,
)
from ..learning import (
    ProjectionSide,
    SupervisedFeatureInput,
    SupervisedProjection,
    SupervisedProjectionRejection,
    SupervisedProjectionSample,
    SupervisedSelectionPolicy,
    SupervisedTrainingPolicy,
)
from ..validation.availability import CandidateClassification, Embargo
from ..validation.labels import LabelResult, LabelStatus, OutcomeState
from ..validation.walk_forward import WalkForwardFold
from .api_transport_server import J02_MAX_WIRE_MESSAGE_BYTES, J02FamilyHandler
from .market_data import ConsumerApiError, ConsumerErrorCode
from .training_consumer import (
    SUPERVISED_TRAIN_EVALUATE_REQUEST_IDENTITY_DOMAIN,
    SupervisedTrainEvaluateRequest,
    SupervisedTrainingConsumerService,
    execute_supervised_train_evaluate,
)


J02_TRAINING_FAMILY = "j02-training-v1"
J02_TRAINING_REQUEST_VERSION = J02_TRAINING_FAMILY + "-request-v1"
J02_TRAINING_RESPONSE_VERSION = J02_TRAINING_FAMILY + "-response-v1"
J02_TRAINING_EVALUATE_SCOPE = "j02.training.evaluate"
J02_TRAINING_REGISTER_SCOPE = "j02.training.register"
J13_RECONSTRUCTION_VERSION = "j13-reconstruction-v1"
TRAIN_EVALUATE_OPERATION = "train_evaluate"

_VALUE_TYPES = ("null", "boolean", "integer", "string", "decimal", "fraction")
_REPOSITORY_UNAVAILABLE = "registration_unavailable"

RepositoryProvider = Callable[[], AbstractContextManager[ExperimentRepository]]


class RequestTooLarge(ValueError):
    """The encoded request exceeds the J02 message bound: refused locally, never sent."""

    code = "request_too_large"


@dataclass(frozen=True, slots=True)
class DecodedTrainEvaluate:
    request: SupervisedTrainEvaluateRequest
    register: bool


# -- lossless JSON helpers ---------------------------------------------------------


def _object(value: Any, keys: set[str]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        raise ValueError("canonical object fields do not match")
    return value


def _json_value(value: Any) -> Any:
    """Copy only lossless JSON data, without coercing Python objects."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is list:
        return [_json_value(item) for item in value]
    if isinstance(value, Mapping) and all(type(key) is str and key for key in value):
        return {key: _json_value(item) for key, item in value.items()}
    raise ValueError("canonical JSON data required")


def _same_json(actual: Any, expected: Any) -> bool:
    # Python equality alone would silently admit True in place of integer 1.
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(
            _same_json(actual[key], expected[key]) for key in actual
        )
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(
            _same_json(left, right) for left, right in zip(actual, expected)
        )
    return actual == expected


def _require_canonical(actual: Any, expected: Any) -> None:
    if not _same_json(_json_value(actual), _json_value(expected)):
        raise ValueError("payload is not canonical")


def _reference(value: Any) -> IdentityReference:
    item = _object(value, {"identity_kind", "identity"})
    reference = IdentityReference(item["identity_kind"], item["identity"])
    _require_canonical(item, reference.stable_dict())
    return reference


def _references(value: Any) -> tuple[IdentityReference, ...]:
    if type(value) is not list:
        raise ValueError("identity references must be an array")
    return tuple(_reference(item) for item in value)


def _optional_reference(value: Any) -> IdentityReference | None:
    return None if value is None else _reference(value)


# -- encoding ----------------------------------------------------------------------------


def _value_type(value: Any) -> str:
    kind = type(value)
    if value is None:
        return "null"
    if kind is bool:
        return "boolean"
    if kind is int:
        return "integer"
    if kind is str:
        return "string"
    if kind is Decimal:
        return "decimal"
    if kind is Fraction:
        return "fraction"
    raise ValueError(f"a feature value of type {kind.__name__} has no lossless wire form")


def _reject_floats(value: Any) -> None:
    if type(value) is float:
        raise ValueError("a float has no lossless wire form")
    if isinstance(value, Mapping):
        for item in value.values():
            _reject_floats(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_floats(item)


def _run_identity_evidence(run_identity: RunIdentity) -> dict[str, Any]:
    spec = run_identity.run_spec_identity
    trial = spec.trial_identity
    spec_payload = spec.fingerprint_payload()
    run_spec = {
        key: spec_payload[key]
        for key in (
            "code_identity", "data_identities", "feature_identities", "research_label_identities",
            "validation_identity", "strategy_policy_execution_identities", "environment_identity",
            "explicit_run_configuration",
        )
    }
    assignments = trial.fingerprint_payload()["trial_assignments"]
    _reject_floats(run_spec)
    _reject_floats(assignments)
    return {
        "study": trial.study_identity.fingerprint_payload(),
        "trial_assignments": assignments,
        "run_spec": run_spec,
    }


def encode_train_evaluate_request(
    request: SupervisedTrainEvaluateRequest, *, register: bool = False,
) -> dict[str, Any]:
    """The canonical request payload plus ``register`` and its reconstruction evidence.

    Refuses (``ValueError``) what the wire cannot carry losslessly, such as a
    float or a feature value that is not a scalar of a known type.
    """

    if not isinstance(request, SupervisedTrainEvaluateRequest):
        raise TypeError("request must be a SupervisedTrainEvaluateRequest")
    if type(register) is not bool:
        raise ValueError("register must be a boolean")
    body = request.canonical_payload()
    body["register"] = register
    body["reconstruction"] = {
        "version": J13_RECONSTRUCTION_VERSION,
        "run_identity": _run_identity_evidence(request.run_identity),
        "samples": [
            {
                "label": sample.label.canonical_payload(),
                "features": [
                    {
                        "support_identity": feature.observation.support_identity.stable_dict(),
                        "value_type": _value_type(feature.observation.value),
                    }
                    for feature in sample.feature_inputs
                ],
            }
            for sample in request.projection.samples
        ],
    }
    return body


def encode_train_evaluate_message(
    request: SupervisedTrainEvaluateRequest, *, register: bool = False, request_id: str,
) -> str:
    """The complete wire message, or :class:`RequestTooLarge` before anything is sent.

    ADR-0069 section 3: the whole request message must fit the 16 MiB bound; the
    caller computes the encoded size before sending, and the server's
    ``max_size`` close is defense in depth, not the resource control.
    """

    message = {
        "message_family": J02_TRAINING_FAMILY,
        "schema_version": J02_TRAINING_REQUEST_VERSION,
        "operation": TRAIN_EVALUATE_OPERATION,
        "request_id": request_id,
        "request": encode_train_evaluate_request(request, register=register),
    }
    wire = canonical_bytes(message, profile="sorted-compact-ascii-v1", allow_nan=False)
    if len(wire) > J02_MAX_WIRE_MESSAGE_BYTES:
        raise RequestTooLarge("the encoded request exceeds the J02 message bound")
    return wire.decode("ascii")


# -- decoding --------------------------------------------------------------------------------


def _decode_run_identity(evidence: Any, execution_id: Any) -> RunIdentity:
    item = _object(evidence, {"study", "trial_assignments", "run_spec"})
    study_payload = _object(item["study"], {
        "identity_type", "identity_version", "study_definition_version", "research_identity",
        "evaluation_objective_identity", "comparison_protocol_identity",
    })
    protocol = _object(study_payload["comparison_protocol_identity"], {"identity", "trial_dimensions"})
    if type(protocol["trial_dimensions"]) is not list:
        raise ValueError("trial dimensions must be an array")
    study = StudyIdentity(
        study_definition_version=study_payload["study_definition_version"],
        research_identity=_reference(study_payload["research_identity"]),
        evaluation_objective_identity=_reference(study_payload["evaluation_objective_identity"]),
        comparison_protocol_identity=ComparisonProtocolIdentity(
            _reference(protocol["identity"]), tuple(protocol["trial_dimensions"]),
        ),
    )
    _require_canonical(study_payload, study.fingerprint_payload())
    assignments = item["trial_assignments"]
    if not isinstance(assignments, Mapping):
        raise ValueError("trial assignments must be an object")
    trial = TrialIdentity(study, _json_value(assignments))
    run_spec = _object(item["run_spec"], {
        "code_identity", "data_identities", "feature_identities", "research_label_identities",
        "validation_identity", "strategy_policy_execution_identities", "environment_identity",
        "explicit_run_configuration",
    })
    configuration = run_spec["explicit_run_configuration"]
    if not isinstance(configuration, Mapping):
        raise ValueError("run configuration must be an object")
    spec = RunSpecIdentity(
        trial_identity=trial,
        code_identity=_reference(run_spec["code_identity"]),
        data_identities=_references(run_spec["data_identities"]),
        feature_identities=_references(run_spec["feature_identities"]),
        research_label_identities=_references(run_spec["research_label_identities"]),
        validation_identity=_optional_reference(run_spec["validation_identity"]),
        strategy_policy_execution_identities=_references(run_spec["strategy_policy_execution_identities"]),
        environment_identity=_optional_reference(run_spec["environment_identity"]),
        run_configuration=_json_value(configuration),
    )
    return RunIdentity(spec, execution_id)


def _decode_label(payload: Any) -> LabelResult:
    item = _object(payload, {
        "identity_domain", "label_definition_id", "source_outcome_id", "source_outcome_spec_id",
        "source_state", "horizon_start", "horizon_end", "causal_available_at",
        "source_realized_value", "status", "value",
    })
    label = LabelResult(
        label_definition_id=item["label_definition_id"],
        source_outcome_id=item["source_outcome_id"],
        source_outcome_spec_id=item["source_outcome_spec_id"],
        source_state=OutcomeState(item["source_state"]),
        horizon_start=Instant.parse(item["horizon_start"]),
        horizon_end=Instant.parse(item["horizon_end"]),
        causal_available_at=Instant.parse(item["causal_available_at"]),
        status=LabelStatus(item["status"]),
        value=item["value"],
        source_realized_value=item["source_realized_value"],
    )
    _require_canonical(item, label.canonical_payload())
    return label


def _decode_support_identity(payload: Any) -> SupportIdentity:
    item = _object(payload, {
        "identity_domain", "input_contract_identity", "observation_identity", "support_reference",
    })
    reference = _object(item["support_reference"], {"kind", "offset", "end_offset", "length", "unit"})
    support = SupportIdentity(
        input_contract_identity=item["input_contract_identity"],
        observation_identity=item["observation_identity"],
        support_reference=SupportReference(
            SupportSelectorKind(reference["kind"]), reference["offset"],
            reference["end_offset"], reference["length"],
        ),
    )
    _require_canonical(item, support.stable_dict())
    return support


def _restore_value(value: Any, kind: Any) -> Any:
    """The original native feature value, from its canonical form and its type evidence.

    Only the type is taken from the evidence; whether the value is that value's
    exact canonical text (or finite, or reduced) is decided by the comparison of
    the whole rebuilt request with the canonical payload that came with it.
    """

    if kind not in _VALUE_TYPES:
        raise ValueError("unknown feature value type")
    if kind == "null":
        restored = None
    elif kind == "boolean":
        restored = value if type(value) is bool else _fail()
    elif kind == "integer":
        restored = value if type(value) is int else _fail()
    elif kind == "string":
        restored = value if type(value) is str else _fail()
    elif kind == "decimal":
        if type(value) is not str:
            _fail()
        try:
            restored = Decimal(value)
        except InvalidOperation:
            _fail()
    else:  # fraction
        if type(value) is not str:
            _fail()
        try:
            restored = Fraction(value)
        except (ValueError, ZeroDivisionError):
            _fail()
    if kind == "null" and value is not None:
        _fail()
    return restored


def _fail() -> Any:
    raise ValueError("feature value does not match its type evidence")


def _decode_feature(payload: Any, evidence: Any) -> SupervisedFeatureInput:
    item = _object(payload, {
        "observation_identity", "definition_id", "support_identity", "support", "value",
        "lifecycle", "causal_available_at", "observed_available_at", "observed_finalized_at",
        "contemporaneous_version_proven",
    })
    proof = _object(evidence, {"support_identity", "value_type"})
    support_identity = _decode_support_identity(proof["support_identity"])
    interval = _object(item["support"], {"start", "end"})
    feature = SupervisedFeatureInput(
        observation=FeatureObservation(
            definition_id=item["definition_id"],
            support_identity=support_identity,
            value=_restore_value(item["value"], proof["value_type"]),
            lifecycle=ObservationLifecycle(item["lifecycle"]),
            causal_available_at=item["causal_available_at"],
            observed_available_at=item["observed_available_at"],
            observed_finalized_at=item["observed_finalized_at"],
        ),
        support=CoverageInterval(Instant.parse(interval["start"]), Instant.parse(interval["end"])),
        contemporaneous_version_proven=item["contemporaneous_version_proven"],
    )
    return feature


def _decode_sample(payload: Any, evidence: Any) -> SupervisedProjectionSample:
    item = _object(payload, {
        "identity_domain", "sample_id", "side", "decision_time", "feature_inputs",
        "label_result_id", "label_value", "sample_uniqueness_weight", "sample_identity",
    })
    proof = _object(evidence, {"label", "features"})
    if type(item["feature_inputs"]) is not list or type(proof["features"]) is not list:
        raise ValueError("features must be arrays")
    if len(item["feature_inputs"]) != len(proof["features"]):
        raise ValueError("feature evidence does not match the features")
    sample = SupervisedProjectionSample(
        sample_id=item["sample_id"],
        side=ProjectionSide(item["side"]),
        decision_time=Instant.parse(item["decision_time"]),
        feature_inputs=tuple(
            _decode_feature(feature, feature_proof)
            for feature, feature_proof in zip(item["feature_inputs"], proof["features"])
        ),
        label=_decode_label(proof["label"]),
        validation_classification=CandidateClassification.ADMITTED,
        sample_uniqueness_weight=item["sample_uniqueness_weight"],
    )
    return sample


def _interval(value: Any) -> CoverageInterval:
    item = _object(value, {"start", "end"})
    return CoverageInterval(Instant.parse(item["start"]), Instant.parse(item["end"]))


def _decode_projection(payload: Any, samples_evidence: Any) -> SupervisedProjection:
    item = _object(payload, {
        "identity_domain", "model_version", "fold", "embargo", "selection_policy",
        "lockbox_identity", "samples", "rejections", "projection_identity",
    })
    if type(item["samples"]) is not list or type(samples_evidence) is not list:
        raise ValueError("samples must be arrays")
    if len(item["samples"]) != len(samples_evidence):
        raise ValueError("sample evidence does not match the samples")
    if type(item["rejections"]) is not list:
        raise ValueError("rejections must be an array")
    fold = _object(item["fold"], {"fold_index", "train", "test"})
    selection = _object(item["selection_policy"], {"identity", "payload"})
    policy_payload = _object(selection["payload"], {
        "identity_domain", "policy_key", "semantic_version", "implementation_code_identity",
        "feature_definition_ids", "require_labeled_targets", "sample_uniqueness_version", "notes",
    })
    policy = SupervisedSelectionPolicy(
        policy_key=policy_payload["policy_key"],
        semantic_version=policy_payload["semantic_version"],
        implementation_code_identity=policy_payload["implementation_code_identity"],
        feature_definition_ids=policy_payload["feature_definition_ids"],
        require_labeled_targets=policy_payload["require_labeled_targets"],
        sample_uniqueness_version=policy_payload["sample_uniqueness_version"],
        notes=policy_payload["notes"],
    )
    _require_canonical(policy_payload, policy.stable_dict())
    if selection["identity"] != policy.identity:
        raise ValueError("selection policy identity mismatch")
    rejections = []
    for rejection in item["rejections"]:
        entry = _object(rejection, {"sample_id", "decision_time", "reason", "detail"})
        if type(entry["detail"]) is not list:
            raise ValueError("rejection detail must be an array")
        rejections.append(SupervisedProjectionRejection(
            sample_id=entry["sample_id"], decision_time=Instant.parse(entry["decision_time"]),
            reason=entry["reason"], detail=tuple(entry["detail"]),
        ))
    projection = SupervisedProjection(
        fold=WalkForwardFold(
            fold_index=fold["fold_index"], train=_interval(fold["train"]), test=_interval(fold["test"]),
        ),
        embargo=Embargo(_object(item["embargo"], {"duration_ns"})["duration_ns"]),
        selection_policy=policy,
        samples=tuple(
            _decode_sample(sample, proof) for sample, proof in zip(item["samples"], samples_evidence)
        ),
        rejections=tuple(rejections),
        lockbox_identity=item["lockbox_identity"],
    )
    return projection


def _decode_policy(payload: Any) -> SupervisedTrainingPolicy:
    item = _object(payload, {
        "identity_domain", "policy_key", "semantic_version", "implementation_code_identity",
        "model_family", "declared_class_labels",
    })
    labels = item["declared_class_labels"]
    if labels is not None and type(labels) is not list:
        raise ValueError("declared class labels must be an array or null")
    policy = SupervisedTrainingPolicy(
        policy_key=item["policy_key"], semantic_version=item["semantic_version"],
        implementation_code_identity=item["implementation_code_identity"],
        model_family=item["model_family"],
        declared_class_labels=None if labels is None else tuple(labels),
    )
    return policy


def decode_train_evaluate_request(payload: Mapping[str, Any]) -> DecodedTrainEvaluate:
    """Rebuild the owned domain values, rejecting every mismatch before execution."""

    try:
        body = _object(payload, {
            "identity_domain", "projection", "run_identity", "policy", "register", "reconstruction",
        })
        if body["identity_domain"] != SUPERVISED_TRAIN_EVALUATE_REQUEST_IDENTITY_DOMAIN:
            raise ValueError("unsupported training request")
        if type(body["register"]) is not bool:
            raise ValueError("register must be a boolean")
        reconstruction = _object(body["reconstruction"], {"version", "run_identity", "samples"})
        if reconstruction["version"] != J13_RECONSTRUCTION_VERSION:
            raise ValueError("unsupported reconstruction version")
        run_identity_payload = _object(body["run_identity"], {"run_spec_identity", "execution_id"})
        run_identity = _decode_run_identity(
            reconstruction["run_identity"], run_identity_payload["execution_id"],
        )
        request = SupervisedTrainEvaluateRequest(
            projection=_decode_projection(body["projection"], reconstruction["samples"]),
            run_identity=run_identity,
            policy=_decode_policy(body["policy"]),
        )
        # The whole canonical payload, as an independent owner computes it.
        expected = request.canonical_payload()
        _require_canonical(
            {key: body[key] for key in expected}, expected,
        )
        return DecodedTrainEvaluate(request, body["register"])
    except Exception as exc:  # noqa: BLE001 - decoding is pure: any failure is an invalid request
        raise ConsumerApiError(
            ConsumerErrorCode.INVALID_REQUEST, "the request is not valid", request_identity=None,
        ) from exc


def encode_training_result(result: Any) -> Mapping[str, Any]:
    """The existing ``SupervisedTrainingRunResult`` payload and identities, unchanged."""
    return result.stable_dict()


# -- the opt-in route ----------------------------------------------------------------------------


def _register_scope_in(body: Mapping[str, Any]) -> frozenset[str]:
    """Total over any raw body: the registration grant is named by exactly ``register: true``."""
    return frozenset({J02_TRAINING_REGISTER_SCOPE}) if body.get("register") is True else frozenset()


def _register_scope_of(decoded: DecodedTrainEvaluate) -> frozenset[str]:
    return frozenset({J02_TRAINING_REGISTER_SCOPE}) if decoded.register else frozenset()


def training_family_handlers(
    repository_provider: RepositoryProvider | None = None,
) -> dict[tuple[str, str, str], J02FamilyHandler]:
    """Explicitly compose J13 into the mTLS server; no other family is installed.

    ``repository_provider`` yields an Experiment repository per registering
    request (its own connection, closed on exit). Without it, ``register: true``
    is ``invalid_request`` / ``registration_unavailable``: the server decides
    where registration may happen, never the request.
    """

    if repository_provider is not None and not callable(repository_provider):
        raise TypeError("repository_provider must be callable")

    def execute(decoded: DecodedTrainEvaluate) -> Mapping[str, Any]:
        if not decoded.register:
            return encode_training_result(execute_supervised_train_evaluate(decoded.request))
        if repository_provider is None:
            raise ConsumerApiError(
                ConsumerErrorCode.INVALID_REQUEST, "the request is not valid",
                context={"reason": _REPOSITORY_UNAVAILABLE},
                request_identity=decoded.request.request_identity,
            )
        with repository_provider() as repository:
            return encode_training_result(SupervisedTrainingConsumerService(repository).execute(decoded.request))

    return {
        (J02_TRAINING_FAMILY, J02_TRAINING_REQUEST_VERSION, TRAIN_EVALUATE_OPERATION): J02FamilyHandler(
            J02_TRAINING_EVALUATE_SCOPE, J02_TRAINING_RESPONSE_VERSION,
            decode_train_evaluate_request, execute,
            additional_scopes=_register_scope_of,
            pre_decode_scopes=_register_scope_in,
        ),
    }


__all__ = [
    "DecodedTrainEvaluate",
    "J02_TRAINING_EVALUATE_SCOPE",
    "J02_TRAINING_FAMILY",
    "J02_TRAINING_REGISTER_SCOPE",
    "J02_TRAINING_REQUEST_VERSION",
    "J02_TRAINING_RESPONSE_VERSION",
    "J13_RECONSTRUCTION_VERSION",
    "RequestTooLarge",
    "TRAIN_EVALUATE_OPERATION",
    "decode_train_evaluate_request",
    "encode_train_evaluate_message",
    "encode_train_evaluate_request",
    "encode_training_result",
    "training_family_handlers",
]
