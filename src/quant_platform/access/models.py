"""Data Access requests, results, catalog locators and read metadata helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
from typing import Any, Iterable

from ..data.models import (
    CoverageInterval,
    DatasetIdentity,
    Instant,
    InvalidRequest,
    NaturalPartitionIdentity,
    RecordTimeBounds,
    TradeRecord,
    _identifier,
)
from quant_platform.canonical import canonical_bytes


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


class CoveragePolicy(str, Enum):
    STRICT = "strict"
    ALLOW_PARTIAL = "allow-partial"

    @classmethod
    def normalize(cls, value: "CoveragePolicy | str") -> "CoveragePolicy":
        if isinstance(value, CoveragePolicy):
            return value
        if not isinstance(value, str):
            raise ValueError(value)
        if value in cls.__members__:
            return cls[value]
        return cls(value)


@dataclass(frozen=True, slots=True)
class DataRequest:
    dataset_selector: DatasetIdentity
    start: Instant | datetime | str
    end: Instant | datetime | str
    schema_requirement: str | None = None
    lifecycle_policy: LifecyclePolicy = LifecyclePolicy.VALID_ONLY
    coverage_policy: CoveragePolicy = CoveragePolicy.STRICT
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
        if not isinstance(self.lifecycle_policy, LifecyclePolicy):
            try:
                object.__setattr__(self, "lifecycle_policy", LifecyclePolicy(self.lifecycle_policy))
            except ValueError as exc:
                raise InvalidRequest("unknown lifecycle policy") from exc
        if not isinstance(self.coverage_policy, CoveragePolicy):
            try:
                object.__setattr__(self, "coverage_policy", CoveragePolicy.normalize(self.coverage_policy))
            except ValueError as exc:
                raise InvalidRequest("unknown coverage policy") from exc
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
            "coverage_policy": self.coverage_policy.value,
            "ordering_policy": self.ordering_policy,
        }

    @property
    def request_identity(self) -> str:
        return _fingerprint(self.stable_dict())


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
    coverage_policy: CoveragePolicy
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


LIVE_STREAM_CURSOR_SCHEMA_V1 = "live-stream-cursor-v1"
LIVE_GAP_EVENT_SCHEMA_V1 = "live-gap-event-v1"


@dataclass(frozen=True, slots=True)
class LiveStreamRequest:
    """A live, resumable read request (B06), additive to :class:`DataRequest`.

    Unlike :class:`DataRequest`, there is no ``start``/``end``: a live stream
    is not bounded by a fixed interval, and resumption is expressed entirely
    through the ``cursor`` argument to :meth:`DataGateway.live_stream`. There
    is no ``coverage_policy`` either -- a live stream never refuses on a gap
    (that is ``STRICT``'s historical-read semantic); it always surfaces a
    gap explicitly as a :class:`LiveGapEvent` instead (ADR-0047).
    """

    dataset_selector: DatasetIdentity
    schema_requirement: str | None = None
    lifecycle_policy: LifecyclePolicy = LifecyclePolicy.VALID_ONLY
    ordering_policy: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_selector, DatasetIdentity):
            raise InvalidRequest("dataset_selector must be DatasetIdentity")
        if not isinstance(self.ordering_policy, str) or not self.ordering_policy.strip():
            raise InvalidRequest("ordering_policy must be a non-empty string")
        if self.schema_requirement is None:
            schema = self.dataset_selector.record_schema_id
        else:
            schema = _identifier(self.schema_requirement, "schema_requirement")
        if not isinstance(self.lifecycle_policy, LifecyclePolicy):
            try:
                object.__setattr__(self, "lifecycle_policy", LifecyclePolicy(self.lifecycle_policy))
            except ValueError as exc:
                raise InvalidRequest("unknown lifecycle policy") from exc
        object.__setattr__(self, "schema_requirement", schema)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "dataset_selector": self.dataset_selector.stable_dict(),
            "schema_requirement": self.schema_requirement,
            "lifecycle_policy": self.lifecycle_policy.value,
            "ordering_policy": self.ordering_policy,
        }


@dataclass(frozen=True, slots=True)
class LiveStreamCursorV1:
    """B06's opaque, caller-owned resume position (ADR-0047 decision 4).

    The gateway never persists this (decision 3: no new physical storage or
    catalog schema); the caller carries it between
    :meth:`DataGateway.live_stream` calls to resume exactly where a prior
    call left off. Deliberately mirrors
    ``operations.checkpoint.LiveCheckpointV1``'s own field vocabulary
    (dataset identity, the canonical ``(exchange_ts, trade_id)`` order key,
    ``last_observed_sequence`` as non-authoritative diagnostic evidence)
    rather than a competing identity model, while carrying no producer-side
    publication-generation fields (``catalog_dataset_id``/``partition_key``/
    ``revision``/manifest hash) -- those are physical detail, not consumer
    semantic identity.
    """

    schema_version: str
    dataset_identity: DatasetIdentity
    ordering_policy: str
    coverage_segment_id: str
    last_canonical_exchange_ts: Instant | None
    last_canonical_trade_id: str | None
    last_observed_sequence: str | None = None

    def __post_init__(self) -> None:
        if self.schema_version != LIVE_STREAM_CURSOR_SCHEMA_V1:
            raise InvalidRequest("unsupported live stream cursor schema_version")
        if not isinstance(self.dataset_identity, DatasetIdentity):
            raise InvalidRequest("dataset_identity must be DatasetIdentity")
        if not isinstance(self.ordering_policy, str) or not self.ordering_policy.strip():
            raise InvalidRequest("ordering_policy must be a non-empty string")
        if not isinstance(self.coverage_segment_id, str) or not self.coverage_segment_id.strip():
            raise InvalidRequest("coverage_segment_id must be a non-empty string")
        if (self.last_canonical_exchange_ts is None) != (self.last_canonical_trade_id is None):
            raise InvalidRequest(
                "last_canonical_exchange_ts and last_canonical_trade_id must both be set or both be None"
            )
        if self.last_canonical_exchange_ts is not None and not isinstance(
            self.last_canonical_exchange_ts, Instant
        ):
            raise InvalidRequest("last_canonical_exchange_ts must be a canonical Instant")
        if self.last_canonical_trade_id is not None and (
            not isinstance(self.last_canonical_trade_id, str) or not self.last_canonical_trade_id.strip()
        ):
            raise InvalidRequest("last_canonical_trade_id must be a non-empty string when set")

    @property
    def last_canonical_order_key(self) -> tuple[Instant, str] | None:
        if self.last_canonical_exchange_ts is None or self.last_canonical_trade_id is None:
            return None
        return (self.last_canonical_exchange_ts, self.last_canonical_trade_id)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "dataset_identity": self.dataset_identity.stable_dict(),
            "ordering_policy": self.ordering_policy,
            "coverage_segment_id": self.coverage_segment_id,
            "last_canonical_exchange_ts": (
                self.last_canonical_exchange_ts.isoformat()
                if self.last_canonical_exchange_ts is not None
                else None
            ),
            "last_canonical_trade_id": self.last_canonical_trade_id,
            "last_observed_sequence": self.last_observed_sequence,
        }


@dataclass(frozen=True, slots=True)
class LiveTradeEvent:
    """Authoritative live data delivery (ADR-0047 decision 6)."""

    record: TradeRecord
    cursor: LiveStreamCursorV1


class LiveSessionState(str, Enum):
    """Diagnostic/operational session evidence, never a completeness claim."""

    HEARTBEAT = "heartbeat"
    DISCONNECTED = "disconnected"
    RECONNECTED = "reconnected"


@dataclass(frozen=True, slots=True)
class LiveSessionEvent:
    state: LiveSessionState
    cursor: LiveStreamCursorV1
    evidence: str | None = None


class LiveGapStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class LiveGapEvent:
    """Explicit, descriptive-only interruption evidence (ADR-0047 decision 6).

    Emitted as an ``OPEN``/``CLOSED`` pair sharing one ``gap_id``: ``OPEN``
    the moment the last durable key cannot be proven contiguous with newly
    eligible coverage (``upper_bound``/``resumed_cursor`` are ``None``
    because they are not yet provable); ``CLOSED`` once a subsequent
    governed segment actually resumes, adding the now-known ``upper_bound``
    and ``resumed_cursor``. Never usable as an implicit completeness claim.
    """

    schema_version: str
    gap_id: str
    status: LiveGapStatus
    previous_cursor: LiveStreamCursorV1
    lower_bound: Instant
    upper_bound: Instant | None
    resumed_cursor: LiveStreamCursorV1 | None
    reason: str

    def __post_init__(self) -> None:
        if self.schema_version != LIVE_GAP_EVENT_SCHEMA_V1:
            raise InvalidRequest("unsupported live gap event schema_version")
        if not isinstance(self.lower_bound, Instant):
            raise InvalidRequest("lower_bound must be a canonical Instant")
        if self.status is LiveGapStatus.OPEN:
            if self.upper_bound is not None or self.resumed_cursor is not None:
                raise InvalidRequest("an OPEN gap must not carry upper_bound or resumed_cursor")
        elif self.status is LiveGapStatus.CLOSED:
            if self.upper_bound is None or self.resumed_cursor is None:
                raise InvalidRequest("a CLOSED gap must carry both upper_bound and resumed_cursor")
            if not isinstance(self.upper_bound, Instant):
                raise InvalidRequest("upper_bound must be a canonical Instant")
            if self.upper_bound <= self.lower_bound:
                raise InvalidRequest("upper_bound must be after lower_bound")
        else:
            raise InvalidRequest("unknown LiveGapStatus")

    @property
    def affected_interval(self) -> CoverageInterval | None:
        """Exact non-complete support, once knowable; ``None`` while OPEN."""

        if self.upper_bound is None:
            return None
        return CoverageInterval(self.lower_bound, self.upper_bound)


LiveStreamEvent = LiveTradeEvent | LiveSessionEvent | LiveGapEvent


def _fingerprint(value: Any) -> str:
    encoded = canonical_bytes(value, profile="sorted-compact-ascii-v1", allow_nan=True)
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
