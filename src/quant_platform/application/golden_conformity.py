#!/usr/bin/env python3
"""Proof-only application support for observing bounded Golden Conformity scans.

This module observes an existing ``DataScan``.  It does not implement a
conformity gate, publication, certification, ordering, coverage, hashing, or
any other producer-side business rule.

Its fixture-bound helpers are application proof machinery, not a supported
installed-package dependency API (ADR-0056).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from numbers import Real
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from quant_platform.access.gateway import (
    DataScan,
    DataScanOpenMetadata,
    ScanState,
)
from quant_platform.access.models import DataSliceMetadata
from quant_platform.data.models import Instant, TradeRecord
from quant_platform.ordering import TRADES_CANONICAL_TOTAL_ORDER_V1
from quant_platform.representation.candles import (
    CandleDefinitionV1,
    HistoricalCandleResult,
    HistoricalCandleSourceEvidence,
    build_historical_candle_result,
)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_GOLDEN_FIXTURE = ROOT / "fixtures" / "conformity" / "golden-bybit-btcusdt-2024-01-15.json"
MemorySampler = Callable[[], int | float]
CANDLE_IMPLEMENTATION_IDENTITY = "quant_platform.representation.candles:d03-v1"


@dataclass(frozen=True, slots=True)
class GoldenCandleExpectation:
    """Frozen expectation for the additive D03 historical candle leg.

    Presence of a ``candle`` section in a Golden fixture is what activates
    the candle-aggregation leg of ``verify_vertical``; its absence leaves
    trade-only verification exactly as it was before this capability existed.
    """

    duration: str
    definition_identity: str
    candle_count: int
    first_candle: Mapping[str, str]
    last_candle: Mapping[str, str]
    result_identity: str

    def __post_init__(self) -> None:
        if not isinstance(self.duration, str) or not self.duration.strip():
            raise ValueError("Golden candle expectation duration must be a non-empty string")
        if not isinstance(self.definition_identity, str) or not self.definition_identity.startswith(
            "candle-definition-v1:sha256:"
        ):
            raise ValueError("Golden candle expectation definition_identity must be a candle-definition-v1 identity")
        if isinstance(self.candle_count, bool) or not isinstance(self.candle_count, int) or self.candle_count < 0:
            raise ValueError("Golden candle expectation candle_count must be a non-negative integer")
        object.__setattr__(self, "first_candle", dict(self.first_candle))
        object.__setattr__(self, "last_candle", dict(self.last_candle))
        if self.candle_count == 0 and (self.first_candle or self.last_candle):
            raise ValueError("Golden candle expectation with zero candles cannot declare first/last candle payloads")
        if self.candle_count > 0 and not (self.first_candle and self.last_candle):
            raise ValueError("Golden candle expectation with candles must declare first and last candle payloads")
        if not isinstance(self.result_identity, str) or not self.result_identity.startswith(
            "historical-candle-result-v1:sha256:"
        ):
            raise ValueError("Golden candle expectation result_identity must be a historical-candle-result-v1 identity")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "GoldenCandleExpectation":
        required = {
            "duration",
            "definition_identity",
            "candle_count",
            "first_candle",
            "last_candle",
            "result_identity",
        }
        if set(payload) != required:
            missing = sorted(required - set(payload))
            extra = sorted(set(payload) - required)
            raise ValueError(f"Golden candle expectation fields mismatch; missing={missing}, extra={extra}")
        return cls(
            duration=payload["duration"],
            definition_identity=payload["definition_identity"],
            candle_count=payload["candle_count"],
            first_candle=payload["first_candle"] or {},
            last_candle=payload["last_candle"] or {},
            result_identity=payload["result_identity"],
        )


@dataclass(frozen=True, slots=True)
class GoldenExpectation:
    venue: str
    instrument: str
    interval_start: str
    interval_end: str
    row_count: int
    buy: int
    sell: int
    first_exchange_ts: str
    last_exchange_ts: str
    candle: GoldenCandleExpectation | None = None

    def __post_init__(self) -> None:
        if not self.venue or not self.instrument:
            raise ValueError("Golden expectation identity must be non-empty")
        for name in ("row_count", "buy", "sell"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"Golden expectation {name} must be a non-negative integer")
        if self.buy + self.sell != self.row_count:
            raise ValueError("Golden expectation buy and sell counts must equal row_count")
        start = Instant.parse(self.interval_start)
        end = Instant.parse(self.interval_end)
        first = Instant.parse(self.first_exchange_ts)
        last = Instant.parse(self.last_exchange_ts)
        if not start < end:
            raise ValueError("Golden expectation interval must be non-empty")
        if not start <= first <= last < end:
            raise ValueError("Golden expectation observed bounds must lie in its interval")
        if self.candle is not None and not isinstance(self.candle, GoldenCandleExpectation):
            raise ValueError("Golden expectation candle must be a GoldenCandleExpectation")

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "GoldenExpectation":
        required = {
            "venue",
            "instrument",
            "interval",
            "row_count",
            "buy",
            "sell",
            "first_exchange_ts",
            "last_exchange_ts",
        }
        optional = {"candle"}
        present = set(payload)
        if not required.issubset(present) or not present.issubset(required | optional):
            missing = sorted(required - present)
            extra = sorted(present - required - optional)
            raise ValueError(f"Golden expectation fields mismatch; missing={missing}, extra={extra}")
        interval = payload["interval"]
        if not isinstance(interval, Mapping) or set(interval) != {"start", "end"}:
            raise ValueError("Golden expectation interval must contain only start and end")
        candle_payload = payload.get("candle")
        candle = None if candle_payload is None else GoldenCandleExpectation.from_mapping(candle_payload)
        return cls(
            venue=payload["venue"],
            instrument=payload["instrument"],
            interval_start=interval["start"],
            interval_end=interval["end"],
            row_count=payload["row_count"],
            buy=payload["buy"],
            sell=payload["sell"],
            first_exchange_ts=payload["first_exchange_ts"],
            last_exchange_ts=payload["last_exchange_ts"],
            candle=candle,
        )


def load_golden_expectation(path: str | Path = DEFAULT_GOLDEN_FIXTURE) -> GoldenExpectation:
    """Load the frozen data-only Golden expectation."""

    fixture_path = Path(path)
    with fixture_path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, Mapping):
        raise ValueError("Golden expectation fixture must contain a JSON object")
    return GoldenExpectation.from_mapping(payload)


@dataclass(frozen=True, slots=True)
class BoundednessTelemetry:
    baseline_sample: int | float
    peak_sample: int | float
    sample_count: int

    @property
    def peak_delta(self) -> int | float:
        return self.peak_sample - self.baseline_sample


class _TelemetryCollector:
    def __init__(self, sampler: MemorySampler | None) -> None:
        self._sampler = sampler
        self._baseline: int | float | None = None
        self._peak: int | float | None = None
        self._sample_count = 0

    def sample(self) -> None:
        if self._sampler is None:
            return
        value = self._sampler()
        if isinstance(value, bool) or not isinstance(value, Real):
            raise TypeError("memory sampler must return an int or float")
        numeric = int(value) if isinstance(value, int) else float(value)
        if self._baseline is None:
            self._baseline = numeric
            self._peak = numeric
        else:
            assert self._peak is not None
            self._peak = max(self._peak, numeric)
        self._sample_count += 1

    def result(self) -> BoundednessTelemetry | None:
        if self._baseline is None or self._peak is None:
            return None
        return BoundednessTelemetry(self._baseline, self._peak, self._sample_count)


@dataclass(frozen=True, slots=True)
class ScanObservation:
    initial_state: ScanState
    initial_completed_metadata_present: bool
    state_after_first_batch: ScanState | None
    final_state: ScanState
    row_count: int
    buy: int
    sell: int
    other_aggressor_side: int
    first_exchange_ts: Instant | None
    last_exchange_ts: Instant | None
    observed_venue: str | None
    observed_instrument: str | None
    batch_count: int
    max_batch_size: int
    completed_metadata: DataSliceMetadata | None
    boundedness: BoundednessTelemetry | None


def observe_scan(
    scan: DataScan,
    *,
    memory_sampler: MemorySampler | None = None,
) -> ScanObservation:
    """Consume an existing ``DataScan`` incrementally and summarize it.

    The observer keeps counters and boundary values only.  It never retains
    records, sorts, calculates ordering keys, or reconstructs any acceptance
    or publication state.
    """

    initial_state = scan.state
    if initial_state is not ScanState.OPEN:
        raise ValueError("observe_scan requires an initially OPEN DataScan")
    initial_metadata_present = scan.completed_metadata is not None
    first_batch_state: ScanState | None = None
    row_count = 0
    buy = 0
    sell = 0
    other_side = 0
    first_exchange_ts: Instant | None = None
    last_exchange_ts: Instant | None = None
    observed_venue: str | None = None
    observed_instrument: str | None = None
    batch_count = 0
    max_batch_size = 0
    telemetry = _TelemetryCollector(memory_sampler)
    telemetry.sample()

    while True:
        try:
            batch = next(scan)
        except StopIteration:
            break
        if first_batch_state is None:
            first_batch_state = scan.state
        batch_count += 1
        batch_size = 0
        for record in batch:
            batch_size += 1
            row_count += 1
            if record.aggressor_side == "buy":
                buy += 1
            elif record.aggressor_side == "sell":
                sell += 1
            else:
                other_side += 1
            if first_exchange_ts is None:
                first_exchange_ts = record.exchange_ts
                observed_venue = record.venue
                observed_instrument = record.instrument
            last_exchange_ts = record.exchange_ts
        max_batch_size = max(max_batch_size, batch_size)
        telemetry.sample()

    final_state = scan.state
    completed_metadata = scan.completed_metadata if final_state == ScanState.COMPLETED else None
    telemetry.sample()
    return ScanObservation(
        initial_state=initial_state,
        initial_completed_metadata_present=initial_metadata_present,
        state_after_first_batch=first_batch_state,
        final_state=final_state,
        row_count=row_count,
        buy=buy,
        sell=sell,
        other_aggressor_side=other_side,
        first_exchange_ts=first_exchange_ts,
        last_exchange_ts=last_exchange_ts,
        observed_venue=observed_venue,
        observed_instrument=observed_instrument,
        batch_count=batch_count,
        max_batch_size=max_batch_size,
        completed_metadata=completed_metadata,
        boundedness=telemetry.result(),
    )


def build_candle_source_evidence(open_metadata: DataScanOpenMetadata) -> HistoricalCandleSourceEvidence:
    """Derive D03 source evidence from evidence a completed scan already owns.

    Every field comes from ``DataScanOpenMetadata`` known at scan-open time
    (before any batch is read): the ``VALID_ONLY``/``strict`` scan that
    produced it already proves the eligible coverage is sealed and immutable,
    so it doubles as ``finalized_source_intervals`` here without a second
    catalog/coverage lookup.
    """

    finalization_evidence = tuple(open_metadata.manifest_hashes) + tuple(open_metadata.content_hashes)
    return HistoricalCandleSourceEvidence(
        dataset_identity=open_metadata.dataset_identity,
        record_schema_id=open_metadata.record_schema_id,
        schema_version=open_metadata.schema_version,
        schema_hash=open_metadata.schema_hash,
        source_ordering_policy=TRADES_CANONICAL_TOTAL_ORDER_V1,
        eligible_source_coverage=open_metadata.eligible_coverage,
        finalized_source_intervals=open_metadata.eligible_coverage,
        finalization_evidence=finalization_evidence,
        natural_partitions=open_metadata.natural_partitions,
        manifest_hashes=open_metadata.manifest_hashes,
        content_hashes=open_metadata.content_hashes,
        source_request_identity=open_metadata.request_identity,
        concrete_ordering_policy=open_metadata.ordering_policy,
    )


@dataclass(slots=True)
class _CandleScanBridge:
    """Streams one ``DataScan`` into ``TradeRecord`` for D03 while folding the
    same trade-level tallies ``observe_scan`` reports separately.

    A second ``DataGateway.scan`` call, or collecting batches into a list
    before handing them to D03, would each read or hold the whole trading day
    twice. This keeps exactly one traversal of the scan.
    """

    scan: DataScan
    state_after_first_batch: ScanState | None = None
    row_count: int = 0
    buy: int = 0
    sell: int = 0
    other_aggressor_side: int = 0
    first_exchange_ts: Instant | None = None
    last_exchange_ts: Instant | None = None
    observed_venue: str | None = None
    observed_instrument: str | None = None
    batch_count: int = 0
    max_batch_size: int = 0

    def stream(self) -> Iterator[TradeRecord]:
        while True:
            try:
                batch = next(self.scan)
            except StopIteration:
                break
            if self.state_after_first_batch is None:
                self.state_after_first_batch = self.scan.state
            self.batch_count += 1
            batch_size = 0
            for record in batch:
                batch_size += 1
                self.row_count += 1
                if record.aggressor_side == "buy":
                    self.buy += 1
                elif record.aggressor_side == "sell":
                    self.sell += 1
                else:
                    self.other_aggressor_side += 1
                if self.first_exchange_ts is None:
                    self.first_exchange_ts = record.exchange_ts
                    self.observed_venue = record.venue
                    self.observed_instrument = record.instrument
                self.last_exchange_ts = record.exchange_ts
                yield record
            self.max_batch_size = max(self.max_batch_size, batch_size)


def observe_scan_with_candles(
    scan: DataScan,
    candle_expectation: GoldenCandleExpectation,
) -> tuple[ScanObservation, HistoricalCandleResult]:
    """Consume an OPEN ``DataScan`` once, producing both a ``ScanObservation``
    and the D03 historical candle result built from the same trades.

    This is the Application-owned adapter required to feed DataGateway scan
    batches into ``build_historical_candle_result`` without a second
    data-access path or a redundant full-day copy: the only full-day
    materialization is the one ``aggregate_historical_candles`` already
    performs as part of its own accepted CLOSED-candle semantics.
    """

    if scan.state is not ScanState.OPEN:
        raise ValueError("observe_scan_with_candles requires an initially OPEN DataScan")
    initial_state = scan.state
    initial_completed_metadata_present = scan.completed_metadata is not None

    bridge = _CandleScanBridge(scan)
    source_evidence = build_candle_source_evidence(scan.open_metadata)
    definition = CandleDefinitionV1.from_duration(candle_expectation.duration)
    interval = scan.open_metadata.requested_interval
    result = build_historical_candle_result(
        bridge.stream(),
        definition,
        interval.start,
        interval.end,
        source_evidence=source_evidence,
        implementation_identity=CANDLE_IMPLEMENTATION_IDENTITY,
    )

    final_state = scan.state
    completed_metadata = scan.completed_metadata if final_state == ScanState.COMPLETED else None
    observation = ScanObservation(
        initial_state=initial_state,
        initial_completed_metadata_present=initial_completed_metadata_present,
        state_after_first_batch=bridge.state_after_first_batch,
        final_state=final_state,
        row_count=bridge.row_count,
        buy=bridge.buy,
        sell=bridge.sell,
        other_aggressor_side=bridge.other_aggressor_side,
        first_exchange_ts=bridge.first_exchange_ts,
        last_exchange_ts=bridge.last_exchange_ts,
        observed_venue=bridge.observed_venue,
        observed_instrument=bridge.observed_instrument,
        batch_count=bridge.batch_count,
        max_batch_size=bridge.max_batch_size,
        completed_metadata=completed_metadata,
        boundedness=None,
    )
    return observation, result


def candle_field_mismatches(
    expectation: GoldenCandleExpectation,
    result: HistoricalCandleResult,
) -> tuple[str, ...]:
    """Return field-level discrepancies between a D03 result and its Golden."""

    mismatches: list[str] = []
    if result.definition_identity != expectation.definition_identity:
        mismatches.append(
            f"candle_definition_identity: expected {expectation.definition_identity!r}, "
            f"got {result.definition_identity!r}"
        )
    if result.row_count != expectation.candle_count:
        mismatches.append(f"candle_count: expected {expectation.candle_count}, got {result.row_count}")
    first = result.records[0].stable_dict() if result.records else None
    last = result.records[-1].stable_dict() if result.records else None
    expected_first = expectation.first_candle or None
    expected_last = expectation.last_candle or None
    if first != expected_first:
        mismatches.append(f"first_candle: expected {expected_first!r}, got {first!r}")
    if last != expected_last:
        mismatches.append(f"last_candle: expected {expected_last!r}, got {last!r}")
    if result.result_identity != expectation.result_identity:
        mismatches.append(
            f"candle_result_identity: expected {expectation.result_identity!r}, got {result.result_identity!r}"
        )
    return tuple(mismatches)


def golden_field_mismatches(
    expectation: GoldenExpectation,
    observation: ScanObservation,
) -> tuple[str, ...]:
    """Return field-level discrepancies without evaluating a conformity gate."""

    mismatches: list[str] = []
    if observation.final_state != ScanState.COMPLETED:
        mismatches.append(f"final_state: expected COMPLETED, got {observation.final_state.name}")
    if observation.initial_state in {ScanState.OPEN, ScanState.READING} and observation.initial_completed_metadata_present:
        mismatches.append("completed metadata was present before exhaustion")
    if observation.final_state == ScanState.COMPLETED and observation.completed_metadata is None:
        mismatches.append("completed metadata is absent after exhaustion")
    checks = (
        ("venue", observation.observed_venue, expectation.venue),
        ("instrument", observation.observed_instrument, expectation.instrument),
        ("row_count", observation.row_count, expectation.row_count),
        ("buy", observation.buy, expectation.buy),
        ("sell", observation.sell, expectation.sell),
        ("other_aggressor_side", observation.other_aggressor_side, 0),
        (
            "first_exchange_ts",
            None if observation.first_exchange_ts is None else observation.first_exchange_ts.isoformat(),
            expectation.first_exchange_ts,
        ),
        (
            "last_exchange_ts",
            None if observation.last_exchange_ts is None else observation.last_exchange_ts.isoformat(),
            expectation.last_exchange_ts,
        ),
    )
    for name, actual, expected in checks:
        if actual != expected:
            mismatches.append(f"{name}: expected {expected!r}, got {actual!r}")
    return tuple(mismatches)


def format_observation(
    observation: ScanObservation,
    expectation: GoldenExpectation | None = None,
    candle: HistoricalCandleResult | None = None,
) -> str:
    """Format expectation and observation details for human inspection."""

    lines: list[str] = []
    if expectation is not None:
        lines.extend(
            [
                "GOLDEN EXPECTATION",
                f"  venue={expectation.venue} instrument={expectation.instrument}",
                f"  interval=[{expectation.interval_start}, {expectation.interval_end})",
                f"  row_count={expectation.row_count} buy={expectation.buy} sell={expectation.sell}",
                f"  first_exchange_ts={expectation.first_exchange_ts}",
                f"  last_exchange_ts={expectation.last_exchange_ts}",
            ]
        )
    lines.extend(
        [
            "SCAN OBSERVATION",
            f"  row_count={observation.row_count} buy={observation.buy} sell={observation.sell}",
            f"  other_aggressor_side={observation.other_aggressor_side}",
            f"  first_exchange_ts={observation.first_exchange_ts}",
            f"  last_exchange_ts={observation.last_exchange_ts}",
            f"  batches={observation.batch_count} max_batch_size={observation.max_batch_size}",
            "LIFECYCLE",
            f"  initial={observation.initial_state.name} after_first_batch={_state_name(observation.state_after_first_batch)} final={observation.final_state.name}",
            f"  completed_metadata_present={observation.completed_metadata is not None}",
            "COVERAGE METADATA",
        ]
    )
    if observation.completed_metadata is None:
        lines.append("  unavailable before successful exhaustion")
    else:
        lines.append(f"  coverage_complete={observation.completed_metadata.coverage_complete}")
        lines.append(f"  coverage_gaps={len(observation.completed_metadata.coverage_gaps)}")
        lines.extend(
            [
                "PROVENANCE",
                f"  result_identity={observation.completed_metadata.result_identity}",
                f"  catalog_partition_count={len(observation.completed_metadata.catalog_partition_ids)}",
            ]
        )
    lines.append("BOUNDEDNESS TELEMETRY")
    if observation.boundedness is None:
        lines.append("  unavailable")
    else:
        lines.append(
            f"  baseline={observation.boundedness.baseline_sample} peak={observation.boundedness.peak_sample}"
        )
        lines.append(
            f"  peak_delta={observation.boundedness.peak_delta} samples={observation.boundedness.sample_count}"
        )
    if candle is not None:
        lines.append("CANDLE RESULT")
        lines.append(f"  definition_identity={candle.definition_identity}")
        lines.append(f"  candle_count={candle.row_count}")
        if candle.records:
            lines.append(f"  first_candle={candle.records[0].stable_dict()}")
            lines.append(f"  last_candle={candle.records[-1].stable_dict()}")
        lines.append(f"  result_identity={candle.result_identity}")
    return "\n".join(lines)


def _state_name(state: ScanState | None) -> str:
    return "none" if state is None else state.name


__all__ = [
    "BoundednessTelemetry",
    "CANDLE_IMPLEMENTATION_IDENTITY",
    "DEFAULT_GOLDEN_FIXTURE",
    "GoldenCandleExpectation",
    "GoldenExpectation",
    "ScanObservation",
    "build_candle_source_evidence",
    "candle_field_mismatches",
    "format_observation",
    "golden_field_mismatches",
    "load_golden_expectation",
    "observe_scan",
    "observe_scan_with_candles",
]
