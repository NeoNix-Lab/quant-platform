from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.admitted_input import (  # noqa: E402
    AdmittedInputAttemptMismatch,
    AdmittedInputConflict,
    AdmittedInputError,
    AdmittedInputExpired,
    AdmittedInputManifestV1,
    AdmittedInputState,
    AdmittedInputStore,
)


def manifest(*, coverage: dict[str, object] | None = None, request: str = "request-v1:alpha") -> AdmittedInputManifestV1:
    return AdmittedInputManifestV1(
        logical_input_identities=("dataset-v1:BTCUSDT",),
        natural_partition_identities=("partition-v1:2024-01-01", "partition-v1:2024-01-02"),
        schema_identity="trades@1",
        schema_version="1",
        schema_hash="schema-sha256:abc",
        manifest_hashes=("manifest-sha256:abc",),
        content_hashes=("content-sha256:abc",),
        declared_coverage={"end": "2024-01-03T00:00:00Z", "start": "2024-01-01T00:00:00Z"}
        if coverage is None
        else coverage,
        request_identity=request,
        result_identity=None,
        definition_identities=("definition-v1:trades",),
        implementation_identity="implementation-v1:abc",
        git_identity="git:abc123",
        operation_identity="strategy-compose-v1",
        profile_identity="profile-v1:default",
    )


