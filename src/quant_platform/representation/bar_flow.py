"""ADR-0073 bar-flow v1: CLOSED OHLC bars with exact taker volumes.

The source-evidence and support rules are the candle's. Its pure internal
accumulator is reused without changing candle code, payloads or identities.
Only the evidence-backed builder certifies CLOSED output; the aggregation
kernel alone makes no finalization claim. Neither opens storage or a gateway.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import re
from typing import Any, Iterable

from quant_platform.canonical import canonical_bytes
from ..data.models import CoverageInterval, Instant, InvalidRequest, RecordTimeBounds, TradeRecord
from . import candles as _candle


BAR_FLOW_V1_RECORD_SCHEMA = "bar-flow-v1"
BAR_FLOW_RESULT_FINGERPRINT_V1_DOMAIN = "historical-bar-flow-result-v1"


class BarFlowInputError(_candle.CandleInputError):
    """Invalid bar-flow records, definitions or bounded trades."""


class BarFlowAggressorSideError(BarFlowInputError):
    """Missing, unknown or unsupported explicit aggression evidence."""


class BarFlowCoverageError(_candle.CandleCoverageError):
    """The complete selected bucket support is not covered."""


class BarFlowFinalizationError(_candle.CandleFinalizationError):
    """The source cannot establish CLOSED output and its causal floor."""


class BarFlowOrderingError(_candle.CandleOrderingError):
    """Source ordering does not satisfy the declared canonical policy."""


class BarFlowProvenanceError(_candle.CandleProvenanceError):
    """Immutable source provenance is missing or conflicts with the trades."""


@dataclass(frozen=True, slots=True)
class HistoricalBarFlowSourceEvidence(_candle.HistoricalCandleSourceEvidence):
    """The candle's source evidence, with nullable observed finalization time."""

    observed_finalized_at: Instant | str | None = None

    def __post_init__(self) -> None:
        _candle.HistoricalCandleSourceEvidence.__post_init__(self)
        if self.observed_finalized_at is not None:
            object.__setattr__(self, "observed_finalized_at", Instant.parse(self.observed_finalized_at))
        if any(part.dataset_identity != self.dataset_identity for part in self.natural_partitions):
            raise BarFlowProvenanceError("natural partitions conflict with source dataset identity")

    def stable_dict(self) -> dict[str, Any]:
        return {
            **_candle.HistoricalCandleSourceEvidence.stable_dict(self),
            "observed_finalized_at": self.observed_finalized_at.isoformat() if self.observed_finalized_at else None,
        }


@dataclass(frozen=True, slots=True)
class BarFlowDefinitionV1:
    duration_ns: int | str

    def __post_init__(self) -> None:
        duration = _candle.parse_duration_ns(self.duration_ns)
        if not 1_000_000_000 <= duration <= 86_400_000_000_000:
            raise InvalidRequest("bar-flow duration must be between 1 second and 1 day inclusive")
        object.__setattr__(self, "duration_ns", duration)

    @classmethod
    def from_duration(cls, value: int | str) -> BarFlowDefinitionV1:
        return cls(value)

    @property
    def canonical_payload(self) -> dict[str, Any]:
        payload = _candle.CandleDefinitionV1(self.duration_ns).canonical_payload
        payload["representation_kind"] = "bar_flow"
        payload["output_record_schema"] = BAR_FLOW_V1_RECORD_SCHEMA
        payload["aggregation"].update({
            "taker_buy_volume": "sum_exact_decimal_size_where_aggressor_side_buy",
            "taker_sell_volume": "sum_exact_decimal_size_where_aggressor_side_sell",
        })
        payload["aggressor_side"] = {
            "accepted": ["buy", "sell"], "missing": "refuse_bucket", "unknown": "refuse_bucket",
        }
        return payload

    @property
    def canonical_utf8_serialization(self) -> str:
        return canonical_bytes(self.canonical_payload, profile="sorted-compact-utf8-v1", allow_nan=False).decode("utf-8")

    @property
    def definition_identity(self) -> str:
        digest = hashlib.sha256(self.canonical_utf8_serialization.encode("utf-8")).hexdigest()
        return f"bar-flow-definition-v1:sha256:{digest}"

    def stable_dict(self) -> dict[str, Any]:
        return {"definition_identity": self.definition_identity, "canonical_payload": self.canonical_payload}


