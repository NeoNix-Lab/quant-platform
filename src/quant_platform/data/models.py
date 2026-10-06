"""Shared canonical identity, time, record, hash and error primitives.

The models deliberately contain no PostgreSQL IDs or physical paths in their
stable identity values.  Access-specific requests, results and runtime locators live in
quant_platform.access.models.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Iterable, Literal


class DataGatewayError(Exception):
    """Base class for expected DataGateway domain failures."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None):
        super().__init__(message)
        self.context = context or {}


class DatasetNotFound(DataGatewayError):
    pass


class NoCoverage(DataGatewayError):
    pass


class CatalogConflict(DataGatewayError):
    pass


class SchemaMismatch(DataGatewayError):
    pass


class InvalidPartitionState(DataGatewayError):
    pass


class CorruptContent(DataGatewayError):
    pass


class DataIntegrityError(CorruptContent):
    pass


class InvalidRequest(DataGatewayError):
    pass


class UnsupportedSchema(DataGatewayError):
    pass


class UnsupportedDatasetKind(DataGatewayError):
    pass


class StorageResolutionError(DataGatewayError):
    pass


class Instant:
    """UTC instant retaining up to nanosecond precision.

    Python's ``datetime`` stops at microseconds.  The gateway accepts
    datetimes for convenience but stores request and record timestamps as an
    integer nanosecond count so equal-event-time ordering and half-open
    boundaries do not lose precision.
    """

    __slots__ = ("epoch_ns",)

    def __init__(self, epoch_ns: int):
        self.epoch_ns = int(epoch_ns)

    @classmethod
    def parse(cls, value: "Instant | datetime | str") -> "Instant":
        if isinstance(value, cls):
            return value
        if isinstance(value, datetime):
            if value.tzinfo is None:
                raise InvalidRequest("timestamps must be timezone-aware")
            utc = value.astimezone(timezone.utc)
            return cls(_datetime_epoch_ns(utc))
        if not isinstance(value, str):
            raise InvalidRequest(f"unsupported timestamp type: {type(value).__name__}")
        text = value.strip()
        if not text.endswith("Z"):
            raise InvalidRequest("timestamps must be UTC RFC 3339 values ending in Z")
        match = re.fullmatch(
            r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d{1,9}))?Z",
            text,
        )
        if not match:
            raise InvalidRequest(f"invalid UTC timestamp: {value!r}")
        try:
            base = datetime.fromisoformat(match.group(1)).replace(tzinfo=timezone.utc)
        except ValueError as exc:
            raise InvalidRequest(f"invalid UTC timestamp: {value!r}") from exc
        fraction = (match.group(2) or "").ljust(9, "0")
        return cls(_datetime_epoch_ns(base) + int(fraction))

    def isoformat(self) -> str:
        seconds, nanos = divmod(self.epoch_ns, 1_000_000_000)
        base = datetime.fromtimestamp(seconds, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        if nanos:
            return f"{base}.{nanos:09d}".rstrip("0") + "Z"
        return base + "Z"

    def to_datetime(self) -> datetime:
        seconds, nanos = divmod(self.epoch_ns, 1_000_000_000)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
            microsecond=nanos // 1_000
        )

    def __repr__(self) -> str:
        return f"Instant({self.isoformat()!r})"

    def __str__(self) -> str:
        return self.isoformat()

    def __hash__(self) -> int:
        return hash(self.epoch_ns)

    def __eq__(self, other: object) -> bool:
        try:
            return self.epoch_ns == Instant.parse(other).epoch_ns  # type: ignore[arg-type]
        except (InvalidRequest, TypeError):
            return False

    def __lt__(self, other: object) -> bool:
        return self.epoch_ns < Instant.parse(other).epoch_ns  # type: ignore[arg-type]

    def __le__(self, other: object) -> bool:
        return self.epoch_ns <= Instant.parse(other).epoch_ns  # type: ignore[arg-type]

    def __gt__(self, other: object) -> bool:
        return self.epoch_ns > Instant.parse(other).epoch_ns  # type: ignore[arg-type]

    def __ge__(self, other: object) -> bool:
        return self.epoch_ns >= Instant.parse(other).epoch_ns  # type: ignore[arg-type]


