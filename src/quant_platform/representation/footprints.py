"""FootprintDefinition v1 historical FINAL footprint computation.

This module owns deterministic footprint representation meaning only. It
consumes already bounded canonical ``TradeRecord`` values plus caller-supplied
source evidence; it never opens DataGateway, catalog, files, source adapters or
storage.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any, Iterable

from ..data.models import (
    CoverageInterval,
    DatasetIdentity,
    Instant,
    InvalidRequest,
    NaturalPartitionIdentity,
    RecordTimeBounds,
    TradeRecord,
)
from ..ordering import TRADES_CANONICAL_TOTAL_ORDER_V1
from .candles import parse_duration_ns


FOOTPRINT_DEFINITION_V1_VERSION = 1
FOOTPRINT_V1_RECORD_SCHEMA = "footprint-v1"
FOOTPRINT_RESULT_FINGERPRINT_V1_DOMAIN = "historical-footprint-result-v1"
_POSITIVE_DECIMAL_RE = re.compile(r"^([1-9][0-9]*(?:\.[0-9]+)?|0\.[0-9]*[1-9][0-9]*)$")
_NON_NEGATIVE_DECIMAL_RE = re.compile(
    r"^(0(?:\.0+)?|[1-9][0-9]*(?:\.[0-9]+)?|0\.[0-9]*[1-9][0-9]*)$"
)


class FootprintComputationError(ValueError):
    """Base class for expected historical footprint runtime failures."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None):
        super().__init__(message)
        self.context = context or {}


class FootprintInputError(FootprintComputationError):
    """Raised when supplied canonical trades cannot be consumed as input."""


class FootprintGridError(FootprintInputError):
    """Raised when tick-grid semantics cannot be satisfied exactly."""


class FootprintAggressorSideError(FootprintInputError):
    """Raised when explicit aggressor side evidence is absent or unusable."""


class FootprintCoverageError(FootprintComputationError):
    """Raised when caller-supplied source support does not cover required buckets."""


class FootprintFinalizationError(FootprintComputationError):
    """Raised when caller-supplied evidence cannot finalize every required bucket."""


class FootprintOrderingError(FootprintComputationError):
    """Raised when source ordering evidence does not satisfy FootprintDefinition v1."""


class FootprintProvenanceError(FootprintComputationError):
    """Raised when reproducibility evidence is missing or inconsistent."""


@dataclass(frozen=True, slots=True)
class _ExactDecimal:
    coefficient: int
    scale: int

    @classmethod
    def parse_positive(cls, value: str, field: str) -> "_ExactDecimal":
        if not isinstance(value, str) or not _POSITIVE_DECIMAL_RE.fullmatch(value):
            raise FootprintInputError(
                f"{field} must be a positive exact decimal string",
                context={"field": field},
            )
        return cls._parse(value)

    @classmethod
    def parse_non_negative(cls, value: str, field: str) -> "_ExactDecimal":
        if not isinstance(value, str) or not _NON_NEGATIVE_DECIMAL_RE.fullmatch(value):
            raise FootprintInputError(
                f"{field} must be a non-negative exact decimal string",
                context={"field": field},
            )
        return cls._parse(value)

    @classmethod
    def zero(cls) -> "_ExactDecimal":
        return cls(0, 0)

    @classmethod
    def _parse(cls, value: str) -> "_ExactDecimal":
        integer, dot, fraction = value.partition(".")
        digits = integer + (fraction if dot else "")
        return cls(int(digits), len(fraction) if dot else 0)

    def __add__(self, other: "_ExactDecimal") -> "_ExactDecimal":
        scale = max(self.scale, other.scale)
        left = self.coefficient * 10 ** (scale - self.scale)
        right = other.coefficient * 10 ** (scale - other.scale)
        return _ExactDecimal(left + right, scale)

    def is_zero(self) -> bool:
        return self.coefficient == 0

    def canonical(self) -> str:
        coefficient = self.coefficient
        scale = self.scale
        while scale and coefficient % 10 == 0:
            coefficient //= 10
            scale -= 1
        digits = str(coefficient)
        if scale == 0:
            return digits
        if len(digits) <= scale:
            return "0." + "0" * (scale - len(digits)) + digits
        return digits[:-scale] + "." + digits[-scale:]

    def exact_integer_quotient(self, divisor: "_ExactDecimal") -> int:
        numerator = self.coefficient * 10**divisor.scale
        denominator = divisor.coefficient * 10**self.scale
        quotient, remainder = divmod(numerator, denominator)
        if remainder:
            raise FootprintGridError(
                "source price is not exactly on the FootprintDefinition v1 tick grid",
                context={"price": self.canonical(), "tick_size": divisor.canonical()},
            )
        return quotient

    def multiply_int(self, multiplier: int) -> "_ExactDecimal":
        return _ExactDecimal(self.coefficient * multiplier, self.scale)


