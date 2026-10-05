from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.admitted_input import (  # noqa: E402
    AdmittedInputConflict,
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
        self.assertEqual(AdmittedInputState.DELIVERY_PENDING, store.begin_delivery(admitted.admission_id).state)

    def test_digest_mismatch_fails_closed_and_retries_same_immutable_admission(self) -> None:
        store = self.store()
        admitted = store.admit(manifest())
        sealed = store.seal(admitted.admission_id)
        store.begin_delivery(sealed.admission_id)

        failed = store.record_delivery(sealed.admission_id, "0" * 64)
        self.assertEqual(AdmittedInputState.DELIVERY_FAILED, failed.state)
        self.assertEqual("manifest_digest_mismatch", failed.delivery_failure_reason)
        self.assertEqual(sealed.manifest.canonical_payload_v1, failed.manifest.canonical_payload_v1)
        self.assertEqual(sealed.manifest.manifest_digest, failed.manifest.manifest_digest)

        retry = store.begin_delivery(sealed.admission_id)
        delivered = store.record_delivery(retry.admission_id, sealed.manifest.manifest_digest)
        self.assertEqual(sealed.admission_id, delivered.admission_id)
        self.assertEqual(AdmittedInputState.DELIVERED, delivered.state)

    def test_expired_admission_cannot_be_revived(self) -> None:
        store = self.store()
        admitted = store.admit(manifest())
        expired = store.expire(admitted.admission_id)

        self.assertEqual(AdmittedInputState.EXPIRED, expired.state)
        with self.assertRaises(AdmittedInputExpired):
            store.begin_delivery(admitted.admission_id)
        with self.assertRaises(AdmittedInputExpired):
            store.admit(manifest())

        renewed = store.admit(manifest(request="request-v1:beta"))
        self.assertNotEqual(expired.admission_id, renewed.admission_id)
        self.assertEqual(AdmittedInputState.ADMITTED, renewed.state)


if __name__ == "__main__":
    unittest.main()
