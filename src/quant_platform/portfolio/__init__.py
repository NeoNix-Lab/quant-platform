"""Hedge-mode positions and the double-entry portfolio ledger v1 (H04).

This package owns H04: independent concurrent LONG/SHORT positions per
instrument (ADR-0046 SS2, "hedge-mode" as implemented natively by major
crypto perpetual venues), and a double-entry accounting ledger over
``quant_platform.execution``'s ``Order``/``Fill`` evidence, guaranteeing
exact ``Decimal`` conservation of the core accounting identities:

    Equity_t = Cash_t + sum(PositionValue_i,t)
    dEquity  = RealizedPnL + dUnrealizedPnL - Fees

A fill's effect on the ledger (open a side vs. reduce/close a side) is
disambiguated purely from ``Order.side`` and ``Order.reduce_only`` -- the
same convention ``quant_platform.execution.translate_intent`` already uses
(``reduce_only=False`` => open/add; ``reduce_only=True`` => reduce/close the
opposite side) -- so no new position-side field is needed on Order/Fill.

It does not implement intra-bar conflict resolution or OCO/exit-leg
management (H03, owned by ``quant_platform.execution``), historical replay
orchestration (H05), or margin/multi-asset mechanics (H06, out of scope).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
import hashlib
import json
import re
from types import MappingProxyType
from typing import Any

from ..data.models import Instant
from ..execution import ExecutionError, Fill, Order, OrderSide


POSITION_SIDE_IDENTITY_DOMAIN = "position-side-v1"
LEDGER_TRANSACTION_IDENTITY_DOMAIN = "ledger-transaction-v1"
LEDGER_IDENTITY_DOMAIN = "portfolio-ledger-v1"

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$")


class PortfolioError(ValueError):
    """A Position/Ledger v1 semantic value or transition violates the contract."""


class PositionDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class AccountType(StrEnum):
    CASH = "CASH"
    CAPITAL = "CAPITAL"
    POSITION_ASSET = "POSITION_ASSET"
    REALIZED_PNL = "REALIZED_PNL"
    FEE_EXPENSE = "FEE_EXPENSE"


class EntrySide(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


def _non_empty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PortfolioError(f"{field} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise PortfolioError(f"{field} must not contain control characters")
    return text


def _key(value: Any, field: str) -> str:
    text = _non_empty_text(value, field)
    if not _KEY_RE.fullmatch(text):
        raise PortfolioError(f"{field} must be a governed canonical key")
    return text


def _decimal(value: Any, field: str, *, allow_zero: bool = True) -> Decimal:
    if isinstance(value, bool):
        raise PortfolioError(f"{field} must be numeric")
    if isinstance(value, Decimal):
        text = str(value)
    elif type(value) is int:
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise PortfolioError(f"{field} must be a Decimal, integer or decimal string")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise PortfolioError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise PortfolioError(f"{field} must be finite")
    if result < 0 or (result == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise PortfolioError(f"{field} must be {qualifier}")
    return result


def _signed_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise PortfolioError(f"{field} must be numeric")
    if isinstance(value, Decimal):
        text = str(value)
    elif type(value) is int:
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise PortfolioError(f"{field} must be a Decimal, integer or decimal string")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise PortfolioError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise PortfolioError(f"{field} must be finite")
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
                raise PortfolioError("canonical mapping keys must be non-empty strings")
            result[key] = _canonical_value(item)
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise PortfolioError(f"unsupported canonical value type: {type(value).__name__}")


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


def _direction_for_fill(order_side: OrderSide, *, is_increase: bool) -> PositionDirection:
    """Hedge-mode fill disambiguation (no new Order/Fill field required).

    ``reduce_only=False`` (an entry per translate_intent's own convention)
    always opens/adds to the side matching the raw trade direction;
    ``reduce_only=True`` (an exit) always reduces the *opposite* side.
    """

    if is_increase:
        return PositionDirection.LONG if order_side is OrderSide.BUY else PositionDirection.SHORT
    return PositionDirection.SHORT if order_side is OrderSide.BUY else PositionDirection.LONG


@dataclass(frozen=True, slots=True)
class PositionSide:
    """One side (LONG or SHORT) of one instrument's hedge-mode position."""

    instrument: str
    direction: PositionDirection
    quantity: Decimal
    average_basis: Decimal
    realized_pnl: Decimal
    opened_at: Instant | None
    updated_at: Instant
    provenance: str
    cost_basis: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument", _non_empty_text(self.instrument, "instrument"))
        object.__setattr__(self, "direction", PositionDirection(self.direction))
        object.__setattr__(self, "quantity", _decimal(self.quantity, "quantity"))
        object.__setattr__(self, "average_basis", _decimal(self.average_basis, "average_basis"))
        object.__setattr__(self, "cost_basis", _decimal(self.cost_basis, "cost_basis"))
        if self.quantity > 0 and self.average_basis == 0:
            raise PortfolioError("average_basis must be positive whenever quantity is open")
        if self.quantity == 0 and self.average_basis != 0:
            raise PortfolioError("average_basis must be zero when quantity is flat")
        if self.quantity > 0 and self.cost_basis == 0 and self.average_basis > 0:
            object.__setattr__(self, "cost_basis", self.quantity * self.average_basis)
        if self.quantity == 0 and self.cost_basis != 0:
            raise PortfolioError("cost_basis must be zero when quantity is flat")
        object.__setattr__(self, "realized_pnl", _signed_decimal(self.realized_pnl, "realized_pnl"))
        if self.opened_at is not None:
            object.__setattr__(self, "opened_at", Instant.parse(self.opened_at))
        elif self.quantity != 0:
            raise PortfolioError("opened_at is required whenever quantity is open")
        object.__setattr__(self, "updated_at", Instant.parse(self.updated_at))
        object.__setattr__(self, "provenance", _non_empty_text(self.provenance, "provenance"))

    @classmethod
    def flat(
        cls,
        *,
        instrument: str,
        direction: PositionDirection | str,
        as_of: Instant | str,
        provenance: str,
    ) -> "PositionSide":
        return cls(
            instrument=instrument,
            direction=PositionDirection(direction),
            quantity=Decimal("0"),
            average_basis=Decimal("0"),
            realized_pnl=Decimal("0"),
            opened_at=None,
            updated_at=Instant.parse(as_of),
            provenance=provenance,
            cost_basis=Decimal("0"),
        )

    def unrealized_pnl(self, mark_price: Decimal | str | int) -> Decimal:
        price = _decimal(mark_price, "mark_price", allow_zero=False)
        if self.quantity == 0:
            return Decimal("0")
        if self.direction is PositionDirection.LONG:
            return (price - self.average_basis) * self.quantity
        return (self.average_basis - price) * self.quantity

    def market_value(self, mark_price: Decimal | str | int) -> Decimal:
        """Mark-to-market carrying value: cost basis plus unrealized PnL.

        Deliberately not ``mark_price * quantity`` -- that naive formula only
        coincides with this one for LONG; for SHORT it has the wrong sign
        sensitivity to price moves (see PR discussion / H04 design notes).
        """

        return self.cost_basis + self.unrealized_pnl(mark_price)

    def apply_increase(
        self,
        *,
        quantity: Decimal | str | int,
        price: Decimal | str | int,
        at: Instant | str,
        provenance: str,
    ) -> "PositionSide":
        qty = _decimal(quantity, "quantity", allow_zero=False)
        px = _decimal(price, "price", allow_zero=False)
        at_instant = Instant.parse(at)
        new_quantity = self.quantity + qty
        new_cost_basis = self.cost_basis + (px * qty)
        new_basis = new_cost_basis / new_quantity
        return replace(
            self,
            quantity=new_quantity,
            average_basis=new_basis,
            cost_basis=new_cost_basis,
            opened_at=self.opened_at or at_instant,
            updated_at=at_instant,
            provenance=provenance,
        )

    def apply_decrease(
        self,
        *,
        quantity: Decimal | str | int,
        price: Decimal | str | int,
        at: Instant | str,
        provenance: str,
    ) -> tuple["PositionSide", Decimal]:
        qty = _decimal(quantity, "quantity", allow_zero=False)
        if qty > self.quantity:
            raise PortfolioError("cannot reduce a position side by more than its open quantity")
        px = _decimal(price, "price", allow_zero=False)
        at_instant = Instant.parse(at)
        new_quantity = self.quantity - qty
        if new_quantity == 0:
            cost_basis_removed = self.cost_basis
            new_cost_basis = Decimal("0")
            new_basis = Decimal("0")
            if self.direction is PositionDirection.LONG:
                delta = (px * qty) - cost_basis_removed
            else:
                delta = cost_basis_removed - (px * qty)
        else:
            cost_basis_removed = qty * self.average_basis
            new_cost_basis = self.cost_basis - cost_basis_removed
            new_basis = self.average_basis
            if self.direction is PositionDirection.LONG:
                delta = (px - self.average_basis) * qty
            else:
                delta = (self.average_basis - px) * qty
        new_side = replace(
            self,
            quantity=new_quantity,
            average_basis=new_basis,
            cost_basis=new_cost_basis,
            realized_pnl=self.realized_pnl + delta,
            opened_at=self.opened_at if new_quantity > 0 else None,
            updated_at=at_instant,
            provenance=provenance,
        )
        return new_side, delta

    def stable_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "direction": self.direction.value,
            "quantity": _decimal_string(self.quantity),
            "average_basis": _decimal_string(self.average_basis),
            "cost_basis": _decimal_string(self.cost_basis),
            "realized_pnl": _decimal_string(self.realized_pnl),
            "opened_at": None if self.opened_at is None else self.opened_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "provenance": self.provenance,
        }

    @property
    def identity(self) -> str:
        return f"{POSITION_SIDE_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class HedgePosition:
    """Both independent sides (LONG and SHORT) of one instrument (ADR-0046 SS2)."""

    instrument: str
    long: PositionSide
    short: PositionSide

    def __post_init__(self) -> None:
        object.__setattr__(self, "instrument", _non_empty_text(self.instrument, "instrument"))
        if not isinstance(self.long, PositionSide) or self.long.direction is not PositionDirection.LONG:
            raise PortfolioError("long must be a PositionSide with direction LONG")
        if not isinstance(self.short, PositionSide) or self.short.direction is not PositionDirection.SHORT:
            raise PortfolioError("short must be a PositionSide with direction SHORT")
        if self.long.instrument != self.instrument or self.short.instrument != self.instrument:
            raise PortfolioError("long/short instrument must match this position's instrument")

    @classmethod
    def flat(cls, *, instrument: str, as_of: Instant | str, provenance: str) -> "HedgePosition":
        return cls(
            instrument=instrument,
            long=PositionSide.flat(
                instrument=instrument, direction=PositionDirection.LONG, as_of=as_of, provenance=provenance
            ),
            short=PositionSide.flat(
                instrument=instrument, direction=PositionDirection.SHORT, as_of=as_of, provenance=provenance
            ),
        )

    def apply_fill(self, fill: Fill, order: Order) -> tuple["HedgePosition", Decimal]:
        if not isinstance(fill, Fill):
            raise PortfolioError("fill must be a Fill")
        if not isinstance(order, Order):
            raise PortfolioError("order must be an Order")
        if fill.order_id != order.order_id:
            raise PortfolioError("fill.order_id does not match order.order_id")
        if order.instrument != self.instrument:
            raise PortfolioError("order.instrument does not match this position's instrument")
        is_increase = not order.reduce_only
        direction = _direction_for_fill(order.side, is_increase=is_increase)
        target = self.long if direction is PositionDirection.LONG else self.short
        if is_increase:
            new_side = target.apply_increase(
                quantity=fill.quantity, price=fill.price, at=fill.fill_time, provenance=fill.source_evidence_identity
            )
            realized_delta = Decimal("0")
        else:
            new_side, realized_delta = target.apply_decrease(
                quantity=fill.quantity, price=fill.price, at=fill.fill_time, provenance=fill.source_evidence_identity
            )
        if direction is PositionDirection.LONG:
            return replace(self, long=new_side), realized_delta
        return replace(self, short=new_side), realized_delta

    def unrealized_pnl(self, mark_price: Decimal | str | int) -> Decimal:
        return self.long.unrealized_pnl(mark_price) + self.short.unrealized_pnl(mark_price)

    def market_value(self, mark_price: Decimal | str | int) -> Decimal:
        return self.long.market_value(mark_price) + self.short.market_value(mark_price)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "long": self.long.stable_dict(),
            "short": self.short.stable_dict(),
        }