@dataclass(frozen=True, slots=True)
class FootprintDefinitionV1:
    """Frozen FootprintDefinition v1 semantic payload for fixed UTC buckets."""

    duration_ns: int | str
    tick_size: str

    def __post_init__(self) -> None:
        try:
            duration = parse_duration_ns(self.duration_ns)
        except InvalidRequest as exc:
            raise FootprintInputError("duration_ns must be a positive duration") from exc
        tick = _ExactDecimal.parse_positive(self.tick_size, "tick_size")
        object.__setattr__(self, "duration_ns", duration)
        object.__setattr__(self, "tick_size", tick.canonical())

    @classmethod
    def from_duration_and_tick(cls, duration: int | str, tick_size: str) -> "FootprintDefinitionV1":
        return cls(duration, tick_size)

    @property
    def canonical_payload(self) -> dict[str, Any]:
        return {
            "aggregation": {
                "buy_volume": "sum_exact_decimal_size_where_aggressor_side_buy",
                "sell_volume": "sum_exact_decimal_size_where_aggressor_side_sell",
                "same_level": "aggregate_to_one_row_per_bucket_and_level_index",
            },
            "aggressor_side": {
                "accepted": ["buy", "sell"],
                "missing": "refuse_bucket",
                "unknown": "refuse_bucket",
            },
            "alignment": {"kind": "utc_epoch", "offset_ns": "0"},
            "availability": {
                "causal_floor": "bucket_end",
                "observed_available_at": "nullable_observed_source_finalization_time",
                "partial_consumption": "forbidden",
            },
            "boundary": {
                "bucket": "[start,end)",
                "query_selection": "bucket_intersects_[query_start,query_end)",
                "timestamp_coordinate": "bucket_start",
            },
            "definition_version": FOOTPRINT_DEFINITION_V1_VERSION,
            "duration_ns": str(self.duration_ns),
            "finality": {
                "final_state": "FINAL",
                "evidence": "complete_finalized_authoritative_source_support_required",
                "partial_state": "deferred",
            },
            "grid": {
                "level_index": "price_div_tick_size_exact_integer",
                "origin": "zero",
                "price": "level_index_times_tick_size",
                "tick_size": self.tick_size,
                "tolerance": "none",
            },
            "output_record_schema": FOOTPRINT_V1_RECORD_SCHEMA,
            "representation_kind": "footprint",
            "sparsity": {
                "empty_covered_bucket": "no_level_rows",
                "missing_grid_level": "absent",
                "ordering": "strictly_increasing_integer_level_index_within_bucket",
            },
            "source": {
                "event_time_field": "exchange_ts",
                "ordering_policy": TRADES_CANONICAL_TOTAL_ORDER_V1,
                "record_schema": "trade-v1",
                "representation": "trades@1",
            },
        }

    @property
    def canonical_utf8_serialization(self) -> str:
        return json.dumps(
            self.canonical_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @property
    def definition_identity(self) -> str:
        digest = hashlib.sha256(self.canonical_utf8_serialization.encode("utf-8")).hexdigest()
        return f"footprint-definition-v1:sha256:{digest}"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "definition_identity": self.definition_identity,
            "canonical_payload": self.canonical_payload,
        }


