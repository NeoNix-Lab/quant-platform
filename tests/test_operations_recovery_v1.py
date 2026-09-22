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
from quant_platform.operations.protection import PROTECTION_UNIT_IDENTITY_DOMAIN  # noqa: E402
from quant_platform.operations.recovery import (  # noqa: E402
    RECOVERY_SET_IDENTITY_DOMAIN,
    RecoveryError,
    RecoverySetV1,
    recovery_set_from_canonical_payload,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
NATURAL = NaturalPartitionIdentity(IDENTITY, "dt=2024-01-15", 1)
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64
COVERAGE = CoverageInterval(Instant.parse("2024-01-15T00:00:00Z"), Instant.parse("2024-01-16T00:00:00Z"))
VALID_K06_IDENTITY = f"{PROTECTION_UNIT_IDENTITY_DOMAIN}:sha256:" + "9" * 64


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

    def test_k06_protection_identity_defaults_to_none_and_is_optional(self):
        base = recovery_set()
        self.assertIsNone(base.k06_protection_identity)
        self.assertIsNone(base.canonical_payload()["k06_protection_identity"])

    def test_k06_protection_identity_changes_recovery_identity(self):
        base = recovery_set()
        with_k06 = recovery_set(k06_protection_identity=VALID_K06_IDENTITY)
        self.assertNotEqual(base.recovery_identity, with_k06.recovery_identity)
        self.assertEqual(with_k06.k06_protection_identity, VALID_K06_IDENTITY)

    def test_malformed_k06_protection_identity_is_refused(self):
        for malformed in ("not-a-protection-identity", "protection-unit-identity-v1:sha256:short", ""):
            with self.subTest(malformed=malformed):
                with self.assertRaises(RecoveryError):
                    recovery_set(k06_protection_identity=malformed)

    def test_recovery_set_from_canonical_payload_round_trips(self):
        original = recovery_set(k06_protection_identity=VALID_K06_IDENTITY)
        reconstructed = recovery_set_from_canonical_payload(
            original.canonical_payload(),
            catalog_dataset_id="a-different-catalog-dataset-id",
            catalog_partition_id="a-different-catalog-partition-id",
        )
        self.assertEqual(reconstructed.recovery_identity, original.recovery_identity)
        self.assertEqual(reconstructed.k06_protection_identity, original.k06_protection_identity)
        # Catalog locators are informational and legitimately caller-supplied
        # on reload; they must not affect the reconstructed identity.
        self.assertNotEqual(reconstructed.catalog_dataset_id, original.catalog_dataset_id)

    def test_recovery_set_from_canonical_payload_refuses_wrong_identity_domain(self):
        payload = dict(recovery_set().canonical_payload())
        payload["identity_domain"] = "some-other-domain-v1"
        with self.assertRaises(RecoveryError):
            recovery_set_from_canonical_payload(
                payload, catalog_dataset_id="d", catalog_partition_id="p",
            )

    def test_recovery_set_from_canonical_payload_refuses_malformed_payload(self):
        for payload in (
            {},
            {"identity_domain": RECOVERY_SET_IDENTITY_DOMAIN},
            "not-a-mapping",
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(RecoveryError):
                    recovery_set_from_canonical_payload(
                        payload, catalog_dataset_id="d", catalog_partition_id="p",
                    )


if __name__ == "__main__":
    unittest.main()
