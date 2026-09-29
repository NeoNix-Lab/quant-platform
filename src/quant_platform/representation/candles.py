"""CandleDefinition v1 historical CLOSED candle computation.

This module owns deterministic candle meaning only.  It consumes already
bounded canonical ``TradeRecord`` values plus caller-supplied source evidence;
it never opens DataGateway, catalog, files, source adapters or storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
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


CANDLE_DEFINITION_V1_VERSION = 1
CANDLE_V1_RECORD_SCHEMA = "candle-v1"
CANDLE_RESULT_FINGERPRINT_V1_DOMAIN = "historical-candle-result-v1"
CANDLE_INCREMENTAL_RUNTIME_V1_DOMAIN = "incremental-candle-runtime-v1"
_NANOS_PER_SECOND = 1_000_000_000
_DURATION_UNITS = {
    "ns": 1,
    "us": 1_000,
    "ms": 1_000_000,
    "s": _NANOS_PER_SECOND,
    "m": 60 * _NANOS_PER_SECOND,
    "h": 3_600 * _NANOS_PER_SECOND,
    "d": 86_400 * _NANOS_PER_SECOND,
}
_DURATION_RE = re.compile(r"^([0-9]+)(ns|us|ms|s|m|h|d)$")
_CLOCK_RE = re.compile(r"^([0-9]+):([0-5][0-9]):([0-5][0-9])(?:\.([0-9]{1,9}))?$")
_POSITIVE_DECIMAL_RE = re.compile(r"^([1-9][0-9]*(?:\.[0-9]+)?|0\.[0-9]*[1-9][0-9]*)$")


class CandleComputationError(ValueError):
    """Base class for expected historical candle runtime failures."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None):
        super().__init__(message)
        self.context = context or {}


class CandleInputError(CandleComputationError):
    """Raised when supplied canonical trades cannot be consumed as bounded input."""


class CandleCoverageError(CandleComputationError):
    """Raised when caller-supplied source support does not cover required buckets."""


class CandleFinalizationError(CandleComputationError):
    """Raised when caller-supplied evidence cannot close every required bucket."""


class CandleOrderingError(CandleComputationError):
    """Raised when source ordering evidence does not satisfy CandleDefinition v1."""


class CandleProvenanceError(CandleComputationError):
    """Raised when reproducibility evidence is missing or inconsistent."""


class CandleRuntimeState(str, Enum):
    """Runtime envelope state for mutable PARTIAL and immutable CLOSED candles."""

    PARTIAL = "PARTIAL"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class _ExactDecimal:
    coefficient: int
    scale: int

    @classmethod
    def parse(cls, value: str, field: str) -> "_ExactDecimal":
        if not isinstance(value, str) or not _POSITIVE_DECIMAL_RE.fullmatch(value):
            raise CandleInputError(
                f"{field} must be a positive exact decimal string",
                context={"field": field},
            )
        integer, dot, fraction = value.partition(".")
        digits = integer + (fraction if dot else "")
        coefficient = int(digits)
        scale = len(fraction) if dot else 0
        if coefficient <= 0:
            raise CandleInputError(f"{field} must be strictly positive", context={"field": field})
        return cls(coefficient, scale)

    def __lt__(self, other: "_ExactDecimal") -> bool:
        return self.coefficient * 10**other.scale < other.coefficient * 10**self.scale

    def __gt__(self, other: "_ExactDecimal") -> bool:
        return other < self

    def __add__(self, other: "_ExactDecimal") -> "_ExactDecimal":
        scale = max(self.scale, other.scale)
        left = self.coefficient * 10 ** (scale - self.scale)
        right = other.coefficient * 10 ** (scale - other.scale)
        return _ExactDecimal(left + right, scale)

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


