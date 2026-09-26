"""StrategySpec, policy composition and DecisionIntent v1.

This package owns the G01/G02 strategy boundary only.  It does not implement
risk/sizing semantics, session calendars, cooldown evaluation, order/fill
mechanics, portfolio state, replay runtime or live execution.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable

from ..data.models import Instant


STRATEGY_SPEC_IDENTITY_DOMAIN = "strategy-spec-v1"
DECISION_INTENT_IDENTITY_DOMAIN = "decision-intent-v1"
NO_DECISION_IDENTITY_DOMAIN = "strategy-no-decision-v1"
STRATEGY_SPEC_MODEL_VERSION = "1"
DECISION_INTENT_MODEL_VERSION = "1"
NO_DECISION_MODEL_VERSION = "1"

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class StrategyError(ValueError):
    """A Strategy/DecisionIntent v1 semantic value violates the contract."""


class Direction(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class SignalCombinationMode(StrEnum):
    ALL = "ALL"
    ANY = "ANY"


class Urgency(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"


@runtime_checkable
class IdentityBackedPolicy(Protocol):
    """Typed policy interface for slots whose semantics are outside #141."""

    @property
    def identity(self) -> str:
        """Deterministic policy identity."""

    def stable_dict(self) -> Mapping[str, Any]:
        """Canonical policy payload or identity reference."""


RiskPolicy = IdentityBackedPolicy
SizingPolicy = IdentityBackedPolicy
ExecutionPolicy = IdentityBackedPolicy
SessionPolicy = IdentityBackedPolicy
CooldownPolicy = IdentityBackedPolicy


def _non_empty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StrategyError(f"{field} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise StrategyError(f"{field} must not contain control characters")
    return text


def _key(value: Any, field: str) -> str:
    text = _non_empty_text(value, field)
    if not _KEY_RE.fullmatch(text):
        raise StrategyError(f"{field} must be a governed canonical key")
    return text


def _semantic_version(value: Any, field: str = "semantic_version") -> str:
    text = _non_empty_text(str(value) if type(value) is int else value, field)
    if not text.isdigit() or text.startswith("0"):
        raise StrategyError(f"{field} must be an explicit positive version")
    return text


def _decimal(value: Any, field: str, *, allow_zero: bool = True) -> Decimal:
    if isinstance(value, bool):
        raise StrategyError(f"{field} must be numeric")
    if isinstance(value, Decimal):
        text = str(value)
    elif type(value) is int:
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise StrategyError(f"{field} must be a Decimal, integer or decimal string")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise StrategyError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise StrategyError(f"{field} must be finite")
    if result < 0 or (result == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise StrategyError(f"{field} must be {qualifier}")
    return result


def _decimal_string(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Instant):
        return value.isoformat()
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, Decimal):
        return _decimal_string(value)
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str) or not key:
                raise StrategyError("canonical mapping keys must be non-empty strings")
            result[key] = _canonical_value(item)
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise StrategyError(f"unsupported canonical value type: {type(value).__name__}")


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        _canonical_value(payload),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _policy_payload(policy: IdentityBackedPolicy, field: str) -> dict[str, Any]:
    if policy is None:
        raise StrategyError(f"{field} is required")
    if not isinstance(policy, IdentityBackedPolicy):
        raise StrategyError(f"{field} must expose identity and stable_dict()")
    identity = _non_empty_text(policy.identity, f"{field}.identity")
    payload = policy.stable_dict()
    if not isinstance(payload, Mapping):
        raise StrategyError(f"{field}.stable_dict() must return a mapping")
    return {"identity": identity, "payload": dict(payload)}


