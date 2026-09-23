#!/usr/bin/env python3
"""Hermetic proof for K10 (#109) operations.checkpoint: the pure identity,
monotonic-advancement, binding-validation and persistence seam ADR-0042
defines, independent of any A11 WebSocket/catalog composition.
"""

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import DatasetIdentity, Instant  # noqa: E402
from quant_platform.operations.checkpoint import (  # noqa: E402
    LIVE_CHECKPOINT_IDENTITY_DOMAIN,
    CheckpointBindingError,
    CheckpointCorruptError,
    CheckpointDomainMismatch,
    CheckpointError,
    CheckpointRegressionError,
    CheckpointStore,
    LiveCheckpointV1,
    advance_checkpoint,
    live_checkpoint_from_canonical_payload,
    validate_publication_binding,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
SEMANTICS = "bybit-public-trades-websocket-v1"


def checkpoint(
    *,
    trade_id: str = "a",
    exchange_ts: int = 1,
    generation: int = 1,
    revision: int = 1,
    partition_key: str = "dt=2026-09-24",
    manifest_sha: str | None = None,
    coverage_segment_id: str = "coverage-1",
    sequence: str | None = "1001",
) -> LiveCheckpointV1:
    return LiveCheckpointV1(
        dataset_identity=IDENTITY,
        source_semantics_id=SEMANTICS,
        last_canonical_exchange_ts=Instant(exchange_ts),
        last_canonical_trade_id=trade_id,
        last_observed_sequence=sequence,
        catalog_dataset_id="dataset-uuid-1",
        partition_key=partition_key,
        revision=revision,
        partition_manifest_sha256=(manifest_sha or "a" * 64),
        coverage_segment_id=coverage_segment_id,
        generation=generation,
        created_at=Instant(1_000_000_000),
    )


class LiveCheckpointV1Tests(unittest.TestCase):
    def test_checkpoint_identity_is_deterministic_for_equivalent_inputs(self):
        # Proof matrix item 15: repeated equivalent durable checkpoint
        # inputs -> deterministic equivalent checkpoint identity/state.
        a = checkpoint()
        b = checkpoint()
        self.assertEqual(a.checkpoint_identity, b.checkpoint_identity)
        self.assertTrue(a.checkpoint_identity.startswith(f"{LIVE_CHECKPOINT_IDENTITY_DOMAIN}:sha256:"))

    def test_checkpoint_identity_changes_with_any_bound_field(self):
        base = checkpoint()
        variants = [
            checkpoint(trade_id="b"),
            checkpoint(exchange_ts=2),
            checkpoint(generation=2, exchange_ts=2, trade_id="b"),
            checkpoint(revision=2),
            checkpoint(coverage_segment_id="coverage-2"),
        ]
        for variant in variants:
            self.assertNotEqual(base.checkpoint_identity, variant.checkpoint_identity)

    def test_last_observed_sequence_is_diagnostic_and_may_be_absent(self):
        # ADR-0040/ADR-0042: seq is never authoritative; a checkpoint must
        # still be constructible without it.
        cp = checkpoint(sequence=None)
        self.assertIsNone(cp.last_observed_sequence)

    def test_malformed_fields_are_rejected(self):
        with self.assertRaises(CheckpointError):
            LiveCheckpointV1(
                dataset_identity="not-an-identity", source_semantics_id=SEMANTICS,
                last_canonical_exchange_ts=Instant(1), last_canonical_trade_id="a",
                last_observed_sequence=None, catalog_dataset_id="d", partition_key="dt=x",
                revision=1, partition_manifest_sha256="a" * 64, coverage_segment_id="c",
                generation=1, created_at=Instant(1),
            )
        with self.assertRaises(CheckpointError):
            checkpoint(revision=0)
        with self.assertRaises(CheckpointError):
            checkpoint(generation=0)
        with self.assertRaises(CheckpointError):
            LiveCheckpointV1(
                dataset_identity=IDENTITY, source_semantics_id=SEMANTICS,
                last_canonical_exchange_ts=Instant(1), last_canonical_trade_id="a",
                last_observed_sequence=None, catalog_dataset_id="d", partition_key="dt=x",
                revision=1, partition_manifest_sha256="not-hex", coverage_segment_id="c",
                generation=1, created_at=Instant(1),
            )

    def test_canonical_payload_round_trips_through_reconstruction(self):
        cp = checkpoint()
        rebuilt = live_checkpoint_from_canonical_payload(cp.canonical_payload())
        self.assertEqual(cp.checkpoint_identity, rebuilt.checkpoint_identity)

    def test_reconstruction_rejects_wrong_identity_domain(self):
        payload = dict(checkpoint().canonical_payload())
        payload["identity_domain"] = "something-else-v1"
        with self.assertRaises(CheckpointCorruptError):
            live_checkpoint_from_canonical_payload(payload)

    def test_reconstruction_rejects_missing_or_malformed_fields(self):
        payload = dict(checkpoint().canonical_payload())
        del payload["revision"]
        with self.assertRaises(CheckpointCorruptError):
            live_checkpoint_from_canonical_payload(payload)


class AdvanceCheckpointTests(unittest.TestCase):
    def test_first_checkpoint_in_a_domain_must_be_generation_one(self):
        with self.assertRaises(CheckpointRegressionError):
            advance_checkpoint(None, checkpoint(generation=2))
        first = checkpoint(generation=1)
        self.assertIs(advance_checkpoint(None, first), first)

    def test_valid_advance_requires_strictly_later_key_and_next_generation(self):
        # Proof matrix item 4: failure after checkpoint advance -> monotonic resume.
        first = checkpoint(generation=1, exchange_ts=1, trade_id="a")
        second = checkpoint(generation=2, exchange_ts=2, trade_id="b")
        self.assertIs(advance_checkpoint(first, second), second)

    def test_regression_generation_skip_is_refused(self):
        # Proof matrix item 6: checkpoint regression -> explicit refusal.
        first = checkpoint(generation=1, exchange_ts=1, trade_id="a")
        skipped = checkpoint(generation=3, exchange_ts=2, trade_id="b")
        with self.assertRaises(CheckpointRegressionError):
            advance_checkpoint(first, skipped)

    def test_regression_same_or_earlier_canonical_key_is_refused(self):
        first = checkpoint(generation=1, exchange_ts=5, trade_id="m")
        earlier = checkpoint(generation=2, exchange_ts=3, trade_id="a")
        with self.assertRaises(CheckpointRegressionError):
            advance_checkpoint(first, earlier)
        same_key_new_generation = checkpoint(generation=2, exchange_ts=5, trade_id="m", coverage_segment_id="coverage-2")
        with self.assertRaises(CheckpointRegressionError):
            advance_checkpoint(first, same_key_new_generation)

    def test_domain_mismatch_is_refused(self):
        # Proof matrix item 7: DatasetIdentity/live-semantics mismatch -> explicit refusal.
        first = checkpoint(generation=1)
        other_identity = LiveCheckpointV1(
            dataset_identity=DatasetIdentity("canonical", "trades", "bybit", "ETHUSDT", "trade-v1"),
            source_semantics_id=SEMANTICS, last_canonical_exchange_ts=Instant(2),
            last_canonical_trade_id="b", last_observed_sequence=None,
            catalog_dataset_id="dataset-uuid-2", partition_key="dt=2026-09-24", revision=1,
            partition_manifest_sha256="b" * 64, coverage_segment_id="coverage-1",
            generation=2, created_at=Instant(2),
        )
        with self.assertRaises(CheckpointDomainMismatch):
            advance_checkpoint(first, other_identity)

        other_semantics = LiveCheckpointV1(
            dataset_identity=IDENTITY, source_semantics_id="a-different-live-semantics-v1",
            last_canonical_exchange_ts=Instant(2), last_canonical_trade_id="b",
            last_observed_sequence=None, catalog_dataset_id="dataset-uuid-1",
            partition_key="dt=2026-09-24", revision=1, partition_manifest_sha256="b" * 64,
            coverage_segment_id="coverage-1", generation=2, created_at=Instant(2),
        )
        with self.assertRaises(CheckpointDomainMismatch):
            advance_checkpoint(first, other_semantics)

    def test_repeated_equivalent_advance_is_idempotent(self):
        # Proof matrix item 15, via advance_checkpoint specifically: a
        # resubmit of the exact same checkpoint content is a no-op, not a
        # regression.
        first = checkpoint(generation=1)
        same = checkpoint(generation=1)
        self.assertIs(advance_checkpoint(first, same), first)


class PublicationBindingTests(unittest.TestCase):
    def test_matching_binding_passes(self):
        cp = checkpoint(revision=3, manifest_sha="c" * 64, partition_key="dt=2026-09-24")
        validate_publication_binding(
            cp, catalog_dataset_id="dataset-uuid-1", partition_key="dt=2026-09-24",
            revision=3, partition_manifest_sha256="c" * 64,
        )

    def test_stale_revision_is_refused(self):
        # Proof matrix item 5/8: checkpoint cannot advance beyond / reference
        # an incompatible durable publication generation.
        cp = checkpoint(revision=2, manifest_sha="c" * 64)
        with self.assertRaises(CheckpointBindingError):
            validate_publication_binding(
                cp, catalog_dataset_id="dataset-uuid-1", partition_key="dt=2026-09-24",
                revision=3, partition_manifest_sha256="c" * 64,
            )

    def test_manifest_hash_mismatch_is_refused(self):
        cp = checkpoint(revision=1, manifest_sha="c" * 64)
        with self.assertRaises(CheckpointBindingError):
            validate_publication_binding(
                cp, catalog_dataset_id="dataset-uuid-1", partition_key="dt=2026-09-24",
                revision=1, partition_manifest_sha256="d" * 64,
            )

    def test_dataset_id_mismatch_is_refused(self):
        cp = checkpoint()
        with self.assertRaises(CheckpointBindingError):
            validate_publication_binding(
                cp, catalog_dataset_id="a-different-dataset-uuid", partition_key="dt=2026-09-24",
                revision=1, partition_manifest_sha256="a" * 64,
            )


class CheckpointStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "live-checkpoint.json"

    def tearDown(self):
        self.tempdir.cleanup()

    def test_missing_file_loads_as_none(self):
        store = CheckpointStore(self.path)
        self.assertIsNone(store.load())

    def test_save_then_load_round_trips_exactly(self):
        store = CheckpointStore(self.path)
        cp = checkpoint()
        store.save(cp)
        loaded = store.load()
        self.assertEqual(loaded.checkpoint_identity, cp.checkpoint_identity)
        self.assertTrue(self.path.exists())
        # No leftover temp file from the atomic write-then-replace.
        leftovers = list(self.path.parent.glob(f"{self.path.name}.tmp-*"))
        self.assertEqual(leftovers, [])

    def test_advance_and_resave_reflects_new_generation(self):
        store = CheckpointStore(self.path)
        first = checkpoint(generation=1, exchange_ts=1, trade_id="a")
        store.save(first)
        second = checkpoint(generation=2, exchange_ts=2, trade_id="b")
        advanced = advance_checkpoint(store.load(), second)
        store.save(advanced)
        reloaded = store.load()
        self.assertEqual(reloaded.generation, 2)
        self.assertEqual(reloaded.last_canonical_trade_id, "b")

    def test_corrupt_json_fails_closed(self):
        # Proof matrix item 9: corrupt/malformed checkpoint -> fail closed.
        self.path.write_text("{not valid json", encoding="utf-8")
        store = CheckpointStore(self.path)
        with self.assertRaises(CheckpointCorruptError):
            store.load()

    def test_malformed_schema_fails_closed(self):
        self.path.write_text('{"identity_domain": "live-checkpoint-v1"}', encoding="utf-8")
        store = CheckpointStore(self.path)
        with self.assertRaises(CheckpointCorruptError):
            store.load()

    def test_unreadable_path_fails_closed_not_as_missing(self):
        # A directory where a file is expected: exists() is True, read fails.
        self.path.mkdir()
        store = CheckpointStore(self.path)
        with self.assertRaises(CheckpointCorruptError):
            store.load()


if __name__ == "__main__":
    unittest.main(verbosity=2)
