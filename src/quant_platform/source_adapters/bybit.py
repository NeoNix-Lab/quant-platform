"""Bybit source-owned policies for canonical ``trade-v1``."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from ..data.models import DataIntegrityError, DatasetIdentity, Instant, InvalidRequest, TradeRecord
from ..data.materializer import ParquetMaterialization, materialize_trade_v1
from ..ordering import OrderingProvider, TRADES_CANONICAL_TOTAL_ORDER_V1


BYBIT_TRADE_V1_ORDERING_POLICY = "bybit-trade-v1-exchange-ts-trade-id-v1"


class BybitTradeV1EligibilityError(ValueError):
    """Raised when schema-valid trade-v1 cannot enter the Bybit valid profile."""


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


def validate_bybit_trade_v1_eligibility(
    identity: DatasetIdentity,
    records: tuple[TradeRecord, ...],
) -> None:
    """Enforce the first-vertical publication profile from conformity §10."""

    if not bybit_trade_v1_applies_to(identity):
        raise BybitTradeV1EligibilityError(
            "Bybit trade-v1 eligibility profile does not apply to the dataset identity"
        )
    seen: set[tuple[int, str]] = set()
    for index, record in enumerate(records):
        trade_id = record.trade_id
        if trade_id is None or trade_id == "":
            raise BybitTradeV1EligibilityError(
                f"record {index} makes the partition ineligible: trade_id must be non-null and non-empty"
            )
        key = (Instant.parse(record.exchange_ts).epoch_ns, trade_id)
        if key in seen:
            raise BybitTradeV1EligibilityError(
                "partition is ineligible: duplicate (exchange_ts, trade_id) ordering key"
            )
        seen.add(key)


def materialize_bybit_trade_v1(
    path: str | Path,
    records: Iterable[TradeRecord],
    *,
    dataset_identity: DatasetIdentity,
    compression: str | None = "zstd",
    row_group_size: int = 65_536,
) -> ParquetMaterialization:
    """Materialize a Bybit canonical trade-v1 partition under the v1 profile."""

    return materialize_trade_v1(
        path,
        records,
        dataset_identity=dataset_identity,
        ordering_provider=BYBIT_ORDERING_PROVIDER,
        eligibility_validator=validate_bybit_trade_v1_eligibility,
        compression=compression,
        row_group_size=row_group_size,
    )


__all__ = [
    "BYBIT_ORDERING_PROVIDER",
    "BYBIT_TRADE_V1_ORDERING_POLICY",
    "BybitTradeV1EligibilityError",
    "bybit_trade_v1_applies_to",
    "bybit_trade_v1_ordering_key",
    "materialize_bybit_trade_v1",
    "validate_bybit_trade_v1_eligibility",
]