@dataclass(frozen=True, slots=True)
class StrategyInput:
    """One bounded input available to the strategy composition step."""

    key: str
    value: Any
    available_at: Instant
    provenance: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _key(self.key, "input key"))
        object.__setattr__(self, "available_at", Instant.parse(self.available_at))
        object.__setattr__(self, "provenance", _non_empty_text(self.provenance, "provenance"))
        _canonical_value(self.value)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": _canonical_value(self.value),
            "available_at": self.available_at.isoformat(),
            "provenance": self.provenance,
        }

    @property
    def identity(self) -> str:
        return f"strategy-input-v1:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class EntryPolicy:
    policy_key: str
    direction: Direction
    signal_key: str
    confidence: Decimal | str | int = Decimal("1")
    urgency: Urgency = Urgency.NORMAL

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_key", _key(self.policy_key, "entry policy key"))
        object.__setattr__(self, "direction", Direction(self.direction))
        if self.direction is Direction.FLAT:
            raise StrategyError("entry direction cannot be FLAT")
        object.__setattr__(self, "signal_key", _key(self.signal_key, "entry signal key"))
        confidence = _decimal(self.confidence, "entry confidence")
        if confidence > Decimal("1"):
            raise StrategyError("entry confidence must be <= 1")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "urgency", Urgency(self.urgency))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "policy_type": "entry-policy-v1",
            "policy_key": self.policy_key,
            "direction": self.direction.value,
            "signal_key": self.signal_key,
            "confidence": _decimal_string(self.confidence),
            "urgency": self.urgency.value,
        }

    @property
    def identity(self) -> str:
        return f"entry-policy-v1:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class ExitPolicy:
    policy_key: str
    signal_key: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_key", _key(self.policy_key, "exit policy key"))
        object.__setattr__(self, "signal_key", _key(self.signal_key, "exit signal key"))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "policy_type": "exit-policy-v1",
            "policy_key": self.policy_key,
            "signal_key": self.signal_key,
        }

    @property
    def identity(self) -> str:
        return f"exit-policy-v1:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class PositionPolicy:
    policy_key: str
    long_target_position: Decimal | str | int
    short_target_position: Decimal | str | int
    flat_target_position: Decimal | str | int = Decimal("0")

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_key", _key(self.policy_key, "position policy key"))
        object.__setattr__(
            self,
            "long_target_position",
            _decimal(self.long_target_position, "long_target_position", allow_zero=False),
        )
        object.__setattr__(
            self,
            "short_target_position",
            -_decimal(self.short_target_position, "short_target_position", allow_zero=False),
        )
        flat = _decimal(self.flat_target_position, "flat_target_position")
        if flat != 0:
            raise StrategyError("flat_target_position must be zero")
        object.__setattr__(self, "flat_target_position", flat)

    def target_for(self, direction: Direction) -> Decimal:
        if direction is Direction.LONG:
            return self.long_target_position
        if direction is Direction.SHORT:
            return self.short_target_position
        return self.flat_target_position

    def stable_dict(self) -> dict[str, Any]:
        return {
            "policy_type": "position-policy-v1",
            "policy_key": self.policy_key,
            "long_target_position": _decimal_string(self.long_target_position),
            "short_target_position": _decimal_string(abs(self.short_target_position)),
            "flat_target_position": _decimal_string(self.flat_target_position),
        }

    @property
    def identity(self) -> str:
        return f"position-policy-v1:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class SignalCombinationPolicy:
    policy_key: str
    mode: SignalCombinationMode
    signal_keys: Iterable[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_key", _key(self.policy_key, "signal policy key"))
        object.__setattr__(self, "mode", SignalCombinationMode(self.mode))
        if isinstance(self.signal_keys, (str, bytes, bytearray)):
            raise StrategyError("signal_keys must be an iterable of keys")
        keys = tuple(_key(key, "signal key") for key in self.signal_keys)
        if not keys:
            raise StrategyError("signal combination requires at least one signal key")
        deduped = tuple(sorted(set(keys)))
        object.__setattr__(self, "signal_keys", deduped)

    def evaluate(self, inputs: Mapping[str, StrategyInput]) -> bool:
        values = [_truthy(inputs[key].value) for key in self.signal_keys if key in inputs]
        if len(values) != len(self.signal_keys):
            return False
        return all(values) if self.mode is SignalCombinationMode.ALL else any(values)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "policy_type": "signal-combination-policy-v1",
            "policy_key": self.policy_key,
            "mode": self.mode.value,
            "signal_keys": list(self.signal_keys),
        }

    @property
    def identity(self) -> str:
        return f"signal-combination-policy-v1:sha256:{_canonical_fingerprint(self.stable_dict())}"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if type(value) is int:
        return value != 0
    if isinstance(value, Decimal):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "long", "short", "exit"}:
            return True
        if normalized in {"false", "no", "0", "flat", "none", ""}:
            return False
    raise StrategyError(f"unsupported signal truth value: {value!r}")


