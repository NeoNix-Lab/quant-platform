#!/usr/bin/env python3
"""K04 one-root capacity observation proof."""

from __future__ import annotations

from collections import namedtuple
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.operations.capacity import (
    CapacityObservation,
    CapacityUnavailable,
    StorageRoot,
    observe_capacity,
)


DiskUsage = namedtuple("DiskUsage", "total used free")


class OperationsCapacityV1Tests(unittest.TestCase):
    def test_one_supplied_root_yields_immutable_observation_with_exact_values(self):
        observed_at = datetime(2026, 9, 14, 10, 30, tzinfo=timezone.utc)
        root = StorageRoot("hot", Path("already-resolved"))
        calls = []
        clock_calls = []

        def fake_disk_usage(path):
            calls.append(path)
            return DiskUsage(total=1000, used=700, free=123)

        def fake_clock():
            clock_calls.append("tick")
            return observed_at

        observation = observe_capacity(
            root,
            _clock=fake_clock,
            _disk_usage=fake_disk_usage,
        )

        self.assertIsInstance(observation, CapacityObservation)
        self.assertEqual([root.root_path], calls)
        self.assertEqual(["tick"], clock_calls)
        self.assertEqual("hot", observation.storage_root_id)
        self.assertEqual(root.root_path, observation.root_path)
        self.assertEqual(observed_at, observation.observed_at)
        self.assertEqual(timezone.utc, observation.observed_at.tzinfo)
        self.assertEqual(1000, observation.total_bytes)
        self.assertEqual(700, observation.used_bytes)
        self.assertEqual(123, observation.available_bytes)
        self.assertNotEqual(
            observation.total_bytes,
            observation.used_bytes + observation.available_bytes,
        )
        with self.assertRaises(FrozenInstanceError):
            observation.available_bytes = 0  # type: ignore[misc]

    def test_real_filesystem_measurement_reports_non_negative_byte_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = StorageRoot("scratch", directory)
            observation = observe_capacity(root)

        self.assertIsInstance(observation, CapacityObservation)
        self.assertEqual("scratch", observation.storage_root_id)
        self.assertEqual(root.root_path, observation.root_path)
        self.assertIsNotNone(observation.observed_at.utcoffset())
        self.assertEqual(timezone.utc, observation.observed_at.tzinfo)
        self.assertGreaterEqual(observation.total_bytes, 0)
        self.assertGreaterEqual(observation.used_bytes, 0)
        self.assertGreaterEqual(observation.available_bytes, 0)

    def test_missing_root_fails_explicitly_without_creating_it(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "missing"
            root = StorageRoot("cold", missing)

            result = observe_capacity(root)

            self.assertIsInstance(result, CapacityUnavailable)
            self.assertEqual("cold", result.storage_root_id)
            self.assertEqual(missing, result.root_path)
            self.assertEqual("root missing", result.reason)
            self.assertFalse(missing.exists())

    def test_inaccessible_or_unmeasurable_root_is_explicit_unavailable(self):
        root = StorageRoot("deepcold", Path("sealed"))

        result = observe_capacity(
            root,
            _disk_usage=lambda path: (_ for _ in ()).throw(PermissionError()),
        )

        self.assertIsInstance(result, CapacityUnavailable)
        self.assertEqual("deepcold", result.storage_root_id)
        self.assertEqual(root.root_path, result.root_path)
        self.assertEqual("root inaccessible", result.reason)
        with self.assertRaises(FrozenInstanceError):
            result.reason = "pressure"  # type: ignore[misc]

    def test_unavailable_never_returns_partial_or_sentinel_capacity_values(self):
        root = StorageRoot("hot", Path("unmeasurable"))

        result = observe_capacity(
            root,
            _disk_usage=lambda path: (_ for _ in ()).throw(OSError("stat failed")),
        )

        self.assertIsInstance(result, CapacityUnavailable)
        self.assertFalse(hasattr(result, "total_bytes"))
        self.assertFalse(hasattr(result, "used_bytes"))
        self.assertFalse(hasattr(result, "available_bytes"))
        self.assertIn("filesystem statistics unavailable", result.reason)


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
