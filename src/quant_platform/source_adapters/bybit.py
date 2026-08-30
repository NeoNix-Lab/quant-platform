"""Bybit source-owned ordering policy for canonical ``trade-v1``."""

from __future__ import annotations

from ..data.models import DataIntegrityError, DatasetIdentity, Instant, InvalidRequest, TradeRecord
from ..ordering import OrderingProvider, TRADES_CANONICAL_TOTAL_ORDER_V1


BYBIT_TRADE_V1_ORDERING_POLICY = "bybit-trade-v1-exchange-ts-trade-id-v1"


def bybit_trade_v1_applies_to(identity: DatasetIdentity) -> bool:
    return (
        identity.venue == "bybit"
        and identity.dataset_kind == "trades"
        and identity.record_schema_id == "trade-v1"
    )


def bybit_trade_v1_ordering_key(record: TradeRecord) -> tuple[Instant, str]:
    """Build Bybit's opaque-ID tie-break key: ``(exchange_ts, trade_id)``."""

    trade_id = record.trade_id
    if trade_id is None or trade_id == "":
        raise DataIntegrityError("Bybit trade-v1 ordering requires a non-empty trade_id")
    if not isinstance(trade_id, str):
        raise DataIntegrityError("Bybit trade-v1 ordering requires trade_id as a string")
    try:
        exchange_ts = Instant.parse(record.exchange_ts)
    except (InvalidRequest, TypeError) as exc:
        raise DataIntegrityError("Bybit trade-v1 ordering requires a valid exchange_ts") from exc
    return exchange_ts, trade_id


BYBIT_ORDERING_PROVIDER = OrderingProvider(
    identity=BYBIT_TRADE_V1_ORDERING_POLICY,
    satisfies_requirements=frozenset({TRADES_CANONICAL_TOTAL_ORDER_V1}),
    key=bybit_trade_v1_ordering_key,
    applies_to=bybit_trade_v1_applies_to,
)


__all__ = [
    "BYBIT_ORDERING_PROVIDER",
    "BYBIT_TRADE_V1_ORDERING_POLICY",
    "bybit_trade_v1_applies_to",
    "bybit_trade_v1_ordering_key",
]
