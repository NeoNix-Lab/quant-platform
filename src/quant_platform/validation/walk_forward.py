"""Deterministic expanding walk-forward schedules for F05."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from quant_platform.data.models import CoverageInterval, Instant, InvalidRequest


@dataclass(frozen=True, slots=True)
class WalkForwardScheduleSpec:
    """Four-field canonical specification for an expanding temporal schedule."""

    start: Instant
    initial_train_duration: int
    test_duration: int
    fold_count: int

    def __post_init__(self) -> None:
        start = _parse_instant(self.start, "start")
        initial_train_duration = _positive_int(
            self.initial_train_duration,
            "initial_train_duration",
        )
        test_duration = _positive_int(self.test_duration, "test_duration")
        fold_count = _positive_int(self.fold_count, "fold_count")

        _assert_representable_instant(start, "start")
        _assert_representable_instant(
            _add_duration(
                start,
                initial_train_duration + test_duration * fold_count,
                "schedule_end",
            ),
            "schedule_end",
        )

        object.__setattr__(self, "start", start)
        object.__setattr__(
            self,
            "initial_train_duration",
            initial_train_duration,
        )
        object.__setattr__(self, "test_duration", test_duration)
        object.__setattr__(self, "fold_count", fold_count)

    def stable_dict(self) -> dict[str, str | int]:
        return {
            "start": self.start.isoformat(),
            "initial_train_duration": self.initial_train_duration,
            "test_duration": self.test_duration,
            "fold_count": self.fold_count,
        }


@dataclass(frozen=True, slots=True)
class WalkForwardFold:
    """One expanding train interval and its adjacent test interval."""

    fold_index: int
    train: CoverageInterval
    test: CoverageInterval

    def __post_init__(self) -> None:
        if type(self.fold_index) is not int:
            raise InvalidRequest("fold_index must be an integer")
        if self.fold_index < 0:
            raise InvalidRequest("fold_index must be non-negative")
        if self.train.start >= self.train.end:
            raise InvalidRequest("train interval must be non-empty")
        if self.test.start >= self.test.end:
            raise InvalidRequest("test interval must be non-empty")
        if self.train.end != self.test.start:
            raise InvalidRequest("train and test intervals must be adjacent")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "fold_index": self.fold_index,
            "train": self.train.stable_dict(),
            "test": self.test.stable_dict(),
        }


def build_walk_forward_folds(
    spec: WalkForwardScheduleSpec,
) -> tuple[WalkForwardFold, ...]:
    """Build deterministic expanding temporal folds from a validated spec."""

    if not isinstance(spec, WalkForwardScheduleSpec):
        raise InvalidRequest("spec must be a WalkForwardScheduleSpec")

    folds = []
    previous_test_end = _add_duration(
        spec.start,
        spec.initial_train_duration,
        "fold_0_train_end",
    )
    for fold_index in range(spec.fold_count):
        train_end = previous_test_end
        test_end = _add_duration(
            train_end,
            spec.test_duration,
            f"fold_{fold_index}_test_end",
        )
        folds.append(
            WalkForwardFold(
                fold_index=fold_index,
                train=CoverageInterval(spec.start, train_end),
                test=CoverageInterval(train_end, test_end),
            )
        )
        previous_test_end = test_end

    return tuple(folds)


def _parse_instant(value: Instant | datetime | str, field: str) -> Instant:
    try:
        return Instant.parse(value)
    except InvalidRequest as exc:
        raise InvalidRequest(f"{field} must be a valid UTC instant") from exc


def _positive_int(value: int, field: str) -> int:
    if type(value) is not int:
        raise InvalidRequest(f"{field} must be an integer")
    if value <= 0:
        raise InvalidRequest(f"{field} must be positive")
    return value


def _add_duration(start: Instant, duration_ns: int, field: str) -> Instant:
    try:
        end = Instant(start.epoch_ns + duration_ns)
    except OverflowError as exc:  # pragma: no cover - defensive for alternate ints
        raise InvalidRequest(f"{field} is not representable") from exc
    _assert_representable_instant(end, field)
    return end


def _assert_representable_instant(value: Instant, field: str) -> None:
    try:
        value.isoformat()
    except (OverflowError, OSError, ValueError) as exc:
        raise InvalidRequest(f"{field} is not representable as a UTC instant") from exc


__all__ = [
    "WalkForwardFold",
    "WalkForwardScheduleSpec",
    "build_walk_forward_folds",
]