@dataclass(frozen=True, slots=True)
class FootprintLevel:
    level_index: int
    price: str
    buy_volume: str
    sell_volume: str

    def __post_init__(self) -> None:
        if type(self.level_index) is not int:
            raise FootprintInputError("level_index must be an integer")
        price = _ExactDecimal.parse_non_negative(self.price, "price")
        buy = _ExactDecimal.parse_non_negative(self.buy_volume, "buy_volume")
        sell = _ExactDecimal.parse_non_negative(self.sell_volume, "sell_volume")
        if buy.is_zero() and sell.is_zero():
            raise FootprintInputError("a Footprint level requires non-zero buy or sell volume")
        object.__setattr__(self, "price", price.canonical())
        object.__setattr__(self, "buy_volume", buy.canonical())
        object.__setattr__(self, "sell_volume", sell.canonical())

    def stable_dict(self) -> dict[str, str | int]:
        return {
            "level_index": self.level_index,
            "price": self.price,
            "buy_volume": self.buy_volume,
            "sell_volume": self.sell_volume,
        }


@dataclass(frozen=True, slots=True)
class FootprintBucket:
    bucket_start: Instant | str
    bucket_end: Instant | str
    levels: tuple[FootprintLevel, ...]
    state: str = "FINAL"

    def __post_init__(self) -> None:
        start = Instant.parse(self.bucket_start)
        end = Instant.parse(self.bucket_end)
        if start >= end:
            raise FootprintInputError("Footprint bucket start must be before end")
        if self.state != "FINAL":
            raise FootprintFinalizationError("FootprintDefinition v1 exposes FINAL buckets only")
        levels = tuple(self.levels)
        indexes = [level.level_index for level in levels]
        if indexes != sorted(indexes):
            raise FootprintInputError("Footprint levels must be ordered by increasing level_index")
        if len(indexes) != len(set(indexes)):
            raise FootprintInputError("Footprint levels contain duplicate level_index values")
        object.__setattr__(self, "bucket_start", start)
        object.__setattr__(self, "bucket_end", end)
        object.__setattr__(self, "levels", levels)

    @property
    def causal_floor(self) -> Instant:
        return self.bucket_end  # type: ignore[return-value]

    def stable_dict(self) -> dict[str, Any]:
        return {
            "bucket_start": self.bucket_start.isoformat(),  # type: ignore[union-attr]
            "bucket_end": self.bucket_end.isoformat(),  # type: ignore[union-attr]
            "causal_floor": self.causal_floor.isoformat(),
            "state": self.state,
            "levels": [level.stable_dict() for level in self.levels],
        }


