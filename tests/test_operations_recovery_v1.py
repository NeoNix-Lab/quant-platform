#!/usr/bin/env python3
"""K08 backup/restore v1: RecoverySetV1 identity proof.

Mirrors the discipline of ``tests/test_operations_protection_v1.py``: pure
identity/validation behavior, no filesystem, no catalog.  Restore/export
mechanics and cross-boundary composition are proven in
``tests/test_application_backup_restore_v1.py``.
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import CoverageInterval, DatasetIdentity, Instant, NaturalPartitionIdentity  # noqa: E402
from quant_platform.operations.recovery import (  # noqa: E402
    RECOVERY_SET_IDENTITY_DOMAIN,
    RecoveryError,
    RecoverySetV1,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
NATURAL = NaturalPartitionIdentity(IDENTITY, "dt=2024-01-15", 1)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
COVERAGE = CoverageInterval(Instant.parse("2024-01-15T00:00:00Z"), Instant.parse("2024-01-16T00:00:00Z"))


def recovery_set(**overrides) -> RecoverySetV1:
    kwargs = dict(
        natural_identity=NATURAL,
        dataset_manifest_sha256=HASH_A,
        partition_manifest_sha256=HASH_B,
        coverage_manifest_sha256=(HASH_C,),
        physical_content_sha256="d" * 64,
        physical_size_bytes=1024,
        declared_coverage=COVERAGE,
        partition_state="closed",
        catalog_dataset_id="dataset-uuid-1",
        catalog_partition_id="partition-uuid-1",
    )
    kwargs.update(overrides)
    return RecoverySetV1(**kwargs)


class RecoverySetV1Tests(unittest.TestCase):
    def test_identity_is_deterministic_and_domain_tagged(self):
        first = recovery_set()
        second = recovery_set()
        self.assertEqual(first.recovery_identity, second.recovery_identity)
        self.assertTrue(first.recovery_identity.startswith(f"{RECOVERY_SET_IDENTITY_DOMAIN}:sha256:"))
        self.assertEqual(len(first.fingerprint_hex), 64)

    def test_semantic_input_change_changes_identity(self):
        base = recovery_set()
        for overrides in (
            {"physical_content_sha256": "e" * 64},
            {"partition_manifest_sha256": "f" * 64},
            {"coverage_manifest_sha256": ("1" * 64,)},
            {"declared_coverage": CoverageInterval(Instant.parse("2024-01-15T01:00:00Z"), Instant.parse("2024-01-16T00:00:00Z"))},
            {"natural_identity": NaturalPartitionIdentity(IDENTITY, "dt=2024-01-16", 1)},
            {"natural_identity": NaturalPartitionIdentity(IDENTITY, "dt=2024-01-15", 2), "partition_state": "closed"},
        ):
            with self.subTest(overrides=overrides):
                other = recovery_set(**overrides)
                self.assertNotEqual(base.recovery_identity, other.recovery_identity)

    def test_catalog_locator_accidents_do_not_change_identity(self):
        base = recovery_set()
        relocated = recovery_set(catalog_dataset_id="dataset-uuid-9", catalog_partition_id="partition-uuid-9")
        self.assertEqual(base.recovery_identity, relocated.recovery_identity)
        self.assertNotEqual(base.catalog_partition_id, relocated.catalog_partition_id)

    def test_coverage_manifest_hash_order_does_not_change_identity(self):
        ordered = recovery_set(coverage_manifest_sha256=(HASH_C, "1" * 64))
        reordered = recovery_set(coverage_manifest_sha256=("1" * 64, HASH_C))
        self.assertEqual(ordered.recovery_identity, reordered.recovery_identity)

    def test_non_finalized_generation_is_refused(self):
        for state in ("writing", "invalid", "superseded"):
            with self.subTest(state=state):
                with self.assertRaises(RecoveryError):
                    recovery_set(partition_state=state)

    def test_missing_coverage_evidence_is_refused(self):
        with self.assertRaises(RecoveryError):
            recovery_set(coverage_manifest_sha256=())

    def test_degenerate_declared_coverage_is_refused(self):
        instant = Instant.parse("2024-01-15T00:00:00Z")
        with self.assertRaises(ValueError):
            recovery_set(declared_coverage=CoverageInterval(instant, instant))

    def test_malformed_hash_is_refused(self):
        with self.assertRaises(RecoveryError):
            recovery_set(physical_content_sha256="not-a-hash")
        with self.assertRaises(RecoveryError):
            recovery_set(physical_content_sha256="A" * 64)

    def test_negative_size_is_refused(self):
        with self.assertRaises(RecoveryError):
            recovery_set(physical_size_bytes=-1)

    def test_blank_catalog_locator_is_refused(self):
        with self.assertRaises(RecoveryError):
            recovery_set(catalog_dataset_id="   ")


if __name__ == "__main__":
    unittest.main()
