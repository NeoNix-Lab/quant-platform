#!/usr/bin/env python3
"""Tests-only support for observing bounded Golden Conformity scans.

This module observes an existing ``DataScan``.  It does not implement a
conformity gate, publication, certification, ordering, coverage, hashing, or
any other producer-side business rule.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from numbers import Real
from pathlib import Path
import sys
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.access.gateway import (  # noqa: E402
    DataScan,
    ScanState,
)
from quant_platform.access.models import DataSliceMetadata  # noqa: E402
from quant_platform.data.models import Instant  # noqa: E402

DEFAULT_GOLDEN_FIXTURE = ROOT / "fixtures" / "conformity" / "golden-bybit-btcusdt-2024-01-15.json"
MemorySampler = Callable[[], int | float]


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
        if set(payload) != required:
            missing = sorted(required - set(payload))
            extra = sorted(set(payload) - required)
            raise ValueError(f"Golden expectation fields mismatch; missing={missing}, extra={extra}")
        interval = payload["interval"]
        if not isinstance(interval, Mapping) or set(interval) != {"start", "end"}:
            raise ValueError("Golden expectation interval must contain only start and end")
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
    return "\n".join(lines)


def _state_name(state: ScanState | None) -> str:
    return "none" if state is None else state.name


__all__ = [
    "BoundednessTelemetry",
    "DEFAULT_GOLDEN_FIXTURE",
    "GoldenExpectation",
    "ScanObservation",
    "format_observation",
    "golden_field_mismatches",
    "load_golden_expectation",
    "observe_scan",
]