@dataclass(frozen=True, slots=True)
class HistoricalFootprintSourceEvidence:
    """Caller-supplied source support, ordering, finalization and provenance."""

    dataset_identity: DatasetIdentity
    record_schema_id: str
    schema_version: int
    schema_hash: str
    source_ordering_policy: str
    eligible_source_coverage: tuple[CoverageInterval, ...]
    finalized_source_intervals: tuple[CoverageInterval, ...]
    finalization_evidence: tuple[str, ...]
    natural_partitions: tuple[NaturalPartitionIdentity, ...] = ()
    manifest_hashes: tuple[str, ...] = ()
    content_hashes: tuple[str, ...] = ()
    source_request_identity: str | None = None
    source_result_identity: str | None = None
    concrete_ordering_policy: str | None = None
    source_representation: str = "trades@1"
    source_record_schema: str = "trade-v1"
    observed_available_at: Instant | str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_identity, DatasetIdentity):
            raise FootprintProvenanceError("dataset_identity must be DatasetIdentity")
        object.__setattr__(self, "eligible_source_coverage", _coverage_tuple(self.eligible_source_coverage))
        object.__setattr__(self, "finalized_source_intervals", _coverage_tuple(self.finalized_source_intervals))
        object.__setattr__(self, "natural_partitions", tuple(self.natural_partitions))
        object.__setattr__(self, "manifest_hashes", _string_tuple(self.manifest_hashes, "manifest_hashes"))
        object.__setattr__(self, "content_hashes", _string_tuple(self.content_hashes, "content_hashes"))
        object.__setattr__(
            self,
            "finalization_evidence",
            _string_tuple(self.finalization_evidence, "finalization_evidence"),
        )
        if self.observed_available_at is not None:
            object.__setattr__(self, "observed_available_at", Instant.parse(self.observed_available_at))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "dataset_identity": self.dataset_identity.stable_dict(),
            "record_schema_id": self.record_schema_id,
            "schema_version": self.schema_version,
            "schema_hash": self.schema_hash,
            "source_ordering_policy": self.source_ordering_policy,
            "concrete_ordering_policy": self.concrete_ordering_policy,
            "source_representation": self.source_representation,
            "source_record_schema": self.source_record_schema,
            "natural_partitions": [item.stable_dict() for item in self.natural_partitions],
            "manifest_hashes": list(self.manifest_hashes),
            "content_hashes": list(self.content_hashes),
            "source_request_identity": self.source_request_identity,
            "source_result_identity": self.source_result_identity,
            "eligible_source_coverage": [item.stable_dict() for item in self.eligible_source_coverage],
            "finalized_source_intervals": [item.stable_dict() for item in self.finalized_source_intervals],
            "finalization_evidence": list(self.finalization_evidence),
            "observed_available_at": (
                self.observed_available_at.isoformat() if self.observed_available_at is not None else None
            ),
        }


@dataclass(frozen=True, slots=True)
class HistoricalFootprintCoverage:
    covered_intervals: tuple[CoverageInterval, ...]
    gaps: tuple[CoverageInterval, ...]
    complete: bool

    def stable_dict(self) -> dict[str, Any]:
        return {
            "covered_intervals": [item.stable_dict() for item in self.covered_intervals],
            "gaps": [item.stable_dict() for item in self.gaps],
            "complete": self.complete,
        }


@dataclass(frozen=True, slots=True)
class HistoricalFootprintResult:
    buckets: tuple[FootprintBucket, ...]
    requested_interval: CoverageInterval
    required_bucket_support: tuple[CoverageInterval, ...]
    coverage: HistoricalFootprintCoverage
    definition: FootprintDefinitionV1
    definition_identity: str
    source_evidence: HistoricalFootprintSourceEvidence
    implementation_identity: str
    result_identity: str
    state: str = "FINAL"
    output_record_schema: str = FOOTPRINT_V1_RECORD_SCHEMA
    returned_record_bounds: RecordTimeBounds | None = None

    @property
    def levels(self) -> tuple[FootprintLevel, ...]:
        return tuple(level for bucket in self.buckets for level in bucket.levels)

    @property
    def row_count(self) -> int:
        return len(self.levels)

    @property
    def bucket_count(self) -> int:
        return len(self.buckets)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "buckets": [bucket.stable_dict() for bucket in self.buckets],
            "requested_interval": self.requested_interval.stable_dict(),
            "required_bucket_support": [item.stable_dict() for item in self.required_bucket_support],
            "coverage": self.coverage.stable_dict(),
            "definition_identity": self.definition_identity,
            "source_evidence": self.source_evidence.stable_dict(),
            "implementation_identity": self.implementation_identity,
            "state": self.state,
            "output_record_schema": self.output_record_schema,
            "returned_record_bounds": (
                self.returned_record_bounds.stable_dict() if self.returned_record_bounds else None
            ),
        }