def _identifier(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequest(f"{field} must be a non-empty string")
    return value.strip().lower()


def _datetime_epoch_ns(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = value - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


@dataclass(frozen=True, slots=True)
class DatasetIdentity:
    layer: str
    dataset_kind: str
    venue: str
    instrument: str
    record_schema_id: str
    feature_set_slug: str | None = None
    feature_set_version: int | None = None

    def __post_init__(self) -> None:
        layer = _identifier(self.layer, "layer")
        kind = _identifier(self.dataset_kind, "dataset_kind")
        venue = _identifier(self.venue, "venue")
        schema = _identifier(self.record_schema_id, "record_schema_id")
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise InvalidRequest("instrument must be a non-empty string")
        if layer not in {"raw", "canonical", "features"}:
            raise InvalidRequest(f"unsupported layer: {self.layer!r}")
        if layer == "features":
            if self.feature_set_slug is None or self.feature_set_version is None:
                raise InvalidRequest("feature datasets require feature-set identity")
            object.__setattr__(self, "feature_set_slug", _identifier(self.feature_set_slug, "feature_set_slug"))
            if self.feature_set_version < 1:
                raise InvalidRequest("feature_set_version must be positive")
        elif self.feature_set_slug is not None or self.feature_set_version is not None:
            raise InvalidRequest("raw/canonical datasets cannot carry feature-set identity")
        object.__setattr__(self, "layer", layer)
        object.__setattr__(self, "dataset_kind", kind)
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "record_schema_id", schema)

    def stable_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "layer": self.layer,
            "dataset_kind": self.dataset_kind,
            "venue": self.venue,
            "instrument": self.instrument,
            "record_schema_id": self.record_schema_id,
        }
        if self.layer == "features":
            result["feature_set_slug"] = self.feature_set_slug
            result["feature_set_version"] = self.feature_set_version
        return result


@dataclass(frozen=True, slots=True)
class CoverageInterval:
    start: Instant
    end: Instant

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise InvalidRequest("coverage interval start must not be after end")

    def stable_dict(self) -> dict[str, str]:
        return {"start": self.start.isoformat(), "end": self.end.isoformat()}


@dataclass(frozen=True, slots=True)
class RecordTimeBounds:
    """Observed temporal extent of records returned by one read.

    This is deliberately separate from ``CoverageInterval``: it is an
    observation about returned records, not a claim about declared support,
    availability, or interval responsibility.
    """

    first: Instant
    last: Instant

    def __post_init__(self) -> None:
        first = Instant.parse(self.first)
        last = Instant.parse(self.last)
        if first > last:
            raise InvalidRequest("record time bounds first must not be after last")
        object.__setattr__(self, "first", first)
        object.__setattr__(self, "last", last)

    def stable_dict(self) -> dict[str, str]:
        return {"first": self.first.isoformat(), "last": self.last.isoformat()}


@dataclass(frozen=True, slots=True)
class NaturalPartitionIdentity:
    dataset_identity: DatasetIdentity
    partition_key: str
    revision: int

    def __post_init__(self) -> None:
        if not self.partition_key or not isinstance(self.partition_key, str):
            raise InvalidRequest("partition_key must be non-empty")
        if self.revision < 1:
            raise InvalidRequest("revision must be positive")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "dataset_identity": self.dataset_identity.stable_dict(),
            "partition_key": self.partition_key,
            "revision": self.revision,
        }


@dataclass(frozen=True, slots=True)
class TradeRecord:
    venue: str
    instrument: str
    exchange_ts: Instant
    price: str
    size: str
    aggressor_side: str
    receive_ts: Instant | None = None
    trade_id: str | None = None
    sequence: str | None = None