class AdmittedInputManifestV1Tests(unittest.TestCase):
    def store(self) -> AdmittedInputStore:
        return AdmittedInputStore(sqlite3.connect(":memory:"))

    def test_canonical_identity_is_deterministic_and_excludes_operational_locator_surface(self) -> None:
        first = manifest()
        reordered = AdmittedInputManifestV1(
            logical_input_identities=("dataset-v1:BTCUSDT",),
            natural_partition_identities=("partition-v1:2024-01-02", "partition-v1:2024-01-01"),
            schema_identity="trades@1",
            schema_version="1",
            schema_hash="schema-sha256:abc",
            manifest_hashes=("manifest-sha256:abc",),
            content_hashes=("content-sha256:abc",),
            declared_coverage={"start": "2024-01-01T00:00:00Z", "end": "2024-01-03T00:00:00Z"},
            request_identity="request-v1:alpha",
            result_identity=None,
            definition_identities=("definition-v1:trades",),
            implementation_identity="implementation-v1:abc",
            git_identity="git:abc123",
            operation_identity="strategy-compose-v1",
            profile_identity="profile-v1:default",
        )

        self.assertEqual(first.canonical_payload_v1, manifest().canonical_payload_v1)
        self.assertNotEqual(first.canonical_payload_v1, reordered.canonical_payload_v1)
        self.assertNotEqual(first.manifest_digest, reordered.manifest_digest)
        self.assertNotEqual(first.admission_id, reordered.admission_id)
        self.assertNotIn("path", first.canonical_payload_v1)
        self.assertNotIn("storage_root", first.canonical_payload_v1)
        self.assertNotIn("catalog_uuid", first.canonical_payload_v1)
        self.assertNotIn("transport_url", first.canonical_payload_v1)
        self.assertNotIn("manifest_digest", first.canonical_payload_v1)
        self.assertNotIn("admission_id", first.canonical_payload_v1)

    def test_only_sealed_evidence_can_start_delivery(self) -> None:
        store = self.store()
        admitted = store.admit(manifest())

        self.assertEqual(AdmittedInputState.ADMITTED, admitted.state)
        with self.assertRaisesRegex(AdmittedInputConflict, "SEALED"):
            store.begin_delivery(admitted.admission_id)
        sealed = store.seal(admitted.admission_id)
        self.assertEqual(AdmittedInputState.SEALED, sealed.state)
        pending = store.begin_delivery(admitted.admission_id)
        self.assertEqual((AdmittedInputState.DELIVERY_PENDING, 1), (pending.state, pending.delivery_attempt))

    def test_digest_mismatch_fails_closed_and_retries_same_immutable_admission(self) -> None:
        store = self.store()
        admitted = store.admit(manifest())
        sealed = store.seal(admitted.admission_id)
        first = store.begin_delivery(sealed.admission_id)

        failed = store.record_delivery(
            sealed.admission_id, "0" * 64, delivery_attempt=first.delivery_attempt,
        )
        self.assertEqual(AdmittedInputState.DELIVERY_FAILED, failed.state)
        self.assertEqual("manifest_digest_mismatch", failed.delivery_failure_reason)
        self.assertEqual(sealed.manifest.canonical_payload_v1, failed.manifest.canonical_payload_v1)
        self.assertEqual(sealed.manifest.manifest_digest, failed.manifest.manifest_digest)

        retry = store.begin_delivery(sealed.admission_id)
        delivered = store.record_delivery(
            retry.admission_id, sealed.manifest.manifest_digest, delivery_attempt=retry.delivery_attempt,
        )
        self.assertEqual(sealed.admission_id, delivered.admission_id)
        self.assertEqual(AdmittedInputState.DELIVERED, delivered.state)

    def test_a_reported_delivery_failure_ends_the_attempt_and_retries_the_same_admission(self) -> None:
        store = self.store()
        sealed = store.seal(store.admit(manifest()).admission_id)

        with self.assertRaises(AdmittedInputError):  # nothing was delivered, so no attempt exists
            store.record_delivery_failure(sealed.admission_id, "delivery_interrupted", delivery_attempt=1)
        attempt = store.begin_delivery(sealed.admission_id).delivery_attempt
        with self.assertRaises(ValueError):
            store.record_delivery_failure(sealed.admission_id, " ", delivery_attempt=attempt)
        failed = store.record_delivery_failure(
            sealed.admission_id, "content_hash_mismatch:member:1", delivery_attempt=attempt,
        )

        self.assertEqual(AdmittedInputState.DELIVERY_FAILED, failed.state)
        self.assertEqual("content_hash_mismatch:member:1", failed.delivery_failure_reason)
        self.assertEqual(sealed.manifest.canonical_payload_v1, failed.manifest.canonical_payload_v1)
        with self.assertRaisesRegex(AdmittedInputConflict, "DELIVERY_PENDING"):
            store.record_delivery_failure(sealed.admission_id, "again", delivery_attempt=attempt)
        retry = store.begin_delivery(sealed.admission_id)
        self.assertEqual(AdmittedInputState.DELIVERY_PENDING, retry.state)
        self.assertIsNone(retry.delivery_failure_reason)

    def test_expired_admission_cannot_be_revived(self) -> None:
        store = self.store()
        admitted = store.admit(manifest())

        with self.assertRaises(AdmittedInputAttemptMismatch):
            store.expire(admitted.admission_id, delivery_attempt=1)
        sealed = store.seal(admitted.admission_id)
        with self.assertRaises(AdmittedInputAttemptMismatch):
            store.expire(sealed.admission_id, delivery_attempt=1)

        pending = store.begin_delivery(sealed.admission_id)
        expired = store.expire(pending.admission_id, delivery_attempt=pending.delivery_attempt)

        self.assertEqual(AdmittedInputState.EXPIRED, expired.state)
        with self.assertRaises(AdmittedInputExpired):
            store.begin_delivery(admitted.admission_id)
        with self.assertRaises(AdmittedInputExpired):
            store.admit(manifest())

        renewed = store.admit(manifest(request="request-v1:beta"))
        self.assertNotEqual(expired.admission_id, renewed.admission_id)
        self.assertEqual(AdmittedInputState.ADMITTED, renewed.state)

    # -- ADR-0064 Amendment 1 (#357): a delivery attempt has a number ----------------

    def test_the_attempt_number_counts_entries_into_pending_and_is_not_identity(self) -> None:
        store = self.store()
        sealed = store.seal(store.admit(manifest()).admission_id)
        self.assertEqual(0, sealed.delivery_attempt)
        first = store.begin_delivery(sealed.admission_id)
        self.assertEqual(1, first.delivery_attempt)
        store.record_delivery_failure(sealed.admission_id, "delivery_interrupted", delivery_attempt=1)
        failed = store.get(sealed.admission_id)
        self.assertEqual(1, failed.delivery_attempt)  # an ended attempt keeps its number
        second = store.begin_delivery(sealed.admission_id)
        self.assertEqual(2, second.delivery_attempt)
        # Metadata only: identity, digest and the stored payload are unchanged.
        self.assertEqual(sealed.admission_id, second.admission_id)
        self.assertEqual(sealed.manifest.canonical_payload_v1, second.manifest.canonical_payload_v1)
        self.assertNotIn("delivery_attempt", second.manifest.canonical_payload_v1)

    def test_a_report_for_an_older_or_unknown_attempt_is_refused_with_no_write(self) -> None:
        store = self.store()
        sealed = store.seal(store.admit(manifest()).admission_id)
        store.begin_delivery(sealed.admission_id)  # attempt 1
        store.record_delivery_failure(sealed.admission_id, "delivery_interrupted", delivery_attempt=1)
        second = store.begin_delivery(sealed.admission_id)  # attempt 2
        digest = sealed.manifest.manifest_digest
        for label, call in (
            ("a repeated failure of attempt 1", lambda: store.record_delivery_failure(
                sealed.admission_id, "again", delivery_attempt=1)),
            ("a late delivered of attempt 1", lambda: store.record_delivery(
                sealed.admission_id, digest, delivery_attempt=1)),
            ("an attempt that never existed", lambda: store.record_delivery(
                sealed.admission_id, digest, delivery_attempt=3)),
            ("an expiry of attempt 1", lambda: store.expire(sealed.admission_id, delivery_attempt=1)),
        ):
            with self.subTest(label), self.assertRaises(AdmittedInputAttemptMismatch):
                call()
            current = store.get(sealed.admission_id)  # nothing was written
            self.assertEqual(
                (AdmittedInputState.DELIVERY_PENDING, 2, None),
                (current.state, current.delivery_attempt, current.delivery_failure_reason),
            )
        # The attempt that is current is still the one that can end.
        delivered = store.record_delivery(sealed.admission_id, digest, delivery_attempt=second.delivery_attempt)
        self.assertEqual(AdmittedInputState.DELIVERED, delivered.state)

    def test_a_repeated_report_for_the_attempt_that_ended_is_refused(self) -> None:
        store = self.store()
        sealed = store.seal(store.admit(manifest()).admission_id)
        attempt = store.begin_delivery(sealed.admission_id).delivery_attempt
        store.record_delivery(sealed.admission_id, sealed.manifest.manifest_digest, delivery_attempt=attempt)
        with self.assertRaises(AdmittedInputConflict):
            store.record_delivery_failure(sealed.admission_id, "late", delivery_attempt=attempt)
        self.assertEqual(AdmittedInputState.DELIVERED, store.get(sealed.admission_id).state)

    def test_an_attempt_must_be_a_positive_integer(self) -> None:
        store = self.store()
        sealed = store.seal(store.admit(manifest()).admission_id)
        store.begin_delivery(sealed.admission_id)
        for bad in (0, -1, True, "1", None, 1.0):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                store.record_delivery_failure(sealed.admission_id, "x", delivery_attempt=bad)
        self.assertEqual(AdmittedInputState.DELIVERY_PENDING, store.get(sealed.admission_id).state)

    def test_an_attempt_that_cannot_publish_is_not_recorded(self) -> None:
        store = self.store()
        sealed = store.seal(store.admit(manifest()).admission_id)

        def refuse(record):
            self.assertEqual((AdmittedInputState.DELIVERY_PENDING, 1), (record.state, record.delivery_attempt))
            raise RuntimeError("publication failed")

        with self.assertRaises(RuntimeError):
            store.begin_delivery(sealed.admission_id, before_commit=refuse)
        after = store.get(sealed.admission_id)
        self.assertEqual((AdmittedInputState.SEALED, 0), (after.state, after.delivery_attempt))
        self.assertEqual(1, store.begin_delivery(sealed.admission_id).delivery_attempt)  # not consumed

    def test_an_expired_admission_refuses_every_report(self) -> None:
        store = self.store()
        sealed = store.seal(store.admit(manifest()).admission_id)
        attempt = store.begin_delivery(sealed.admission_id).delivery_attempt
        store.expire(sealed.admission_id, delivery_attempt=attempt)
        with self.assertRaises(AdmittedInputExpired):
            store.record_delivery(sealed.admission_id, sealed.manifest.manifest_digest, delivery_attempt=attempt)
        with self.assertRaises(AdmittedInputExpired):
            store.record_delivery_failure(sealed.admission_id, "x", delivery_attempt=attempt)
        self.assertEqual(AdmittedInputState.EXPIRED, store.get(sealed.admission_id).state)


if __name__ == "__main__":
    unittest.main()