@dataclass(slots=True)
class _LevelAccumulator:
    buy_volume: _ExactDecimal
    sell_volume: _ExactDecimal

    @classmethod
    def empty(cls) -> "_LevelAccumulator":
        return cls(_ExactDecimal.zero(), _ExactDecimal.zero())

    def consume(self, side: str, size: _ExactDecimal) -> None:
        if side == "buy":
            self.buy_volume = self.buy_volume + size
        elif side == "sell":
            self.sell_volume = self.sell_volume + size
        else:  # pragma: no cover - guarded by _aggressor_side
            raise AssertionError("unsupported normalized aggressor side")

    def level(self, level_index: int, tick_size: _ExactDecimal) -> FootprintLevel:
        return FootprintLevel(
            level_index=level_index,
            price=tick_size.multiply_int(level_index).canonical(),
            buy_volume=self.buy_volume.canonical(),
            sell_volume=self.sell_volume.canonical(),
        )


def required_footprint_bucket_support(
    definition: FootprintDefinitionV1,
    query_start: Instant | str,
    query_end: Instant | str,
) -> tuple[CoverageInterval, ...]:
    """Return complete bucket-support interval(s) selected by query intersection."""

    start = Instant.parse(query_start)
    end = Instant.parse(query_end)
    if start > end:
        raise InvalidRequest("query start must not be after end")
    if start == end:
        return ()
    duration = definition.duration_ns
    first_start_ns = _bucket_start_ns(start.epoch_ns, duration)
    last_start_ns = _bucket_start_ns(end.epoch_ns - 1, duration)
    return (CoverageInterval(Instant(first_start_ns), Instant(last_start_ns + duration)),)


def aggregate_historical_footprints(
    trades: Iterable[TradeRecord],
    definition: FootprintDefinitionV1,
    query_start: Instant | str,
    query_end: Instant | str,
) -> tuple[FootprintBucket, ...]:
    """Pure FINAL footprint aggregation over already canonical trades."""

    support = required_footprint_bucket_support(definition, query_start, query_end)
    rows = tuple(trades)
    if not support:
        if rows:
            raise FootprintInputError("zero-length footprint query must receive no bounded trades")
        return ()
    support_start = support[0].start
    support_end = support[0].end
    tick = _ExactDecimal.parse_positive(definition.tick_size, "tick_size")
    by_level: dict[tuple[int, int], _LevelAccumulator] = {}
    source_binding: tuple[str, str] | None = None
    for record in rows:
        if not isinstance(record, TradeRecord):
            raise FootprintInputError("historical footprints require TradeRecord input")
        binding = (record.venue, record.instrument)
        if source_binding is None:
            source_binding = binding
        elif binding != source_binding:
            raise FootprintProvenanceError("footprint input trades contain conflicting venue/instrument")
        exchange_ts = Instant.parse(record.exchange_ts)
        if exchange_ts < support_start or exchange_ts >= support_end:
            raise FootprintInputError(
                "trade record falls outside required bucket support",
                context={
                    "exchange_ts": exchange_ts.isoformat(),
                    "required_bucket_support": [item.stable_dict() for item in support],
                },
            )
        side = _aggressor_side(record.aggressor_side)
        price = _ExactDecimal.parse_positive(record.price, "price")
        size = _ExactDecimal.parse_positive(record.size, "size")
        level_index = price.exact_integer_quotient(tick)
        bucket_start = _bucket_start_ns(exchange_ts.epoch_ns, definition.duration_ns)
        accumulator = by_level.setdefault((bucket_start, level_index), _LevelAccumulator.empty())
        accumulator.consume(side, size)

    buckets: list[FootprintBucket] = []
    for bucket_start in _bucket_starts_for_support(support, definition.duration_ns):
        levels = tuple(
            by_level[(bucket_start, level_index)].level(level_index, tick)
            for level_index in sorted(index for start, index in by_level if start == bucket_start)
        )
        buckets.append(
            FootprintBucket(
                bucket_start=Instant(bucket_start),
                bucket_end=Instant(bucket_start + definition.duration_ns),
                levels=levels,
            )
        )
    return tuple(buckets)


