"""Data Access requests, results, catalog locators and read metadata helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import hashlib
import json
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