L2_SIDE_VALUES = frozenset({"bid", "ask"})
L2_ACTION_VALUES = frozenset({"upsert", "delete"})
L2_EVENT_TYPE_VALUES = frozenset({"snapshot", "delta"})
L2_ACQUISITION_MODE_VALUES = frozenset(
    {"historical_archive", "live_websocket", "rest_snapshot"}
)
L2_MARKET_TYPE_VALUES = frozenset({"spot", "linear_perp", "inverse_perp", "futures"})
L2_BOOK_EVENT_V1_IDENTITY = "l2-book-event-v1"
L2_BOOK_EVENT_HASH_V1_DOMAIN_TAG = b"quant-platform/l2-book-event-v1\x00"
_NON_NEGATIVE_DECIMAL_RE = re.compile(
    r"^(?:0|[1-9][0-9]*(?:\.[0-9]+)?|0\.[0-9]+)$"
)
_POSITIVE_DECIMAL_RE = re.compile(
    r"^(?:[1-9][0-9]*(?:\.[0-9]+)?|0\.[0-9]*[1-9][0-9]*)$"
)
_DIGIT_STRING_RE = re.compile(r"^(0|[1-9][0-9]*)$")


@dataclass(frozen=True, slots=True)
class L2LevelChange:
    """One normalized aggregate price-level mutation.

    ``delete`` always uses size ``"0"``.  ``upsert`` always uses a strictly
    positive aggregate size.  L2 never carries order identifiers or queue
    position; those are L3/MBO semantics and must remain separate.
    """

    side: Literal["bid", "ask"]
    price: str
    size: str
    action: Literal["upsert", "delete"]
    order_count: str | None = None

    def __post_init__(self) -> None:
        side = _enum_value(self.side, "side", L2_SIDE_VALUES)
        action = _enum_value(self.action, "action", L2_ACTION_VALUES)
        price = _decimal_text(self.price, "price", positive=True)
        size = _decimal_text(self.size, "size", positive=False)
        if action == "delete" and size != "0":
            raise InvalidRequest("L2 delete changes must carry size '0'")
        if action == "upsert" and size == "0":
            raise InvalidRequest("L2 upsert changes require positive size")
        order_count = None
        if self.order_count is not None:
            order_count = _digit_string(self.order_count, "order_count")
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "action", action)
        object.__setattr__(self, "price", price)
        object.__setattr__(self, "size", size)
        object.__setattr__(self, "order_count", order_count)

    def stable_dict(self) -> dict[str, str]:
        result = {
            "side": self.side,
            "price": self.price,
            "size": self.size,
            "action": self.action,
        }
        if self.order_count is not None:
            result["order_count"] = self.order_count
        return result


