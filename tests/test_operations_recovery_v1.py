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
    K06_NOT_APPLICABLE_IDENTITY_DOMAIN,
    RECOVERY_SET_IDENTITY_DOMAIN,
    K06NotApplicableAssertion,
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
VALID_K06_ASSESSMENT_SHA = "8" * 64


def not_applicable(**overrides) -> K06NotApplicableAssertion:
    kwargs = dict(
        asserting_authority_id="adr:k08-test-authority-v1",
        asserted_at=Instant.parse("2024-01-15T00:00:00Z"),
        rationale="canonical publication is self-contained; no RAW source reconstruction is required",
    )
    kwargs.update(overrides)
    return K06NotApplicableAssertion(**kwargs)


def recovery_set(**overrides) -> RecoverySetV1:
    kwargs = dict(
        natural_identity=NATURAL,
        dataset_manifest_sha256=HASH_A,
        partition_manifest_sha256=HASH_B,
        coverage_manifest_sha256=(HASH_C,),
        physical_content_sha256="d" * 64,
        physical_size_bytes=1024,
        declared_coverage=COVERAGE,
        catalog_dataset_id="dataset-uuid-1",
        catalog_partition_id="partition-uuid-1",
        k06_not_applicable_fingerprint=not_applicable().fingerprint,
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

    # -- K06 applicability is a mandatory, exclusive, attributed decision --

    def test_neither_k06_branch_is_refused(self):
        with self.assertRaises(RecoveryError):
            recovery_set(k06_not_applicable_fingerprint=None)

    def test_both_k06_branches_is_refused(self):
        with self.assertRaises(RecoveryError):
            recovery_set(
                k06_not_applicable_fingerprint=not_applicable().fingerprint,
                k06_protection_identity=VALID_K06_IDENTITY,
                k06_assessment_sha256=VALID_K06_ASSESSMENT_SHA,
            )

    def test_k06_protection_identity_and_assessment_sha_must_be_paired(self):
        with self.assertRaises(RecoveryError):
            recovery_set(
                k06_not_applicable_fingerprint=None,
                k06_protection_identity=VALID_K06_IDENTITY,
            )
        with self.assertRaises(RecoveryError):
            recovery_set(
                k06_not_applicable_fingerprint=None,
                k06_assessment_sha256=VALID_K06_ASSESSMENT_SHA,
            )

    def test_k06_protection_branch_changes_recovery_identity(self):
        not_applicable_variant = recovery_set()
        protected_variant = recovery_set(
            k06_not_applicable_fingerprint=None,
            k06_protection_identity=VALID_K06_IDENTITY,
            k06_assessment_sha256=VALID_K06_ASSESSMENT_SHA,
        )
        self.assertNotEqual(not_applicable_variant.recovery_identity, protected_variant.recovery_identity)
        self.assertEqual(protected_variant.k06_protection_identity, VALID_K06_IDENTITY)
        self.assertEqual(protected_variant.k06_assessment_sha256, VALID_K06_ASSESSMENT_SHA)

    def test_k06_assessment_sha_change_changes_identity(self):
        base = recovery_set(
            k06_not_applicable_fingerprint=None,
            k06_protection_identity=VALID_K06_IDENTITY,
            k06_assessment_sha256=VALID_K06_ASSESSMENT_SHA,
        )
        other = recovery_set(
            k06_not_applicable_fingerprint=None,
            k06_protection_identity=VALID_K06_IDENTITY,
            k06_assessment_sha256="7" * 64,
        )
        self.assertNotEqual(base.recovery_identity, other.recovery_identity)

    def test_not_applicable_reason_change_changes_identity(self):
        base = recovery_set(k06_not_applicable_fingerprint=not_applicable().fingerprint)
        other = recovery_set(
            k06_not_applicable_fingerprint=not_applicable(rationale="a different rationale").fingerprint
        )
        self.assertNotEqual(base.recovery_identity, other.recovery_identity)

    def test_malformed_k06_protection_identity_is_refused(self):
        for malformed in ("not-a-protection-identity", "protection-unit-identity-v1:sha256:short"):
            with self.subTest(malformed=malformed):
                with self.assertRaises(RecoveryError):
                    recovery_set(
                        k06_not_applicable_fingerprint=None,
                        k06_protection_identity=malformed,
                        k06_assessment_sha256=VALID_K06_ASSESSMENT_SHA,
                    )

    def test_k06_not_applicable_assertion_requires_attribution(self):
        with self.assertRaises(RecoveryError):
            not_applicable(asserting_authority_id="   ")
        with self.assertRaises(RecoveryError):
            not_applicable(rationale="")

    # -- reconstruction from a durably persisted canonical payload ---------

    def test_recovery_set_from_canonical_payload_round_trips_not_applicable(self):
        original = recovery_set()
        reconstructed = recovery_set_from_canonical_payload(
            original.canonical_payload(),
            catalog_dataset_id="a-different-catalog-dataset-id",
            catalog_partition_id="a-different-catalog-partition-id",
        )
        self.assertEqual(reconstructed.recovery_identity, original.recovery_identity)
        self.assertEqual(
            reconstructed.k06_not_applicable_fingerprint, original.k06_not_applicable_fingerprint
        )
        # Catalog locators are informational and legitimately caller-supplied
        # on reload; they must not affect the reconstructed identity.
        self.assertNotEqual(reconstructed.catalog_dataset_id, original.catalog_dataset_id)

    def test_recovery_set_from_canonical_payload_round_trips_protected(self):
        original = recovery_set(
            k06_not_applicable_fingerprint=None,
            k06_protection_identity=VALID_K06_IDENTITY,
            k06_assessment_sha256=VALID_K06_ASSESSMENT_SHA,
        )
        reconstructed = recovery_set_from_canonical_payload(
            original.canonical_payload(), catalog_dataset_id="d", catalog_partition_id="p",
        )
        self.assertEqual(reconstructed.recovery_identity, original.recovery_identity)
        self.assertEqual(reconstructed.k06_protection_identity, VALID_K06_IDENTITY)
        self.assertEqual(reconstructed.k06_assessment_sha256, VALID_K06_ASSESSMENT_SHA)

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

    def test_k06_not_applicable_fingerprint_is_domain_tagged_and_deterministic(self):
        first = not_applicable()
        second = not_applicable()
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(
            first.canonical_payload()["identity_domain"], K06_NOT_APPLICABLE_IDENTITY_DOMAIN
        )


if __name__ == "__main__":
    unittest.main()
