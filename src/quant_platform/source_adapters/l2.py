"""Aggregated L2 book normalization adapters.

These adapters deliberately stop at aggregated price-level semantics.  They do
not infer queue position, order identifiers, add/cancel attribution, or native
MBO/L3 behavior from L2 updates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..data.models import Instant, L2BookEvent, L2LevelChange


L2_BOOK_EVENT_SCHEMA_ID = "l2-book-event-v1"
BYBIT_L2_SOURCE_SEMANTICS_V1 = "bybit-orderbook-l2-v1"
OKX_L2_SOURCE_SEMANTICS_V1 = "okx-books-l2-v1"
BINANCE_L2_SOURCE_SEMANTICS_V1 = "binance-depth-l2-v1"
KRAKEN_L2_SOURCE_SEMANTICS_V1 = "kraken-book-l2-v1"
COINBASE_L2_SOURCE_SEMANTICS_V1 = "coinbase-level2-l2-v1"


class L2SourceError(ValueError):
    """A venue L2 message failed source-specific validation."""

    def __init__(self, message: str, *, field: str | None = None):
        self.field = field
        suffix = f"  [field={field!r}]" if field is not None else ""
        super().__init__(message + suffix)


def normalize_bybit_l2_message(
    message: Mapping[str, Any],
    *,
    acquisition_mode: str,
    market_type: str = "linear_perp",
    instrument: str | None = None,
) -> L2BookEvent:
    """Normalize Bybit orderbook snapshot/delta messages.

    Applies to both live websocket messages and Bybit's public historical L2
    archives: both carry the same topic/type/data shape; only
    ``acquisition_mode`` differs.
    """

    topic = _required_str(message, "topic")
    parts = topic.split(".")
    if len(parts) != 3 or parts[0] != "orderbook":
        raise L2SourceError("unsupported Bybit orderbook topic", field="topic")
    depth = _positive_int_text(parts[1], "topic.depth")
    symbol = parts[2]
    msg_type = _event_type(_required_str(message, "type"))
    data = _required_mapping(message, "data")
    native_symbol = _required_str(data, "s")
    if native_symbol != symbol:
        raise L2SourceError("Bybit topic symbol and payload symbol differ", field="data.s")
    ts_ms = _required_int({"ts": message.get("ts", data.get("ts"))}, "ts")
    update_id = _required_int(data, "u")
    seq = _required_int(data, "seq")
    bids = _levels(_required_sequence(data, "b"), side="bid", item_width=2, venue="bybit")
    asks = _levels(_required_sequence(data, "a"), side="ask", item_width=2, venue="bybit")
    return L2BookEvent(
        venue="bybit",
        market_type=market_type,
        instrument=instrument or native_symbol,
        native_symbol=native_symbol,
        source_channel=topic,
        acquisition_mode=acquisition_mode,
        event_type=msg_type,
        exchange_ts=_millis(ts_ms),
        provider_ts=_millis(ts_ms),
        source_depth_limit=depth,
        native_sequence=str(seq),
        native_update_id=str(update_id),
        continuity_token=f"seq:{seq}|u:{update_id}",
        bids=bids,
        asks=asks,
    )


def normalize_okx_l2_message(
    message: Mapping[str, Any],
    *,
    acquisition_mode: str,
    market_type: str = "linear_perp",
    instrument: str | None = None,
) -> L2BookEvent:
    arg = _required_mapping(message, "arg")
    channel = _required_str(arg, "channel")
    if channel not in {"books", "books-l2-tbt"}:
        raise L2SourceError("unsupported OKX L2 channel", field="arg.channel")
    native_symbol = _required_str(arg, "instId")
    action = _required_str(message, "action")
    if action not in {"snapshot", "update"}:
        raise L2SourceError("unsupported OKX book action", field="action")
    payloads = _required_sequence(message, "data")
    if len(payloads) != 1 or not isinstance(payloads[0], Mapping):
        raise L2SourceError("OKX L2 messages require exactly one data object", field="data")
    data = payloads[0]
    ts_ms = int(_required_str(data, "ts"))
    seq = _optional_sequence_text(data.get("seqId"))
    prev_seq = _optional_sequence_text(data.get("prevSeqId"))
    return L2BookEvent(
        venue="okx",
        market_type=market_type,
        instrument=instrument or native_symbol,
        native_symbol=native_symbol,
        source_channel=channel,
        acquisition_mode=acquisition_mode,
        event_type="snapshot" if action == "snapshot" else "delta",
        exchange_ts=_millis(ts_ms),
        provider_ts=_millis(ts_ms),
        native_sequence=seq,
        native_prev_sequence=prev_seq,
        continuity_token=_join_tokens(("seq", seq), ("prevSeq", prev_seq)),
        bids=_levels(_required_sequence(data, "bids"), side="bid", item_width=4, venue="okx"),
        asks=_levels(_required_sequence(data, "asks"), side="ask", item_width=4, venue="okx"),
    )


def normalize_binance_l2_diff(
    message: Mapping[str, Any],
    *,
    acquisition_mode: str = "live_websocket",
    market_type: str = "linear_perp",
    instrument: str | None = None,
) -> L2BookEvent:
    if _required_str(message, "e") != "depthUpdate":
        raise L2SourceError("unsupported Binance event type", field="e")
    native_symbol = _required_str(message, "s")
    event_ts = _required_int(message, "E")
    transaction_ts = message.get("T")
    update_first = _required_int(message, "U")
    update_last = _required_int(message, "u")
    prev_update = message.get("pu")
    prev_update_text = str(_required_int({"pu": prev_update}, "pu")) if prev_update is not None else None
    return L2BookEvent(
        venue="binance",
        market_type=market_type,
        instrument=instrument or native_symbol,
        native_symbol=native_symbol,
        source_channel="depthUpdate",
        acquisition_mode=acquisition_mode,
        event_type="delta",
        exchange_ts=_millis(_required_int({"T": transaction_ts}, "T") if transaction_ts is not None else event_ts),
        provider_ts=_millis(event_ts),
        native_sequence=str(update_last),
        native_prev_sequence=prev_update_text,
        native_update_id=str(update_last),
        continuity_token=_join_tokens(("U", str(update_first)), ("u", str(update_last)), ("pu", prev_update_text)),
        bids=_levels(_required_sequence(message, "b"), side="bid", item_width=2, venue="binance"),
        asks=_levels(_required_sequence(message, "a"), side="ask", item_width=2, venue="binance"),
    )


def normalize_binance_l2_snapshot(
    message: Mapping[str, Any],
    *,
    exchange_ts: Instant | str,
    market_type: str = "linear_perp",
    instrument: str,
    native_symbol: str | None = None,
) -> L2BookEvent:
    update_id = _required_int(message, "lastUpdateId")
    symbol = native_symbol or instrument
    return L2BookEvent(
        venue="binance",
        market_type=market_type,
        instrument=instrument,
        native_symbol=symbol,
        source_channel="depthSnapshot",
        acquisition_mode="rest_snapshot",
        event_type="snapshot",
        exchange_ts=Instant.parse(exchange_ts),
        native_sequence=str(update_id),
        native_update_id=str(update_id),
        continuity_token=f"lastUpdateId:{update_id}",
        bids=_levels(_required_sequence(message, "bids"), side="bid", item_width=2, venue="binance"),
        asks=_levels(_required_sequence(message, "asks"), side="ask", item_width=2, venue="binance"),
    )


def normalize_kraken_l2_message(
    message: Mapping[str, Any],
    *,
    acquisition_mode: str = "live_websocket",
    market_type: str = "spot",
    instrument: str | None = None,
) -> L2BookEvent:
    if _required_str(message, "channel") != "book":
        raise L2SourceError("unsupported Kraken channel", field="channel")
    msg_type = _event_type(_required_str(message, "type"))
    payloads = _required_sequence(message, "data")
    if len(payloads) != 1 or not isinstance(payloads[0], Mapping):
        raise L2SourceError("Kraken book messages require exactly one data object", field="data")
    data = payloads[0]
    native_symbol = _required_str(data, "symbol")
    timestamp = _required_str(data, "timestamp")
    checksum = data.get("checksum")
    sequence = str(_required_int({"checksum": checksum}, "checksum")) if checksum is not None else None
    return L2BookEvent(
        venue="kraken",
        market_type=market_type,
        instrument=instrument or native_symbol,
        native_symbol=native_symbol,
        source_channel="book",
        acquisition_mode=acquisition_mode,
        event_type=msg_type,
        exchange_ts=Instant.parse(timestamp),
        native_sequence=sequence,
        continuity_token=_join_tokens(("checksum", sequence)),
        bids=_kraken_levels(_required_sequence(data, "bids"), side="bid"),
        asks=_kraken_levels(_required_sequence(data, "asks"), side="ask"),
    )


def normalize_coinbase_level2_message(
    message: Mapping[str, Any],
    *,
    exchange_ts: Instant | str | None = None,
    acquisition_mode: str = "live_websocket",
    market_type: str = "spot",
    instrument: str | None = None,
) -> L2BookEvent:
    msg_type = _required_str(message, "type")
    native_symbol = _required_str(message, "product_id")
    canonical_instrument = instrument or native_symbol
    if msg_type == "snapshot":
        if exchange_ts is None:
            raise L2SourceError("Coinbase level2 snapshot requires caller-supplied exchange_ts", field="exchange_ts")
        return L2BookEvent(
            venue="coinbase",
            market_type=market_type,
            instrument=canonical_instrument,
            native_symbol=native_symbol,
            source_channel="level2",
            acquisition_mode=acquisition_mode,
            event_type="snapshot",
            exchange_ts=Instant.parse(exchange_ts),
            bids=_levels(_required_sequence(message, "bids"), side="bid", item_width=2, venue="coinbase"),
            asks=_levels(_required_sequence(message, "asks"), side="ask", item_width=2, venue="coinbase"),
        )
    if msg_type != "l2update":
        raise L2SourceError("unsupported Coinbase message for L2 adapter; full/MBO events are out of scope", field="type")
    timestamp = _required_str(message, "time")
    bid_changes: list[L2LevelChange] = []
    ask_changes: list[L2LevelChange] = []
    for row in _required_sequence(message, "changes"):
        values = _row(row, 3, "coinbase change")
        side = {"buy": "bid", "sell": "ask"}.get(_required_str({"side": values[0]}, "side"))
        if side is None:
            raise L2SourceError("unsupported Coinbase level2 side", field="changes.side")
        change = _level(side=side, price=values[1], size=values[2])
        (bid_changes if side == "bid" else ask_changes).append(change)
    return L2BookEvent(
        venue="coinbase",
        market_type=market_type,
        instrument=canonical_instrument,
        native_symbol=native_symbol,
        source_channel="level2",
        acquisition_mode=acquisition_mode,
        event_type="delta",
        exchange_ts=Instant.parse(timestamp),
        bids=tuple(bid_changes),
        asks=tuple(ask_changes),
    )


def _levels(rows: Sequence[Any], *, side: str, item_width: int, venue: str) -> tuple[L2LevelChange, ...]:
    changes: list[L2LevelChange] = []
    for row in rows:
        values = _row(row, item_width, f"{venue} {side}")
        order_count = values[3] if len(values) >= 4 and values[3] not in {"", "0"} else None
        changes.append(_level(side=side, price=values[0], size=values[1], order_count=order_count))
    return tuple(changes)


def _kraken_levels(rows: Sequence[Any], *, side: str) -> tuple[L2LevelChange, ...]:
    changes: list[L2LevelChange] = []
    for row in rows:
        if isinstance(row, Mapping):
            price, qty = _required_str(row, "price"), _required_str(row, "qty")
        else:
            values = _row(row, 2, f"kraken {side}")
            price, qty = values[0], values[1]
        changes.append(_level(side=side, price=price, size=qty))
    return tuple(changes)


def _level(*, side: str, price: Any, size: Any, order_count: Any = None) -> L2LevelChange:
    size_text = _required_str({"size": size}, "size")
    action = "delete" if size_text == "0" else "upsert"
    return L2LevelChange(
        side=side, price=_required_str({"price": price}, "price"),
        size=size_text, action=action,
        order_count=str(order_count) if order_count is not None else None,
    )


def _event_type(value: str) -> str:
    if value == "snapshot":
        return "snapshot"
    if value in {"delta", "update"}:
        return "delta"
    raise L2SourceError("unsupported L2 event type", field="type")


def _millis(value: int) -> Instant:
    if value < 0:
        raise L2SourceError("timestamp milliseconds must be non-negative")
    return Instant(value * 1_000_000)


def _required_mapping(source: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = source.get(field)
    if not isinstance(value, Mapping):
        raise L2SourceError("field must be an object", field=field)
    return value


def _required_sequence(source: Mapping[str, Any], field: str) -> Sequence[Any]:
    value = source.get(field)
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise L2SourceError("field must be an array", field=field)
    return value


def _required_str(source: Mapping[str, Any], field: str) -> str:
    value = source.get(field)
    if not isinstance(value, str) or not value.strip():
        raise L2SourceError("field must be a non-empty string", field=field)
    return value.strip()


def _required_int(source: Mapping[str, Any], field: str) -> int:
    value = source.get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        raise L2SourceError("field must be an integer", field=field)
    if value < 0:
        raise L2SourceError("field must be non-negative", field=field)
    return value


def _positive_int_text(value: str, field: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise L2SourceError("field must be a positive integer", field=field) from exc
    if parsed < 1 or str(parsed) != value:
        raise L2SourceError("field must be a canonical positive integer", field=field)
    return parsed


def _optional_sequence_text(value: Any) -> str | None:
    if value in {None, ""}:
        return None
    if not isinstance(value, str):
        raise L2SourceError("sequence fields must be strings when present")
    try:
        int(value)
    except ValueError as exc:
        raise L2SourceError("sequence fields must be decimal strings") from exc
    return value


def _row(row: Any, minimum_width: int, context: str) -> tuple[Any, ...]:
    if isinstance(row, (str, bytes)) or not isinstance(row, Sequence):
        raise L2SourceError(f"{context} row must be an array")
    if len(row) < minimum_width:
        raise L2SourceError(f"{context} row is too short")
    return tuple(row)


def _join_tokens(*items: tuple[str, str | None]) -> str | None:
    parts = [f"{key}:{value}" for key, value in items if value is not None]
    return "|".join(parts) if parts else None


__all__ = [
    "BINANCE_L2_SOURCE_SEMANTICS_V1",
    "BYBIT_L2_SOURCE_SEMANTICS_V1",
    "COINBASE_L2_SOURCE_SEMANTICS_V1",
    "KRAKEN_L2_SOURCE_SEMANTICS_V1",
    "L2_BOOK_EVENT_SCHEMA_ID",
    "OKX_L2_SOURCE_SEMANTICS_V1",
    "L2SourceError",
    "normalize_binance_l2_diff",
    "normalize_binance_l2_snapshot",
    "normalize_bybit_l2_message",
    "normalize_coinbase_level2_message",
    "normalize_kraken_l2_message",
    "normalize_okx_l2_message",
]