@dataclass(frozen=True, slots=True)
class L2BookEvent:
    """Venue-independent aggregated book snapshot or incremental update."""

    venue: str
    market_type: Literal["spot", "linear_perp", "inverse_perp", "futures"]
    instrument: str
    native_symbol: str
    source_channel: str
    acquisition_mode: Literal["historical_archive", "live_websocket", "rest_snapshot"]
    event_type: Literal["snapshot", "delta"]
    exchange_ts: Instant
    bids: tuple[L2LevelChange, ...]
    asks: tuple[L2LevelChange, ...]
    provider_ts: Instant | None = None
    receive_ts: Instant | None = None
    source_depth_limit: int | None = None
    native_sequence: str | None = None
    native_prev_sequence: str | None = None
    native_update_id: str | None = None
    continuity_token: str | None = None

    def __post_init__(self) -> None:
        venue = _identifier(self.venue, "venue")
        market_type = _enum_value(self.market_type, "market_type", L2_MARKET_TYPE_VALUES)
        if not isinstance(self.instrument, str) or not self.instrument.strip():
            raise InvalidRequest("instrument must be a non-empty string")
        if not isinstance(self.native_symbol, str) or not self.native_symbol.strip():
            raise InvalidRequest("native_symbol must be a non-empty string")
        if not isinstance(self.source_channel, str) or not self.source_channel.strip():
            raise InvalidRequest("source_channel must be a non-empty string")
        acquisition_mode = _enum_value(
            self.acquisition_mode, "acquisition_mode", L2_ACQUISITION_MODE_VALUES
        )
        event_type = _enum_value(self.event_type, "event_type", L2_EVENT_TYPE_VALUES)
        exchange_ts = Instant.parse(self.exchange_ts)
        provider_ts = Instant.parse(self.provider_ts) if self.provider_ts is not None else None
        receive_ts = Instant.parse(self.receive_ts) if self.receive_ts is not None else None
        bids = tuple(_require_l2_level(change, "bid") for change in self.bids)
        asks = tuple(_require_l2_level(change, "ask") for change in self.asks)
        if not bids and not asks:
            raise InvalidRequest("L2 book events require at least one bid or ask change")
        source_depth_limit = self.source_depth_limit
        if source_depth_limit is not None:
            if not isinstance(source_depth_limit, int) or isinstance(source_depth_limit, bool):
                raise InvalidRequest("source_depth_limit must be an integer")
            if source_depth_limit < 1:
                raise InvalidRequest("source_depth_limit must be positive")
        native_sequence = _optional_token(self.native_sequence, "native_sequence")
        native_prev_sequence = _optional_token(self.native_prev_sequence, "native_prev_sequence")
        native_update_id = _optional_token(self.native_update_id, "native_update_id")
        continuity_token = _optional_token(self.continuity_token, "continuity_token")
        object.__setattr__(self, "venue", venue)
        object.__setattr__(self, "market_type", market_type)
        object.__setattr__(self, "instrument", self.instrument.strip())
        object.__setattr__(self, "native_symbol", self.native_symbol.strip())
        object.__setattr__(self, "source_channel", self.source_channel.strip())
        object.__setattr__(self, "acquisition_mode", acquisition_mode)
        object.__setattr__(self, "event_type", event_type)
        object.__setattr__(self, "exchange_ts", exchange_ts)
        object.__setattr__(self, "provider_ts", provider_ts)
        object.__setattr__(self, "receive_ts", receive_ts)
        object.__setattr__(self, "bids", bids)
        object.__setattr__(self, "asks", asks)
        object.__setattr__(self, "source_depth_limit", source_depth_limit)
        object.__setattr__(self, "native_sequence", native_sequence)
        object.__setattr__(self, "native_prev_sequence", native_prev_sequence)
        object.__setattr__(self, "native_update_id", native_update_id)
        object.__setattr__(self, "continuity_token", continuity_token)

    def stable_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "venue": self.venue,
            "market_type": self.market_type,
            "instrument": self.instrument,
            "native_symbol": self.native_symbol,
            "source_channel": self.source_channel,
            "acquisition_mode": self.acquisition_mode,
            "event_type": self.event_type,
            "exchange_ts": self.exchange_ts.isoformat(),
            "bids": [change.stable_dict() for change in self.bids],
            "asks": [change.stable_dict() for change in self.asks],
        }
        if self.provider_ts is not None:
            result["provider_ts"] = self.provider_ts.isoformat()
        if self.receive_ts is not None:
            result["receive_ts"] = self.receive_ts.isoformat()
        if self.source_depth_limit is not None:
            result["source_depth_limit"] = self.source_depth_limit
        if self.native_sequence is not None:
            result["native_sequence"] = self.native_sequence
        if self.native_prev_sequence is not None:
            result["native_prev_sequence"] = self.native_prev_sequence
        if self.native_update_id is not None:
            result["native_update_id"] = self.native_update_id
        if self.continuity_token is not None:
            result["continuity_token"] = self.continuity_token
        return result

    @property
    def identity(self) -> str:
        return l2_book_event_identity_v1(self)