@dataclass(frozen=True, slots=True)
class StrategySpec:
    strategy_key: str
    semantic_version: str | int
    entry_policy: EntryPolicy
    exit_policy: ExitPolicy
    position_policy: PositionPolicy
    sizing_policy: SizingPolicy
    risk_policy: RiskPolicy
    session_policy: SessionPolicy
    cooldown_policy: CooldownPolicy
    signal_combination_policy: SignalCombinationPolicy
    execution_policy: ExecutionPolicy
    notes: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "strategy_key", _key(self.strategy_key, "strategy_key"))
        object.__setattr__(self, "semantic_version", _semantic_version(self.semantic_version))
        for field_name, expected_type in (
            ("entry_policy", EntryPolicy),
            ("exit_policy", ExitPolicy),
            ("position_policy", PositionPolicy),
            ("signal_combination_policy", SignalCombinationPolicy),
        ):
            if not isinstance(getattr(self, field_name), expected_type):
                raise StrategyError(f"{field_name} must be {expected_type.__name__}")
        for field_name in (
            "sizing_policy",
            "risk_policy",
            "session_policy",
            "cooldown_policy",
            "execution_policy",
        ):
            _policy_payload(getattr(self, field_name), field_name)
        if self.notes is not None:
            object.__setattr__(self, "notes", _non_empty_text(self.notes, "notes"))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_type": "strategy-spec",
            "identity_version": STRATEGY_SPEC_MODEL_VERSION,
            "strategy_key": self.strategy_key,
            "semantic_version": self.semantic_version,
            "entry_policy": self.entry_policy.stable_dict(),
            "exit_policy": self.exit_policy.stable_dict(),
            "position_policy": self.position_policy.stable_dict(),
            "sizing_policy": _policy_payload(self.sizing_policy, "sizing_policy"),
            "risk_policy": _policy_payload(self.risk_policy, "risk_policy"),
            "session_policy": _policy_payload(self.session_policy, "session_policy"),
            "cooldown_policy": _policy_payload(self.cooldown_policy, "cooldown_policy"),
            "signal_combination_policy": self.signal_combination_policy.stable_dict(),
            "execution_policy": _policy_payload(self.execution_policy, "execution_policy"),
        }

    @property
    def strategy_identity(self) -> str:
        return f"{STRATEGY_SPEC_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"

    @property
    def identity(self) -> str:
        return self.strategy_identity

    def stable_dict(self) -> dict[str, Any]:
        return {
            "strategy_identity": self.strategy_identity,
            "canonical_payload": self.canonical_payload(),
        }


@dataclass(frozen=True, slots=True)
class DecisionIntent:
    decision_time: Instant
    instrument: str
    target_position: Decimal | str | int
    direction: Direction
    strategy_identity: str
    policy_identities: Mapping[str, str]
    input_identities: Sequence[str]
    confidence: Decimal | str | int | None = None
    urgency: Urgency | None = None
    risk_budget: Mapping[str, Any] | None = None
    size: Decimal | str | int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_time", Instant.parse(self.decision_time))
        object.__setattr__(self, "instrument", _non_empty_text(self.instrument, "instrument"))
        object.__setattr__(self, "target_position", _signed_decimal(self.target_position, "target_position"))
        object.__setattr__(self, "direction", Direction(self.direction))
        object.__setattr__(
            self, "strategy_identity", _identity_text(self.strategy_identity, "strategy_identity")
        )
        policy_identities = _identity_mapping(self.policy_identities, "policy_identities")
        object.__setattr__(self, "policy_identities", MappingProxyType(policy_identities))
        input_identities = tuple(_identity_text(item, "input identity") for item in self.input_identities)
        if len(set(input_identities)) != len(input_identities):
            raise StrategyError("input_identities must not contain duplicates")
        object.__setattr__(self, "input_identities", tuple(sorted(input_identities)))
        if self.confidence is not None:
            confidence = _decimal(self.confidence, "confidence")
            if confidence > Decimal("1"):
                raise StrategyError("confidence must be <= 1")
            object.__setattr__(self, "confidence", confidence)
        if self.urgency is not None:
            object.__setattr__(self, "urgency", Urgency(self.urgency))
        if self.risk_budget is not None:
            if not isinstance(self.risk_budget, Mapping):
                raise StrategyError("risk_budget must be a mapping")
            object.__setattr__(self, "risk_budget", MappingProxyType(dict(self.risk_budget)))
        if self.size is not None:
            object.__setattr__(self, "size", _decimal(self.size, "size"))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_type": "decision-intent",
            "identity_version": DECISION_INTENT_MODEL_VERSION,
            "decision_time": self.decision_time.isoformat(),
            "instrument": self.instrument,
            "target_position": _decimal_string(self.target_position),
            "direction": self.direction.value,
            "size": None if self.size is None else _decimal_string(self.size),
            "risk_budget": None if self.risk_budget is None else dict(self.risk_budget),
            "confidence": None if self.confidence is None else _decimal_string(self.confidence),
            "urgency": None if self.urgency is None else self.urgency.value,
            "strategy_identity": self.strategy_identity,
            "policy_identities": dict(self.policy_identities),
            "input_identities": list(self.input_identities),
        }

    @property
    def intent_identity(self) -> str:
        return f"{DECISION_INTENT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"

    @property
    def identity(self) -> str:
        return self.intent_identity

    def stable_dict(self) -> dict[str, Any]:
        return {
            "intent_identity": self.intent_identity,
            "canonical_payload": self.canonical_payload(),
        }