@dataclass(frozen=True, slots=True)
class CandleDefinitionV1:
    """Frozen CandleDefinition v1 semantic payload for fixed UTC durations."""

    duration_ns: int | str

    def __post_init__(self) -> None:
        duration = parse_duration_ns(self.duration_ns)
        object.__setattr__(self, "duration_ns", duration)

    @classmethod
    def from_duration(cls, value: int | str) -> "CandleDefinitionV1":
        return cls(value)

    @property
    def canonical_payload(self) -> dict[str, Any]:
        return {
            "aggregation": {
                "close": "last_by_source_order",
                "high": "max_exact_decimal_price",
                "low": "min_exact_decimal_price",
                "open": "first_by_source_order",
                "trade_count": "count_source_trades",
                "volume": "sum_exact_decimal_size",
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
            "closure": {
                "closed_state": "CLOSED",
                "evidence": "source_finalization_required",
                "partial_state": "PARTIAL",
            },
            "definition_version": CANDLE_DEFINITION_V1_VERSION,
            "duration_ns": str(self.duration_ns),
            "empty_bucket_policy": "omit",
            "late_event_policy": "revisioned_rebuild",
            "numerical_semantics": "exact_decimal_no_rounding_v1",
            "output_record_schema": CANDLE_V1_RECORD_SCHEMA,
            "representation_kind": "candle",
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
        return f"candle-definition-v1:sha256:{digest}"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "definition_identity": self.definition_identity,
            "canonical_payload": self.canonical_payload,
        }


@dataclass(frozen=True, slots=True)
class CandleRecord:
    bucket_start: str
    bucket_end: str
    open: str
    high: str
    low: str
    close: str
    volume: str
    trade_count: str

    def stable_dict(self) -> dict[str, str]:
        return {
            "bucket_start": self.bucket_start,
            "bucket_end": self.bucket_end,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "trade_count": self.trade_count,
        }


@dataclass(frozen=True, slots=True)
class IncrementalCandleUpdate:
    """One incremental/live candle observation envelope.

    ``record`` uses the same canonical OHLCV shape as ``CandleRecord`` so that
    CLOSED output can be compared directly with the historical D03 runtime. The
    runtime ``state`` remains outside the row, preserving the v1 rule that
    materialized ``candle-v1`` rows are CLOSED records only.
    """

    state: CandleRuntimeState
    record: CandleRecord
    definition_identity: str
    causal_floor: Instant
    observed_available_at: Instant | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, CandleRuntimeState):
            try:
                object.__setattr__(self, "state", CandleRuntimeState(self.state))
            except ValueError as exc:
                raise CandleInputError("unknown candle runtime state") from exc
        if not isinstance(self.record, CandleRecord):
            raise CandleInputError("record must be a CandleRecord")
        if not isinstance(self.definition_identity, str) or not self.definition_identity.strip():
            raise CandleInputError("definition_identity must be a non-empty string")
        if not isinstance(self.causal_floor, Instant):
            raise CandleInputError("causal_floor must be an Instant")
        if self.observed_available_at is not None:
            object.__setattr__(self, "observed_available_at", Instant.parse(self.observed_available_at))
            if self.observed_available_at < self.causal_floor:
                raise CandleFinalizationError(
                    "observed availability cannot precede the candle causal floor",
                    context={
                        "observed_available_at": self.observed_available_at.isoformat(),
                        "causal_floor": self.causal_floor.isoformat(),
                    },
                )

    @property
    def bucket_start(self) -> Instant:
        return Instant.parse(self.record.bucket_start)

    @property
    def bucket_end(self) -> Instant:
        return Instant.parse(self.record.bucket_end)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "record": self.record.stable_dict(),
            "definition_identity": self.definition_identity,
            "causal_floor": self.causal_floor.isoformat(),
            "observed_available_at": (
                self.observed_available_at.isoformat() if self.observed_available_at else None
            ),
        }