def l2_book_event_identity_v1(event: L2BookEvent) -> str:
    """Hash one normalized L2 event without sorting source level order."""

    import json

    payload = json.dumps(
        event.stable_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = hashlib.sha256(L2_BOOK_EVENT_HASH_V1_DOMAIN_TAG)
    digest.update(payload)
    return f"{L2_BOOK_EVENT_V1_IDENTITY}:sha256:{digest.hexdigest()}"


def _enum_value(value: Any, field: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str):
        raise InvalidRequest(f"{field} must be a string")
    normalized = value.strip().lower()
    if normalized not in allowed:
        raise InvalidRequest(f"unsupported {field}: {value!r}")
    return normalized


def _decimal_text(value: Any, field: str, *, positive: bool) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequest(f"{field} must be a non-empty decimal string")
    text = value.strip()
    pattern = _POSITIVE_DECIMAL_RE if positive else _NON_NEGATIVE_DECIMAL_RE
    if not pattern.fullmatch(text):
        qualifier = "positive" if positive else "non-negative"
        raise InvalidRequest(f"{field} must be a {qualifier} decimal string")
    return text


def _digit_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _DIGIT_STRING_RE.fullmatch(value):
        raise InvalidRequest(f"{field} must be a canonical non-negative integer string")
    return value


def _optional_token(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise InvalidRequest(f"{field} must be a non-empty string when present")
    return value.strip()


def _require_l2_level(change: L2LevelChange, side: str) -> L2LevelChange:
    if not isinstance(change, L2LevelChange):
        raise InvalidRequest("L2 book events require L2LevelChange values")
    if change.side != side:
        raise InvalidRequest(f"{side} collection contains {change.side!r} change")
    return change


CANONICAL_CONTENT_HASH_V1_IDENTITY = "canonical-content-hash-v1"
CANONICAL_CONTENT_HASH_V1_DOMAIN_TAG = (
    b"quant-platform/canonical-content-hash-v1/trade-v1\x00"
)

def _canonical_timestamp_text(value: Instant) -> str:
    instant = Instant.parse(value)
    seconds, nanos = divmod(instant.epoch_ns, 1_000_000_000)
    base = datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{nanos:09d}Z"


def _canonical_field_frame(value: Any, field: str, *, timestamp: bool = False) -> bytes:
    if value is None:
        return b"\x00"
    if timestamp:
        encoded = _canonical_timestamp_text(value).encode("utf-8")
    elif isinstance(value, str):
        encoded = value.encode("utf-8")
    else:
        raise InvalidRequest(f"{field} must be a canonical string or timestamp")
    return b"\x01" + len(encoded).to_bytes(8, "big") + encoded


def _canonical_trade_record_frame(record: TradeRecord) -> bytes:
    if not isinstance(record, TradeRecord):
        raise InvalidRequest("CanonicalContentHashV1 requires TradeRecord values")
    fields = (
        _canonical_field_frame(record.venue, "venue"),
        _canonical_field_frame(record.instrument, "instrument"),
        _canonical_field_frame(record.exchange_ts, "exchange_ts", timestamp=True),
        _canonical_field_frame(record.receive_ts, "receive_ts", timestamp=True),
        _canonical_field_frame(record.price, "price"),
        _canonical_field_frame(record.size, "size"),
        _canonical_field_frame(record.aggressor_side, "aggressor_side"),
        _canonical_field_frame(record.trade_id, "trade_id"),
        _canonical_field_frame(record.sequence, "sequence"),
    )
    return b"".join(fields)


def canonical_content_hash_v1(records: Iterable[TradeRecord]) -> str:
    """Hash ordered canonical ``trade-v1`` logical records.

    The input order is consumed exactly as supplied.  This function never
    sorts records, because a permutation is a different ordered content
    identity under the frozen contract.
    """

    ordered_records = list(records)
    try:
        count = len(ordered_records).to_bytes(8, "big", signed=False)
    except OverflowError as exc:  # pragma: no cover - impossible for memory-sized input
        raise InvalidRequest("CanonicalContentHashV1 record count exceeds uint64") from exc
    digest = hashlib.sha256()
    digest.update(CANONICAL_CONTENT_HASH_V1_DOMAIN_TAG)
    digest.update(count)
    for record in ordered_records:
        digest.update(_canonical_trade_record_frame(record))
    return f"{CANONICAL_CONTENT_HASH_V1_IDENTITY}:sha256:{digest.hexdigest()}"


class CanonicalContentHashV1:
    """Named facade for the canonical content hash primitive."""

    identity = CANONICAL_CONTENT_HASH_V1_IDENTITY
    domain_tag = CANONICAL_CONTENT_HASH_V1_DOMAIN_TAG

    @staticmethod
    def compute(records: Iterable[TradeRecord]) -> str:
        return canonical_content_hash_v1(records)