def _signed_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, Decimal):
        text = str(value)
    elif type(value) is int:
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise StrategyError(f"{field} must be a Decimal, integer or decimal string")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise StrategyError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise StrategyError(f"{field} must be finite")
    return result


def _identity_text(value: Any, field: str) -> str:
    text = _non_empty_text(value, field)
    if ":sha256:" not in text:
        raise StrategyError(f"{field} must be a content-derived identity")
    suffix = text.rsplit(":sha256:", 1)[1]
    if not _SHA256_HEX_RE.fullmatch(suffix):
        raise StrategyError(f"{field} must end with a sha256 digest")
    return text


def _identity_mapping(value: Mapping[str, str], field: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise StrategyError(f"{field} must be a non-empty mapping")
    result = {}
    for key, item in value.items():
        result[_key(key, f"{field} key")] = _identity_text(item, f"{field}.{key}")
    return {key: result[key] for key in sorted(result)}


@dataclass(frozen=True, slots=True)
class NoDecision:
    decision_time: Instant
    instrument: str
    strategy_identity: str
    reason: str
    input_identities: Sequence[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_time", Instant.parse(self.decision_time))
        object.__setattr__(self, "instrument", _non_empty_text(self.instrument, "instrument"))
        object.__setattr__(
            self, "strategy_identity", _identity_text(self.strategy_identity, "strategy_identity")
        )
        object.__setattr__(self, "reason", _key(self.reason, "reason"))
        identities = tuple(_identity_text(item, "input identity") for item in self.input_identities)
        object.__setattr__(self, "input_identities", tuple(sorted(set(identities))))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_type": "strategy-no-decision",
            "identity_version": NO_DECISION_MODEL_VERSION,
            "decision_time": self.decision_time.isoformat(),
            "instrument": self.instrument,
            "strategy_identity": self.strategy_identity,
            "reason": self.reason,
            "input_identities": list(self.input_identities),
        }

    @property
    def no_decision_identity(self) -> str:
        return f"{NO_DECISION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"

    @property
    def identity(self) -> str:
        return self.no_decision_identity

    def stable_dict(self) -> dict[str, Any]:
        return {
            "no_decision_identity": self.no_decision_identity,
            "canonical_payload": self.canonical_payload(),
        }


@dataclass(frozen=True, slots=True)
class StrategyCompositionResult:
    decision_intent: DecisionIntent | None = None
    no_decision: NoDecision | None = None

    def __post_init__(self) -> None:
        if (self.decision_intent is None) == (self.no_decision is None):
            raise StrategyError("composition result must contain exactly one outcome")

    @property
    def outcome(self) -> DecisionIntent | NoDecision:
        return self.decision_intent if self.decision_intent is not None else self.no_decision  # type: ignore[return-value]

    def stable_dict(self) -> dict[str, Any]:
        return {
            "decision_intent": None if self.decision_intent is None else self.decision_intent.stable_dict(),
            "no_decision": None if self.no_decision is None else self.no_decision.stable_dict(),
        }


def compose_decision(
    spec: StrategySpec,
    inputs: Iterable[StrategyInput],
    *,
    decision_time: Instant,
    instrument: str,
) -> StrategyCompositionResult:
    """Pure deterministic G02 composition for one decision instant."""

    if not isinstance(spec, StrategySpec):
        raise StrategyError("spec must be a StrategySpec")
    decision_instant = Instant.parse(decision_time)
    bounded_inputs = _canonical_inputs(inputs)
    _enforce_availability_floor(bounded_inputs.values(), decision_instant)
    input_identities = tuple(item.identity for item in bounded_inputs.values())

    exit_requested = (
        spec.exit_policy.signal_key in bounded_inputs
        and _truthy(bounded_inputs[spec.exit_policy.signal_key].value)
    )
    entry_allowed = spec.signal_combination_policy.evaluate(bounded_inputs)

    if exit_requested:
        direction = Direction.FLAT
        target_position = spec.position_policy.target_for(direction)
        confidence = Decimal("1")
        urgency = Urgency.HIGH
    elif entry_allowed and spec.entry_policy.signal_key in bounded_inputs:
        direction = spec.entry_policy.direction
        target_position = spec.position_policy.target_for(direction)
        confidence = spec.entry_policy.confidence
        urgency = spec.entry_policy.urgency
    else:
        return StrategyCompositionResult(
            no_decision=NoDecision(
                decision_time=decision_instant,
                instrument=instrument,
                strategy_identity=spec.strategy_identity,
                reason="signal_conditions_not_met",
                input_identities=input_identities,
            )
        )

    return StrategyCompositionResult(
        decision_intent=DecisionIntent(
            decision_time=decision_instant,
            instrument=instrument,
            target_position=target_position,
            direction=direction,
            confidence=confidence,
            urgency=urgency,
            strategy_identity=spec.strategy_identity,
            policy_identities=_policy_identities(spec),
            input_identities=input_identities,
        )
    )


def _canonical_inputs(inputs: Iterable[StrategyInput]) -> dict[str, StrategyInput]:
    if isinstance(inputs, (str, bytes, bytearray)):
        raise StrategyError("inputs must be an iterable of StrategyInput")
    result: dict[str, StrategyInput] = {}
    for item in inputs:
        if not isinstance(item, StrategyInput):
            raise StrategyError("inputs must contain StrategyInput values")
        if item.key in result:
            raise StrategyError(f"duplicate strategy input key: {item.key}")
        result[item.key] = item
    return {key: result[key] for key in sorted(result)}


def _enforce_availability_floor(inputs: Iterable[StrategyInput], decision_time: Instant) -> None:
    for item in inputs:
        if item.available_at > decision_time:
            raise StrategyError(
                f"input {item.key!r} is not available by decision_time"
            )


def _policy_identities(spec: StrategySpec) -> dict[str, str]:
    return {
        "entry_policy": spec.entry_policy.identity,
        "exit_policy": spec.exit_policy.identity,
        "position_policy": spec.position_policy.identity,
        "sizing_policy": spec.sizing_policy.identity,
        "risk_policy": spec.risk_policy.identity,
        "session_policy": spec.session_policy.identity,
        "cooldown_policy": spec.cooldown_policy.identity,
        "signal_combination_policy": spec.signal_combination_policy.identity,
        "execution_policy": spec.execution_policy.identity,
    }


__all__ = [
    "CooldownPolicy",
    "DecisionIntent",
    "Direction",
    "EntryPolicy",
    "ExecutionPolicy",
    "ExitPolicy",
    "IdentityBackedPolicy",
    "NoDecision",
    "PositionPolicy",
    "RiskPolicy",
    "SessionPolicy",
    "SignalCombinationMode",
    "SignalCombinationPolicy",
    "SizingPolicy",
    "StrategyCompositionResult",
    "StrategyError",
    "StrategyInput",
    "StrategySpec",
    "Urgency",
    "compose_decision",
]