@dataclass(frozen=True, slots=True)
class HistoricalCandleSourceEvidence:
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
            raise CandleProvenanceError("dataset_identity must be DatasetIdentity")
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
class HistoricalCandleCoverage:
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
class HistoricalCandleResult:
    records: tuple[CandleRecord, ...]
    requested_interval: CoverageInterval
    required_bucket_support: tuple[CoverageInterval, ...]
    coverage: HistoricalCandleCoverage
    definition: CandleDefinitionV1
    definition_identity: str
    source_evidence: HistoricalCandleSourceEvidence
    implementation_identity: str
    result_identity: str
    state: str = "CLOSED"
    output_record_schema: str = CANDLE_V1_RECORD_SCHEMA
    returned_record_bounds: RecordTimeBounds | None = None

    @property
    def row_count(self) -> int:
        return len(self.records)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "records": [record.stable_dict() for record in self.records],
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
class _BucketAccumulator:
    start_ns: int
    duration_ns: int
    open: _ExactDecimal | None = None
    high: _ExactDecimal | None = None
    low: _ExactDecimal | None = None
    close: _ExactDecimal | None = None
    volume: _ExactDecimal | None = None
    count: int = 0

    def consume(self, price: _ExactDecimal, size: _ExactDecimal) -> None:
        if self.count == 0:
            self.open = price
            self.high = price
            self.low = price
            self.volume = size
        else:
            if self.high is None or self.low is None or self.volume is None:
                raise AssertionError("non-empty bucket is missing aggregation state")
            if price > self.high:
                self.high = price
            if price < self.low:
                self.low = price
            self.volume = self.volume + size
        self.close = price
        self.count += 1

    def record(self) -> CandleRecord:
        if not all((self.open, self.high, self.low, self.close, self.volume)) or self.count < 1:
            raise AssertionError("empty bucket cannot be serialized as candle-v1")
        return CandleRecord(
            bucket_start=Instant(self.start_ns).isoformat(),
            bucket_end=Instant(self.start_ns + self.duration_ns).isoformat(),
            open=self.open.canonical(),  # type: ignore[union-attr]
            high=self.high.canonical(),  # type: ignore[union-attr]
            low=self.low.canonical(),  # type: ignore[union-attr]
            close=self.close.canonical(),  # type: ignore[union-attr]
            volume=self.volume.canonical(),  # type: ignore[union-attr]
            trade_count=str(self.count),
        )


class IncrementalCandleBuilder:
    """Mutable PARTIAL / explicit-watermark CLOSED candle runtime.

    The builder consumes already ordered canonical ``TradeRecord`` values and
    never owns live acquisition, cursoring, source repair, catalog access or
    finalization policy. A caller must provide explicit source finalization via
    :meth:`close_through`; wall-clock passage alone never seals a bucket.
    """

    def __init__(self, definition: CandleDefinitionV1):
        if not isinstance(definition, CandleDefinitionV1):
            raise CandleInputError("definition must be CandleDefinitionV1")
        self.definition = definition
        self._buckets: dict[int, _BucketAccumulator] = {}
        self._closed_bucket_starts: set[int] = set()
        self._previous_exchange_ns: int | None = None
        self._finalized_until_ns: int | None = None

    def consume(self, record: TradeRecord) -> IncrementalCandleUpdate:
        """Consume one observed trade and return the updated PARTIAL envelope."""

        if not isinstance(record, TradeRecord):
            raise CandleInputError("incremental candles require TradeRecord input")
        exchange_ts = Instant.parse(record.exchange_ts)
        if self._previous_exchange_ns is not None and exchange_ts.epoch_ns < self._previous_exchange_ns:
            raise CandleOrderingError("supplied trades are not in deterministic source order")
        if self._finalized_until_ns is not None and exchange_ts.epoch_ns < self._finalized_until_ns:
            raise CandleFinalizationError(
                "late trade falls inside already finalized candle support",
                context={
                    "exchange_ts": exchange_ts.isoformat(),
                    "finalized_until": Instant(self._finalized_until_ns).isoformat(),
                },
            )
        bucket_start = _bucket_start_ns(exchange_ts.epoch_ns, self.definition.duration_ns)
        if bucket_start in self._closed_bucket_starts:
            raise CandleFinalizationError(
                "trade falls inside an already CLOSED candle bucket",
                context={"bucket_start": Instant(bucket_start).isoformat()},
            )
        self._previous_exchange_ns = exchange_ts.epoch_ns
        accumulator = self._buckets.setdefault(
            bucket_start,
            _BucketAccumulator(bucket_start, self.definition.duration_ns),
        )
        accumulator.consume(
            _ExactDecimal.parse(record.price, "price"),
            _ExactDecimal.parse(record.size, "size"),
        )
        return self._update_for(bucket_start, CandleRuntimeState.PARTIAL)

    def close_through(
        self,
        finalized_until: Instant | str,
        *,
        observed_available_at: Instant | str | None = None,
    ) -> tuple[IncrementalCandleUpdate, ...]:
        """Seal every non-empty bucket whose full support is finalized.

        ``finalized_until`` is caller-supplied source finalization/watermark
        evidence. It must advance monotonically. Empty finalized buckets remain
        omitted, matching CandleDefinition v1.
        """

        watermark = Instant.parse(finalized_until)
        if self._finalized_until_ns is not None and watermark.epoch_ns < self._finalized_until_ns:
            raise CandleFinalizationError("source finalization watermark cannot move backward")
        observed = Instant.parse(observed_available_at) if observed_available_at is not None else None
        closable = tuple(
            bucket_start
            for bucket_start in sorted(self._buckets)
            if bucket_start not in self._closed_bucket_starts
            and bucket_start + self.definition.duration_ns <= watermark.epoch_ns
        )
        if observed is not None:
            for bucket_start in closable:
                bucket_end_ns = bucket_start + self.definition.duration_ns
                if observed.epoch_ns < bucket_end_ns:
                    raise CandleFinalizationError(
                        "observed availability cannot precede the candle causal floor",
                        context={
                            "observed_available_at": observed.isoformat(),
                            "causal_floor": Instant(bucket_end_ns).isoformat(),
                        },
                    )
        self._finalized_until_ns = watermark.epoch_ns
        closed: list[IncrementalCandleUpdate] = []
        for bucket_start in closable:
            self._closed_bucket_starts.add(bucket_start)
            closed.append(self._update_for(bucket_start, CandleRuntimeState.CLOSED, observed))
        return tuple(closed)

    def _update_for(
        self,
        bucket_start: int,
        state: CandleRuntimeState,
        observed_available_at: Instant | None = None,
    ) -> IncrementalCandleUpdate:
        accumulator = self._buckets[bucket_start]
        return IncrementalCandleUpdate(
            state=state,
            record=accumulator.record(),
            definition_identity=self.definition.definition_identity,
            causal_floor=Instant(bucket_start + self.definition.duration_ns),
            observed_available_at=observed_available_at,
        )


