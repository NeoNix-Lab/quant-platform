"""J12's finite, transport-neutral Validation Consumer-API seams."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Any

from quant_platform.canonical import canonical_bytes
from quant_platform.data.models import InvalidRequest
from quant_platform.validation import (
    CandidateClassificationResult,
    ComparableTrialPanel,
    DSRResult,
    EffectiveTrialCountEvidence,
    Embargo,
    PBOResult,
    RobustnessError,
    ValidationCandidate,
    WalkForwardFold,
    WalkForwardScheduleSpec,
    build_walk_forward_folds,
    classify_candidate,
    evaluate_dsr_v1,
    evaluate_pbo_v1,
)

from .market_data import ConsumerApiError, ConsumerErrorCode


VALIDATION_BUILD_FOLDS_REQUEST_IDENTITY_DOMAIN = "validation-build-folds-request-v1"
VALIDATION_CLASSIFY_CANDIDATE_REQUEST_IDENTITY_DOMAIN = "validation-classify-candidate-request-v1"
VALIDATION_EVALUATE_DSR_REQUEST_IDENTITY_DOMAIN = "validation-evaluate-dsr-request-v1"
VALIDATION_EVALUATE_PBO_REQUEST_IDENTITY_DOMAIN = "validation-evaluate-pbo-request-v1"

# ADR-0065 Amendment 1 bounds the only synchronous PBO operation.  Larger
# PBO requests require a separately designed J03 dispatch/result boundary.
MAX_SYNC_PBO_BLOCK_COUNT = 16
MAX_SYNC_PBO_WORK = 5_000_000
_PBO_SYNC_BUDGET_EXCEEDED = "pbo_sync_budget_exceeded"


@dataclass(frozen=True, slots=True)
class ValidationBuildFoldsRequest:
    schedule: WalkForwardScheduleSpec

    def canonical_payload(self) -> dict[str, Any]:
        return {"identity_domain": VALIDATION_BUILD_FOLDS_REQUEST_IDENTITY_DOMAIN, "schedule": self.schedule.stable_dict()}

    @property
    def request_identity(self) -> str:
        return _request_identity(VALIDATION_BUILD_FOLDS_REQUEST_IDENTITY_DOMAIN, self.canonical_payload())


@dataclass(frozen=True, slots=True)
class ValidationClassifyCandidateRequest:
    fold: WalkForwardFold
    embargo: Embargo
    candidate: ValidationCandidate

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": VALIDATION_CLASSIFY_CANDIDATE_REQUEST_IDENTITY_DOMAIN,
            "fold": self.fold.stable_dict(),
            "embargo": self.embargo.stable_dict(),
            "candidate": _candidate_payload(self.candidate),
        }

    @property
    def request_identity(self) -> str:
        return _request_identity(VALIDATION_CLASSIFY_CANDIDATE_REQUEST_IDENTITY_DOMAIN, self.canonical_payload())


@dataclass(frozen=True, slots=True)
class ValidationEvaluateDsrRequest:
    panel: ComparableTrialPanel
    effective_trial_count: EffectiveTrialCountEvidence

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": VALIDATION_EVALUATE_DSR_REQUEST_IDENTITY_DOMAIN,
            "panel": self.panel.canonical_payload(),
            "effective_trial_count": {
                "k_eff": self.effective_trial_count.k_eff,
                "evidence_id": self.effective_trial_count.evidence_id,
            },
        }

    @property
    def request_identity(self) -> str:
        return _request_identity(VALIDATION_EVALUATE_DSR_REQUEST_IDENTITY_DOMAIN, self.canonical_payload())


@dataclass(frozen=True, slots=True)
class ValidationEvaluatePboRequest:
    panel: ComparableTrialPanel
    block_count: int

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": VALIDATION_EVALUATE_PBO_REQUEST_IDENTITY_DOMAIN,
            "panel": self.panel.canonical_payload(),
            "block_count": self.block_count,
        }

    @property
    def request_identity(self) -> str:
        return _request_identity(VALIDATION_EVALUATE_PBO_REQUEST_IDENTITY_DOMAIN, self.canonical_payload())


def execute_validation_build_folds(request: ValidationBuildFoldsRequest) -> tuple[WalkForwardFold, ...]:
    try:
        if not isinstance(request, ValidationBuildFoldsRequest) or not isinstance(request.schedule, WalkForwardScheduleSpec):
            raise TypeError("request must carry a WalkForwardScheduleSpec")
        return build_walk_forward_folds(request.schedule)
    except (InvalidRequest, TypeError, ValueError, OverflowError) as error:
        raise _invalid_request(error) from error


def execute_validation_classify_candidate(request: ValidationClassifyCandidateRequest) -> CandidateClassificationResult:
    try:
        if not isinstance(request, ValidationClassifyCandidateRequest):
            raise TypeError("request must be a ValidationClassifyCandidateRequest")
        if not isinstance(request.fold, WalkForwardFold) or not isinstance(request.embargo, Embargo) or not isinstance(request.candidate, ValidationCandidate):
            raise TypeError("request must carry established Validation evidence")
        return classify_candidate(request.fold, request.embargo, request.candidate)
    except (InvalidRequest, TypeError, ValueError) as error:
        raise _invalid_request(error) from error


def execute_validation_evaluate_dsr(request: ValidationEvaluateDsrRequest) -> DSRResult:
    try:
        if not isinstance(request, ValidationEvaluateDsrRequest) or not isinstance(request.panel, ComparableTrialPanel) or not isinstance(request.effective_trial_count, EffectiveTrialCountEvidence):
            raise TypeError("request must carry a ComparableTrialPanel and EffectiveTrialCountEvidence")
        return evaluate_dsr_v1(request.panel, request.effective_trial_count)
    except (RobustnessError, TypeError, ValueError) as error:
        raise _invalid_request(error) from error


def execute_validation_evaluate_pbo(request: ValidationEvaluatePboRequest) -> PBOResult:
    try:
        if not isinstance(request, ValidationEvaluatePboRequest) or not isinstance(request.panel, ComparableTrialPanel):
            raise TypeError("request must carry a ComparableTrialPanel")
        _require_synchronous_pbo_budget(request)
        return evaluate_pbo_v1(request.panel, request.block_count)
    except (RobustnessError, TypeError, ValueError) as error:
        raise _invalid_request(error) from error


def _require_synchronous_pbo_budget(request: ValidationEvaluatePboRequest) -> None:
    """Refuse work outside ADR-0065 Amendment 1 before CSCV materialization."""
    block_count = request.block_count
    # Leave every established evaluator precondition to ``evaluate_pbo_v1`` so
    # its existing invalid-request translation remains unchanged.  In
    # particular, do not call ``math.comb`` for arbitrarily large integers.
    if type(block_count) is not int or block_count < 4 or block_count % 2:
        return
    if block_count > MAX_SYNC_PBO_BLOCK_COUNT:
        raise _pbo_sync_budget_error(request)

    work = math.comb(block_count, block_count // 2) * request.panel.trial_count * request.panel.observation_count
    if work > MAX_SYNC_PBO_WORK:
        raise _pbo_sync_budget_error(request)


def _pbo_sync_budget_error(request: ValidationEvaluatePboRequest) -> ConsumerApiError:
    return ConsumerApiError(
        ConsumerErrorCode.INVALID_REQUEST,
        "the request is not valid",
        context={"reason": _PBO_SYNC_BUDGET_EXCEEDED},
        request_identity=request.request_identity,
    )


def _candidate_payload(candidate: ValidationCandidate) -> dict[str, Any]:
    return {
        "d": candidate.d.isoformat(),
        "dependencies": [
            {
                "identity": dependency.identity,
                "cutoff_role": dependency.cutoff_role.value,
                "sufficient": dependency.sufficient,
                "support": None if dependency.support is None else dependency.support.stable_dict(),
                "required_maturity": None if dependency.required_maturity is None else dependency.required_maturity.value,
                "lifecycle": None if dependency.lifecycle is None else dependency.lifecycle.value,
                "causal_available_at": None if dependency.causal_available_at is None else dependency.causal_available_at.isoformat(),
                "observed_available_at": None if dependency.observed_available_at is None else dependency.observed_available_at.isoformat(),
                "observed_finalized_at": None if dependency.observed_finalized_at is None else dependency.observed_finalized_at.isoformat(),
                "contemporaneous_version_proven": dependency.contemporaneous_version_proven,
                "detail": dependency.detail,
            }
            for dependency in candidate.dependencies
        ],
    }


def _request_identity(domain: str, payload: dict[str, Any]) -> str:
    encoded = canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=False)
    return f"{domain}:sha256:{hashlib.sha256(encoded).hexdigest()}"


def _invalid_request(error: Exception) -> ConsumerApiError:
    return ConsumerApiError(ConsumerErrorCode.INVALID_REQUEST, "the request is not valid")


__all__ = [
    "VALIDATION_BUILD_FOLDS_REQUEST_IDENTITY_DOMAIN",
    "VALIDATION_CLASSIFY_CANDIDATE_REQUEST_IDENTITY_DOMAIN",
    "VALIDATION_EVALUATE_DSR_REQUEST_IDENTITY_DOMAIN",
    "VALIDATION_EVALUATE_PBO_REQUEST_IDENTITY_DOMAIN",
    "MAX_SYNC_PBO_BLOCK_COUNT",
    "MAX_SYNC_PBO_WORK",
    "ValidationBuildFoldsRequest",
    "ValidationClassifyCandidateRequest",
    "ValidationEvaluateDsrRequest",
    "ValidationEvaluatePboRequest",
    "execute_validation_build_folds",
    "execute_validation_classify_candidate",
    "execute_validation_evaluate_dsr",
    "execute_validation_evaluate_pbo",
]
