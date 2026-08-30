"""Small, typed domain models for the DataGateway contract.

The models deliberately contain no PostgreSQL IDs or physical paths in their
stable identity values.  Runtime locators are kept in catalog-owned records
and are exposed only as diagnostic provenance.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
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


class LifecyclePolicy(str, Enum):
    VALID_ONLY = "valid-only"
    VALID_AND_CLOSED = "valid-and-closed"
    VALID_CLOSED_AND_DEGRADED = "valid-closed-and-degraded"

    @property
    def states(self) -> tuple[str, ...]:
        return {
            LifecyclePolicy.VALID_ONLY: ("valid",),
            LifecyclePolicy.VALID_AND_CLOSED: ("valid", "closed"),
            LifecyclePolicy.VALID_CLOSED_AND_DEGRADED: ("valid", "closed", "degraded"),
        }[self]


@dataclass(frozen=True, slots=True)
class DataRequest:
    dataset_selector: DatasetIdentity
    start: Instant | datetime | str
    end: Instant | datetime | str
    schema_requirement: str | None = None
    lifecycle_policy: LifecyclePolicy = LifecyclePolicy.VALID_ONLY
    coverage_policy: str = "strict"
    # This is an opaque, frozen request-contract token.  Its implementation
    # and compatibility claim are supplied by the source adapter, not here.
    ordering_policy: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_selector, DatasetIdentity):
            raise InvalidRequest("dataset_selector must be DatasetIdentity")
        if not isinstance(self.ordering_policy, str) or not self.ordering_policy.strip():
            raise InvalidRequest("ordering_policy must be a non-empty string")
        start = Instant.parse(self.start)
        end = Instant.parse(self.end)
        if start > end:
            raise InvalidRequest("request start must not be after end")
        if self.schema_requirement is None:
            schema = self.dataset_selector.record_schema_id
        else:
            schema = _identifier(self.schema_requirement, "schema_requirement")
        if self.coverage_policy != "strict":
            raise InvalidRequest("DataGateway v1 supports only strict coverage")
        if not isinstance(self.lifecycle_policy, LifecyclePolicy):
            try:
                object.__setattr__(self, "lifecycle_policy", LifecyclePolicy(self.lifecycle_policy))
            except ValueError as exc:
                raise InvalidRequest("unknown lifecycle policy") from exc
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "schema_requirement", schema)

    @property
    def empty(self) -> bool:
        return self.start == self.end

    def stable_dict(self) -> dict[str, Any]:
        return {
            "dataset_selector": self.dataset_selector.stable_dict(),
            "schema_requirement": self.schema_requirement,
            "interval": {"start": self.start.isoformat(), "end": self.end.isoformat()},
            "lifecycle_policy": self.lifecycle_policy.value,
            "coverage_policy": self.coverage_policy,
            "ordering_policy": self.ordering_policy,
        }

    @property
    def request_identity(self) -> str:
        return _fingerprint(self.stable_dict())


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


@dataclass(frozen=True, slots=True)
class CatalogPartition:
    natural_identity: NaturalPartitionIdentity
    catalog_partition_id: str
    storage_root_id: str
    storage_root: str
    dataset_rel_root: str
    rel_path: str
    ts_start: Instant | None
    ts_end: Instant | None
    row_count: int
    content_sha256: str | None
    manifest_sha256: str | None
    state: str
    producer: str
    code_ref: str

    @property
    def coverage(self) -> CoverageInterval | None:
        if self.ts_start is None or self.ts_end is None or self.ts_start == self.ts_end:
            return None
        return CoverageInterval(self.ts_start, self.ts_end)


@dataclass(frozen=True, slots=True)
class CatalogDataset:
    identity: DatasetIdentity
    catalog_dataset_id: str
    rel_root: str
    manifest_sha256: str
    schema_version: int
    schema_hash: str


@dataclass(frozen=True, slots=True)
class DataSliceMetadata:
    dataset_identity: DatasetIdentity
    record_schema_id: str
    schema_version: int
    schema_hash: str
    natural_partitions: tuple[NaturalPartitionIdentity, ...]
    manifest_hashes: tuple[str, ...]
    content_hashes: tuple[str, ...]
    request_identity: str
    result_identity: str
    requested_interval: CoverageInterval
    eligible_coverage: tuple[CoverageInterval, ...]
    coverage_gaps: tuple[CoverageInterval, ...]
    coverage_complete: bool
    returned_record_bounds: RecordTimeBounds | None
    row_count: int
    ordering_policy: str
    lifecycle_policy: LifecyclePolicy
    coverage_policy: str
    catalog_dataset_id: str
    catalog_partition_ids: tuple[str, ...]
    storage_root_ids: tuple[str, ...]
    rel_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DataSlice:
    records: tuple[TradeRecord, ...]
    metadata: DataSliceMetadata

    def __iter__(self):
        return iter(self.records)


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def merge_intervals(intervals: Iterable[CoverageInterval]) -> tuple[CoverageInterval, ...]:
    ordered = sorted(intervals, key=lambda item: (item.start.epoch_ns, item.end.epoch_ns))
    merged: list[CoverageInterval] = []
    for interval in ordered:
        if not merged or interval.start > merged[-1].end:
            merged.append(interval)
        elif interval.end > merged[-1].end:
            merged[-1] = CoverageInterval(merged[-1].start, interval.end)
    return tuple(merged)


def intersect(interval: CoverageInterval, request: CoverageInterval) -> CoverageInterval | None:
    start = max(interval.start, request.start)
    end = min(interval.end, request.end)
    return CoverageInterval(start, end) if start < end else None


def gaps_for(request: CoverageInterval, covered: Iterable[CoverageInterval]) -> tuple[CoverageInterval, ...]:
    cursor = request.start
    gaps: list[CoverageInterval] = []
    for interval in merge_intervals(covered):
        if interval.end <= request.start or interval.start >= request.end:
            continue
        clipped = intersect(interval, request)
        if clipped is None:
            continue
        if cursor < clipped.start:
            gaps.append(CoverageInterval(cursor, clipped.start))
        if clipped.end > cursor:
            cursor = clipped.end
    if cursor < request.end:
        gaps.append(CoverageInterval(cursor, request.end))
    return tuple(gaps)


def result_fingerprint(payload: dict[str, Any]) -> str:
    return _fingerprint(payload)


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
