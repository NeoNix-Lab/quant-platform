"""J10's in-process, transport-neutral Strategy composition seam."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from ..data.models import Instant
from ..strategy import (
    StrategyCompositionResult,
    StrategyError,
    StrategyInput,
    StrategySpec,
    compose_decision,
)
from .market_data import ConsumerApiError, ConsumerErrorCode


STRATEGY_COMPOSE_REQUEST_IDENTITY_DOMAIN = "strategy-compose-request-v1"


@dataclass(frozen=True, slots=True)
class StrategyComposeRequest:
    """The complete semantic input to the existing ``compose_decision`` operation."""

    strategy_spec: StrategySpec
    strategy_identity: str
    inputs: tuple[StrategyInput, ...]
    decision_time: Instant | str
    instrument: str

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_spec, StrategySpec):
            raise TypeError("strategy_spec must be a StrategySpec")
        if self.strategy_identity != self.strategy_spec.strategy_identity:
            raise ValueError("strategy_identity must match strategy_spec")
        if isinstance(self.inputs, (str, bytes)) or not isinstance(self.inputs, tuple):
            raise TypeError("inputs must be a tuple of StrategyInput")
        if any(not isinstance(item, StrategyInput) for item in self.inputs):
            raise TypeError("inputs must contain StrategyInput values")
        object.__setattr__(self, "decision_time", Instant.parse(self.decision_time))
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise ValueError("instrument must be a non-empty string")

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
        payload = json.dumps(
            self.canonical_payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        return f"{STRATEGY_COMPOSE_REQUEST_IDENTITY_DOMAIN}:sha256:{hashlib.sha256(payload).hexdigest()}"


def execute_strategy_compose(request: StrategyComposeRequest) -> StrategyCompositionResult:
    """Compose a decision and translate semantic refusal without exposing domain errors."""
    try:
        if not isinstance(request, StrategyComposeRequest):
            raise TypeError("request must be a StrategyComposeRequest")
        if any(item.available_at > request.decision_time for item in request.inputs):
            raise ConsumerApiError(
                ConsumerErrorCode.NO_COVERAGE,
                "the requested interval is not fully covered",
                request_identity=request.request_identity,
            )
        return compose_decision(
            request.strategy_spec,
            request.inputs,
            decision_time=request.decision_time,
            instrument=request.instrument,
        )
    except ConsumerApiError:
        raise
    except (StrategyError, TypeError, ValueError) as exc:
        raise ConsumerApiError(
            ConsumerErrorCode.INVALID_REQUEST,
            "the request is not valid",
            request_identity=request.request_identity if isinstance(request, StrategyComposeRequest) else None,
        ) from exc


__all__ = [
    "STRATEGY_COMPOSE_REQUEST_IDENTITY_DOMAIN",
    "StrategyComposeRequest",
    "execute_strategy_compose",
]
