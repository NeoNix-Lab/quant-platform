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
from typing import Any, Iterable


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
