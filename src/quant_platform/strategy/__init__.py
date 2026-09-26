"""StrategySpec, policy composition and DecisionIntent v1.

This package owns the G01-G04 strategy boundary. It implements pure policy
definitions and evaluations for deterministic strategy composition, risk
budgets, position sizing, reference-session calendars and cooldown gates. It
does not implement order/fill mechanics, portfolio ledger state, replay
runtime or live execution.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
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
RISK_POLICY_IDENTITY_DOMAIN = "risk-policy-definition-v1"
RISK_DECISION_IDENTITY_DOMAIN = "risk-decision-v1"
SIZING_POLICY_IDENTITY_DOMAIN = "sizing-policy-definition-v1"
SIZING_DECISION_IDENTITY_DOMAIN = "sizing-decision-v1"
SESSION_POLICY_IDENTITY_DOMAIN = "session-policy-definition-v1"
SESSION_DECISION_IDENTITY_DOMAIN = "session-decision-v1"
COOLDOWN_POLICY_IDENTITY_DOMAIN = "cooldown-policy-definition-v1"
COOLDOWN_DECISION_IDENTITY_DOMAIN = "cooldown-decision-v1"
COOLDOWN_UNAVAILABLE_IDENTITY_DOMAIN = "cooldown-decision-unavailable-v1"
STRATEGY_SPEC_MODEL_VERSION = "1"
DECISION_INTENT_MODEL_VERSION = "1"
NO_DECISION_MODEL_VERSION = "1"
POLICY_MODEL_VERSION = "1"

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_NS_PER_SECOND = 1_000_000_000
_NS_PER_DAY = 86_400 * _NS_PER_SECOND


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


class RiskDecisionState(StrEnum):
    ACCEPTED = "ACCEPTED"
    REFUSED = "REFUSED"


class SessionReferenceMarket(StrEnum):
    NEW_YORK = "NEW_YORK"
    LONDON = "LONDON"
    TOKYO = "TOKYO"


class SessionState(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class CooldownState(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    COOLING_DOWN = "COOLING_DOWN"
    FREQUENCY_LIMITED = "FREQUENCY_LIMITED"


class CooldownUnavailableReason(StrEnum):
    MISSING_TRADE_HISTORY = "missing_trade_history"
    TRADE_HISTORY_STALE = "trade_history_stale"
    TRADE_HISTORY_FUTURE_DATED = "trade_history_future_dated"
    TRADE_HISTORY_MALFORMED = "trade_history_malformed"


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


@dataclass(frozen=True, slots=True)
class RiskSnapshot:
    """Caller-supplied G03 capital state available at one decision instant."""

    observed_at: Instant
    equity: Decimal | str | int
    peak_equity: Decimal | str | int
    current_exposure_notional: Decimal | str | int
    evidence_identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", Instant.parse(self.observed_at))
        equity = _decimal(self.equity, "equity", allow_zero=False)
        peak = _decimal(self.peak_equity, "peak_equity", allow_zero=False)
        if peak < equity:
            raise StrategyError("peak_equity must be >= equity")
        object.__setattr__(self, "equity", equity)
        object.__setattr__(self, "peak_equity", peak)
        object.__setattr__(
            self,
            "current_exposure_notional",
            _signed_decimal(self.current_exposure_notional, "current_exposure_notional"),
        )
        object.__setattr__(
            self,
            "evidence_identity",
            _non_empty_text(self.evidence_identity, "risk evidence_identity"),
        )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at.isoformat(),
            "equity": _decimal_string(self.equity),
            "peak_equity": _decimal_string(self.peak_equity),
            "current_exposure_notional": _decimal_string(self.current_exposure_notional),
            "evidence_identity": self.evidence_identity,
        }


@dataclass(frozen=True, slots=True)
class CapitalRiskPolicy:
    policy_key: str
    max_drawdown_fraction: Decimal | str | int
    max_position_notional_fraction: Decimal | str | int
    max_total_exposure_fraction: Decimal | str | int
    risk_per_trade_fraction: Decimal | str | int
    max_evidence_age_seconds: int
    semantic_version: str | int = POLICY_MODEL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_key", _key(self.policy_key, "risk policy key"))
        object.__setattr__(self, "semantic_version", _semantic_version(self.semantic_version))
        for field_name in (
            "max_drawdown_fraction",
            "max_position_notional_fraction",
            "max_total_exposure_fraction",
            "risk_per_trade_fraction",
        ):
            value = _decimal(getattr(self, field_name), field_name, allow_zero=False)
            if value > 1:
                raise StrategyError(f"{field_name} must be <= 1")
            object.__setattr__(self, field_name, value)
        if type(self.max_evidence_age_seconds) is not int or self.max_evidence_age_seconds < 0:
            raise StrategyError("max_evidence_age_seconds must be a non-negative integer")

    def evaluate(
        self,
        *,
        snapshot: RiskSnapshot | None,
        as_of: Instant,
        reference_price: Decimal | str | int,
        target_position: Decimal | str | int,
    ) -> "RiskDecision":
        evaluation_time = Instant.parse(as_of)
        if snapshot is None:
            return RiskDecision(
                policy_identity=self.identity,
                as_of=evaluation_time,
                state=RiskDecisionState.REFUSED,
                reason="missing_risk_snapshot",
                risk_budget_notional=Decimal("0"),
                max_position_notional=Decimal("0"),
                max_total_exposure_notional=Decimal("0"),
                requested_notional=Decimal("0"),
                evidence=None,
            )
        if snapshot.observed_at > evaluation_time:
            return self._risk_refusal("risk_snapshot_future_dated", snapshot, evaluation_time)
        if evaluation_time.epoch_ns - snapshot.observed_at.epoch_ns > self.max_evidence_age_seconds * _NS_PER_SECOND:
            return self._risk_refusal("risk_snapshot_stale", snapshot, evaluation_time)
        price = _decimal(reference_price, "reference_price", allow_zero=False)
        position = _signed_decimal(target_position, "target_position")
        requested_notional = abs(position) * price
        drawdown = (snapshot.peak_equity - snapshot.equity) / snapshot.peak_equity
        max_position_notional = snapshot.equity * self.max_position_notional_fraction
        max_total_exposure_notional = snapshot.equity * self.max_total_exposure_fraction
        remaining_total_exposure = max_total_exposure_notional - abs(snapshot.current_exposure_notional)
        if remaining_total_exposure < 0:
            remaining_total_exposure = Decimal("0")
        risk_budget_notional = min(
            snapshot.equity * self.risk_per_trade_fraction,
            max_position_notional,
            remaining_total_exposure,
        )
        reason = "accepted"
        state = RiskDecisionState.ACCEPTED
        if drawdown >= self.max_drawdown_fraction:
            reason = "max_drawdown_breached"
            state = RiskDecisionState.REFUSED
            risk_budget_notional = Decimal("0")
        elif requested_notional > max_position_notional:
            reason = "position_notional_limit_breached"
            state = RiskDecisionState.REFUSED
            risk_budget_notional = Decimal("0")
        elif requested_notional > remaining_total_exposure:
            reason = "total_exposure_limit_breached"
            state = RiskDecisionState.REFUSED
            risk_budget_notional = Decimal("0")
        return RiskDecision(
            policy_identity=self.identity,
            as_of=evaluation_time,
            state=state,
            reason=reason,
            risk_budget_notional=risk_budget_notional,
            max_position_notional=max_position_notional,
            max_total_exposure_notional=max_total_exposure_notional,
            requested_notional=requested_notional,
            evidence=snapshot,
        )

    def _risk_refusal(self, reason: str, snapshot: RiskSnapshot, as_of: Instant) -> "RiskDecision":
        return RiskDecision(
            policy_identity=self.identity,
            as_of=as_of,
            state=RiskDecisionState.REFUSED,
            reason=reason,
            risk_budget_notional=Decimal("0"),
            max_position_notional=Decimal("0"),
            max_total_exposure_notional=Decimal("0"),
            requested_notional=Decimal("0"),
            evidence=snapshot,
        )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "policy_type": RISK_POLICY_IDENTITY_DOMAIN,
            "policy_key": self.policy_key,
            "semantic_version": self.semantic_version,
            "max_drawdown_fraction": _decimal_string(self.max_drawdown_fraction),
            "max_position_notional_fraction": _decimal_string(self.max_position_notional_fraction),
            "max_total_exposure_fraction": _decimal_string(self.max_total_exposure_fraction),
            "risk_per_trade_fraction": _decimal_string(self.risk_per_trade_fraction),
            "max_evidence_age_seconds": self.max_evidence_age_seconds,
        }

    @property
    def identity(self) -> str:
        return f"{RISK_POLICY_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class RiskDecision:
    policy_identity: str
    as_of: Instant
    state: RiskDecisionState
    reason: str
    risk_budget_notional: Decimal
    max_position_notional: Decimal
    max_total_exposure_notional: Decimal
    requested_notional: Decimal
    evidence: RiskSnapshot | None

    @property
    def accepted(self) -> bool:
        return self.state is RiskDecisionState.ACCEPTED

    @property
    def identity(self) -> str:
        return f"{RISK_DECISION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict(include_identity=False))}"

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": RISK_DECISION_IDENTITY_DOMAIN,
            "policy_identity": self.policy_identity,
            "as_of": self.as_of.isoformat(),
            "state": self.state.value,
            "reason": self.reason,
            "risk_budget_notional": _decimal_string(self.risk_budget_notional),
            "max_position_notional": _decimal_string(self.max_position_notional),
            "max_total_exposure_notional": _decimal_string(self.max_total_exposure_notional),
            "requested_notional": _decimal_string(self.requested_notional),
            "evidence": None if self.evidence is None else self.evidence.stable_dict(),
        }
        if include_identity:
            payload["decision_identity"] = self.identity
        return payload


@dataclass(frozen=True, slots=True)
class FixedFractionSizingPolicy:
    policy_key: str
    lot_size: Decimal | str | int
    min_size: Decimal | str | int = Decimal("0")
    max_size: Decimal | str | int | None = None
    semantic_version: str | int = POLICY_MODEL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_key", _key(self.policy_key, "sizing policy key"))
        object.__setattr__(self, "semantic_version", _semantic_version(self.semantic_version))
        object.__setattr__(self, "lot_size", _decimal(self.lot_size, "lot_size", allow_zero=False))
        object.__setattr__(self, "min_size", _decimal(self.min_size, "min_size"))
        if self.max_size is not None:
            max_size = _decimal(self.max_size, "max_size", allow_zero=False)
            if max_size < self.min_size:
                raise StrategyError("max_size must be >= min_size")
            object.__setattr__(self, "max_size", max_size)

    def evaluate(
        self,
        *,
        risk_decision: RiskDecision,
        reference_price: Decimal | str | int,
        target_position: Decimal | str | int,
    ) -> "SizingDecision":
        price = _decimal(reference_price, "reference_price", allow_zero=False)
        target = abs(_signed_decimal(target_position, "target_position"))
        budget_size = risk_decision.risk_budget_notional / price
        raw_size = min(target, budget_size)
        if self.max_size is not None:
            raw_size = min(raw_size, self.max_size)
        lots = (raw_size / self.lot_size).to_integral_value(rounding=ROUND_DOWN)
        size = lots * self.lot_size
        reason = "sized"
        if not risk_decision.accepted:
            size = Decimal("0")
            reason = "risk_refused"
        elif size < self.min_size:
            size = Decimal("0")
            reason = "below_min_size"
        return SizingDecision(
            policy_identity=self.identity,
            risk_decision_identity=risk_decision.identity,
            size=size,
            reference_price=price,
            reason=reason,
        )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "policy_type": SIZING_POLICY_IDENTITY_DOMAIN,
            "policy_key": self.policy_key,
            "semantic_version": self.semantic_version,
            "lot_size": _decimal_string(self.lot_size),
            "min_size": _decimal_string(self.min_size),
            "max_size": None if self.max_size is None else _decimal_string(self.max_size),
        }

    @property
    def identity(self) -> str:
        return f"{SIZING_POLICY_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class SizingDecision:
    policy_identity: str
    risk_decision_identity: str
    size: Decimal
    reference_price: Decimal
    reason: str

    @property
    def identity(self) -> str:
        return f"{SIZING_DECISION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict(include_identity=False))}"

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": SIZING_DECISION_IDENTITY_DOMAIN,
            "policy_identity": self.policy_identity,
            "risk_decision_identity": self.risk_decision_identity,
            "size": _decimal_string(self.size),
            "reference_price": _decimal_string(self.reference_price),
            "reason": self.reason,
        }
        if include_identity:
            payload["decision_identity"] = self.identity
        return payload


@dataclass(frozen=True, slots=True)
class SessionPolicyDefinition:
    policy_key: str
    reference_markets: Sequence[SessionReferenceMarket | str]
    semantic_version: str | int = POLICY_MODEL_VERSION
    calendar_version: str = "reference-market-static-calendar-2020-2035-v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_key", _key(self.policy_key, "session policy key"))
        object.__setattr__(self, "semantic_version", _semantic_version(self.semantic_version))
        markets = tuple(SessionReferenceMarket(market) for market in self.reference_markets)
        if not markets:
            raise StrategyError("reference_markets must not be empty")
        object.__setattr__(self, "reference_markets", tuple(sorted(set(markets), key=lambda item: item.value)))
        object.__setattr__(self, "calendar_version", _non_empty_text(self.calendar_version, "calendar_version"))

    def evaluate(self, as_of: Instant) -> tuple["SessionDecision", ...]:
        evaluation_time = Instant.parse(as_of)
        return tuple(_evaluate_reference_market(self, market, evaluation_time) for market in self.reference_markets)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "policy_type": SESSION_POLICY_IDENTITY_DOMAIN,
            "policy_key": self.policy_key,
            "semantic_version": self.semantic_version,
            "calendar_version": self.calendar_version,
            "reference_markets": [market.value for market in self.reference_markets],
        }

    @property
    def identity(self) -> str:
        return f"{SESSION_POLICY_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class SessionDecision:
    policy_identity: str
    as_of: Instant
    reference_market: SessionReferenceMarket
    state: SessionState
    phase: str | None
    trading_date: str
    reason: str

    @property
    def open(self) -> bool:
        return self.state is SessionState.OPEN

    @property
    def identity(self) -> str:
        return f"{SESSION_DECISION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict(include_identity=False))}"

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": SESSION_DECISION_IDENTITY_DOMAIN,
            "policy_identity": self.policy_identity,
            "as_of": self.as_of.isoformat(),
            "reference_market": self.reference_market.value,
            "state": self.state.value,
            "phase": self.phase,
            "trading_date": self.trading_date,
            "reason": self.reason,
        }
        if include_identity:
            payload["decision_identity"] = self.identity
        return payload


@dataclass(frozen=True, slots=True)
class RealizedPositionOutcome:
    opened_at: Instant
    settled_at: Instant
    realized_pnl: Decimal | str | int
    evidence_identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "opened_at", Instant.parse(self.opened_at))
        object.__setattr__(self, "settled_at", Instant.parse(self.settled_at))
        if self.settled_at < self.opened_at:
            raise StrategyError("settled_at must not precede opened_at")
        object.__setattr__(self, "realized_pnl", _signed_decimal(self.realized_pnl, "realized_pnl"))
        object.__setattr__(self, "evidence_identity", _non_empty_text(self.evidence_identity, "outcome evidence_identity"))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "opened_at": self.opened_at.isoformat(),
            "settled_at": self.settled_at.isoformat(),
            "realized_pnl": _decimal_string(self.realized_pnl),
            "evidence_identity": self.evidence_identity,
        }


@dataclass(frozen=True, slots=True)
class TradeHistoryEvidence:
    observed_at: Instant
    outcomes: Sequence[RealizedPositionOutcome]
    evidence_identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", Instant.parse(self.observed_at))
        object.__setattr__(self, "evidence_identity", _non_empty_text(self.evidence_identity, "trade history evidence_identity"))
        object.__setattr__(self, "outcomes", tuple(self.outcomes))
        for outcome in self.outcomes:
            if not isinstance(outcome, RealizedPositionOutcome):
                raise StrategyError("trade history outcomes must be RealizedPositionOutcome values")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at.isoformat(),
            "evidence_identity": self.evidence_identity,
            "outcomes": [outcome.stable_dict() for outcome in self.outcomes],
        }


@dataclass(frozen=True, slots=True)
class CooldownPolicyDefinition:
    policy_key: str
    post_loss_cooldown_seconds: int
    consecutive_loss_count: int
    consecutive_loss_cooldown_seconds: int
    max_entries_per_utc_day: int
    max_trade_history_age_seconds: int
    semantic_version: str | int = POLICY_MODEL_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_key", _key(self.policy_key, "cooldown policy key"))
        object.__setattr__(self, "semantic_version", _semantic_version(self.semantic_version))
        for field_name in (
            "post_loss_cooldown_seconds",
            "consecutive_loss_cooldown_seconds",
            "max_trade_history_age_seconds",
        ):
            value = getattr(self, field_name)
            if type(value) is not int or value < 0:
                raise StrategyError(f"{field_name} must be a non-negative integer")
        if type(self.consecutive_loss_count) is not int or self.consecutive_loss_count < 1:
            raise StrategyError("consecutive_loss_count must be a positive integer")
        if type(self.max_entries_per_utc_day) is not int or self.max_entries_per_utc_day < 1:
            raise StrategyError("max_entries_per_utc_day must be a positive integer")

    def evaluate(
        self,
        *,
        trade_history: TradeHistoryEvidence | None,
        as_of: Instant,
        requires_new_entry: bool,
    ) -> "CooldownDecision | CooldownDecisionUnavailable":
        evaluation_time = Instant.parse(as_of)
        if type(requires_new_entry) is not bool:
            raise StrategyError("requires_new_entry must be boolean")
        if not requires_new_entry:
            return self._decision(
                as_of=evaluation_time,
                state=CooldownState.ELIGIBLE,
                reason="not_new_entry",
                cooldown_until=None,
                trade_history_identity="trade-history-not-required-for-exit",
            )
        if trade_history is None:
            return self._unavailable(
                CooldownUnavailableReason.MISSING_TRADE_HISTORY,
                evaluation_time,
                {"required": "fresh TradeHistoryEvidence"},
            )
        try:
            self._validate_trade_history(trade_history, evaluation_time)
        except StrategyError as exc:
            return self._unavailable(
                CooldownUnavailableReason.TRADE_HISTORY_MALFORMED,
                evaluation_time,
                {"error": str(exc), "trade_history": trade_history.stable_dict()},
            )
        if trade_history.observed_at > evaluation_time:
            return self._unavailable(
                CooldownUnavailableReason.TRADE_HISTORY_FUTURE_DATED,
                evaluation_time,
                {"observed_at": trade_history.observed_at.isoformat()},
            )
        if evaluation_time.epoch_ns - trade_history.observed_at.epoch_ns > self.max_trade_history_age_seconds * _NS_PER_SECOND:
            return self._unavailable(
                CooldownUnavailableReason.TRADE_HISTORY_STALE,
                evaluation_time,
                {"observed_at": trade_history.observed_at.isoformat()},
            )
        future_outcomes = tuple(
            outcome for outcome in trade_history.outcomes if outcome.opened_at > evaluation_time or outcome.settled_at > evaluation_time
        )
        if future_outcomes:
            return self._unavailable(
                CooldownUnavailableReason.TRADE_HISTORY_FUTURE_DATED,
                evaluation_time,
                {"future_outcomes": [outcome.stable_dict() for outcome in future_outcomes]},
            )
        outcomes = tuple(
            sorted(
                (outcome for outcome in trade_history.outcomes if outcome.settled_at <= evaluation_time),
                key=lambda outcome: (outcome.settled_at.epoch_ns, outcome.evidence_identity),
            )
        )
        frequency_count = sum(
            1
            for outcome in outcomes
            if _utc_date(outcome.opened_at) == _utc_date(evaluation_time)
            and outcome.opened_at <= evaluation_time
        )
        if frequency_count >= self.max_entries_per_utc_day:
            return self._decision(
                as_of=evaluation_time,
                state=CooldownState.FREQUENCY_LIMITED,
                reason="max_entries_per_utc_day_reached",
                cooldown_until=None,
                trade_history=trade_history,
                entries_today=frequency_count,
            )
        loss_outcomes = tuple(outcome for outcome in outcomes if outcome.realized_pnl < 0)
        post_loss_until = (
            Instant(loss_outcomes[-1].settled_at.epoch_ns + self.post_loss_cooldown_seconds * _NS_PER_SECOND)
            if loss_outcomes and self.post_loss_cooldown_seconds
            else None
        )
        consecutive_losses = 0
        for outcome in reversed(outcomes):
            if outcome.realized_pnl < 0:
                consecutive_losses += 1
            elif outcome.realized_pnl > 0:
                break
        consecutive_until = (
            Instant(outcomes[-1].settled_at.epoch_ns + self.consecutive_loss_cooldown_seconds * _NS_PER_SECOND)
            if consecutive_losses >= self.consecutive_loss_count and self.consecutive_loss_cooldown_seconds
            else None
        )
        active_untils = tuple(
            instant
            for instant in (post_loss_until, consecutive_until)
            if instant is not None and instant > evaluation_time
        )
        if active_untils:
            cooldown_until = max(active_untils, key=lambda instant: instant.epoch_ns)
            return self._decision(
                as_of=evaluation_time,
                state=CooldownState.COOLING_DOWN,
                reason="loss_cooldown_active",
                cooldown_until=cooldown_until,
                trade_history=trade_history,
                entries_today=frequency_count,
                consecutive_losses=consecutive_losses,
            )
        return self._decision(
            as_of=evaluation_time,
            state=CooldownState.ELIGIBLE,
            reason="eligible",
            cooldown_until=None,
            trade_history=trade_history,
            entries_today=frequency_count,
            consecutive_losses=consecutive_losses,
        )

    def _validate_trade_history(self, trade_history: TradeHistoryEvidence, as_of: Instant) -> None:
        if not isinstance(trade_history, TradeHistoryEvidence):
            raise StrategyError("trade_history must be TradeHistoryEvidence")

    def _unavailable(
        self,
        reason: CooldownUnavailableReason,
        as_of: Instant,
        evidence: Mapping[str, Any],
    ) -> "CooldownDecisionUnavailable":
        return CooldownDecisionUnavailable(
            reason=reason,
            policy_identity=self.identity,
            as_of=as_of,
            evidence=evidence,
        )

    def _decision(
        self,
        *,
        as_of: Instant,
        state: CooldownState,
        reason: str,
        cooldown_until: Instant | None,
        trade_history: TradeHistoryEvidence | None = None,
        trade_history_identity: str | None = None,
        entries_today: int = 0,
        consecutive_losses: int = 0,
    ) -> "CooldownDecision":
        identity = trade_history_identity
        if identity is None:
            if trade_history is None:
                raise StrategyError("trade_history or trade_history_identity is required")
            identity = trade_history.evidence_identity
        return CooldownDecision(
            policy_identity=self.identity,
            as_of=as_of,
            state=state,
            reason=reason,
            cooldown_until=cooldown_until,
            new_entries_allowed=state is CooldownState.ELIGIBLE,
            exits_allowed=True,
            trade_history_identity=identity,
            entries_today=entries_today,
            consecutive_losses=consecutive_losses,
        )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "policy_type": COOLDOWN_POLICY_IDENTITY_DOMAIN,
            "policy_key": self.policy_key,
            "semantic_version": self.semantic_version,
            "post_loss_cooldown_seconds": self.post_loss_cooldown_seconds,
            "consecutive_loss_count": self.consecutive_loss_count,
            "consecutive_loss_cooldown_seconds": self.consecutive_loss_cooldown_seconds,
            "max_entries_per_utc_day": self.max_entries_per_utc_day,
            "max_trade_history_age_seconds": self.max_trade_history_age_seconds,
            "restrictions_are_upper_bounds_only": True,
            "cooldown_blocks_new_entries_only": True,
        }

    @property
    def identity(self) -> str:
        return f"{COOLDOWN_POLICY_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class CooldownDecision:
    policy_identity: str
    as_of: Instant
    state: CooldownState
    reason: str
    cooldown_until: Instant | None
    new_entries_allowed: bool
    exits_allowed: bool
    trade_history_identity: str
    entries_today: int
    consecutive_losses: int

    @property
    def identity(self) -> str:
        return f"{COOLDOWN_DECISION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict(include_identity=False))}"

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": COOLDOWN_DECISION_IDENTITY_DOMAIN,
            "policy_identity": self.policy_identity,
            "as_of": self.as_of.isoformat(),
            "state": self.state.value,
            "reason": self.reason,
            "cooldown_until": None if self.cooldown_until is None else self.cooldown_until.isoformat(),
            "restrictions": {
                "new_entries_allowed": self.new_entries_allowed,
                "exits_allowed": self.exits_allowed,
                "eligible_is_not_authorization": True,
            },
            "trade_history_identity": self.trade_history_identity,
            "entries_today": self.entries_today,
            "consecutive_losses": self.consecutive_losses,
        }
        if include_identity:
            payload["decision_identity"] = self.identity
        return payload


@dataclass(frozen=True, slots=True)
class CooldownDecisionUnavailable:
    reason: CooldownUnavailableReason
    policy_identity: str
    as_of: Instant
    evidence: Mapping[str, Any]

    @property
    def identity(self) -> str:
        return f"{COOLDOWN_UNAVAILABLE_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict(include_identity=False))}"

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": COOLDOWN_UNAVAILABLE_IDENTITY_DOMAIN,
            "reason": self.reason.value,
            "policy_identity": self.policy_identity,
            "as_of": self.as_of.isoformat(),
            "evidence": dict(self.evidence),
        }
        if include_identity:
            payload["decision_identity"] = self.identity
        return payload


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


def _evaluate_reference_market(
    policy: SessionPolicyDefinition,
    market: SessionReferenceMarket,
    as_of: Instant,
) -> SessionDecision:
    candidates = _session_candidates(market, as_of)
    for trading_day, phase_name, start_ns, end_ns in candidates:
        if _is_market_open_day(market, trading_day) and start_ns <= as_of.epoch_ns < end_ns:
            return SessionDecision(
                policy_identity=policy.identity,
                as_of=as_of,
                reference_market=market,
                state=SessionState.OPEN,
                phase=phase_name,
                trading_date=trading_day.isoformat(),
                reason="phase_open",
            )
    trading_day = _utc_date(as_of)
    reason = "market_closed_date" if not _is_market_open_day(market, trading_day) else "outside_session_phase"
    return SessionDecision(
        policy_identity=policy.identity,
        as_of=as_of,
        reference_market=market,
        state=SessionState.CLOSED,
        phase=None,
        trading_date=trading_day.isoformat(),
        reason=reason,
    )


def _session_candidates(
    market: SessionReferenceMarket,
    instant: Instant,
) -> tuple[tuple[date, str, int, int], ...]:
    trading_day = _utc_date(instant)
    days = (trading_day - timedelta(days=1), trading_day)
    result: list[tuple[date, str, int, int]] = []
    for day in days:
        offset_hours = _session_utc_offset_hours(market, day)
        for phase_name, local_start, local_end in _local_session_phases(market):
            start_ns = _local_day_time_epoch_ns(day, local_start, offset_hours)
            end_ns = _local_day_time_epoch_ns(day, local_end, offset_hours)
            if local_end <= local_start:
                end_ns += _NS_PER_DAY
            override_end = _early_close_epoch_ns(market, day, phase_name, offset_hours)
            if override_end is not None:
                end_ns = min(end_ns, override_end)
            if end_ns > start_ns:
                result.append((day, phase_name, start_ns, end_ns))
    return tuple(result)


def _local_session_phases(market: SessionReferenceMarket) -> tuple[tuple[str, str, str], ...]:
    if market is SessionReferenceMarket.NEW_YORK:
        return (
            ("pre_market", "04:00", "09:30"),
            ("regular", "09:30", "16:00"),
            ("post_market", "16:00", "20:00"),
        )
    if market is SessionReferenceMarket.LONDON:
        return (
            ("pre_trading", "05:05", "07:50"),
            ("pre_open_auction", "07:50", "08:00"),
            ("regular", "08:00", "16:30"),
            ("closing_auction", "16:30", "16:35"),
            ("post_trading", "16:40", "17:15"),
        )
    if market is SessionReferenceMarket.TOKYO:
        return (("morning", "09:00", "11:30"), ("afternoon", "12:30", "15:30"))
    raise AssertionError("unreachable reference market")


def _session_utc_offset_hours(market: SessionReferenceMarket, day: date) -> int:
    if market is SessionReferenceMarket.NEW_YORK:
        return -4 if _ny_dst(day) else -5
    if market is SessionReferenceMarket.LONDON:
        return 1 if _london_dst(day) else 0
    if market is SessionReferenceMarket.TOKYO:
        return 9
    raise AssertionError("unreachable reference market")


def _is_market_open_day(market: SessionReferenceMarket, day: date) -> bool:
    if day.year < 2020 or day.year > 2035:
        return False
    if day.weekday() >= 5:
        return False
    return day not in _market_closed_dates(market, day.year)


def _early_close_epoch_ns(
    market: SessionReferenceMarket,
    day: date,
    phase_name: str,
    offset_hours: int,
) -> int | None:
    if phase_name != "regular":
        return None
    close_time = _market_early_closes(market, day.year).get(day)
    if close_time is None:
        return None
    return _local_day_time_epoch_ns(day, close_time, offset_hours)


def _local_day_time_epoch_ns(day: date, hhmm: str, offset_hours: int) -> int:
    hour, minute = (int(part) for part in hhmm.split(":", 1))
    local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=timezone.utc)
    utc_dt = local - timedelta(hours=offset_hours)
    return int(utc_dt.timestamp()) * _NS_PER_SECOND


def _utc_date(value: Instant) -> date:
    return datetime.fromtimestamp(value.epoch_ns // _NS_PER_SECOND, tz=timezone.utc).date()


def _ny_dst(day: date) -> bool:
    start = _nth_weekday(day.year, 3, 6, 2)
    end = _nth_weekday(day.year, 11, 6, 1)
    return start <= day < end


def _london_dst(day: date) -> bool:
    start = _last_weekday(day.year, 3, 6)
    end = _last_weekday(day.year, 10, 6)
    return start <= day < end


def _market_closed_dates(market: SessionReferenceMarket, year: int) -> frozenset[date]:
    if market is SessionReferenceMarket.NEW_YORK:
        dates = {
            _observed(date(year, 1, 1)),
            _nth_weekday(year, 1, 0, 3),
            _nth_weekday(year, 2, 0, 3),
            _good_friday(year),
            _last_weekday(year, 5, 0),
            _observed(date(year, 7, 4)),
            _nth_weekday(year, 9, 0, 1),
            _nth_weekday(year, 11, 3, 4),
            _observed(date(year, 12, 25)),
        }
        if year >= 2022:
            dates.add(_observed(date(year, 6, 19)))
        if year == 2025:
            dates.add(date(2025, 1, 9))
        return frozenset(dates)
    if market is SessionReferenceMarket.LONDON:
        dates = {
            _observed(date(year, 1, 1)),
            _good_friday(year),
            _easter_sunday(year) + timedelta(days=1),
            _nth_weekday(year, 5, 0, 1),
            _last_weekday(year, 5, 0),
            _last_weekday(year, 8, 0),
            *_uk_christmas_observed(year),
        }
        if year == 2022:
            dates.update({date(2022, 6, 2), date(2022, 6, 3), date(2022, 9, 19)})
        if year == 2023:
            dates.add(date(2023, 5, 8))
        return frozenset(dates)
    if market is SessionReferenceMarket.TOKYO:
        return frozenset(_tokyo_closed_dates(year))
    raise AssertionError("unreachable reference market")


def _market_early_closes(market: SessionReferenceMarket, year: int) -> dict[date, str]:
    if market is SessionReferenceMarket.NEW_YORK:
        closes = {
            _nth_weekday(year, 11, 3, 4) + timedelta(days=1): "13:00",
        }
        july3 = date(year, 7, 3)
        christmas_eve = date(year, 12, 24)
        if july3.weekday() < 5:
            closes[july3] = "13:00"
        if christmas_eve.weekday() < 5:
            closes[christmas_eve] = "13:00"
        return closes
    if market is SessionReferenceMarket.LONDON:
        return {
            day: "12:30"
            for day in (date(year, 12, 24), date(year, 12, 31))
            if day.weekday() < 5
        }
    return {}


def _observed(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    cursor = date(year, month, 1)
    while cursor.weekday() != weekday:
        cursor += timedelta(days=1)
    return cursor + timedelta(days=7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    cursor = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    while cursor.weekday() != weekday:
        cursor -= timedelta(days=1)
    return cursor


def _easter_sunday(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _good_friday(year: int) -> date:
    return _easter_sunday(year) - timedelta(days=2)


def _uk_christmas_observed(year: int) -> tuple[date, date]:
    christmas = date(year, 12, 25)
    boxing = date(year, 12, 26)
    if christmas.weekday() == 5:
        return date(year, 12, 27), date(year, 12, 28)
    if christmas.weekday() == 6:
        return date(year, 12, 26), date(year, 12, 27)
    if boxing.weekday() == 5:
        return christmas, date(year, 12, 28)
    if boxing.weekday() == 6:
        return christmas, date(year, 12, 27)
    return christmas, boxing


def _tokyo_closed_dates(year: int) -> set[date]:
    dates = {
        date(year, 1, 1),
        date(year, 1, 2),
        date(year, 1, 3),
        _nth_weekday(year, 1, 0, 2),
        _observed(date(year, 2, 11)),
        _observed(date(year, 2, 23)),
        _japan_vernal_equinox(year),
        _observed(date(year, 4, 29)),
        _observed(date(year, 5, 3)),
        _observed(date(year, 5, 4)),
        _observed(date(year, 5, 5)),
        _nth_weekday(year, 7, 0, 3),
        _observed(date(year, 8, 11)),
        _nth_weekday(year, 9, 0, 3),
        _japan_autumn_equinox(year),
        _nth_weekday(year, 10, 0, 2),
        _observed(date(year, 11, 3)),
        _observed(date(year, 11, 23)),
        date(year, 12, 31),
    }
    if year == 2020:
        dates.discard(_nth_weekday(year, 7, 0, 3))
        dates.discard(_nth_weekday(year, 10, 0, 2))
        dates.discard(_observed(date(year, 8, 11)))
        dates.update({date(2020, 7, 23), date(2020, 7, 24), date(2020, 8, 10)})
    if year == 2021:
        dates.discard(_nth_weekday(year, 7, 0, 3))
        dates.discard(_nth_weekday(year, 10, 0, 2))
        dates.discard(_observed(date(year, 8, 11)))
        dates.update({date(2021, 7, 22), date(2021, 7, 23), date(2021, 8, 9)})
    return dates


def _japan_vernal_equinox(year: int) -> date:
    return date(year, 3, int(20.8431 + 0.242194 * (year - 1980) - ((year - 1980) // 4)))


def _japan_autumn_equinox(year: int) -> date:
    return date(year, 9, int(23.2488 + 0.242194 * (year - 1980) - ((year - 1980) // 4)))


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
    "CapitalRiskPolicy",
    "CooldownDecision",
    "CooldownDecisionUnavailable",
    "CooldownPolicy",
    "CooldownPolicyDefinition",
    "CooldownState",
    "CooldownUnavailableReason",
    "DecisionIntent",
    "Direction",
    "EntryPolicy",
    "ExecutionPolicy",
    "ExitPolicy",
    "FixedFractionSizingPolicy",
    "IdentityBackedPolicy",
    "NoDecision",
    "PositionPolicy",
    "RealizedPositionOutcome",
    "RiskDecision",
    "RiskDecisionState",
    "RiskPolicy",
    "RiskSnapshot",
    "SessionDecision",
    "SessionPolicy",
    "SessionPolicyDefinition",
    "SessionReferenceMarket",
    "SessionState",
    "SignalCombinationMode",
    "SignalCombinationPolicy",
    "SizingDecision",
    "SizingPolicy",
    "StrategyCompositionResult",
    "StrategyError",
    "StrategyInput",
    "StrategySpec",
    "TradeHistoryEvidence",
    "Urgency",
    "compose_decision",
]
