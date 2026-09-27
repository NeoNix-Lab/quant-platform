"""Order/Fill lifecycle, fee/slippage cost models, and the G03/G04 admission seam v1.

This package owns H01/H02: the executable ``Order`` state machine, the
``Fill`` value type, a parameterized fee schedule, and a deterministic
synthetic slippage model. It also owns the seam translating a Strategy
``DecisionIntent`` into an admitted (or explicitly refused) ``Order`` by
evaluating ``StrategySpec``'s session, cooldown, risk and sizing policies --
closing the gap left open by G02's pure ``compose_decision``, which never
consults those policies itself.

It does not implement same-bar/OCO conflict resolution or intra-bar
sequencing (H03), double-entry portfolio/ledger accounting (H04), or live
venue adapters -- all out of scope for this slice.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

from ..data.models import Instant
from ..strategy import (
    CooldownDecision,
    CooldownDecisionUnavailable,
    DecisionIntent,
    Direction,
    NoDecision,
    RiskDecision,
    RiskSnapshot,
    SessionDecision,
    SizingDecision,
    StrategyCompositionResult,
    StrategySpec,
    TradeHistoryEvidence,
)


ORDER_IDENTITY_DOMAIN = "order-v1"
FILL_IDENTITY_DOMAIN = "fill-v1"
FEE_SCHEDULE_IDENTITY_DOMAIN = "fee-schedule-v1"
SLIPPAGE_MODEL_IDENTITY_DOMAIN = "slippage-model-v1"

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# Canonical Bybit BTCUSDT linear perpetual VIP0 rates, used only as the
# FeeSchedule default -- callers may override with venue-specific rates.
_DEFAULT_MAKER_FEE_RATE = Decimal("0.0002")
_DEFAULT_TAKER_FEE_RATE = Decimal("0.00055")


class ExecutionError(ValueError):
    """An Order/Fill v1 semantic value or state transition violates the contract."""


class OrderSide(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(StrEnum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"


class TimeInForce(StrEnum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


class OrderStatus(StrEnum):
    PENDING_NEW = "PENDING_NEW"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class LiquidityRole(StrEnum):
    MAKER = "MAKER"
    TAKER = "TAKER"


class SlippageModelKind(StrEnum):
    FIXED_BPS = "FIXED_BPS"


class AdmissionOutcome(StrEnum):
    ADMITTED = "ADMITTED"
    REFUSED = "REFUSED"


class AdmissionRefusalReason(StrEnum):
    NO_DECISION = "no_decision"
    SESSION_CLOSED = "session_closed"
    COOLDOWN_ACTIVE = "cooldown_active"
    COOLDOWN_UNAVAILABLE = "cooldown_unavailable"
    RISK_REFUSED = "risk_refused"
    SIZING_ZERO = "sizing_zero"


_OPEN_STATUSES = frozenset(
    {OrderStatus.PENDING_NEW, OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED}
)
_VALID_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING_NEW: frozenset({OrderStatus.NEW, OrderStatus.REJECTED}),
    OrderStatus.NEW: frozenset(
        {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED}
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED}
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}
_PRICE_REPLACEABLE_STATUSES = frozenset({OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED})
_FILLABLE_STATUSES = frozenset({OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED})


def _non_empty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExecutionError(f"{field} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise ExecutionError(f"{field} must not contain control characters")
    return text


def _key(value: Any, field: str) -> str:
    text = _non_empty_text(value, field)
    if not _KEY_RE.fullmatch(text):
        raise ExecutionError(f"{field} must be a governed canonical key")
    return text


def _decimal(value: Any, field: str, *, allow_zero: bool = True) -> Decimal:
    if isinstance(value, bool):
        raise ExecutionError(f"{field} must be numeric")
    if isinstance(value, Decimal):
        text = str(value)
    elif type(value) is int:
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise ExecutionError(f"{field} must be a Decimal, integer or decimal string")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ExecutionError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise ExecutionError(f"{field} must be finite")
    if result < 0 or (result == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ExecutionError(f"{field} must be {qualifier}")
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
                raise ExecutionError("canonical mapping keys must be non-empty strings")
            result[key] = _canonical_value(item)
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise ExecutionError(f"unsupported canonical value type: {type(value).__name__}")


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


def _identity_text(value: Any, field: str) -> str:
    text = _non_empty_text(value, field)
    if ":sha256:" not in text:
        raise ExecutionError(f"{field} must be a content-derived identity")
    suffix = text.rsplit(":sha256:", 1)[1]
    if not _SHA256_HEX_RE.fullmatch(suffix):
        raise ExecutionError(f"{field} must end with a sha256 digest")
    return text


def _side_for_direction(direction: Direction) -> OrderSide:
    if direction is Direction.LONG:
        return OrderSide.BUY
    if direction is Direction.SHORT:
        return OrderSide.SELL
    raise ExecutionError("FLAT direction does not map to an entry order side")


def _compute_order_id(
    *,
    instrument: str,
    side: OrderSide,
    order_type: OrderType,
    quantity: Decimal,
    limit_price: Decimal | None,
    stop_price: Decimal | None,
    time_in_force: TimeInForce,
    reduce_only: bool,
    submitted_at: Instant,
    provenance: str,
) -> str:
    payload = {
        "identity_domain": ORDER_IDENTITY_DOMAIN,
        "instrument": instrument,
        "side": side.value,
        "order_type": order_type.value,
        "quantity": _decimal_string(quantity),
        "limit_price": None if limit_price is None else _decimal_string(limit_price),
        "stop_price": None if stop_price is None else _decimal_string(stop_price),
        "time_in_force": time_in_force.value,
        "reduce_only": reduce_only,
        "submitted_at": submitted_at.isoformat(),
        "provenance": provenance,
    }
    return f"{ORDER_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}"


@dataclass(frozen=True, slots=True)
class Order:
    """One executable order. Immutable: every transition returns a new value."""

    order_id: str
    instrument: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None
    stop_price: Decimal | None
    time_in_force: TimeInForce
    reduce_only: bool
    status: OrderStatus
    submitted_at: Instant
    provenance: str
    filled_quantity: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        object.__setattr__(self, "order_id", _identity_text(self.order_id, "order_id"))
        object.__setattr__(self, "instrument", _non_empty_text(self.instrument, "instrument"))
        object.__setattr__(self, "side", OrderSide(self.side))
        object.__setattr__(self, "order_type", OrderType(self.order_type))
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", allow_zero=False))
        limit_price = None if self.limit_price is None else _decimal(self.limit_price, "limit_price", allow_zero=False)
        stop_price = None if self.stop_price is None else _decimal(self.stop_price, "stop_price", allow_zero=False)
        object.__setattr__(self, "limit_price", limit_price)
        object.__setattr__(self, "stop_price", stop_price)
        order_type = self.order_type
        if order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and limit_price is None:
            raise ExecutionError(f"{order_type.value} orders require limit_price")
        if order_type in (OrderType.STOP_MARKET, OrderType.STOP_LIMIT) and stop_price is None:
            raise ExecutionError(f"{order_type.value} orders require stop_price")
        if order_type in (OrderType.MARKET, OrderType.STOP_MARKET) and limit_price is not None:
            raise ExecutionError(f"{order_type.value} orders must not carry limit_price")
        if order_type in (OrderType.MARKET,) and stop_price is not None:
            raise ExecutionError(f"{order_type.value} orders must not carry stop_price")
        object.__setattr__(self, "time_in_force", TimeInForce(self.time_in_force))
        if type(self.reduce_only) is not bool:
            raise ExecutionError("reduce_only must be boolean")
        object.__setattr__(self, "status", OrderStatus(self.status))
        object.__setattr__(self, "submitted_at", Instant.parse(self.submitted_at))
        object.__setattr__(self, "provenance", _non_empty_text(self.provenance, "provenance"))
        object.__setattr__(self, "filled_quantity", _decimal(self.filled_quantity, "filled_quantity"))
        if self.filled_quantity > self.quantity:
            raise ExecutionError("filled_quantity must not exceed quantity")

    @classmethod
    def create(
        cls,
        *,
        instrument: str,
        side: OrderSide | str,
        order_type: OrderType | str,
        quantity: Decimal | str | int,
        submitted_at: Instant | str,
        provenance: str,
        limit_price: Decimal | str | int | None = None,
        stop_price: Decimal | str | int | None = None,
        time_in_force: TimeInForce | str = TimeInForce.GTC,
        reduce_only: bool = False,
    ) -> "Order":
        """Construct a new order in ``PENDING_NEW`` with a deterministic identity."""

        normalized_side = OrderSide(side)
        normalized_type = OrderType(order_type)
        normalized_quantity = _decimal(quantity, "quantity", allow_zero=False)
        normalized_limit = None if limit_price is None else _decimal(limit_price, "limit_price", allow_zero=False)
        normalized_stop = None if stop_price is None else _decimal(stop_price, "stop_price", allow_zero=False)
        normalized_tif = TimeInForce(time_in_force)
        normalized_submitted_at = Instant.parse(submitted_at)
        normalized_provenance = _non_empty_text(provenance, "provenance")
        order_id = _compute_order_id(
            instrument=instrument,
            side=normalized_side,
            order_type=normalized_type,
            quantity=normalized_quantity,
            limit_price=normalized_limit,
            stop_price=normalized_stop,
            time_in_force=normalized_tif,
            reduce_only=reduce_only,
            submitted_at=normalized_submitted_at,
            provenance=normalized_provenance,
        )
        return cls(
            order_id=order_id,
            instrument=instrument,
            side=normalized_side,
            order_type=normalized_type,
            quantity=normalized_quantity,
            limit_price=normalized_limit,
            stop_price=normalized_stop,
            time_in_force=normalized_tif,
            reduce_only=reduce_only,
            status=OrderStatus.PENDING_NEW,
            submitted_at=normalized_submitted_at,
            provenance=normalized_provenance,
        )

    @property
    def open_quantity(self) -> Decimal:
        return self.quantity - self.filled_quantity

    @property
    def is_open(self) -> bool:
        return self.status in _OPEN_STATUSES

    def _require_transition(self, new_status: OrderStatus) -> None:
        allowed = _VALID_TRANSITIONS.get(self.status, frozenset())
        if new_status not in allowed:
            raise ExecutionError(
                f"illegal order transition {self.status.value} -> {new_status.value}"
            )

    def acknowledge(self) -> "Order":
        self._require_transition(OrderStatus.NEW)
        return replace(self, status=OrderStatus.NEW)

    def reject(self) -> "Order":
        self._require_transition(OrderStatus.REJECTED)
        return replace(self, status=OrderStatus.REJECTED)

    def cancel(self) -> "Order":
        self._require_transition(OrderStatus.CANCELED)
        return replace(self, status=OrderStatus.CANCELED)

    def expire(self) -> "Order":
        self._require_transition(OrderStatus.EXPIRED)
        return replace(self, status=OrderStatus.EXPIRED)

    def apply_fill(self, fill: "Fill") -> "Order":
        if not isinstance(fill, Fill):
            raise ExecutionError("fill must be a Fill")
        if fill.order_id != self.order_id:
            raise ExecutionError("fill.order_id does not match this order")
        if self.status not in _FILLABLE_STATUSES:
            raise ExecutionError(f"cannot apply fill to order in status {self.status.value}")
        if fill.fill_time < self.submitted_at:
            raise ExecutionError("fill_time must not precede order submission")
        new_filled = self.filled_quantity + fill.quantity
        if new_filled > self.quantity:
            raise ExecutionError("fill would exceed order quantity")
        new_status = OrderStatus.FILLED if new_filled == self.quantity else OrderStatus.PARTIALLY_FILLED
        self._require_transition(new_status)
        return replace(self, filled_quantity=new_filled, status=new_status)

    def replace_price(
        self,
        *,
        limit_price: Decimal | str | int | None = None,
        stop_price: Decimal | str | int | None = None,
        at: Instant | str,
    ) -> "Order":
        """In-place price replace (ADR-0046 SS5): preserves order_id; price only."""

        if self.status not in _PRICE_REPLACEABLE_STATUSES:
            raise ExecutionError(f"cannot replace price on order in status {self.status.value}")
        replace_at = Instant.parse(at)
        if replace_at < self.submitted_at:
            raise ExecutionError("replace instant must not precede order submission")
        if limit_price is None and stop_price is None:
            raise ExecutionError("replace requires at least one of limit_price or stop_price")
        updates: dict[str, Any] = {}
        if limit_price is not None:
            if self.limit_price is None:
                raise ExecutionError("order does not carry a limit_price to replace")
            updates["limit_price"] = _decimal(limit_price, "limit_price", allow_zero=False)
        if stop_price is not None:
            if self.stop_price is None:
                raise ExecutionError("order does not carry a stop_price to replace")
            updates["stop_price"] = _decimal(stop_price, "stop_price", allow_zero=False)
        return replace(self, **updates)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "instrument": self.instrument,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": _decimal_string(self.quantity),
            "limit_price": None if self.limit_price is None else _decimal_string(self.limit_price),
            "stop_price": None if self.stop_price is None else _decimal_string(self.stop_price),
            "time_in_force": self.time_in_force.value,
            "reduce_only": self.reduce_only,
            "status": self.status.value,
            "submitted_at": self.submitted_at.isoformat(),
            "provenance": self.provenance,
            "filled_quantity": _decimal_string(self.filled_quantity),
        }

    @property
    def identity(self) -> str:
        return self.order_id


def _compute_fill_id(
    *,
    order_id: str,
    fill_time: Instant,
    price: Decimal,
    quantity: Decimal,
    fee: Decimal,
    liquidity_role: LiquidityRole,
    source_evidence_identity: str,
) -> str:
    payload = {
        "identity_domain": FILL_IDENTITY_DOMAIN,
        "order_id": order_id,
        "fill_time": fill_time.isoformat(),
        "price": _decimal_string(price),
        "quantity": _decimal_string(quantity),
        "fee": _decimal_string(fee),
        "liquidity_role": liquidity_role.value,
        "source_evidence_identity": source_evidence_identity,
    }
    return f"{FILL_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}"


@dataclass(frozen=True, slots=True)
class Fill:
    """One realized fill against exactly one order."""

    fill_id: str
    order_id: str
    fill_time: Instant
    price: Decimal
    quantity: Decimal
    fee: Decimal
    liquidity_role: LiquidityRole
    source_evidence_identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "fill_id", _identity_text(self.fill_id, "fill_id"))
        object.__setattr__(self, "order_id", _identity_text(self.order_id, "order_id"))
        object.__setattr__(self, "fill_time", Instant.parse(self.fill_time))
        object.__setattr__(self, "price", _decimal(self.price, "price", allow_zero=False))
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity", allow_zero=False))
        object.__setattr__(self, "fee", _decimal(self.fee, "fee"))
        object.__setattr__(self, "liquidity_role", LiquidityRole(self.liquidity_role))
        object.__setattr__(
            self,
            "source_evidence_identity",
            _non_empty_text(self.source_evidence_identity, "source_evidence_identity"),
        )

    @classmethod
    def create(
        cls,
        *,
        order_id: str,
        fill_time: Instant | str,
        price: Decimal | str | int,
        quantity: Decimal | str | int,
        fee: Decimal | str | int,
        liquidity_role: LiquidityRole | str,
        source_evidence_identity: str,
    ) -> "Fill":
        normalized_order_id = _identity_text(order_id, "order_id")
        normalized_time = Instant.parse(fill_time)
        normalized_price = _decimal(price, "price", allow_zero=False)
        normalized_quantity = _decimal(quantity, "quantity", allow_zero=False)
        normalized_fee = _decimal(fee, "fee")
        normalized_role = LiquidityRole(liquidity_role)
        normalized_evidence = _non_empty_text(source_evidence_identity, "source_evidence_identity")
        fill_id = _compute_fill_id(
            order_id=normalized_order_id,
            fill_time=normalized_time,
            price=normalized_price,
            quantity=normalized_quantity,
            fee=normalized_fee,
            liquidity_role=normalized_role,
            source_evidence_identity=normalized_evidence,
        )
        return cls(
            fill_id=fill_id,
            order_id=normalized_order_id,
            fill_time=normalized_time,
            price=normalized_price,
            quantity=normalized_quantity,
            fee=normalized_fee,
            liquidity_role=normalized_role,
            source_evidence_identity=normalized_evidence,
        )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "fill_id": self.fill_id,
            "order_id": self.order_id,
            "fill_time": self.fill_time.isoformat(),
            "price": _decimal_string(self.price),
            "quantity": _decimal_string(self.quantity),
            "fee": _decimal_string(self.fee),
            "liquidity_role": self.liquidity_role.value,
            "source_evidence_identity": self.source_evidence_identity,
        }

    @property
    def identity(self) -> str:
        return self.fill_id


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    """Parameterized maker/taker fee rates, applied to fill notional."""

    policy_key: str
    maker_fee_rate: Decimal | str | int = _DEFAULT_MAKER_FEE_RATE
    taker_fee_rate: Decimal | str | int = _DEFAULT_TAKER_FEE_RATE

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_key", _key(self.policy_key, "fee schedule key"))
        object.__setattr__(self, "maker_fee_rate", _decimal(self.maker_fee_rate, "maker_fee_rate"))
        object.__setattr__(self, "taker_fee_rate", _decimal(self.taker_fee_rate, "taker_fee_rate"))

    def fee_for(self, *, notional: Decimal | str | int, liquidity_role: LiquidityRole | str) -> Decimal:
        notional_amount = _decimal(notional, "notional")
        role = LiquidityRole(liquidity_role)
        rate = self.maker_fee_rate if role is LiquidityRole.MAKER else self.taker_fee_rate
        return notional_amount * rate

    def stable_dict(self) -> dict[str, Any]:
        return {
            "policy_type": FEE_SCHEDULE_IDENTITY_DOMAIN,
            "policy_key": self.policy_key,
            "maker_fee_rate": _decimal_string(self.maker_fee_rate),
            "taker_fee_rate": _decimal_string(self.taker_fee_rate),
        }

    @property
    def identity(self) -> str:
        return f"{FEE_SCHEDULE_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class SyntheticSlippageModel:
    """Deterministic fixed-basis-point slippage applied against a reference price.

    v1 supports ``FIXED_BPS`` only; spread/volatility-scaled slippage is not
    designed now and is not precluded by this shape.
    """

    policy_key: str
    slippage_bps: Decimal | str | int
    kind: SlippageModelKind = SlippageModelKind.FIXED_BPS

    def __post_init__(self) -> None:
        object.__setattr__(self, "policy_key", _key(self.policy_key, "slippage model key"))
        object.__setattr__(self, "slippage_bps", _decimal(self.slippage_bps, "slippage_bps"))
        object.__setattr__(self, "kind", SlippageModelKind(self.kind))

    def adjusted_price(self, *, reference_price: Decimal | str | int, side: OrderSide | str) -> Decimal:
        price = _decimal(reference_price, "reference_price", allow_zero=False)
        normalized_side = OrderSide(side)
        adjustment = price * self.slippage_bps / Decimal("10000")
        if normalized_side is OrderSide.BUY:
            return price + adjustment
        return price - adjustment

    def stable_dict(self) -> dict[str, Any]:
        return {
            "policy_type": SLIPPAGE_MODEL_IDENTITY_DOMAIN,
            "policy_key": self.policy_key,
            "kind": self.kind.value,
            "slippage_bps": _decimal_string(self.slippage_bps),
        }

    @property
    def identity(self) -> str:
        return f"{SLIPPAGE_MODEL_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


def build_fill(
    order: Order,
    *,
    fill_time: Instant | str,
    quantity: Decimal | str | int,
    reference_price: Decimal | str | int,
    liquidity_role: LiquidityRole | str,
    fee_schedule: FeeSchedule,
    source_evidence_identity: str,
    slippage_model: SyntheticSlippageModel | None = None,
) -> Fill:
    """Build a deterministic Fill for ``order`` from an explicit reference price.

    Fee and (optional) slippage are computed exactly with Decimal arithmetic;
    no rounding is applied beyond what the inputs themselves carry.
    """

    if not isinstance(order, Order):
        raise ExecutionError("order must be an Order")
    if not isinstance(fee_schedule, FeeSchedule):
        raise ExecutionError("fee_schedule must be a FeeSchedule")
    normalized_quantity = _decimal(quantity, "quantity", allow_zero=False)
    if normalized_quantity > order.open_quantity:
        raise ExecutionError("fill quantity must not exceed the order's open quantity")
    normalized_role = LiquidityRole(liquidity_role)
    price = (
        _decimal(reference_price, "reference_price", allow_zero=False)
        if slippage_model is None
        else slippage_model.adjusted_price(reference_price=reference_price, side=order.side)
    )
    notional = normalized_quantity * price
    fee = fee_schedule.fee_for(notional=notional, liquidity_role=normalized_role)
    return Fill.create(
        order_id=order.order_id,
        fill_time=fill_time,
        price=price,
        quantity=normalized_quantity,
        fee=fee,
        liquidity_role=normalized_role,
        source_evidence_identity=source_evidence_identity,
    )


def validate_leg_quantity_conservation(
    *,
    legs: Iterable[Order],
    open_position_quantity: Decimal | str | int,
) -> None:
    """Enforce ADR-0046 SS4: sum of active independent-leg quantities <= open quantity.

    A reduce-only order with no siblings is the degenerate N=1 case of this
    same mechanism; this check applies identically whether one or many legs
    are passed. Refuses explicitly rather than silently clamping.
    """

    limit = _decimal(open_position_quantity, "open_position_quantity")
    total = Decimal("0")
    for leg in legs:
        if not isinstance(leg, Order):
            raise ExecutionError("legs must contain Order values")
        if leg.is_open:
            total += leg.open_quantity
    if total > limit:
        raise ExecutionError(
            "sum of active independent leg quantities exceeds the open position quantity"
        )


@dataclass(frozen=True, slots=True)
class OrderAdmission:
    """Result of evaluating one DecisionIntent against G03/G04 policies.

    Exactly one of ``order`` (ADMITTED) or a non-empty ``reasons`` (REFUSED)
    holds -- never both, never neither.
    """

    outcome: AdmissionOutcome
    reasons: tuple[AdmissionRefusalReason, ...]
    order: Order | None
    risk_decision: RiskDecision | None
    sizing_decision: SizingDecision | None
    session_decisions: tuple[SessionDecision, ...]
    cooldown_decision: "CooldownDecision | CooldownDecisionUnavailable | None"

    def __post_init__(self) -> None:
        if self.outcome is AdmissionOutcome.ADMITTED:
            if self.order is None or self.reasons:
                raise ExecutionError("ADMITTED admission must carry an order and no refusal reasons")
        else:
            if self.order is not None or not self.reasons:
                raise ExecutionError("REFUSED admission must carry no order and at least one reason")

    @property
    def admitted(self) -> bool:
        return self.outcome is AdmissionOutcome.ADMITTED

    def stable_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome.value,
            "reasons": [reason.value for reason in self.reasons],
            "order": None if self.order is None else self.order.stable_dict(),
            "risk_decision": None if self.risk_decision is None else self.risk_decision.stable_dict(),
            "sizing_decision": None if self.sizing_decision is None else self.sizing_decision.stable_dict(),
            "session_decisions": [decision.stable_dict() for decision in self.session_decisions],
            "cooldown_decision": (
                None if self.cooldown_decision is None else self.cooldown_decision.stable_dict()
            ),
        }


def _refused(
    reasons: tuple[AdmissionRefusalReason, ...],
    *,
    risk_decision: RiskDecision | None = None,
    sizing_decision: SizingDecision | None = None,
    session_decisions: tuple[SessionDecision, ...] = (),
    cooldown_decision: "CooldownDecision | CooldownDecisionUnavailable | None" = None,
) -> OrderAdmission:
    return OrderAdmission(
        outcome=AdmissionOutcome.REFUSED,
        reasons=reasons,
        order=None,
        risk_decision=risk_decision,
        sizing_decision=sizing_decision,
        session_decisions=session_decisions,
        cooldown_decision=cooldown_decision,
    )


def translate_intent(
    intent: DecisionIntent,
    spec: StrategySpec,
    *,
    submitted_at: Instant | str,
    reference_price: Decimal | str | int,
    risk_snapshot: RiskSnapshot | None,
    trade_history: TradeHistoryEvidence | None,
    provenance: str,
    time_in_force: TimeInForce | str = TimeInForce.GTC,
    close_side: OrderSide | str | None = None,
    close_quantity: Decimal | str | int | None = None,
) -> OrderAdmission:
    """Evaluate G04 (session/cooldown) and G03 (risk/sizing) and admit-or-refuse an Order.

    This is the seam #141/#142 left open: ``compose_decision`` never consults
    ``spec``'s session/cooldown/risk/sizing policies, so nothing upstream of
    this function enforces them. An entry (``LONG``/``SHORT``) is sized from
    ``spec.sizing_policy``; the quantity/side to close for an exit (``FLAT``)
    intent cannot be derived here -- this package has no position/ledger
    state (that is H04, issue #144) -- so ``close_side``/``close_quantity``
    must be supplied explicitly by the caller for exits.

    Order type is fixed to ``MARKET`` in v1: ``StrategySpec.execution_policy``
    is still the generic identity-backed slot (unresolved concrete type), so
    no price-policy signal is available yet to pick a different order type.
    """

    if not isinstance(intent, DecisionIntent):
        raise ExecutionError("intent must be a DecisionIntent")
    if not isinstance(spec, StrategySpec):
        raise ExecutionError("spec must be a StrategySpec")
    if spec.strategy_identity != intent.strategy_identity:
        raise ExecutionError("spec does not match intent.strategy_identity")
    evaluation_time = Instant.parse(submitted_at)
    if evaluation_time < intent.decision_time:
        raise ExecutionError("submitted_at must not precede decision_time")

    is_entry = intent.direction is not Direction.FLAT
    reasons: list[AdmissionRefusalReason] = []

    session_decisions = spec.session_policy.evaluate(evaluation_time)
    if is_entry and not all(decision.open for decision in session_decisions):
        reasons.append(AdmissionRefusalReason.SESSION_CLOSED)

    cooldown_decision = spec.cooldown_policy.evaluate(
        trade_history=trade_history,
        as_of=evaluation_time,
        requires_new_entry=is_entry,
    )
    if is_entry:
        if isinstance(cooldown_decision, CooldownDecisionUnavailable):
            reasons.append(AdmissionRefusalReason.COOLDOWN_UNAVAILABLE)
        elif not cooldown_decision.new_entries_allowed:
            reasons.append(AdmissionRefusalReason.COOLDOWN_ACTIVE)

    risk_decision = spec.risk_policy.evaluate(
        snapshot=risk_snapshot,
        as_of=evaluation_time,
        reference_price=reference_price,
        target_position=intent.target_position,
    )
    if not risk_decision.accepted:
        reasons.append(AdmissionRefusalReason.RISK_REFUSED)

    sizing_decision = spec.sizing_policy.evaluate(
        risk_decision=risk_decision,
        reference_price=reference_price,
        target_position=intent.target_position,
    )

    if is_entry:
        quantity = sizing_decision.size
        side = _side_for_direction(intent.direction)
        if quantity == 0:
            reasons.append(AdmissionRefusalReason.SIZING_ZERO)
    else:
        if close_side is None or close_quantity is None:
            raise ExecutionError(
                "close_side and close_quantity are required to admit an exit (FLAT) DecisionIntent"
            )
        side = OrderSide(close_side)
        quantity = _decimal(close_quantity, "close_quantity", allow_zero=False)

    if reasons:
        return _refused(
            tuple(reasons),
            risk_decision=risk_decision,
            sizing_decision=sizing_decision,
            session_decisions=session_decisions,
            cooldown_decision=cooldown_decision,
        )

    order = Order.create(
        instrument=intent.instrument,
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        submitted_at=evaluation_time,
        provenance=provenance,
        time_in_force=time_in_force,
        reduce_only=not is_entry,
    )
    return OrderAdmission(
        outcome=AdmissionOutcome.ADMITTED,
        reasons=(),
        order=order,
        risk_decision=risk_decision,
        sizing_decision=sizing_decision,
        session_decisions=session_decisions,
        cooldown_decision=cooldown_decision,
    )


def admit_composition_result(
    result: StrategyCompositionResult,
    spec: StrategySpec,
    **kwargs: Any,
) -> OrderAdmission:
    """Convenience seam directly over G02's own output type.

    If ``result`` carries a ``NoDecision``, refuses immediately with
    ``NO_DECISION`` -- no session/cooldown/risk/sizing evaluation is
    meaningful without a directional intent to gate.
    """

    if not isinstance(result, StrategyCompositionResult):
        raise ExecutionError("result must be a StrategyCompositionResult")
    if result.no_decision is not None:
        return _refused((AdmissionRefusalReason.NO_DECISION,))
    if not isinstance(result.decision_intent, DecisionIntent):
        raise ExecutionError("composition result carries neither a decision nor a no-decision")
    return translate_intent(result.decision_intent, spec, **kwargs)


__all__ = [
    "AdmissionOutcome",
    "AdmissionRefusalReason",
    "ExecutionError",
    "FeeSchedule",
    "Fill",
    "LiquidityRole",
    "Order",
    "OrderAdmission",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "SlippageModelKind",
    "SyntheticSlippageModel",
    "TimeInForce",
    "admit_composition_result",
    "build_fill",
    "translate_intent",
    "validate_leg_quantity_conservation",
]