def _decimal(value: str, field: str, *, allow_zero: bool = False) -> _candle._ExactDecimal:
    if allow_zero and isinstance(value, str) and re.fullmatch(r"0(?:\.0+)?", value):
        return _candle._ExactDecimal(0, 0)
    try:
        return _candle._ExactDecimal.parse(value, field)
    except _candle.CandleInputError as exc:
        raise BarFlowInputError(str(exc), context=exc.context) from exc


@dataclass(frozen=True, slots=True)
class BarFlowRecord:
    bucket_start: str
    bucket_end: str
    open: str
    high: str
    low: str
    close: str
    volume: str
    trade_count: str
    taker_buy_volume: str
    taker_sell_volume: str

    def __post_init__(self) -> None:
        start, end = Instant.parse(self.bucket_start), Instant.parse(self.bucket_end)
        if start >= end:
            raise BarFlowInputError("bar-flow bucket must have positive half-open support")
        object.__setattr__(self, "bucket_start", start.isoformat())
        object.__setattr__(self, "bucket_end", end.isoformat())
        values = {}
        for field in ("open", "high", "low", "close", "volume", "taker_buy_volume", "taker_sell_volume"):
            value = _decimal(getattr(self, field), field, allow_zero=field.startswith("taker_"))
            values[field] = value
            object.__setattr__(self, field, value.canonical())
        if (values["taker_buy_volume"] + values["taker_sell_volume"]).canonical() != self.volume:
            raise BarFlowInputError("taker_buy_volume + taker_sell_volume must equal volume")
        if not isinstance(self.trade_count, str) or not re.fullmatch(r"[1-9][0-9]*", self.trade_count):
            raise BarFlowInputError("trade_count must be a positive integer string")
        if any(values[field] < values["low"] or values[field] > values["high"] for field in ("open", "close")):
            raise BarFlowInputError("OHLC prices must lie within low and high")

    @property
    def causal_floor(self) -> Instant:
        return Instant.parse(self.bucket_end)

    def stable_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class HistoricalBarFlowResult:
    records: tuple[BarFlowRecord, ...]
    requested_interval: CoverageInterval
    required_bucket_support: tuple[CoverageInterval, ...]
    coverage: _candle.HistoricalCandleCoverage
    definition: BarFlowDefinitionV1
    source_evidence: HistoricalBarFlowSourceEvidence
    implementation_identity: str
    returned_record_bounds: RecordTimeBounds | None

    @property
    def definition_identity(self) -> str:
        return self.definition.definition_identity

    @property
    def state(self) -> str:
        return "CLOSED"

    @property
    def output_record_schema(self) -> str:
        return BAR_FLOW_V1_RECORD_SCHEMA

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
            "returned_record_bounds": self.returned_record_bounds.stable_dict() if self.returned_record_bounds else None,
            "state": self.state,
            "output_record_schema": self.output_record_schema,
        }

    @property
    def result_identity(self) -> str:
        payload = {"domain": BAR_FLOW_RESULT_FINGERPRINT_V1_DOMAIN, **self.stable_dict()}
        digest = hashlib.sha256(canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=False)).hexdigest()
        return f"{BAR_FLOW_RESULT_FINGERPRINT_V1_DOMAIN}:sha256:{digest}"


def required_bar_flow_bucket_support(
    definition: BarFlowDefinitionV1, query_start: Instant | str, query_end: Instant | str,
) -> tuple[CoverageInterval, ...]:
    if not isinstance(definition, BarFlowDefinitionV1):
        raise BarFlowInputError("definition must be BarFlowDefinitionV1")
    return _candle.required_bucket_support(definition, query_start, query_end)


