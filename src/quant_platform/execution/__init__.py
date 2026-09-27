"""Order/Fill lifecycle, fee/slippage cost models, and conflict resolution v1.

This package owns H01/H02/H03: the executable ``Order`` state machine, the
``Fill`` value type, a parameterized fee schedule, a deterministic synthetic
slippage model, the seam translating a Strategy ``DecisionIntent`` into an
admitted (or explicitly refused) ``Order`` (evaluating ``StrategySpec``'s
session, cooldown, risk and sizing policies), the intra-bar chronological
conflict resolver (ADR-0046 SS1), OCO entry-group cancellation cascades
(ADR-0046 SS3), and independent multileg exit-leg management (ADR-0046 SS4).

It does not implement double-entry portfolio/ledger accounting (H04, owned
by ``quant_platform.portfolio``), historical replay orchestration (H05), or
live venue adapters -- all out of scope for this package.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import hashlib
import json
import re
from types import MappingProxyType
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
    last_fill_time: Instant | None = None
    updated_at: Instant | None = None

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
        if self.last_fill_time is not None:
            last_fill_time = Instant.parse(self.last_fill_time)
            if last_fill_time < self.submitted_at:
                raise ExecutionError("last_fill_time must not precede order submission")
            object.__setattr__(self, "last_fill_time", last_fill_time)
        if self.updated_at is not None:
            updated_at = Instant.parse(self.updated_at)
            if updated_at < self.submitted_at:
                raise ExecutionError("updated_at must not precede order submission")
            object.__setattr__(self, "updated_at", updated_at)

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
        if not self.is_open:
            return Decimal("0")
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
        if self.last_fill_time is not None and fill.fill_time < self.last_fill_time:
            raise ExecutionError("fill_time must not precede the order's previous fill")
        new_filled = self.filled_quantity + fill.quantity
        if new_filled > self.quantity:
            raise ExecutionError("fill would exceed order quantity")
        new_status = OrderStatus.FILLED if new_filled == self.quantity else OrderStatus.PARTIALLY_FILLED
        self._require_transition(new_status)
        return replace(self, filled_quantity=new_filled, status=new_status, last_fill_time=fill.fill_time)

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
        if self.last_fill_time is not None and replace_at < self.last_fill_time:
            raise ExecutionError("replace instant must not precede the order's last fill")
        if self.updated_at is not None and replace_at < self.updated_at:
            raise ExecutionError("replace instant must not precede the order's previous replace")
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
        updates["updated_at"] = replace_at
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
            "last_fill_time": None if self.last_fill_time is None else self.last_fill_time.isoformat(),
            "updated_at": None if self.updated_at is None else self.updated_at.isoformat(),
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
    if order.status not in _FILLABLE_STATUSES:
        raise ExecutionError(f"cannot build fill for order in status {order.status.value}")
    normalized_fill_time = Instant.parse(fill_time)
    if normalized_fill_time < order.submitted_at:
        raise ExecutionError("fill_time must not precede order submission")
    if order.last_fill_time is not None and normalized_fill_time < order.last_fill_time:
        raise ExecutionError("fill_time must not precede the order's previous fill")
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
        fill_time=normalized_fill_time,
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
        object.__setattr__(self, "outcome", AdmissionOutcome(self.outcome))
        object.__setattr__(
            self, "reasons", tuple(AdmissionRefusalReason(reason) for reason in self.reasons)
        )
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
    if is_entry and not risk_decision.accepted:
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


# ---------------------------------------------------------------------------
# H03: intra-bar chronological conflict resolution (ADR-0046 SS1), OCO entry
# groups (SS3), and independent multileg exit management (SS4).
# ---------------------------------------------------------------------------


class TriggerRole(StrEnum):
    """Tie-break priority when >1 trigger fires on the exact same price event.

    Lower priority value wins ties (STOP_LOSS is the most conservative
    outcome and always wins over ENTRY or TAKE_PROFIT per ADR-0046 SS1).
    """

    STOP_LOSS = "STOP_LOSS"
    ENTRY = "ENTRY"
    TAKE_PROFIT = "TAKE_PROFIT"


_TRIGGER_ROLE_PRIORITY: dict[TriggerRole, int] = {
    TriggerRole.STOP_LOSS: 0,
    TriggerRole.ENTRY: 1,
    TriggerRole.TAKE_PROFIT: 2,
}


class TriggerDirection(StrEnum):
    AT_OR_BELOW = "AT_OR_BELOW"
    AT_OR_ABOVE = "AT_OR_ABOVE"


@dataclass(frozen=True, slots=True)
class PriceEvent:
    """One ordered chronological trade-path event within a bar (ADR-0046 SS1).

    Deliberately minimal and execution-owned (not ``TradeRecord``): the
    intra-bar engine only needs an ordered ``(exchange_ts, trade_id)`` stream
    and a price, per the ADR's forward-compatibility note -- adapting a real
    dataset's trade stream into this shape is the replay orchestration's job
    (H05, issue #145), not this package's.
    """

    exchange_ts: Instant
    trade_id: str
    price: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "exchange_ts", Instant.parse(self.exchange_ts))
        object.__setattr__(self, "trade_id", _non_empty_text(self.trade_id, "trade_id"))
        object.__setattr__(self, "price", _decimal(self.price, "price", allow_zero=False))

    @property
    def sort_key(self) -> tuple[int, str]:
        return (self.exchange_ts.epoch_ns, self.trade_id)


@dataclass(frozen=True, slots=True)
class PendingTrigger:
    """One resting order awaiting a triggering price event within a bar.

    ``conflict_group`` scopes the tie-break rule: only triggers sharing the
    same group (e.g. one position side's stop + targets) compete for
    conservative precedence on a single event. Unrelated triggers (different
    groups) never suppress each other even if they fire on the same event.
    """

    order: Order
    trigger_price: Decimal
    direction: TriggerDirection
    role: TriggerRole
    conflict_group: str

    def __post_init__(self) -> None:
        if not isinstance(self.order, Order):
            raise ExecutionError("order must be an Order")
        object.__setattr__(self, "trigger_price", _decimal(self.trigger_price, "trigger_price", allow_zero=False))
        object.__setattr__(self, "direction", TriggerDirection(self.direction))
        object.__setattr__(self, "role", TriggerRole(self.role))
        object.__setattr__(self, "conflict_group", _key(self.conflict_group, "conflict_group"))

    def is_triggered_by(self, price: Decimal) -> bool:
        if self.direction is TriggerDirection.AT_OR_BELOW:
            return price <= self.trigger_price
        return price >= self.trigger_price


@dataclass(frozen=True, slots=True)
class TriggerEvent:
    """One ``PendingTrigger`` resolved as fired against one ``PriceEvent``."""

    trigger: PendingTrigger
    price_event: PriceEvent


def _trigger_sort_key(trigger: PendingTrigger) -> tuple[str, int, Decimal, int, str]:
    """Total, content-derived order: never depends on caller-supplied list order."""

    return (
        trigger.conflict_group,
        _TRIGGER_ROLE_PRIORITY[trigger.role],
        trigger.trigger_price,
        trigger.order.submitted_at.epoch_ns,
        trigger.order.order_id,
    )


def resolve_intra_bar_triggers(
    pending: Iterable[PendingTrigger],
    events: Iterable[PriceEvent],
) -> tuple[TriggerEvent, ...]:
    """Walk ``events`` in canonical ``(exchange_ts, trade_id)`` order (ADR-0046
    SS1). A still-pending trigger fires on the first event that crosses its
    level. If more than one trigger *in the same conflict_group* fires on the
    exact same event, only the most conservative role fires (STOP_LOSS before
    ENTRY before TAKE_PROFIT); ties within the same role are broken by
    ``_trigger_sort_key`` (trigger_price, then submitted_at, then order_id) --
    never by caller-supplied list order. Losing candidates remain pending for
    a later event -- never bar-level high/low heuristics, only the real
    reconstructed path.

    Pure and deterministic: identical ``pending``/``events`` (in any input
    order) always produce the identical resolution sequence.
    """

    ordered_events = sorted(events, key=lambda event: event.sort_key)
    remaining = sorted(pending, key=_trigger_sort_key)
    resolved: list[TriggerEvent] = []
    for event in ordered_events:
        if not remaining:
            break
        still_remaining: list[PendingTrigger] = []
        candidates_by_group: dict[str, list[PendingTrigger]] = {}
        for trigger in remaining:
            if trigger.is_triggered_by(event.price):
                candidates_by_group.setdefault(trigger.conflict_group, []).append(trigger)
            else:
                still_remaining.append(trigger)
        for group in sorted(candidates_by_group):
            group_candidates = candidates_by_group[group]  # already sorted: see remaining's construction
            winner, *losers = group_candidates
            resolved.append(TriggerEvent(trigger=winner, price_event=event))
            still_remaining.extend(losers)
        remaining = sorted(still_remaining, key=_trigger_sort_key)
    return tuple(resolved)


def _withdraw_if_open(order: Order) -> Order:
    """Terminate ``order`` if still open, using whichever transition is legal.

    A never-acknowledged order (``PENDING_NEW``) cannot be cancelled -- only
    ``NEW``/``REJECTED`` are legal next states for it -- so it is rejected
    instead; an already-resting order (``NEW``/``PARTIALLY_FILLED``) is
    cancelled as usual. A terminal order is returned unchanged.
    """

    if order.status is OrderStatus.PENDING_NEW:
        return order.reject()
    if order.is_open:
        return order.cancel()
    return order


@dataclass(frozen=True, slots=True)
class OcoGroup:
    """A dynamic One-Cancels-Other entry group (ADR-0046 SS3).

    Any fill (full or partial) of any member immediately cascades
    cancellation to every other current member. No sub-groups: membership is
    always a flat set of ``Order`` values.
    """

    group_id: str
    members: Mapping[str, Order]

    def __post_init__(self) -> None:
        object.__setattr__(self, "group_id", _key(self.group_id, "oco group_id"))
        if not isinstance(self.members, Mapping):
            raise ExecutionError("members must be a mapping of order_id -> Order")
        normalized: dict[str, Order] = {}
        for order_id, order in self.members.items():
            if not isinstance(order, Order):
                raise ExecutionError("OCO members must be Order values")
            if order.order_id != order_id:
                raise ExecutionError("members mapping key must match order.order_id")
            normalized[order_id] = order
        object.__setattr__(self, "members", MappingProxyType(normalized))

    @classmethod
    def create(cls, group_id: str, orders: Iterable[Order]) -> "OcoGroup":
        return cls(group_id=group_id, members={order.order_id: order for order in orders})

    def add_leg(self, order: Order) -> "OcoGroup":
        if not isinstance(order, Order):
            raise ExecutionError("order must be an Order")
        if order.order_id in self.members:
            raise ExecutionError("order is already a member of this OCO group")
        updated = dict(self.members)
        updated[order.order_id] = order
        return OcoGroup(group_id=self.group_id, members=updated)

    def remove_leg(self, order_id: str) -> "OcoGroup":
        if order_id not in self.members:
            raise ExecutionError("order_id is not a member of this OCO group")
        updated = dict(self.members)
        del updated[order_id]
        return OcoGroup(group_id=self.group_id, members=updated)

    def apply_fill(self, fill: "Fill") -> "OcoGroup":
        """Apply ``fill`` to its member order; cascade-cancel every sibling."""

        if not isinstance(fill, Fill):
            raise ExecutionError("fill must be a Fill")
        target = self.members.get(fill.order_id)
        if target is None:
            raise ExecutionError("fill.order_id is not a member of this OCO group")
        updated_members = {fill.order_id: target.apply_fill(fill)}
        for order_id, order in self.members.items():
            if order_id == fill.order_id:
                continue
            updated_members[order_id] = _withdraw_if_open(order)
        return OcoGroup(group_id=self.group_id, members=updated_members)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "members": {order_id: order.stable_dict() for order_id, order in self.members.items()},
        }


@dataclass(frozen=True, slots=True)
class ExitLegManager:
    """Independent multileg exit management for one position side (ADR-0046 SS4).

    Reuses ``validate_leg_quantity_conservation`` (the same invariant #143
    already enforces standalone) as the admission gate for every new leg, so
    the two never diverge. Filling one leg never touches any sibling.
    """

    position_key: str
    legs: Mapping[str, Order]

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_key", _key(self.position_key, "position_key"))
        if not isinstance(self.legs, Mapping):
            raise ExecutionError("legs must be a mapping of order_id -> Order")
        normalized: dict[str, Order] = {}
        for order_id, order in self.legs.items():
            if not isinstance(order, Order):
                raise ExecutionError("legs must contain Order values")
            if not order.reduce_only:
                raise ExecutionError("exit legs must be reduce_only orders")
            if order.order_id != order_id:
                raise ExecutionError("legs mapping key must match order.order_id")
            normalized[order_id] = order
        object.__setattr__(self, "legs", MappingProxyType(normalized))

    @classmethod
    def create(cls, position_key: str, legs: Iterable[Order] = ()) -> "ExitLegManager":
        return cls(position_key=position_key, legs={order.order_id: order for order in legs})

    def add_leg(self, order: Order, *, open_position_quantity: Decimal | str | int) -> "ExitLegManager":
        if not isinstance(order, Order):
            raise ExecutionError("order must be an Order")
        if not order.reduce_only:
            raise ExecutionError("exit legs must be reduce_only orders")
        if order.order_id in self.legs:
            raise ExecutionError("order is already a member exit leg")
        candidate_legs = [*self.legs.values(), order]
        validate_leg_quantity_conservation(legs=candidate_legs, open_position_quantity=open_position_quantity)
        updated = dict(self.legs)
        updated[order.order_id] = order
        return ExitLegManager(position_key=self.position_key, legs=updated)

    def apply_fill(self, fill: "Fill") -> "ExitLegManager":
        """Apply ``fill`` to its member leg only; siblings are never touched."""

        if not isinstance(fill, Fill):
            raise ExecutionError("fill must be a Fill")
        target = self.legs.get(fill.order_id)
        if target is None:
            raise ExecutionError("fill.order_id is not a member exit leg")
        updated = dict(self.legs)
        updated[fill.order_id] = target.apply_fill(fill)
        return ExitLegManager(position_key=self.position_key, legs=updated)

    def remove_leg(self, order_id: str) -> "ExitLegManager":
        if order_id not in self.legs:
            raise ExecutionError("order_id is not a member exit leg")
        updated = dict(self.legs)
        del updated[order_id]
        return ExitLegManager(position_key=self.position_key, legs=updated)

    @property
    def active_open_quantity(self) -> Decimal:
        return sum((leg.open_quantity for leg in self.legs.values() if leg.is_open), Decimal("0"))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "position_key": self.position_key,
            "legs": {order_id: order.stable_dict() for order_id, order in self.legs.items()},
        }


__all__ = [
    "AdmissionOutcome",
    "AdmissionRefusalReason",
    "ExecutionError",
    "ExitLegManager",
    "FeeSchedule",
    "Fill",
    "LiquidityRole",
    "OcoGroup",
    "Order",
    "OrderAdmission",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "PendingTrigger",
    "PriceEvent",
    "SlippageModelKind",
    "SyntheticSlippageModel",
    "TimeInForce",
    "TriggerDirection",
    "TriggerEvent",
    "TriggerRole",
    "admit_composition_result",
    "build_fill",
    "resolve_intra_bar_triggers",
    "translate_intent",
    "validate_leg_quantity_conservation",
]
