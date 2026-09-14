#!/usr/bin/env python3
"""F05 deterministic expanding walk-forward schedule proof."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import Instant, InvalidRequest  # noqa: E402
from quant_platform.validation.walk_forward import (  # noqa: E402
    WalkForwardFold,
    WalkForwardScheduleSpec,
    build_walk_forward_folds,
)


def spec() -> WalkForwardScheduleSpec:
    return WalkForwardScheduleSpec(
        start="2024-01-01T00:00:00Z",
        initial_train_duration=10,
        test_duration=3,
        fold_count=3,
    )


class ValidationWalkForwardV1Tests(unittest.TestCase):
    def test_expanding_folds_are_exact_adjacent_and_half_open(self):
        folds = build_walk_forward_folds(spec())

        self.assertEqual(3, len(folds))
        self.assertEqual(
            [fold.stable_dict() for fold in folds],
            [
                {
                    "fold_index": 0,
                    "train": {
                        "start": "2024-01-01T00:00:00Z",
                        "end": "2024-01-01T00:00:00.00000001Z",
                    },
                    "test": {
                        "start": "2024-01-01T00:00:00.00000001Z",
                        "end": "2024-01-01T00:00:00.000000013Z",
                    },
                },
                {
                    "fold_index": 1,
                    "train": {
                        "start": "2024-01-01T00:00:00Z",
                        "end": "2024-01-01T00:00:00.000000013Z",
                    },
                    "test": {
                        "start": "2024-01-01T00:00:00.000000013Z",
                        "end": "2024-01-01T00:00:00.000000016Z",
                    },
                },
                {
                    "fold_index": 2,
                    "train": {
                        "start": "2024-01-01T00:00:00Z",
                        "end": "2024-01-01T00:00:00.000000016Z",
                    },
                    "test": {
                        "start": "2024-01-01T00:00:00.000000016Z",
                        "end": "2024-01-01T00:00:00.000000019Z",
                    },
                },
            ],
        )
        for left, right in zip(folds, folds[1:]):
            self.assertEqual(left.test.end, right.train.end)
            self.assertEqual(left.test.end, right.test.start)
            self.assertEqual(left.train.start, right.train.start)
            self.assertLess(left.test.start, left.test.end)
            self.assertLess(left.test.end, right.test.end)

    def test_identical_specs_produce_identical_immutable_fold_values(self):
        first = build_walk_forward_folds(spec())
        second = build_walk_forward_folds(
            WalkForwardScheduleSpec(
                start=Instant.parse("2024-01-01T00:00:00.000000000Z"),
                initial_train_duration=10,
                test_duration=3,
                fold_count=3,
            )
        )

        self.assertEqual(first, second)
        self.assertIsInstance(first[0], WalkForwardFold)
        with self.assertRaises(FrozenInstanceError):
            first[0].fold_index = 10  # type: ignore[misc]

    def test_start_accepts_timezone_aware_datetimes_and_canonicalizes_to_utc(self):
        aware_start = datetime(2024, 1, 1, 1, 0, tzinfo=timezone.utc)

        canonical = WalkForwardScheduleSpec(
            start=aware_start,
            initial_train_duration=1_000_000_000,
            test_duration=1_000_000_000,
            fold_count=1,
        )

        fold = build_walk_forward_folds(canonical)[0]
        self.assertEqual("2024-01-01T01:00:00Z", fold.train.start.isoformat())
        self.assertEqual("2024-01-01T01:00:01Z", fold.test.start.isoformat())
        self.assertEqual("2024-01-01T01:00:02Z", fold.test.end.isoformat())

    def test_invalid_or_contradictory_specs_fail_explicitly(self):
        cases = [
            {"initial_train_duration": 0},
            {"initial_train_duration": -1},
            {"initial_train_duration": True},
            {"test_duration": 0},
            {"test_duration": -1},
            {"test_duration": "3"},
            {"fold_count": 0},
            {"fold_count": -1},
            {"fold_count": False},
            {"start": "2024-01-01T00:00:00+00:00"},
            {"start": datetime(2024, 1, 1)},
        ]
        base = {
            "start": "2024-01-01T00:00:00Z",
            "initial_train_duration": 10,
            "test_duration": 3,
            "fold_count": 2,
        }

        for override in cases:
            with self.subTest(override=override), self.assertRaises(InvalidRequest):
                WalkForwardScheduleSpec(**{**base, **override})

    def test_builder_rejects_unvalidated_or_non_schedule_inputs(self):
        with self.assertRaisesRegex(InvalidRequest, "WalkForwardScheduleSpec"):
            build_walk_forward_folds(object())  # type: ignore[arg-type]

    def test_no_dataset_label_model_or_storage_inputs_participate(self):
        schedule = spec()

        self.assertEqual(
            {
                "start": "2024-01-01T00:00:00Z",
                "initial_train_duration": 10,
                "test_duration": 3,
                "fold_count": 3,
            },
            schedule.stable_dict(),
        )
        self.assertEqual(
            build_walk_forward_folds(schedule),
            build_walk_forward_folds(schedule),
        )


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
