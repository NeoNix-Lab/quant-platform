"""J10's in-process, transport-neutral Strategy composition seam."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib

from ..data.models import Instant
from ..strategy import (
    StrategyCompositionResult,
    StrategyError,
    StrategyInput,
    StrategySpec,
    compose_decision,
)
from .market_data import ConsumerApiError, ConsumerErrorCode
from quant_platform.canonical import canonical_bytes


STRATEGY_COMPOSE_REQUEST_IDENTITY_DOMAIN = "strategy-compose-request-v1"


@dataclass(frozen=True, slots=True)
class StrategyComposeRequest:
    """The complete semantic input to the existing ``compose_decision`` operation."""

    strategy_spec: StrategySpec
    strategy_identity: str
    inputs: tuple[StrategyInput, ...]
    decision_time: Instant | str
    instrument: str

    def canonical_payload(self) -> dict[str, object]:
        return {
            "identity_domain": STRATEGY_COMPOSE_REQUEST_IDENTITY_DOMAIN,
            "strategy_spec": self.strategy_spec.canonical_payload(),
            "strategy_identity": self.strategy_identity,
            "inputs": [item.stable_dict() for item in sorted(self.inputs, key=lambda item: item.key)],
            "decision_time": self.decision_time.isoformat(),
            "instrument": self.instrument,
        }

    @property
    def request_identity(self) -> str:
        payload = canonical_bytes(self.canonical_payload(), profile="sorted-compact-ascii-v1", allow_nan=True)
        return f"{STRATEGY_COMPOSE_REQUEST_IDENTITY_DOMAIN}:sha256:{hashlib.sha256(payload).hexdigest()}"


def execute_strategy_compose(request: StrategyComposeRequest) -> StrategyCompositionResult:
    """Compose a decision and translate semantic refusal without exposing domain errors."""
    try:
        normalized = _normalize_request(request)
        if any(item.available_at > normalized.decision_time for item in normalized.inputs):
            raise ConsumerApiError(
                ConsumerErrorCode.NO_COVERAGE,
                "the requested interval is not fully covered",
                request_identity=normalized.request_identity,
            )
        return compose_decision(
            normalized.strategy_spec,
            normalized.inputs,
            decision_time=normalized.decision_time,
            instrument=normalized.instrument,
        )
    except ConsumerApiError:
        raise
    except (StrategyError, TypeError, ValueError) as exc:
        raise ConsumerApiError(
            ConsumerErrorCode.INVALID_REQUEST,
            "the request is not valid",
            request_identity=None,
        ) from exc


def _normalize_request(request: StrategyComposeRequest) -> StrategyComposeRequest:
    if not isinstance(request, StrategyComposeRequest):
        raise TypeError("request must be a StrategyComposeRequest")
    if not isinstance(request.strategy_spec, StrategySpec):
        raise TypeError("strategy_spec must be a StrategySpec")
    if request.strategy_identity != request.strategy_spec.strategy_identity:
        raise ValueError("strategy_identity must match strategy_spec")
    if isinstance(request.inputs, (str, bytes)) or not isinstance(request.inputs, tuple):
        raise TypeError("inputs must be a tuple of StrategyInput")
    if any(not isinstance(item, StrategyInput) for item in request.inputs):
        raise TypeError("inputs must contain StrategyInput values")
    if not isinstance(request.instrument, str) or not request.instrument.strip():
        raise ValueError("instrument must be a non-empty string")
    return StrategyComposeRequest(
        strategy_spec=request.strategy_spec,
        strategy_identity=request.strategy_identity,
        inputs=request.inputs,
        decision_time=Instant.parse(request.decision_time),
        instrument=request.instrument,
    )


__all__ = [
    "STRATEGY_COMPOSE_REQUEST_IDENTITY_DOMAIN",
    "StrategyComposeRequest",
    "execute_strategy_compose",
]
