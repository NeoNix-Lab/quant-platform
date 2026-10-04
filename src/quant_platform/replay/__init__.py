"""Deterministic historical replay runtime v1.

The runtime is an H05 composition layer: it owns no market-data loader, no
feature framework, and no second execution engine. Historical records enter
only through ``DataGateway.scan()``; feature inputs are supplied by an explicit
pure provider; strategy, execution and ledger semantics remain in their
canonical packages.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
from types import MappingProxyType
from typing import Any

from ..access import CoveragePolicy, DataRequest, DataSliceMetadata, LifecyclePolicy
from ..data.models import DatasetIdentity, Instant, TradeRecord
from ..execution import (
    FeeSchedule,
    Fill,
    LiquidityRole,
    Order,
    OrderSide,
    SyntheticSlippageModel,
    build_fill,
    translate_intent,
)
from ..portfolio import PortfolioLedger
from ..strategy import (
    Direction,
    RealizedPositionOutcome,
    RiskSnapshot,
    StrategyCompositionResult,
    StrategyInput,
    StrategySpec,
    TradeHistoryEvidence,
    compose_decision,
)


REPLAY_SPEC_IDENTITY_DOMAIN = "replay-spec-v1"
REPLAY_RESULT_IDENTITY_DOMAIN = "replay-result-v1"
REPLAY_TRACE_IDENTITY_DOMAIN = "replay-trace-v1"
REPLAY_EQUITY_SNAPSHOT_IDENTITY_DOMAIN = "replay-equity-snapshot-v1"
REPLAY_TRADE_HISTORY_IDENTITY_DOMAIN = "replay-trade-history-v1"
REPLAY_RISK_SNAPSHOT_IDENTITY_DOMAIN = "replay-risk-snapshot-v1"


class ReplayError(Exception):
    """Base class for deterministic replay failures."""


class ReplayOutputMode(str, Enum):
    """Retained replay evidence detail, separate from replay semantics."""

    FULL_TRACE = "full_trace"
    SUMMARY = "summary"


@dataclass(frozen=True, slots=True)
class ReplayOutputConfig:
    """Output-retention policy; it is deliberately excluded from ReplaySpec identity."""

    mode: ReplayOutputMode | str = ReplayOutputMode.FULL_TRACE

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "mode", ReplayOutputMode(self.mode))
        except (TypeError, ValueError) as exc:
            raise ReplayError("mode must be a ReplayOutputMode") from exc


# ADR-0053: this runtime inherits translate_intent's stateless, per-tick
# admission behavior verbatim and adds no entry gating of its own -- a
# feature_provider that keeps an entry signal True across consecutive ticks
# (rather than True only on the tick the condition first becomes true) will
# pyramid, admitting one full-size order per tick, not one order total. The
# ledger is already in scope here (used for risk snapshots and FLAT-side
# close instructions) but is deliberately not consulted to no-op a repeated
# entry -- debouncing "is this still the same entry condition" requires
# knowing the signal's own semantics, which only feature_provider's author
# has; see ADR-0053 for the full reasoning.
FeatureProvider = Callable[[TradeRecord, "ReplayContext"], Iterable[StrategyInput]]


@dataclass(frozen=True, slots=True)
class ReplaySpec:
    """Immutable replay configuration and provenance."""

    dataset: DatasetIdentity
    start: Instant | str
    end: Instant | str
    strategy: StrategySpec
    initial_capital: Decimal | str | int
    fee_schedule: FeeSchedule
    ordering_policy: str
    slippage_model: SyntheticSlippageModel | None = None
    lifecycle_policy: LifecyclePolicy = LifecyclePolicy.VALID_ONLY
    coverage_policy: CoveragePolicy = CoveragePolicy.STRICT
    batch_size: int = 65_536

    def __post_init__(self) -> None:
        if not isinstance(self.dataset, DatasetIdentity):
            raise ReplayError("dataset must be a DatasetIdentity")
        start = Instant.parse(self.start)
        end = Instant.parse(self.end)
        if start > end:
            raise ReplayError("start must not be after end")
        if not isinstance(self.strategy, StrategySpec):
            raise ReplayError("strategy must be a StrategySpec")
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "initial_capital", _decimal(self.initial_capital, "initial_capital", allow_zero=False))
        if not isinstance(self.fee_schedule, FeeSchedule):
            raise ReplayError("fee_schedule must be a FeeSchedule")
        if self.slippage_model is not None and not isinstance(self.slippage_model, SyntheticSlippageModel):
            raise ReplayError("slippage_model must be a SyntheticSlippageModel")
        object.__setattr__(self, "lifecycle_policy", LifecyclePolicy(self.lifecycle_policy))
        object.__setattr__(self, "coverage_policy", CoveragePolicy.normalize(self.coverage_policy))
        object.__setattr__(self, "ordering_policy", _non_empty_text(self.ordering_policy, "ordering_policy"))
        if type(self.batch_size) is not int or self.batch_size < 1:
            raise ReplayError("batch_size must be a positive integer")

    def data_request(self) -> DataRequest:
        return DataRequest(
            self.dataset,
            start=self.start,
            end=self.end,
            lifecycle_policy=self.lifecycle_policy,
            coverage_policy=self.coverage_policy,
            ordering_policy=self.ordering_policy,
        )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "identity_domain": REPLAY_SPEC_IDENTITY_DOMAIN,
            "dataset": self.dataset.stable_dict(),
            "interval": {"start": self.start.isoformat(), "end": self.end.isoformat()},
            "strategy_identity": self.strategy.strategy_identity,
            "strategy": self.strategy.stable_dict(),
            "initial_capital": _decimal_string(self.initial_capital),
            "fee_schedule": self.fee_schedule.stable_dict(),
            "slippage_model": None if self.slippage_model is None else self.slippage_model.stable_dict(),
            "lifecycle_policy": self.lifecycle_policy.value,
            "coverage_policy": self.coverage_policy.value,
            "ordering_policy": self.ordering_policy,
            # batch_size is an I/O paging knob, not a semantic replay
            # parameter -- it must not change identity/reproducibility.
        }

    @property
    def identity(self) -> str:
        return f"{REPLAY_SPEC_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class ReplayContext:
    """Inputs visible to the feature provider at one historical event."""

    spec: ReplaySpec
    ledger: PortfolioLedger
    peak_equity: Decimal
    closed_outcomes: tuple[RealizedPositionOutcome, ...]


@dataclass(frozen=True, slots=True)
class EquitySnapshot:
    as_of: Instant
    cash: Decimal
    position_value: Decimal
    equity: Decimal
    mark_prices: Mapping[str, Decimal]
    ledger_identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "as_of", Instant.parse(self.as_of))
        object.__setattr__(self, "cash", _signed_decimal(self.cash, "cash"))
        object.__setattr__(self, "position_value", _signed_decimal(self.position_value, "position_value"))
        object.__setattr__(self, "equity", _signed_decimal(self.equity, "equity"))
        if not isinstance(self.mark_prices, Mapping):
            raise ReplayError("mark_prices must be a mapping")
        marks = {str(key): _decimal(value, f"mark_prices[{key}]", allow_zero=False) for key, value in self.mark_prices.items()}
        object.__setattr__(self, "mark_prices", MappingProxyType({key: marks[key] for key in sorted(marks)}))
        object.__setattr__(self, "ledger_identity", _identity_text(self.ledger_identity, "ledger_identity"))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "identity_domain": REPLAY_EQUITY_SNAPSHOT_IDENTITY_DOMAIN,
            "as_of": self.as_of.isoformat(),
            "cash": _decimal_string(self.cash),
            "position_value": _decimal_string(self.position_value),
            "equity": _decimal_string(self.equity),
            "mark_prices": {key: _decimal_string(value) for key, value in self.mark_prices.items()},
            "ledger_identity": self.ledger_identity,
        }

    @property
    def identity(self) -> str:
        return f"{REPLAY_EQUITY_SNAPSHOT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class ReplaySummary:
    """Constant-size deterministic evidence for either replay output mode."""

    spec_identity: str
    decision_count: int
    admission_count: int
    equity_snapshot_count: int
    order_count: int
    fill_count: int
    digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "spec_identity", _identity_text(self.spec_identity, "spec_identity"))
        for field in (
            "decision_count",
            "admission_count",
            "equity_snapshot_count",
            "order_count",
            "fill_count",
        ):
            if type(getattr(self, field)) is not int or getattr(self, field) < 0:
                raise ReplayError(f"{field} must be a non-negative integer")
        object.__setattr__(self, "digest", _identity_text(self.digest, "digest"))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "spec_identity": self.spec_identity,
            "decision_count": self.decision_count,
            "admission_count": self.admission_count,
            "equity_snapshot_count": self.equity_snapshot_count,
            "order_count": self.order_count,
            "fill_count": self.fill_count,
            "digest": self.digest,
        }


@dataclass(frozen=True, slots=True)
class ReplayResult:
    spec_identity: str
    final_ledger: PortfolioLedger
    equity_curve: tuple[EquitySnapshot, ...]
    decisions: tuple[dict[str, Any], ...]
    admissions: tuple[dict[str, Any], ...]
    orders: tuple[Order, ...]
    fills: tuple[Fill, ...]
    data_metadata: DataSliceMetadata | None
    summary: ReplaySummary

    def __post_init__(self) -> None:
        object.__setattr__(self, "spec_identity", _identity_text(self.spec_identity, "spec_identity"))
        if not isinstance(self.final_ledger, PortfolioLedger):
            raise ReplayError("final_ledger must be a PortfolioLedger")
        object.__setattr__(self, "equity_curve", tuple(self.equity_curve))
        if not all(isinstance(snapshot, EquitySnapshot) for snapshot in self.equity_curve):
            raise ReplayError("equity_curve must contain EquitySnapshot values")
        object.__setattr__(self, "decisions", tuple(dict(item) for item in self.decisions))
        object.__setattr__(self, "admissions", tuple(dict(item) for item in self.admissions))
        object.__setattr__(self, "orders", tuple(self.orders))
        object.__setattr__(self, "fills", tuple(self.fills))
        if not all(isinstance(order, Order) for order in self.orders):
            raise ReplayError("orders must contain Order values")
        if not all(isinstance(fill, Fill) for fill in self.fills):
            raise ReplayError("fills must contain Fill values")
        if self.data_metadata is not None and not isinstance(self.data_metadata, DataSliceMetadata):
            raise ReplayError("data_metadata must be DataSliceMetadata")
        if not isinstance(self.summary, ReplaySummary):
            raise ReplayError("summary must be a ReplaySummary")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "identity_domain": REPLAY_RESULT_IDENTITY_DOMAIN,
            "spec_identity": self.spec_identity,
            "final_ledger": self.final_ledger.stable_dict(),
            "equity_curve": [snapshot.stable_dict() for snapshot in self.equity_curve],
            "decisions": list(self.decisions),
            "admissions": list(self.admissions),
            "orders": [order.stable_dict() for order in self.orders],
            "fills": [fill.stable_dict() for fill in self.fills],
            "ledger_transactions": [txn.stable_dict() for txn in self.final_ledger.transactions],
            "data_metadata": None if self.data_metadata is None else _metadata_dict(self.data_metadata),
        }

    @property
    def trace_fingerprint(self) -> str:
        return f"{REPLAY_TRACE_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"

    @property
    def identity(self) -> str:
        return f"{REPLAY_RESULT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict())}"


@dataclass(frozen=True, slots=True)
class HistoricalReplayRuntime:
    """H05 runtime that composes DataGateway, Strategy, Execution and Ledger.

    Repeated-entry gating is intentionally owned here, not by
    ``execution.translate_intent()``: H05 already has the live
    ``PortfolioLedger`` needed to detect that a LONG/SHORT decision is at its
    target for this replay, while the lower execution seam remains stateless
    and usable by callers without ledger state.
    """

    gateway: Any
    feature_provider: FeatureProvider

    def run(self, spec: ReplaySpec, *, output: ReplayOutputConfig | None = None) -> ReplayResult:
        """Run unchanged replay semantics with independently selected output retention."""
        if not isinstance(spec, ReplaySpec):
            raise ReplayError("spec must be a ReplaySpec")
        if not callable(self.feature_provider):
            raise ReplayError("feature_provider must be callable")
        if not hasattr(self.gateway, "scan") or not callable(self.gateway.scan):
            raise ReplayError("gateway must expose scan()")
        if output is None:
            output = ReplayOutputConfig()
        if not isinstance(output, ReplayOutputConfig):
            raise ReplayError("output must be a ReplayOutputConfig")

        ledger = PortfolioLedger.open(initial_capital=spec.initial_capital, as_of=spec.start)
        peak_equity = ledger.book_equity
        outcomes: tuple[RealizedPositionOutcome, ...] = ()
        orders: list[Order] = []
        fills: list[Fill] = []
        decisions: list[dict[str, Any]] = []
        admissions: list[dict[str, Any]] = []
        equity_curve: list[EquitySnapshot] = []
        summary = _ReplaySummaryAccumulator(spec.identity)

        def capture(kind: str, value: Any, retained: list[Any]) -> None:
            summary.record(kind, value)
            if output.mode is ReplayOutputMode.FULL_TRACE:
                retained.append(value)
        last_event_time: Instant | None = None

        scan = self.gateway.scan(spec.data_request(), batch_size=spec.batch_size)
        for batch in scan:
            for record in batch:
                if not isinstance(record, TradeRecord):
                    raise ReplayError("DataGateway.scan() yielded a non-TradeRecord")
                event_time = Instant.parse(record.exchange_ts)
                if last_event_time is not None and event_time < last_event_time:
                    raise ReplayError("DataGateway.scan() yielded records out of temporal order")
                last_event_time = event_time
                if record.instrument != spec.dataset.instrument:
                    raise ReplayError("record instrument does not match replay dataset")

                # Peak equity must reflect this record's own mark-to-market
                # swing *before* the strategy decides anything on it -- using
                # only the current, already-known price, never a future one.
                # Otherwise a price spike-then-reversal with no trade in
                # between would leave `peak_equity` stale, understating the
                # true drawdown a subsequent CapitalRiskPolicy check must see.
                current_snapshot = _equity_snapshot(ledger, record)
                if current_snapshot.equity > peak_equity:
                    peak_equity = current_snapshot.equity

                context = ReplayContext(
                    spec=spec,
                    ledger=ledger,
                    peak_equity=peak_equity,
                    closed_outcomes=outcomes,
                )
                strategy_inputs = tuple(self.feature_provider(record, context))
                for strategy_input in strategy_inputs:
                    if not isinstance(strategy_input, StrategyInput):
                        raise ReplayError("feature_provider must yield StrategyInput values")
                    if strategy_input.available_at > event_time:
                        raise ReplayError("feature input availability exceeds decision_time")

                composition = compose_decision(
                    spec.strategy,
                    strategy_inputs,
                    decision_time=event_time,
                    instrument=record.instrument,
                )
                capture("decision", _composition_trace(composition), decisions)
                if composition.decision_intent is None:
                    capture("equity_snapshot", current_snapshot, equity_curve)
                    continue

                reference_price = _decimal(record.price, "record.price", allow_zero=False)
                risk_snapshot = _risk_snapshot(
                    ledger=ledger,
                    instrument=record.instrument,
                    reference_price=reference_price,
                    as_of=event_time,
                    peak_equity=peak_equity,
                )
                trade_history = _trade_history(outcomes, event_time)
                close_side, close_quantity = _close_instruction(
                    ledger,
                    record.instrument,
                    composition.decision_intent.direction,
                )
                if composition.decision_intent.direction is Direction.FLAT and close_quantity is None:
                    capture(
                        "admission",
                        _flat_no_position_admission(composition.decision_intent),
                        admissions,
                    )
                    capture("equity_snapshot", current_snapshot, equity_curve)
                    continue
                already_at_target = _entry_already_at_target_quantities(
                    ledger,
                    record.instrument,
                    composition.decision_intent,
                )
                if already_at_target is not None:
                    risk_decision = spec.strategy.risk_policy.evaluate(
                        snapshot=risk_snapshot,
                        as_of=event_time,
                        reference_price=reference_price,
                        target_position=composition.decision_intent.target_position,
                    )
                    capture(
                        "admission",
                        _entry_already_at_target_admission(
                            ledger,
                            composition.decision_intent,
                            risk_decision,
                            current_quantity=already_at_target[0],
                            target_quantity=already_at_target[1],
                        ),
                        admissions,
                    )
                    capture("equity_snapshot", current_snapshot, equity_curve)
                    continue
                admission = translate_intent(
                    composition.decision_intent,
                    spec.strategy,
                    submitted_at=event_time,
                    reference_price=reference_price,
                    risk_snapshot=risk_snapshot,
                    trade_history=trade_history,
                    provenance=composition.decision_intent.intent_identity,
                    close_side=close_side,
                    close_quantity=close_quantity,
                )
                capture("admission", admission.stable_dict(), admissions)
                if admission.order is None:
                    capture("equity_snapshot", current_snapshot, equity_curve)
                    continue

                acknowledged = admission.order.acknowledge()
                fill = build_fill(
                    acknowledged,
                    fill_time=event_time,
                    quantity=acknowledged.open_quantity,
                    reference_price=reference_price,
                    liquidity_role=LiquidityRole.TAKER,
                    fee_schedule=spec.fee_schedule,
                    source_evidence_identity=_record_evidence_identity(record),
                    slippage_model=spec.slippage_model,
                )
                filled_order = acknowledged.apply_fill(fill)
                ledger_before = ledger
                ledger = ledger.apply_fill(fill, acknowledged)
                capture("order", filled_order, orders)
                capture("fill", fill, fills)
                closed = _closed_outcome(
                    before=ledger_before,
                    after=ledger,
                    order=acknowledged,
                    fill=fill,
                )
                if closed is not None:
                    outcomes = (*outcomes, closed)
                snapshot = _equity_snapshot(ledger, record)
                capture("equity_snapshot", snapshot, equity_curve)
                if snapshot.equity > peak_equity:
                    peak_equity = snapshot.equity

        metadata = getattr(scan, "completed_metadata", None)
        return ReplayResult(
            spec_identity=spec.identity,
            final_ledger=ledger,
            equity_curve=tuple(equity_curve),
            decisions=tuple(decisions),
            admissions=tuple(admissions),
            orders=tuple(orders),
            fills=tuple(fills),
            data_metadata=metadata,
            summary=summary.complete(ledger, metadata),
        )


ReplayEngine = HistoricalReplayRuntime


class _ReplaySummaryAccumulator:
    def __init__(self, spec_identity: str) -> None:
        self._spec_identity = spec_identity
        self._stream = hashlib.sha256()
        self._counts = {
            "decision": 0,
            "admission": 0,
            "equity_snapshot": 0,
            "order": 0,
            "fill": 0,
        }

    def record(self, kind: str, value: Any) -> None:
        if kind not in self._counts:
            raise ReplayError(f"unsupported replay summary event: {kind}")
        if isinstance(value, (EquitySnapshot, Order, Fill)):
            value = value.stable_dict()
        self._stream.update(_canonical_json({"kind": kind, "value": value}).encode("utf-8"))
        self._stream.update(b"\n")
        self._counts[kind] += 1

    def complete(self, ledger: PortfolioLedger, metadata: DataSliceMetadata | None) -> ReplaySummary:
        payload = {
            "identity_domain": "replay-summary-v1",
            "spec_identity": self._spec_identity,
            "event_stream_sha256": self._stream.hexdigest(),
            "counts": self._counts,
            "final_ledger": ledger.stable_dict(),
            "data_metadata": None if metadata is None else _metadata_dict(metadata),
        }
        digest = f"replay-summary-v1:sha256:{_canonical_fingerprint(payload)}"
        return ReplaySummary(
            spec_identity=self._spec_identity,
            decision_count=self._counts["decision"],
            admission_count=self._counts["admission"],
            equity_snapshot_count=self._counts["equity_snapshot"],
            order_count=self._counts["order"],
            fill_count=self._counts["fill"],
            digest=digest,
        )


def _close_instruction(ledger: PortfolioLedger, instrument: str, direction: Direction) -> tuple[OrderSide | None, Decimal | None]:
    if direction is not Direction.FLAT:
        return None, None
    position = ledger.positions.get(instrument)
    if position is None:
        return None, None
    if position.long.quantity > 0:
        return OrderSide.SELL, position.long.quantity
    if position.short.quantity > 0:
        return OrderSide.BUY, position.short.quantity
    return None, None


def _flat_no_position_admission(intent: Any) -> dict[str, Any]:
    return {
        "outcome": "REFUSED",
        "reasons": ["no_open_position_to_close"],
        "decision_intent": intent.stable_dict(),
        "order": None,
    }


def _entry_already_at_target_quantities(
    ledger: PortfolioLedger,
    instrument: str,
    intent: Any,
) -> tuple[Decimal, Decimal] | None:
    if intent.direction is Direction.FLAT:
        return None
    position = ledger.positions.get(instrument)
    if intent.direction is Direction.LONG:
        current_quantity = Decimal("0") if position is None else position.long.quantity
        target_quantity = abs(intent.target_position)
    else:
        current_quantity = Decimal("0") if position is None else position.short.quantity
        target_quantity = abs(intent.target_position)
    if current_quantity < target_quantity:
        return None
    return current_quantity, target_quantity


def _entry_already_at_target_admission(
    ledger: PortfolioLedger,
    intent: Any,
    risk_decision: Any,
    *,
    current_quantity: Decimal,
    target_quantity: Decimal,
) -> dict[str, Any]:
    return {
        "outcome": "REFUSED",
        "reasons": ["already_at_target_position"],
        "decision_intent": intent.stable_dict(),
        "order": None,
        "risk_decision": risk_decision.stable_dict(),
        "ledger_identity": ledger.identity,
        "current_quantity": _decimal_string(current_quantity),
        "target_quantity": _decimal_string(target_quantity),
    }


def _risk_snapshot(
    *,
    ledger: PortfolioLedger,
    instrument: str,
    reference_price: Decimal,
    as_of: Instant,
    peak_equity: Decimal,
) -> RiskSnapshot:
    mark_prices = {instrument: reference_price}
    equity = ledger.mark_to_market_equity(mark_prices)
    current_exposure, current_target_exposure = _exposure(ledger, instrument, reference_price)
    evidence = {
        "ledger_identity": ledger.identity,
        "as_of": as_of.isoformat(),
        "mark_prices": {instrument: _decimal_string(reference_price)},
    }
    return RiskSnapshot(
        observed_at=as_of,
        equity=equity,
        peak_equity=max(peak_equity, equity),
        current_exposure_notional=current_exposure,
        current_target_instrument_exposure_notional=current_target_exposure,
        evidence_identity=f"{REPLAY_RISK_SNAPSHOT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(evidence)}",
    )


def _trade_history(outcomes: Sequence[RealizedPositionOutcome], as_of: Instant) -> TradeHistoryEvidence:
    payload = {
        "observed_at": as_of.isoformat(),
        "outcomes": [outcome.stable_dict() for outcome in outcomes],
    }
    return TradeHistoryEvidence(
        observed_at=as_of,
        outcomes=tuple(outcomes),
        evidence_identity=f"{REPLAY_TRADE_HISTORY_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}",
    )


def _closed_outcome(
    *,
    before: PortfolioLedger,
    after: PortfolioLedger,
    order: Order,
    fill: Fill,
) -> RealizedPositionOutcome | None:
    if not order.reduce_only:
        return None
    before_position = before.positions.get(order.instrument)
    after_position = after.positions.get(order.instrument)
    if before_position is None or after_position is None:
        return None
    if order.side is OrderSide.SELL:
        before_side = before_position.long
        after_side = after_position.long
    else:
        before_side = before_position.short
        after_side = after_position.short
    realized_delta = after_side.realized_pnl - before_side.realized_pnl
    opened_at = before_side.opened_at or order.submitted_at
    return RealizedPositionOutcome(
        opened_at=opened_at,
        settled_at=fill.fill_time,
        realized_pnl=realized_delta,
        evidence_identity=fill.fill_id,
    )


def _equity_snapshot(ledger: PortfolioLedger, record: TradeRecord) -> EquitySnapshot:
    price = _decimal(record.price, "record.price", allow_zero=False)
    mark_prices = {record.instrument: price}
    equity = ledger.mark_to_market_equity(mark_prices)
    return EquitySnapshot(
        as_of=record.exchange_ts,
        cash=ledger.cash,
        position_value=equity - ledger.cash,
        equity=equity,
        mark_prices=mark_prices,
        ledger_identity=ledger.identity,
    )


def _exposure(ledger: PortfolioLedger, instrument: str, price: Decimal) -> tuple[Decimal, Decimal]:
    position = ledger.positions.get(instrument)
    if position is None:
        return Decimal("0"), Decimal("0")
    long_notional = position.long.quantity * price
    short_notional = position.short.quantity * price
    current_total = long_notional + short_notional
    current_target = long_notional - short_notional
    return current_total, current_target


def _composition_trace(result: StrategyCompositionResult) -> dict[str, Any]:
    return result.stable_dict()


def _record_evidence_identity(record: TradeRecord) -> str:
    payload = _trade_record_dict(record)
    return f"trade-record-v1:sha256:{_canonical_fingerprint(payload)}"


def _trade_record_dict(record: TradeRecord) -> dict[str, Any]:
    return {
        "venue": record.venue,
        "instrument": record.instrument,
        "exchange_ts": Instant.parse(record.exchange_ts).isoformat(),
        "receive_ts": None if record.receive_ts is None else Instant.parse(record.receive_ts).isoformat(),
        "price": record.price,
        "size": record.size,
        "aggressor_side": record.aggressor_side,
        "trade_id": record.trade_id,
        "sequence": record.sequence,
    }


def _metadata_dict(metadata: DataSliceMetadata) -> dict[str, Any]:
    return {
        "dataset_identity": metadata.dataset_identity.stable_dict(),
        "record_schema_id": metadata.record_schema_id,
        "schema_version": metadata.schema_version,
        "schema_hash": metadata.schema_hash,
        "natural_partitions": [partition.stable_dict() for partition in metadata.natural_partitions],
        "manifest_hashes": list(metadata.manifest_hashes),
        "content_hashes": list(metadata.content_hashes),
        "request_identity": metadata.request_identity,
        "result_identity": metadata.result_identity,
        "requested_interval": metadata.requested_interval.stable_dict(),
        "eligible_coverage": [interval.stable_dict() for interval in metadata.eligible_coverage],
        "coverage_gaps": [interval.stable_dict() for interval in metadata.coverage_gaps],
        "coverage_complete": metadata.coverage_complete,
        "returned_record_bounds": None if metadata.returned_record_bounds is None else metadata.returned_record_bounds.stable_dict(),
        "row_count": metadata.row_count,
        "ordering_policy": metadata.ordering_policy,
        "lifecycle_policy": metadata.lifecycle_policy.value,
        "coverage_policy": metadata.coverage_policy.value,
        "catalog_dataset_id": metadata.catalog_dataset_id,
        "catalog_partition_ids": list(metadata.catalog_partition_ids),
        "storage_root_ids": list(metadata.storage_root_ids),
        "rel_paths": list(metadata.rel_paths),
    }


def _decimal(value: Any, field: str, *, allow_zero: bool = True) -> Decimal:
    if isinstance(value, bool):
        raise ReplayError(f"{field} must be numeric")
    if isinstance(value, Decimal):
        text = str(value)
    elif type(value) is int:
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise ReplayError(f"{field} must be a Decimal, integer or decimal string")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ReplayError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise ReplayError(f"{field} must be finite")
    if result < 0 or (result == 0 and not allow_zero):
        qualifier = "non-negative" if allow_zero else "positive"
        raise ReplayError(f"{field} must be {qualifier}")
    return result


def _signed_decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ReplayError(f"{field} must be numeric")
    if isinstance(value, Decimal):
        text = str(value)
    elif type(value) is int:
        text = str(value)
    elif isinstance(value, str):
        text = value
    else:
        raise ReplayError(f"{field} must be a Decimal, integer or decimal string")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ReplayError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise ReplayError(f"{field} must be finite")
    return result


def _decimal_string(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"", "-0"} else text


def _non_empty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReplayError(f"{field} must be a non-empty string")
    return value.strip()


def _identity_text(value: Any, field: str) -> str:
    text = _non_empty_text(value, field)
    if ":sha256:" not in text:
        raise ReplayError(f"{field} must be a content-derived identity")
    return text


def _canonical_value(value: Any) -> Any:
    if isinstance(value, Instant):
        return value.isoformat()
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
                raise ReplayError("canonical mapping keys must be non-empty strings")
            result[key] = _canonical_value(item)
        return {key: result[key] for key in sorted(result)}
    if isinstance(value, (list, tuple)):
        return [_canonical_value(item) for item in value]
    raise ReplayError(f"unsupported canonical value type: {type(value).__name__}")


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


__all__ = [
    "EquitySnapshot",
    "FeatureProvider",
    "HistoricalReplayRuntime",
    "ReplayContext",
    "ReplayEngine",
    "ReplayError",
    "ReplayOutputConfig",
    "ReplayOutputMode",
    "ReplayResult",
    "ReplaySummary",
    "ReplaySpec",
]