def parse_duration_ns(value: int | str) -> int:
    """Return the canonical positive integer nanosecond duration."""

    if isinstance(value, bool):
        raise InvalidRequest("duration_ns must be a positive integer duration")
    if isinstance(value, int):
        if value < 1:
            raise InvalidRequest("duration_ns must be positive")
        return value
    if not isinstance(value, str) or not value:
        raise InvalidRequest("duration must be a non-empty string or positive integer")
    if value.isdigit():
        duration = int(value)
        if duration < 1:
            raise InvalidRequest("duration_ns must be positive")
        return duration
    unit_match = _DURATION_RE.fullmatch(value)
    if unit_match:
        amount = int(unit_match.group(1))
        if amount < 1:
            raise InvalidRequest("duration must be positive")
        return amount * _DURATION_UNITS[unit_match.group(2)]
    clock_match = _CLOCK_RE.fullmatch(value)
    if clock_match:
        hours = int(clock_match.group(1))
        minutes = int(clock_match.group(2))
        seconds = int(clock_match.group(3))
        fraction = (clock_match.group(4) or "").ljust(9, "0")
        duration = (
            ((hours * 60 + minutes) * 60 + seconds) * _NANOS_PER_SECOND
            + int(fraction or "0")
        )
        if duration < 1:
            raise InvalidRequest("duration must be positive")
        return duration
    raise InvalidRequest(f"unsupported CandleDefinition v1 duration spelling: {value!r}")