def build_historical_footprint_result(
    trades: Iterable[TradeRecord],
    definition: FootprintDefinitionV1,
    query_start: Instant | str,
    query_end: Instant | str,
    *,
    source_evidence: HistoricalFootprintSourceEvidence,
    implementation_identity: str,
) -> HistoricalFootprintResult:
    """Build a typed reproducible FINAL footprint result from supplied evidence."""

    requested_interval = CoverageInterval(Instant.parse(query_start), Instant.parse(query_end))
    if requested_interval.start > requested_interval.end:
        raise InvalidRequest("query start must not be after end")
    if not isinstance(implementation_identity, str) or not implementation_identity.strip():
        raise FootprintProvenanceError("implementation_identity must be a non-empty string")
    _validate_source_evidence(source_evidence)
    support = required_footprint_bucket_support(definition, requested_interval.start, requested_interval.end)
    if support:
        _assert_fully_covered(support, source_evidence.eligible_source_coverage, FootprintCoverageError)
        _assert_fully_covered(support, source_evidence.finalized_source_intervals, FootprintFinalizationError)
        if not source_evidence.finalization_evidence:
            raise FootprintFinalizationError("source finalization evidence is required for FINAL footprints")
        _assert_reproducible_source_path(source_evidence)
        if (
            source_evidence.observed_available_at is not None
            and source_evidence.observed_available_at < support[-1].end
        ):
            raise FootprintFinalizationError(
                "observed source availability cannot precede the required support end",
                context={
                    "observed_available_at": source_evidence.observed_available_at.isoformat(),
                    "required_support_end": support[-1].end.isoformat(),
                },
            )
    rows = tuple(trades)
    _validate_trade_binding(rows, source_evidence.dataset_identity)
    buckets = aggregate_historical_footprints(rows, definition, requested_interval.start, requested_interval.end)
    coverage = _project_complete_coverage(requested_interval, support)
    returned_bounds = None
    if rows:
        returned_bounds = RecordTimeBounds(
            first=min(Instant.parse(row.exchange_ts) for row in rows),
            last=max(Instant.parse(row.exchange_ts) for row in rows),
        )
    identity = _result_identity(
        buckets=buckets,
        requested_interval=requested_interval,
        required_support=support,
        coverage=coverage,
        definition=definition,
        source_evidence=source_evidence,
        implementation_identity=implementation_identity,
        returned_bounds=returned_bounds,
    )
    return HistoricalFootprintResult(
        buckets=buckets,
        requested_interval=requested_interval,
        required_bucket_support=support,
        coverage=coverage,
        definition=definition,
        definition_identity=definition.definition_identity,
        source_evidence=source_evidence,
        implementation_identity=implementation_identity,
        result_identity=identity,
        returned_record_bounds=returned_bounds,
    )


def validate_footprint_bucket(definition: FootprintDefinitionV1, bucket: FootprintBucket) -> FootprintBucket:
    """Validate a caller-supplied realized bucket against FootprintDefinition v1."""

    tick = _ExactDecimal.parse_positive(definition.tick_size, "tick_size")
    for level in bucket.levels:
        expected_price = tick.multiply_int(level.level_index).canonical()
        if level.price != expected_price:
            raise FootprintGridError(
                "Footprint level price does not match level_index * tick_size",
                context={
                    "level_index": level.level_index,
                    "price": level.price,
                    "expected_price": expected_price,
                },
            )
    return bucket