@dataclass(frozen=True, slots=True)
class JournalLine:
    account: AccountType
    side: EntrySide
    amount: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "account", AccountType(self.account))
        object.__setattr__(self, "side", EntrySide(self.side))
        object.__setattr__(self, "amount", _decimal(self.amount, "amount", allow_zero=False))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "account": self.account.value,
            "side": self.side.value,
            "amount": _decimal_string(self.amount),
        }


@dataclass(frozen=True, slots=True)
class LedgerTransaction:
    """One atomic, balanced double-entry journal entry."""

    transaction_id: str
    recorded_at: Instant
    description: str
    lines: tuple[JournalLine, ...]
    evidence: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "transaction_id", _non_empty_text(self.transaction_id, "transaction_id"))
        object.__setattr__(self, "recorded_at", Instant.parse(self.recorded_at))
        object.__setattr__(self, "description", _key(self.description, "description"))
        lines = tuple(self.lines)
        if not all(isinstance(line, JournalLine) for line in lines):
            raise PortfolioError("lines must contain JournalLine values")
        if len(lines) < 2:
            raise PortfolioError("a journal entry requires at least two lines")
        object.__setattr__(self, "lines", lines)
        debits = sum((line.amount for line in lines if line.side is EntrySide.DEBIT), Decimal("0"))
        credits = sum((line.amount for line in lines if line.side is EntrySide.CREDIT), Decimal("0"))
        if debits != credits:
            raise PortfolioError(
                f"journal entry is not balanced: debits={_decimal_string(debits)} "
                f"credits={_decimal_string(credits)}"
            )
        if not isinstance(self.evidence, Mapping):
            raise PortfolioError("evidence must be a mapping")
        normalized_evidence: dict[str, str] = {}
        for key, value in self.evidence.items():
            normalized_evidence[_key(key, "evidence key")] = _non_empty_text(value, f"evidence[{key}]")
        object.__setattr__(self, "evidence", MappingProxyType(normalized_evidence))

    @classmethod
    def create(
        cls,
        *,
        recorded_at: Instant | str,
        description: str,
        lines: tuple[JournalLine, ...],
        evidence: Mapping[str, str] | None = None,
    ) -> "LedgerTransaction":
        recorded_at_instant = Instant.parse(recorded_at)
        normalized_evidence = {} if evidence is None else dict(evidence)
        payload = {
            "recorded_at": recorded_at_instant.isoformat(),
            "description": description,
            "lines": [line.stable_dict() for line in lines],
            "evidence": normalized_evidence,
        }
        transaction_id = f"{LEDGER_TRANSACTION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}"
        return cls(
            transaction_id=transaction_id,
            recorded_at=recorded_at_instant,
            description=description,
            lines=lines,
            evidence=normalized_evidence,
        )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "recorded_at": self.recorded_at.isoformat(),
            "description": self.description,
            "lines": [line.stable_dict() for line in self.lines],
            "evidence": dict(self.evidence),
        }

    @property
    def identity(self) -> str:
        return self.transaction_id