def required_bucket_support(
    definition: CandleDefinitionV1,
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


def aggregate_historical_candles(
    trades: Iterable[TradeRecord],
    definition: CandleDefinitionV1,
    query_start: Instant | str,
    query_end: Instant | str,
) -> tuple[CandleRecord, ...]:
    """Pure CLOSED candle aggregation over already ordered canonical trades."""

    support = required_bucket_support(definition, query_start, query_end)
    rows = tuple(trades)
    if not support:
        if rows:
            raise CandleInputError("zero-length candle query must receive no bounded trades")
        return ()
    support_start = support[0].start
    support_end = support[0].end
    buckets: dict[int, _BucketAccumulator] = {}
    previous_exchange_ns: int | None = None
    for record in rows:
        if not isinstance(record, TradeRecord):
            raise CandleInputError("historical candles require TradeRecord input")
        exchange_ts = Instant.parse(record.exchange_ts)
        if exchange_ts < support_start or exchange_ts >= support_end:
            raise CandleInputError(
                "trade record falls outside required bucket support",
                context={
                    "exchange_ts": exchange_ts.isoformat(),
                    "required_bucket_support": [item.stable_dict() for item in support],
                },
            )
        if previous_exchange_ns is not None and exchange_ts.epoch_ns < previous_exchange_ns:
            raise CandleOrderingError("supplied trades are not in deterministic source order")
        previous_exchange_ns = exchange_ts.epoch_ns
        bucket_start = _bucket_start_ns(exchange_ts.epoch_ns, definition.duration_ns)
        accumulator = buckets.setdefault(
            bucket_start,
            _BucketAccumulator(bucket_start, definition.duration_ns),
        )
        accumulator.consume(
            _ExactDecimal.parse(record.price, "price"),
            _ExactDecimal.parse(record.size, "size"),
        )
    return tuple(buckets[start_ns].record() for start_ns in sorted(buckets))


def closed_candle_records(updates: Iterable[IncrementalCandleUpdate]) -> tuple[CandleRecord, ...]:
    """Return only CLOSED records from incremental/live envelopes."""

    return tuple(update.record for update in updates if update.state is CandleRuntimeState.CLOSED)


def build_historical_candle_result(
    trades: Iterable[TradeRecord],
    definition: CandleDefinitionV1,
    query_start: Instant | str,
    query_end: Instant | str,
    *,
    source_evidence: HistoricalCandleSourceEvidence,
    implementation_identity: str,
) -> HistoricalCandleResult:
    """Build a typed reproducible CLOSED candle result from supplied evidence."""

    requested_interval = CoverageInterval(Instant.parse(query_start), Instant.parse(query_end))
    if requested_interval.start > requested_interval.end:
        raise InvalidRequest("query start must not be after end")
    if not isinstance(implementation_identity, str) or not implementation_identity.strip():
        raise CandleProvenanceError("implementation_identity must be a non-empty string")
    _validate_source_evidence(source_evidence)
    support = required_bucket_support(definition, requested_interval.start, requested_interval.end)
    if support:
        _assert_fully_covered(support, source_evidence.eligible_source_coverage, CandleCoverageError)
        _assert_fully_covered(support, source_evidence.finalized_source_intervals, CandleFinalizationError)
        if not source_evidence.finalization_evidence:
            raise CandleFinalizationError("source finalization evidence is required for CLOSED candles")
        _assert_reproducible_source_path(source_evidence)
        if (
            source_evidence.observed_available_at is not None
            and source_evidence.observed_available_at < support[-1].end
        ):
            raise CandleFinalizationError(
                "observed source availability cannot precede the required support end",
                context={
                    "observed_available_at": source_evidence.observed_available_at.isoformat(),
                    "required_support_end": support[-1].end.isoformat(),
                },
            )
    records = aggregate_historical_candles(trades, definition, requested_interval.start, requested_interval.end)
    coverage = _project_complete_coverage(requested_interval, support)
    returned_bounds = None
    if records:
        returned_bounds = RecordTimeBounds(
            first=Instant.parse(records[0].bucket_start),
            last=Instant.parse(records[-1].bucket_start),
        )
    identity = _result_identity(
        records=records,
        requested_interval=requested_interval,
        required_support=support,
        coverage=coverage,
        definition=definition,
        source_evidence=source_evidence,
        implementation_identity=implementation_identity,
        returned_bounds=returned_bounds,
    )
    return HistoricalCandleResult(
        records=records,
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


def _bucket_start_ns(epoch_ns: int, duration_ns: int) -> int:
    return (epoch_ns // duration_ns) * duration_ns


def _coverage_tuple(values: Iterable[CoverageInterval]) -> tuple[CoverageInterval, ...]:
    result = tuple(values)
    if not all(isinstance(item, CoverageInterval) for item in result):
        raise CandleProvenanceError("coverage evidence must contain CoverageInterval values")
    return result


def _string_tuple(values: Iterable[str], field: str) -> tuple[str, ...]:
    result = tuple(values)
    if not all(isinstance(item, str) and item.strip() for item in result):
        raise CandleProvenanceError(f"{field} must contain non-empty strings")
    return result


def _validate_source_evidence(evidence: HistoricalCandleSourceEvidence) -> None:
    identity = evidence.dataset_identity
    if identity.layer != "canonical" or identity.dataset_kind != "trades":
        raise CandleProvenanceError("CandleDefinition v1 requires canonical trades source evidence")
    if evidence.record_schema_id != "trade-v1" or evidence.source_record_schema != "trade-v1":
        raise CandleProvenanceError("CandleDefinition v1 requires trade-v1 source evidence")
    if evidence.source_representation != "trades@1":
        raise CandleProvenanceError("CandleDefinition v1 requires trades@1 source evidence")
    if evidence.source_ordering_policy != TRADES_CANONICAL_TOTAL_ORDER_V1:
        raise CandleOrderingError(
            "source ordering evidence does not satisfy CandleDefinition v1",
            context={
                "required_ordering_policy": TRADES_CANONICAL_TOTAL_ORDER_V1,
                "source_ordering_policy": evidence.source_ordering_policy,
            },
        )
    if evidence.schema_version < 1:
        raise CandleProvenanceError("schema_version must be positive")
    if not isinstance(evidence.schema_hash, str) or not evidence.schema_hash.strip():
        raise CandleProvenanceError("schema_hash is required")


def _assert_reproducible_source_path(evidence: HistoricalCandleSourceEvidence) -> None:
    partition_path = (
        bool(evidence.natural_partitions)
        and bool(evidence.manifest_hashes)
        and bool(evidence.content_hashes)
    )
    snapshot_path = bool(evidence.source_result_identity)
    if not (partition_path or snapshot_path):
        raise CandleProvenanceError(
            "CLOSED candle result requires immutable source snapshot or partition/content evidence"
        )


def _assert_fully_covered(
    required: tuple[CoverageInterval, ...],
    available: tuple[CoverageInterval, ...],
    error_type: type[CandleComputationError],
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
) -> HistoricalCandleCoverage:
    if requested_interval.start == requested_interval.end:
        return HistoricalCandleCoverage(covered_intervals=(), gaps=(), complete=True)
    projected = tuple(
        clipped
        for interval in support
        if (clipped := _intersect(interval, requested_interval)) is not None
    )
    return HistoricalCandleCoverage(covered_intervals=_merge_intervals(projected), gaps=(), complete=True)


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
    records: tuple[CandleRecord, ...],
    requested_interval: CoverageInterval,
    required_support: tuple[CoverageInterval, ...],
    coverage: HistoricalCandleCoverage,
    definition: CandleDefinitionV1,
    source_evidence: HistoricalCandleSourceEvidence,
    implementation_identity: str,
    returned_bounds: RecordTimeBounds | None,
) -> str:
    payload = {
        "domain": CANDLE_RESULT_FINGERPRINT_V1_DOMAIN,
        "records": [record.stable_dict() for record in records],
        "requested_interval": requested_interval.stable_dict(),
        "required_bucket_support": [item.stable_dict() for item in required_support],
        "coverage": coverage.stable_dict(),
        "definition_identity": definition.definition_identity,
        "source_evidence": source_evidence.stable_dict(),
        "implementation_identity": implementation_identity,
        "state": "CLOSED",
        "output_record_schema": CANDLE_V1_RECORD_SCHEMA,
        "returned_record_bounds": returned_bounds.stable_dict() if returned_bounds else None,
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return f"{CANDLE_RESULT_FINGERPRINT_V1_DOMAIN}:sha256:{digest}"


__all__ = [
    "CANDLE_DEFINITION_V1_VERSION",
    "CANDLE_INCREMENTAL_RUNTIME_V1_DOMAIN",
    "CANDLE_RESULT_FINGERPRINT_V1_DOMAIN",
    "CANDLE_V1_RECORD_SCHEMA",
    "CandleComputationError",
    "CandleCoverageError",
    "CandleDefinitionV1",
    "CandleFinalizationError",
    "CandleInputError",
    "CandleOrderingError",
    "CandleProvenanceError",
    "CandleRecord",
    "CandleRuntimeState",
    "HistoricalCandleCoverage",
    "HistoricalCandleResult",
    "HistoricalCandleSourceEvidence",
    "IncrementalCandleBuilder",
    "IncrementalCandleUpdate",
    "aggregate_historical_candles",
    "build_historical_candle_result",
    "closed_candle_records",
    "parse_duration_ns",
    "required_bucket_support",
]
