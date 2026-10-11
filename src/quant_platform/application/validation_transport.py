"""Opt-in J12 carriage over authenticated J02 (issue #338).

The maintainer-approved J12-only representation replaces canonical float leaves
with ``{"binary64": float.hex()}`` in a transport ``payload`` wrapper. Hex text
is exact, including signed zero and subnormals; ordinary strings stay strings.
The wrapper identities attest the reconstructed canonical payload, never the
tagged wire bytes. Shared parsing and other families are unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import math
from typing import Any

from quant_platform.canonical import canonical_bytes
from quant_platform.data.models import CoverageInterval, Instant, InvalidRequest
from quant_platform.validation import (
    CandidateClassificationResult, ComparableTrialPanel, DSRResult,
    DependencyEvidence, EffectiveTrialCountEvidence, Embargo, PBOResult,
    ValidationCandidate, WalkForwardFold, WalkForwardScheduleSpec,
)

from .api_transport_server import J02FamilyHandler
from .market_data import ConsumerApiError, ConsumerErrorCode
from .validation_consumer import (
    ValidationBuildFoldsRequest, ValidationClassifyCandidateRequest,
    ValidationEvaluateDsrRequest, ValidationEvaluatePboRequest,
    execute_validation_build_folds, execute_validation_classify_candidate,
    execute_validation_evaluate_dsr, execute_validation_evaluate_pbo,
)

J02_VALIDATION_FAMILY = "j02-validation-v1"
J02_VALIDATION_REQUEST_VERSION = J02_VALIDATION_FAMILY + "-request-v1"
J02_VALIDATION_RESPONSE_VERSION = J02_VALIDATION_FAMILY + "-response-v1"


def _object(value: Any, keys: set[str]) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("canonical object fields do not match")
    return value


def _wire(value: Any) -> Any:
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("finite binary64 required")
        return {"binary64": value.hex()}
    if type(value) is list:
        return [_wire(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: _wire(item) for key, item in value.items()}
    if value is None or type(value) in (str, bool, int):
        return value
    raise ValueError("canonical JSON data required")


def _unwire(value: Any) -> Any:
    if type(value) is dict:
        # A panel may contain a single trial literally named "binary64";
        # its return series is an array, not a scalar tag.
        if set(value) == {"binary64"} and type(value["binary64"]) is str:
            text = value["binary64"]
            if type(text) is not str:
                raise ValueError("binary64 hex text required")
            number = float.fromhex(text)
            if not math.isfinite(number) or number.hex() != text:
                raise ValueError("canonical finite binary64 required")
            return number
        if not all(type(key) is str for key in value):
            raise ValueError("JSON object keys required")
        return {key: _unwire(item) for key, item in value.items()}
    if type(value) is list:
        return [_unwire(item) for item in value]
    if value is None or type(value) in (str, bool, int):
        return value
    raise ValueError("lossless J12 JSON required")


def _bytes(value: Any) -> bytes:
    return canonical_bytes(value, profile="sorted-compact-ascii-v1", allow_nan=False)


def encode_validation_request(request: Any) -> dict[str, Any]:
    """Encode one of the four existing semantic requests, outside its identity."""
    if type(request) not in (
        ValidationBuildFoldsRequest, ValidationClassifyCandidateRequest,
        ValidationEvaluateDsrRequest, ValidationEvaluatePboRequest,
    ):
        raise TypeError("established J12 request required")
    return {"payload": _wire(request.canonical_payload()), "request_identity": request.request_identity}


def _interval(value: Any) -> CoverageInterval:
    item = _object(value, {"start", "end"})
    return CoverageInterval(Instant.parse(item["start"]), Instant.parse(item["end"]))


def _panel(value: Any) -> ComparableTrialPanel:
    item = _object(value, {
        "identity_domain", "population_id", "return_semantics_id",
        "trial_ids", "observation_ids", "returns",
    })
    if type(item["observation_ids"]) is not list:
        raise ValueError("observation identities must be an array")
    observations = []
    for observation in item["observation_ids"]:
        if type(observation) is not list or len(observation) != 2:
            raise ValueError("typed observation identity required")
        kind, text = observation
        if type(text) is not str or kind not in ("str", "int"):
            raise ValueError("typed observation identity required")
        observations.append(int(text) if kind == "int" else text)
    return ComparableTrialPanel(
        item["population_id"], item["trial_ids"], tuple(observations),
        item["returns"], item["return_semantics_id"],
    )


def _request(operation: str, value: Any) -> Any:
    if operation == "build_folds":
        item = _object(value, {"identity_domain", "schedule"})
        schedule = _object(item["schedule"], {
            "start", "initial_train_duration", "test_duration", "fold_count",
        })
        return ValidationBuildFoldsRequest(WalkForwardScheduleSpec(**schedule))
    if operation == "classify_candidate":
        item = _object(value, {"identity_domain", "fold", "embargo", "candidate"})
        fold = _object(item["fold"], {"fold_index", "train", "test"})
        embargo = _object(item["embargo"], {"duration_ns"})
        candidate = _object(item["candidate"], {"d", "dependencies"})
        if type(candidate["dependencies"]) is not list:
            raise ValueError("dependencies must be an array")
        dependencies = []
        for raw in candidate["dependencies"]:
            dependency = dict(_object(raw, {
                "identity", "cutoff_role", "sufficient", "support",
                "required_maturity", "lifecycle", "causal_available_at",
                "observed_available_at", "observed_finalized_at",
                "contemporaneous_version_proven", "detail",
            }))
            if dependency["support"] is not None:
                dependency["support"] = _interval(dependency["support"])
            dependencies.append(DependencyEvidence(**dependency))
        return ValidationClassifyCandidateRequest(
            WalkForwardFold(fold["fold_index"], _interval(fold["train"]), _interval(fold["test"])),
            Embargo(**embargo), ValidationCandidate(candidate["d"], tuple(dependencies)),
        )
    if operation == "evaluate_dsr":
        item = _object(value, {"identity_domain", "panel", "effective_trial_count"})
        evidence = _object(item["effective_trial_count"], {"k_eff", "evidence_id"})
        return ValidationEvaluateDsrRequest(_panel(item["panel"]), EffectiveTrialCountEvidence(**evidence))
    if operation == "evaluate_pbo":
        item = _object(value, {"identity_domain", "panel", "block_count"})
        # The owning executor, not this codec, admits/refuses the PBO budget.
        return ValidationEvaluatePboRequest(_panel(item["panel"]), item["block_count"])
    raise ValueError("unsupported J12 operation")


def decode_validation_request(operation: str, body: Mapping[str, Any]) -> Any:
    """Reconstruct owned values and verify canonical fields/identity before work."""
    try:
        item = _object(body, {"payload", "request_identity"})
        request = _request(operation, _unwire(item["payload"]))
        # Byte equality catches constructor normalization, identity-domain
        # mutations, bool/int confusion and signed-zero/type changes alike.
        if _bytes(body) != _bytes(encode_validation_request(request)):
            raise ValueError("canonical request or identity mismatch")
        return request
    except (InvalidRequest, ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as exc:
        raise ConsumerApiError(ConsumerErrorCode.INVALID_REQUEST, "the request is not valid") from exc


def encode_validation_result(result: Any) -> dict[str, Any]:
    """Carry existing result payloads and, where defined, their existing identity."""
    if isinstance(result, (DSRResult, PBOResult)):
        payload, identity = result.canonical_payload(), result.result_id
    elif isinstance(result, CandidateClassificationResult):
        payload, identity = result.stable_dict(), None
    elif type(result) is tuple and all(isinstance(fold, WalkForwardFold) for fold in result):
        payload, identity = [fold.stable_dict() for fold in result], None
    else:
        raise TypeError("established J12 result required")
    return {"payload": _wire(payload), "result_identity": identity}


def decode_validation_result(body: Mapping[str, Any]) -> Any:
    """Recover canonical result data without applying any client-side metric logic."""
    item = _object(body, {"payload", "result_identity"})
    payload = _unwire(item["payload"])
    domain = payload.get("identity_domain") if type(payload) is dict else None
    identity = (
        f"{domain}:sha256:{hashlib.sha256(_bytes(payload)).hexdigest()}"
        if domain in ("dsr-l-v1", "cscv-pbo-v1") else None
    )
    if item["result_identity"] != identity:
        raise ValueError("result identity mismatch")
    return payload


def validation_family_handlers() -> dict[tuple[str, str, str], J02FamilyHandler]:
    """Explicit server composition only; no host, Job, or other family is added."""
    executors = {
        "build_folds": execute_validation_build_folds,
        "classify_candidate": execute_validation_classify_candidate,
        "evaluate_dsr": execute_validation_evaluate_dsr,
        "evaluate_pbo": execute_validation_evaluate_pbo,
    }
    return {
        (J02_VALIDATION_FAMILY, J02_VALIDATION_REQUEST_VERSION, operation): J02FamilyHandler(
            "j02.validation.evaluate", J02_VALIDATION_RESPONSE_VERSION,
            lambda body, operation=operation: decode_validation_request(operation, body),
            lambda request, execute=execute: encode_validation_result(execute(request)),
        ) for operation, execute in executors.items()
    }