@dataclass(frozen=True, slots=True)
class PortfolioLedger:
    """Double-entry accounting state over Order/Fill evidence (H04)."""

    initial_capital: Decimal
    cash: Decimal
    position_asset: Decimal
    realized_pnl_total: Decimal
    fees_paid_total: Decimal
    positions: Mapping[str, HedgePosition]
    transactions: tuple[LedgerTransaction, ...]
    updated_at: Instant
    consumed_fill_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "initial_capital", _decimal(self.initial_capital, "initial_capital", allow_zero=False))
        object.__setattr__(self, "cash", _signed_decimal(self.cash, "cash"))
        object.__setattr__(self, "position_asset", _decimal(self.position_asset, "position_asset"))
        object.__setattr__(self, "realized_pnl_total", _signed_decimal(self.realized_pnl_total, "realized_pnl_total"))
        object.__setattr__(self, "fees_paid_total", _decimal(self.fees_paid_total, "fees_paid_total"))
        if not isinstance(self.positions, Mapping):
            raise PortfolioError("positions must be a mapping of instrument -> HedgePosition")
        normalized_positions: dict[str, HedgePosition] = {}
        for instrument, position in self.positions.items():
            if not isinstance(position, HedgePosition):
                raise PortfolioError("positions must contain HedgePosition values")
            if position.instrument != instrument:
                raise PortfolioError("positions mapping key must match position.instrument")
            normalized_positions[instrument] = position
        object.__setattr__(self, "positions", MappingProxyType(normalized_positions))
        transactions = tuple(self.transactions)
        if not all(isinstance(txn, LedgerTransaction) for txn in transactions):
            raise PortfolioError("transactions must contain LedgerTransaction values")
        transaction_ids = [txn.transaction_id for txn in transactions]
        if len(transaction_ids) != len(set(transaction_ids)):
            raise PortfolioError("transactions must not contain duplicate transaction_id values")
        object.__setattr__(self, "transactions", transactions)
        object.__setattr__(self, "updated_at", Instant.parse(self.updated_at))
        consumed_fill_ids = tuple(_non_empty_text(fill_id, "consumed_fill_id") for fill_id in self.consumed_fill_ids)
        if len(consumed_fill_ids) != len(set(consumed_fill_ids)):
            raise PortfolioError("consumed_fill_ids must be unique")
        object.__setattr__(self, "consumed_fill_ids", consumed_fill_ids)

    @classmethod
    def open(cls, *, initial_capital: Decimal | str | int, as_of: Instant | str) -> "PortfolioLedger":
        capital = _decimal(initial_capital, "initial_capital", allow_zero=False)
        opened_at = Instant.parse(as_of)
        opening_transaction = LedgerTransaction.create(
            recorded_at=opened_at,
            description="initial_capital_deposit",
            lines=(
                JournalLine(account=AccountType.CASH, side=EntrySide.DEBIT, amount=capital),
                JournalLine(account=AccountType.CAPITAL, side=EntrySide.CREDIT, amount=capital),
            ),
        )
        return cls(
            initial_capital=capital,
            cash=capital,
            position_asset=Decimal("0"),
            realized_pnl_total=Decimal("0"),
            fees_paid_total=Decimal("0"),
            positions={},
            transactions=(opening_transaction,),
            updated_at=opened_at,
            consumed_fill_ids=(),
        )

    def apply_fill(self, fill: Fill, order: Order) -> "PortfolioLedger":
        if not isinstance(fill, Fill):
            raise PortfolioError("fill must be a Fill")
        if not isinstance(order, Order):
            raise PortfolioError("order must be an Order")
        if fill.order_id != order.order_id:
            raise PortfolioError("fill.order_id does not match order.order_id")
        if fill.fill_time < self.updated_at:
            raise PortfolioError("fill_time must not precede the ledger's current state")
        if fill.fill_id in self.consumed_fill_ids:
            raise PortfolioError("fill_id was already consumed by this ledger")
        try:
            order.apply_fill(fill)
        except ExecutionError as exc:
            raise PortfolioError(f"order fill admission failed: {exc}") from exc

        instrument = order.instrument
        old_hedge = self.positions.get(instrument)
        if old_hedge is None:
            old_hedge = HedgePosition.flat(
                instrument=instrument, as_of=fill.fill_time, provenance=fill.source_evidence_identity
            )
        is_increase = not order.reduce_only
        direction = _direction_for_fill(order.side, is_increase=is_increase)
        old_side = old_hedge.long if direction is PositionDirection.LONG else old_hedge.short

        new_hedge, realized_delta = old_hedge.apply_fill(fill, order)
        new_side = new_hedge.long if direction is PositionDirection.LONG else new_hedge.short

        lines: list[JournalLine] = []
        if is_increase:
            notional = fill.quantity * fill.price
            lines.append(JournalLine(account=AccountType.POSITION_ASSET, side=EntrySide.DEBIT, amount=notional))
            lines.append(JournalLine(account=AccountType.CASH, side=EntrySide.CREDIT, amount=notional))
            new_position_asset = self.position_asset + notional
            new_cash = self.cash - notional
            new_realized_total = self.realized_pnl_total
        else:
            cost_basis_removed = old_side.cost_basis - new_side.cost_basis
            cash_delta = cost_basis_removed + realized_delta
            lines.append(JournalLine(account=AccountType.POSITION_ASSET, side=EntrySide.CREDIT, amount=cost_basis_removed))
            if realized_delta > 0:
                lines.append(JournalLine(account=AccountType.REALIZED_PNL, side=EntrySide.CREDIT, amount=realized_delta))
            elif realized_delta < 0:
                lines.append(JournalLine(account=AccountType.REALIZED_PNL, side=EntrySide.DEBIT, amount=-realized_delta))
            if cash_delta > 0:
                lines.append(JournalLine(account=AccountType.CASH, side=EntrySide.DEBIT, amount=cash_delta))
            elif cash_delta < 0:
                lines.append(JournalLine(account=AccountType.CASH, side=EntrySide.CREDIT, amount=-cash_delta))
            new_position_asset = self.position_asset - cost_basis_removed
            new_cash = self.cash + cash_delta
            new_realized_total = self.realized_pnl_total + realized_delta

        if fill.fee > 0:
            lines.append(JournalLine(account=AccountType.FEE_EXPENSE, side=EntrySide.DEBIT, amount=fill.fee))
            lines.append(JournalLine(account=AccountType.CASH, side=EntrySide.CREDIT, amount=fill.fee))
            new_cash -= fill.fee
            new_fees_total = self.fees_paid_total + fill.fee
        else:
            new_fees_total = self.fees_paid_total

        transaction = LedgerTransaction.create(
            recorded_at=fill.fill_time,
            description="fill_settlement",
            lines=tuple(lines),
            evidence={
                "fill_id": fill.fill_id,
                "order_id": order.order_id,
                "instrument": order.instrument,
                "source_evidence_identity": fill.source_evidence_identity,
            },
        )

        new_positions = dict(self.positions)
        new_positions[instrument] = new_hedge
        if all(pos.long.quantity == 0 and pos.short.quantity == 0 for pos in new_positions.values()):
            new_position_asset = Decimal("0")

        return replace(
            self,
            cash=new_cash,
            position_asset=new_position_asset,
            realized_pnl_total=new_realized_total,
            fees_paid_total=new_fees_total,
            positions=new_positions,
            transactions=(*self.transactions, transaction),
            updated_at=fill.fill_time,
            consumed_fill_ids=(*self.consumed_fill_ids, fill.fill_id),
        )

    @property
    def book_equity(self) -> Decimal:
        """Realized-only equity: cash plus positions carried at cost basis.

        Always exactly reconciled by the double-entry transactions alone,
        independent of any current market price.
        """

        return self.cash + self.position_asset

    def mark_to_market_equity(self, mark_prices: Mapping[str, Decimal | str | int]) -> Decimal:
        """``Equity_t = Cash_t + sum(PositionValue_i,t)`` at the given marks."""

        total = self.cash
        for instrument, position in self.positions.items():
            if position.long.quantity == 0 and position.short.quantity == 0:
                continue  # fully closed: no live exposure, no mark price needed
            if instrument not in mark_prices:
                raise PortfolioError(f"missing mark price for instrument: {instrument}")
            price = _decimal(mark_prices[instrument], "mark_price", allow_zero=False)
            total += position.market_value(price)
        return total

    def stable_dict(self) -> dict[str, Any]:
        return {
            "initial_capital": _decimal_string(self.initial_capital),
            "cash": _decimal_string(self.cash),
            "position_asset": _decimal_string(self.position_asset),
            "realized_pnl_total": _decimal_string(self.realized_pnl_total),
            "fees_paid_total": _decimal_string(self.fees_paid_total),
            "positions": {
                instrument: position.stable_dict() for instrument, position in sorted(self.positions.items())
            },
            "transactions": [txn.stable_dict() for txn in self.transactions],
            "consumed_fill_ids": list(self.consumed_fill_ids),
            "updated_at": self.updated_at.isoformat(),
        }

    @property
    def identity(self) -> str:
        return f"{LEDGER_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


__all__ = [
    "AccountType",
    "EntrySide",
    "HedgePosition",
    "JournalLine",
    "LedgerTransaction",
    "PortfolioError",
    "PortfolioLedger",
    "PositionDirection",
    "PositionSide",
]