def _bucket_start_ns(epoch_ns: int, duration_ns: int) -> int:
    return (epoch_ns // duration_ns) * duration_ns


def _bucket_starts_for_support(
    support: tuple[CoverageInterval, ...],
    duration_ns: int,
) -> tuple[int, ...]:
    starts: list[int] = []
    for interval in support:
        cursor = interval.start.epoch_ns
        while cursor < interval.end.epoch_ns:
            starts.append(cursor)
            cursor += duration_ns
    return tuple(starts)


def _aggressor_side(value: str) -> str:
    if value is None or not isinstance(value, str) or not value.strip():
        raise FootprintAggressorSideError("aggressor_side is required")
    if value == "unknown":
        raise FootprintAggressorSideError("aggressor_side == 'unknown' is insufficient for Footprint v1")
    if value not in {"buy", "sell"}:
        raise FootprintAggressorSideError(
            "unsupported aggressor_side for Footprint v1",
            context={"aggressor_side": value},
        )
    return value


def _coverage_tuple(values: Iterable[CoverageInterval]) -> tuple[CoverageInterval, ...]:
    result = tuple(values)
    if not all(isinstance(item, CoverageInterval) for item in result):
        raise FootprintProvenanceError("coverage evidence must contain CoverageInterval values")
    return result


def _string_tuple(values: Iterable[str], field: str) -> tuple[str, ...]:
    result = tuple(values)
    if not all(isinstance(item, str) and item.strip() for item in result):
        raise FootprintProvenanceError(f"{field} must contain non-empty strings")
    return result


def _validate_source_evidence(evidence: HistoricalFootprintSourceEvidence) -> None:
    identity = evidence.dataset_identity
    if identity.layer != "canonical" or identity.dataset_kind != "trades":
        raise FootprintProvenanceError("FootprintDefinition v1 requires canonical trades source evidence")
    if evidence.record_schema_id != "trade-v1" or evidence.source_record_schema != "trade-v1":
        raise FootprintProvenanceError("FootprintDefinition v1 requires trade-v1 source evidence")
    if evidence.source_representation != "trades@1":
        raise FootprintProvenanceError("FootprintDefinition v1 requires trades@1 source evidence")
    if evidence.source_ordering_policy != TRADES_CANONICAL_TOTAL_ORDER_V1:
        raise FootprintOrderingError(
            "source ordering evidence does not satisfy FootprintDefinition v1",
            context={
                "required_ordering_policy": TRADES_CANONICAL_TOTAL_ORDER_V1,
                "source_ordering_policy": evidence.source_ordering_policy,
            },
        )
    if evidence.schema_version < 1:
        raise FootprintProvenanceError("schema_version must be positive")
    if not isinstance(evidence.schema_hash, str) or not evidence.schema_hash.strip():
        raise FootprintProvenanceError("schema_hash is required")


def _validate_trade_binding(rows: tuple[TradeRecord, ...], identity: DatasetIdentity) -> None:
    for record in rows:
        if record.venue != identity.venue or record.instrument != identity.instrument:
            raise FootprintProvenanceError(
                "trade record conflicts with source dataset venue/instrument",
                context={
                    "dataset_identity": identity.stable_dict(),
                    "record_venue": record.venue,
                    "record_instrument": record.instrument,
                },
            )


def _assert_reproducible_source_path(evidence: HistoricalFootprintSourceEvidence) -> None:
    partition_path = (
        bool(evidence.natural_partitions)
        and bool(evidence.manifest_hashes)
        and bool(evidence.content_hashes)
    )
    snapshot_path = bool(evidence.source_result_identity)
    if not (partition_path or snapshot_path):
        raise FootprintProvenanceError(
            "FINAL footprint result requires immutable source snapshot or partition/content evidence"
        )


def _assert_fully_covered(
    required: tuple[CoverageInterval, ...],
    available: tuple[CoverageInterval, ...],
    error_type: type[FootprintComputationError],
) -> None:
    gaps = _gaps_for(required, available)
    if gaps:
        raise error_type(
            "required bucket support is not fully covered by supplied evidence",
            context={"gaps": [gap.stable_dict() for gap in gaps]},
        )


def _project_complete_coverage(
    requested_interval: CoverageInterval,
    support: tuple[CoverageInterval, ...],
) -> HistoricalFootprintCoverage:
    if requested_interval.start == requested_interval.end:
        return HistoricalFootprintCoverage(covered_intervals=(), gaps=(), complete=True)
    projected = tuple(
        clipped
        for interval in support
        if (clipped := _intersect(interval, requested_interval)) is not None
    )
    return HistoricalFootprintCoverage(covered_intervals=_merge_intervals(projected), gaps=(), complete=True)


def _gaps_for(
    required: tuple[CoverageInterval, ...],
    available: tuple[CoverageInterval, ...],
) -> tuple[CoverageInterval, ...]:
    gaps: list[CoverageInterval] = []
    merged_available = _merge_intervals(available)
    for interval in required:
        cursor = interval.start
        for covered in merged_available:
            clipped = _intersect(covered, interval)
            if clipped is None:
                continue
            if cursor < clipped.start:
                gaps.append(CoverageInterval(cursor, clipped.start))
            if clipped.end > cursor:
                cursor = clipped.end
        if cursor < interval.end:
            gaps.append(CoverageInterval(cursor, interval.end))
    return tuple(gaps)


def _merge_intervals(intervals: Iterable[CoverageInterval]) -> tuple[CoverageInterval, ...]:
    ordered = sorted(intervals, key=lambda item: (item.start.epoch_ns, item.end.epoch_ns))
    merged: list[CoverageInterval] = []
    for interval in ordered:
        if not merged or interval.start > merged[-1].end:
            merged.append(interval)
        elif interval.end > merged[-1].end:
            merged[-1] = CoverageInterval(merged[-1].start, interval.end)
    return tuple(merged)


def _intersect(left: CoverageInterval, right: CoverageInterval) -> CoverageInterval | None:
    start = max(left.start, right.start)
    end = min(left.end, right.end)
    return CoverageInterval(start, end) if start < end else None


def _result_identity(
    *,
    buckets: tuple[FootprintBucket, ...],
    requested_interval: CoverageInterval,
    required_support: tuple[CoverageInterval, ...],
    coverage: HistoricalFootprintCoverage,
    definition: FootprintDefinitionV1,
    source_evidence: HistoricalFootprintSourceEvidence,
    implementation_identity: str,
    returned_bounds: RecordTimeBounds | None,
) -> str:
    payload = {
        "domain": FOOTPRINT_RESULT_FINGERPRINT_V1_DOMAIN,
        "buckets": [bucket.stable_dict() for bucket in buckets],
        "requested_interval": requested_interval.stable_dict(),
        "required_bucket_support": [item.stable_dict() for item in required_support],
        "coverage": coverage.stable_dict(),
        "definition_identity": definition.definition_identity,
        "source_evidence": source_evidence.stable_dict(),
        "implementation_identity": implementation_identity,
        "state": "FINAL",
        "output_record_schema": FOOTPRINT_V1_RECORD_SCHEMA,
        "returned_record_bounds": returned_bounds.stable_dict() if returned_bounds else None,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return f"{FOOTPRINT_RESULT_FINGERPRINT_V1_DOMAIN}:sha256:{digest}"


__all__ = [
    "FOOTPRINT_DEFINITION_V1_VERSION",
    "FOOTPRINT_RESULT_FINGERPRINT_V1_DOMAIN",
    "FOOTPRINT_V1_RECORD_SCHEMA",
    "FootprintAggressorSideError",
    "FootprintBucket",
    "FootprintComputationError",
    "FootprintCoverageError",
    "FootprintDefinitionV1",
    "FootprintFinalizationError",
    "FootprintGridError",
    "FootprintInputError",
    "FootprintLevel",
    "FootprintOrderingError",
    "FootprintProvenanceError",
    "HistoricalFootprintCoverage",
    "HistoricalFootprintResult",
    "HistoricalFootprintSourceEvidence",
    "aggregate_historical_footprints",
    "build_historical_footprint_result",
    "required_footprint_bucket_support",
    "validate_footprint_bucket",
]