def aggregate_historical_bar_flow(
    trades: Iterable[TradeRecord], definition: BarFlowDefinitionV1,
    query_start: Instant | str, query_end: Instant | str,
) -> tuple[BarFlowRecord, ...]:
    """Pure one-pass kernel over already ordered trades; no finalization assertion."""
    support = required_bar_flow_bucket_support(definition, query_start, query_end)
    buckets: dict[int, _candle._BucketAccumulator] = {}
    volumes: dict[int, dict[str, _candle._ExactDecimal]] = {}
    previous_ns: int | None = None
    for record in trades:
        if not isinstance(record, TradeRecord):
            raise BarFlowInputError("bar-flow requires TradeRecord input")
        ts = Instant.parse(record.exchange_ts)
        if not support or ts < support[0].start or ts >= support[0].end:
            raise BarFlowInputError("trade falls outside required bar-flow bucket support")
        if previous_ns is not None and ts.epoch_ns < previous_ns:
            raise BarFlowOrderingError("supplied trades are not in deterministic source order")
        previous_ns = ts.epoch_ns
        side = record.aggressor_side
        if side not in ("buy", "sell"):
            raise BarFlowAggressorSideError("bar-flow requires explicit buy or sell aggressor_side")
        price, size = _decimal(record.price, "price"), _decimal(record.size, "size")
        start = (ts.epoch_ns // definition.duration_ns) * definition.duration_ns
        if start not in buckets:
            buckets[start] = _candle._BucketAccumulator(start, definition.duration_ns)
            volumes[start] = {"buy": _candle._ExactDecimal(0, 0), "sell": _candle._ExactDecimal(0, 0)}
        buckets[start].consume(price, size)
        volumes[start][side] = volumes[start][side] + size
    return tuple(
        BarFlowRecord(
            **buckets[start].record().stable_dict(),
            taker_buy_volume=volumes[start]["buy"].canonical(),
            taker_sell_volume=volumes[start]["sell"].canonical(),
        )
        for start in sorted(buckets)
    )


def build_historical_bar_flow_result(
    trades: Iterable[TradeRecord], definition: BarFlowDefinitionV1,
    query_start: Instant | str, query_end: Instant | str, *,
    source_evidence: HistoricalBarFlowSourceEvidence, implementation_identity: str,
) -> HistoricalBarFlowResult:
    """Establish complete, reproducible CLOSED bars with explicit source evidence."""
    if not isinstance(source_evidence, HistoricalBarFlowSourceEvidence):
        raise BarFlowProvenanceError("source_evidence must be HistoricalBarFlowSourceEvidence")
    if not isinstance(implementation_identity, str) or not implementation_identity.strip():
        raise BarFlowProvenanceError("implementation_identity must be a non-empty string")
    requested = CoverageInterval(Instant.parse(query_start), Instant.parse(query_end))
    support = required_bar_flow_bucket_support(definition, requested.start, requested.end)
    try:
        _candle._validate_source_evidence(source_evidence)
        if support:
            _candle._assert_fully_covered(support, source_evidence.eligible_source_coverage, BarFlowCoverageError)
            _candle._assert_fully_covered(support, source_evidence.finalized_source_intervals, BarFlowFinalizationError)
            if not source_evidence.finalization_evidence:
                raise BarFlowFinalizationError("source finalization evidence is required for CLOSED bar-flow")
            _candle._assert_reproducible_source_path(source_evidence)
            if source_evidence.observed_available_at is not None and source_evidence.observed_available_at < support[-1].end:
                raise BarFlowFinalizationError("observed availability precedes the required support end")
            if source_evidence.observed_finalized_at is not None and source_evidence.observed_finalized_at < support[-1].end:
                raise BarFlowFinalizationError("observed finalization precedes the required support end")
    except _candle.CandleOrderingError as exc:
        raise BarFlowOrderingError(str(exc), context=exc.context) from exc
    except _candle.CandleProvenanceError as exc:
        raise BarFlowProvenanceError(str(exc), context=exc.context) from exc

    def bound_trades() -> Iterable[TradeRecord]:
        identity = source_evidence.dataset_identity
        for record in trades:
            if not isinstance(record, TradeRecord):
                raise BarFlowInputError("bar-flow requires TradeRecord input")
            if record.venue != identity.venue or record.instrument != identity.instrument:
                raise BarFlowProvenanceError("trade conflicts with source dataset venue/instrument")
            yield record

    records = aggregate_historical_bar_flow(bound_trades(), definition, requested.start, requested.end)
    return HistoricalBarFlowResult(
        records=records, requested_interval=requested, required_bucket_support=support,
        coverage=_candle._project_complete_coverage(requested, support), definition=definition,
        source_evidence=source_evidence, implementation_identity=implementation_identity,
        returned_record_bounds=(
            RecordTimeBounds(Instant.parse(records[0].bucket_start), Instant.parse(records[-1].bucket_start))
            if records else None
        ),
    )


__all__ = [
    "BAR_FLOW_V1_RECORD_SCHEMA", "BAR_FLOW_RESULT_FINGERPRINT_V1_DOMAIN",
    "BarFlowDefinitionV1", "BarFlowRecord", "HistoricalBarFlowSourceEvidence", "HistoricalBarFlowResult",
    "BarFlowInputError", "BarFlowAggressorSideError", "BarFlowCoverageError", "BarFlowFinalizationError",
    "BarFlowOrderingError", "BarFlowProvenanceError", "required_bar_flow_bucket_support",
    "aggregate_historical_bar_flow", "build_historical_bar_flow_result",
]
