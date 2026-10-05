"""J13's bounded, transport-neutral supervised Training Consumer-API seam."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from quant_platform.experiments import ExperimentRepository
from quant_platform.learning import (
    LearningError,
    ProjectionSide,
    SupervisedProjection,
    SupervisedTrainingError,
    SupervisedTrainingPolicy,
    SupervisedTrainingRunResult,
    record_supervised_training_run,
    train_evaluate_supervised_baseline,
)
from quant_platform.experiments import RunIdentity

from .market_data import ConsumerApiError, ConsumerErrorCode


SUPERVISED_TRAIN_EVALUATE_REQUEST_IDENTITY_DOMAIN = "supervised-train-evaluate-request-v1"

# ADR-0065 Amendment 2 bounds the only synchronous training operation.  Larger
# projections require a separately designed J03 dispatch/result boundary.
MAX_SYNC_TRAINING_WORK = 20_000
MAX_SYNC_TRAINING_TEST_SAMPLES = 5_000
_TRAINING_SYNC_BUDGET_EXCEEDED = "training_sync_budget_exceeded"


@dataclass(frozen=True, slots=True)
class SupervisedTrainEvaluateRequest:
    """Complete semantic input to the established supervised baseline."""

    projection: SupervisedProjection
    run_identity: RunIdentity
    policy: SupervisedTrainingPolicy

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": SUPERVISED_TRAIN_EVALUATE_REQUEST_IDENTITY_DOMAIN,
            "projection": self.projection.stable_dict(),
            "run_identity": self.run_identity.stable_dict(),
            "policy": self.policy.stable_dict(),
        }

    @property
    def request_identity(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
        ).encode("utf-8")
        return f"{SUPERVISED_TRAIN_EVALUATE_REQUEST_IDENTITY_DOMAIN}:sha256:{hashlib.sha256(encoded).hexdigest()}"


class SupervisedTrainingConsumerService:
    """Server-composed J13 execution with an optional owned Experiment repository."""

    def __init__(self, repository: ExperimentRepository | None = None):
        if repository is not None and not isinstance(repository, ExperimentRepository):
            raise TypeError("repository must be an ExperimentRepository when supplied")
        self._repository = repository

    def execute(self, request: SupervisedTrainEvaluateRequest) -> SupervisedTrainingRunResult:
        try:
            _normalize_request(request)
            _require_synchronous_training_budget(request)
            result = train_evaluate_supervised_baseline(
                projection=request.projection,
                run_identity=request.run_identity,
                policy=request.policy,
            )
        except ConsumerApiError:
            raise
        except (LearningError, SupervisedTrainingError, TypeError, ValueError) as error:
            raise _invalid_request(error) from error

        if self._repository is not None:
            try:
                record_supervised_training_run(self._repository, result)
            except Exception as error:
                raise ConsumerApiError(
                    ConsumerErrorCode.INTEGRITY_FAILURE,
                    "the platform cannot produce a trustworthy result",
                    request_identity=request.request_identity,
                ) from error
        return result


def execute_supervised_train_evaluate(request: SupervisedTrainEvaluateRequest) -> SupervisedTrainingRunResult:
    """Execute the bounded, non-registering J13 operation."""
    return SupervisedTrainingConsumerService().execute(request)


def _normalize_request(request: SupervisedTrainEvaluateRequest) -> None:
    if not isinstance(request, SupervisedTrainEvaluateRequest):
        raise TypeError("request must be a SupervisedTrainEvaluateRequest")
    if not isinstance(request.projection, SupervisedProjection):
        raise TypeError("request must carry a SupervisedProjection")
    if not isinstance(request.run_identity, RunIdentity):
        raise TypeError("request must carry a RunIdentity")
    if not isinstance(request.policy, SupervisedTrainingPolicy):
        raise TypeError("request must carry a SupervisedTrainingPolicy")


def _require_synchronous_training_budget(request: SupervisedTrainEvaluateRequest) -> None:
    train_count = len(request.projection.samples_for_side(ProjectionSide.TRAIN))
    test_count = len(request.projection.samples_for_side(ProjectionSide.TEST))
    work = (train_count + test_count) * len(request.projection.feature_schema)
    if test_count > MAX_SYNC_TRAINING_TEST_SAMPLES or work > MAX_SYNC_TRAINING_WORK:
        raise _training_sync_budget_error(request)


def _training_sync_budget_error(request: SupervisedTrainEvaluateRequest) -> ConsumerApiError:
    return ConsumerApiError(
        ConsumerErrorCode.INVALID_REQUEST,
        "the request is not valid",
        context={"reason": _TRAINING_SYNC_BUDGET_EXCEEDED},
        request_identity=request.request_identity,
    )


def _invalid_request(error: Exception) -> ConsumerApiError:
    return ConsumerApiError(ConsumerErrorCode.INVALID_REQUEST, "the request is not valid")


__all__ = [
    "MAX_SYNC_TRAINING_TEST_SAMPLES",
    "MAX_SYNC_TRAINING_WORK",
    "SUPERVISED_TRAIN_EVALUATE_REQUEST_IDENTITY_DOMAIN",
    "SupervisedTrainEvaluateRequest",
    "SupervisedTrainingConsumerService",
    "execute_supervised_train_evaluate",
]
