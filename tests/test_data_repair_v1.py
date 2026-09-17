#!/usr/bin/env python3
"""A10 backfill/repair v1: pure intent/candidate identity and eligibility proof."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.manifests import (  # noqa: E402
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
)
from quant_platform.data.materializer import ParquetMaterialization  # noqa: E402
from quant_platform.data.models import CoverageInterval, DatasetIdentity, Instant  # noqa: E402
from quant_platform.data.repair import (  # noqa: E402
    CandidateAttempt,
    CandidateProof,
    CoverageGapTrigger,
    InvalidLiveRevisionTrigger,
    PredecessorReference,
    RepairError,
    RepairIneligible,
    RepairIntent,
    RepairOutcome,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
REQUIRED = CoverageInterval(Instant.parse("2024-01-15T00:00:00Z"), Instant.parse("2024-01-16T00:00:00Z"))
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def gap(start: str, end: str) -> CoverageInterval:
    return CoverageInterval(Instant.parse(start), Instant.parse(end))


def predecessor(state: str = "valid", revision: int = 1, partition_id: str = "pred-1") -> PredecessorReference:
    return PredecessorReference(partition_id=partition_id, revision=revision, state=state)


def candidate(
    *,
    intent_identity: str = "x",
    content_sha256: str = HASH_C,
    natural_partition_key: str = "dt=2024-01-15",
    code_ref: str = "repair-commit-1",
) -> CandidateAttempt:
    return CandidateAttempt(
        intent_identity=intent_identity,
        dataset_identity=IDENTITY,
        natural_partition_key=natural_partition_key,
        source_semantics_id="bybit-public-trades-sqlite-v1",
        mapping_id="bybit-sqlite-day-extract-v1",
        dataset_sha256=HASH_A,
        partition_sha256=HASH_B,
        content_sha256=content_sha256,
        code_ref=code_ref,
    )


class CoverageGapTriggerV1Tests(unittest.TestCase):
    # 2. event silence with declared coverage -> no trigger
    def test_empty_gaps_is_ineligible(self):
        with self.assertRaises(RepairIneligible):
            CoverageGapTrigger(gaps=())

    def test_degenerate_gap_is_ineligible(self):
        point = Instant.parse("2024-01-15T12:00:00Z")
        with self.assertRaises(RepairIneligible):
            CoverageGapTrigger(gaps=(CoverageInterval(point, point),))

    def test_overlapping_gaps_are_refused(self):
        with self.assertRaises(RepairError):
            CoverageGapTrigger(
                gaps=(
                    gap("2024-01-15T00:00:00Z", "2024-01-15T02:00:00Z"),
                    gap("2024-01-15T01:00:00Z", "2024-01-15T03:00:00Z"),
                )
            )

    def test_gaps_are_canonically_ordered(self):
        trigger = CoverageGapTrigger(
            gaps=(
                gap("2024-01-15T10:00:00Z", "2024-01-15T12:00:00Z"),
                gap("2024-01-15T01:00:00Z", "2024-01-15T02:00:00Z"),
            )
        )
        self.assertEqual(trigger.gaps[0].start, Instant.parse("2024-01-15T01:00:00Z"))


class InvalidLiveRevisionTriggerV1Tests(unittest.TestCase):
    # 3. invalid live revision -> replacement eligible
    def test_invalid_predecessor_with_fail_status_is_eligible(self):
        trigger = InvalidLiveRevisionTrigger(
            predecessor=predecessor(state="invalid"),
            assessment_signature="sig-1",
            assessment_status="fail",
        )
        self.assertEqual("invalid", trigger.predecessor.state)

    # 4. accepted degraded only -> no automatic trigger
    def test_degraded_predecessor_is_ineligible(self):
        with self.assertRaises(RepairIneligible):
            InvalidLiveRevisionTrigger(
                predecessor=predecessor(state="degraded"),
                assessment_signature="sig-1",
                assessment_status="fail",
            )

    def test_non_fail_assessment_status_is_ineligible(self):
        with self.assertRaises(RepairIneligible):
            InvalidLiveRevisionTrigger(
                predecessor=predecessor(state="invalid"),
                assessment_signature="sig-1",
                assessment_status="warn",
            )


class RepairIntentV1Tests(unittest.TestCase):
    # 1. exact internal gap -> repair intent
    def test_internal_gap_builds_a_repair_intent(self):
        trigger = CoverageGapTrigger(gaps=(gap("2024-01-15T10:00:00Z", "2024-01-15T11:00:00Z"),))
        intent = RepairIntent(
            dataset_identity=IDENTITY,
            partition_key="dt=2024-01-15",
            required_support=REQUIRED,
            predecessor=predecessor(state="valid"),
            trigger=trigger,
        )
        self.assertEqual(RepairOutcome.REPAIR_REQUIRED, intent.outcome)
        self.assertTrue(intent.intent_identity.startswith("a10-repair-intent-v1:sha256:"))

    # 23. multi-partition B04 gaps project to independent partition repair intents
    def test_multi_partition_gaps_are_independent_intents(self):
        trigger_a = CoverageGapTrigger(gaps=(gap("2024-01-15T10:00:00Z", "2024-01-15T11:00:00Z"),))
        trigger_b = CoverageGapTrigger(gaps=(gap("2024-01-16T10:00:00Z", "2024-01-16T11:00:00Z"),))
        required_b = CoverageInterval(Instant.parse("2024-01-16T00:00:00Z"), Instant.parse("2024-01-17T00:00:00Z"))
        intent_a = RepairIntent(
            dataset_identity=IDENTITY, partition_key="dt=2024-01-15", required_support=REQUIRED,
            predecessor=None, trigger=trigger_a,
        )
        intent_b = RepairIntent(
            dataset_identity=IDENTITY, partition_key="dt=2024-01-16", required_support=required_b,
            predecessor=None, trigger=trigger_b,
        )
        self.assertNotEqual(intent_a.intent_identity, intent_b.intent_identity)
        self.assertEqual("dt=2024-01-15", intent_a.partition_key)
        self.assertEqual("dt=2024-01-16", intent_b.partition_key)

    def test_gap_outside_required_support_is_refused(self):
        trigger = CoverageGapTrigger(gaps=(gap("2024-01-16T00:00:00Z", "2024-01-16T01:00:00Z"),))
        with self.assertRaises(RepairError):
            RepairIntent(
                dataset_identity=IDENTITY, partition_key="dt=2024-01-15", required_support=REQUIRED,
                predecessor=None, trigger=trigger,
            )

    def test_superseded_predecessor_is_refused(self):
        trigger = CoverageGapTrigger(gaps=(gap("2024-01-15T10:00:00Z", "2024-01-15T11:00:00Z"),))
        with self.assertRaises(RepairError):
            RepairIntent(
                dataset_identity=IDENTITY, partition_key="dt=2024-01-15", required_support=REQUIRED,
                predecessor=predecessor(state="superseded"), trigger=trigger,
            )

    def test_invalid_live_revision_trigger_predecessor_must_equal_intents_predecessor(self):
        trigger = InvalidLiveRevisionTrigger(
            predecessor=predecessor(state="invalid", partition_id="pred-x"),
            assessment_signature="sig-1", assessment_status="fail",
        )
        with self.assertRaises(RepairError):
            RepairIntent(
                dataset_identity=IDENTITY, partition_key="dt=2024-01-15", required_support=REQUIRED,
                predecessor=predecessor(state="invalid", partition_id="pred-y"), trigger=trigger,
            )

    def test_backfill_without_predecessor_is_allowed(self):
        trigger = CoverageGapTrigger(gaps=(REQUIRED,))
        intent = RepairIntent(
            dataset_identity=IDENTITY, partition_key="dt=2024-01-15", required_support=REQUIRED,
            predecessor=None, trigger=trigger,
        )
        self.assertIsNone(intent.predecessor)

    def test_identical_inputs_produce_identical_intent_identity(self):
        trigger = CoverageGapTrigger(gaps=(gap("2024-01-15T10:00:00Z", "2024-01-15T11:00:00Z"),))
        first = RepairIntent(
            dataset_identity=IDENTITY, partition_key="dt=2024-01-15", required_support=REQUIRED,
            predecessor=predecessor(), trigger=trigger,
        )
        second = RepairIntent(
            dataset_identity=IDENTITY, partition_key="dt=2024-01-15", required_support=REQUIRED,
            predecessor=predecessor(), trigger=trigger,
        )
        self.assertEqual(first.intent_identity, second.intent_identity)

    # 17. old trigger/predecessor changes -> stale conflict, no silent retarget
    def test_different_predecessor_changes_intent_identity(self):
        trigger = InvalidLiveRevisionTrigger(
            predecessor=predecessor(state="invalid", revision=1),
            assessment_signature="sig-1", assessment_status="fail",
        )
        intent_1 = RepairIntent(
            dataset_identity=IDENTITY, partition_key="dt=2024-01-15", required_support=REQUIRED,
            predecessor=predecessor(state="invalid", revision=1), trigger=trigger,
        )
        trigger_2 = InvalidLiveRevisionTrigger(
            predecessor=predecessor(state="invalid", revision=2, partition_id="pred-2"),
            assessment_signature="sig-2", assessment_status="fail",
        )
        intent_2 = RepairIntent(
            dataset_identity=IDENTITY, partition_key="dt=2024-01-15", required_support=REQUIRED,
            predecessor=predecessor(state="invalid", revision=2, partition_id="pred-2"), trigger=trigger_2,
        )
        self.assertNotEqual(intent_1.intent_identity, intent_2.intent_identity)


class CandidateAttemptV1Tests(unittest.TestCase):
    # 10. identical retry -> idempotent candidate
    def test_identical_evidence_is_the_same_candidate(self):
        first = candidate()
        second = candidate()
        self.assertEqual(first.candidate_identity, second.candidate_identity)
        self.assertEqual(first.staging_partition_key, second.staging_partition_key)

    # 11. different retry output -> distinct candidate
    def test_different_content_is_a_distinct_candidate(self):
        first = candidate(content_sha256=HASH_C)
        second = candidate(content_sha256="d" * 64)
        self.assertNotEqual(first.candidate_identity, second.candidate_identity)
        self.assertNotEqual(first.staging_partition_key, second.staging_partition_key)

    # 22. two candidate attempts targeting the same nominal revision use
    # isolated staging and cannot overwrite each other: distinct staging keys
    # are distinct catalog rows and distinct rel_path prefixes by construction.
    def test_distinct_candidates_never_share_a_staging_partition_key(self):
        attempts = [candidate(content_sha256=format(n, "064x")) for n in range(1, 6)]
        keys = {attempt.staging_partition_key for attempt in attempts}
        self.assertEqual(len(attempts), len(keys))
        for attempt in attempts:
            self.assertTrue(attempt.staging_partition_key.startswith("dt=2024-01-15/repair="))
            self.assertNotEqual(attempt.staging_partition_key, attempt.natural_partition_key)

    def test_staging_partition_key_matches_the_frozen_v1_grammar(self):
        import re

        pattern = re.compile(r"^[a-z_]+=[A-Za-z0-9._-]+(?:/[a-z_]+=[A-Za-z0-9._-]+)*$")
        self.assertRegex(candidate().staging_partition_key, pattern)

    def test_malformed_evidence_is_refused(self):
        with self.assertRaises(RepairError):
            candidate(content_sha256="not-a-hash")
        with self.assertRaises(RepairError):
            CandidateAttempt(
                intent_identity="", dataset_identity=IDENTITY, natural_partition_key="dt=2024-01-15",
                source_semantics_id="s", mapping_id="m", dataset_sha256=HASH_A,
                partition_sha256=HASH_B, content_sha256=HASH_C, code_ref="c",
            )


def _real_documents(dataset_root: Path, *, staging_key: str = "dt=2024-01-15/repair=proof-fixture"):
    """Build genuinely valid, hash-consistent dataset/partition/coverage
    documents through the REAL credited manifest machinery -- CandidateProof
    now recomputes and cross-verifies these hashes and schema-validates
    every document, so a hand-typed stub document can no longer construct
    one.
    """

    dataset = emit_dataset_manifest(
        dataset_root / "dataset-manifest.json", dataset_identity=IDENTITY, created_at="2026-08-31T10:00:00Z",
        derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
        transform="canonicalize-trades-v1",
    )
    content = b"proof-fixture-payload-1"
    content_sha256 = hashlib.sha256(content).hexdigest()
    rel_path = f"{staging_key}/part-001.parquet"
    artifact_path = dataset_root / rel_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(content)
    materialization = ParquetMaterialization(
        path=artifact_path, dataset_identity=IDENTITY, file_size_bytes=len(content), row_count=1,
        sha256=content_sha256, canonical_content_hash_v1="d" * 64,
        first_exchange_ts=Instant.parse("2024-01-15T06:00:00Z"), last_exchange_ts=Instant.parse("2024-01-15T06:00:00Z"),
    )
    partition = emit_partition_manifest(
        dataset_root / "partition-manifest.json", materialization, dataset_identity=IDENTITY,
        dataset_root=dataset_root, partition_key=staging_key, revision=1, rel_path=rel_path,
        created_at="2026-08-31T10:00:00Z", closed_at="2026-08-31T10:05:00Z",
        producer="test-repair-producer", code_ref="repair-commit-1",
    )
    coverage = emit_coverage_manifest(
        dataset_root / "coverage-manifest.json", dataset_identity=IDENTITY, coverage_id="proof-fixture-coverage",
        supersedes=None, created_at="2026-08-31T10:00:00Z",
        acquisition={
            "basis": "reconciliation", "intent_start": "2024-01-15T00:00:00Z", "intent_end": "2024-01-16T00:00:00Z",
            "source_semantics": "bybit-public-trades-sqlite-v1", "mapping": "bybit-sqlite-day-extract-v1",
        },
        assertions=[{
            "assertion_id": "proof-fixture-complete", "start": "2024-01-15T00:00:00Z", "end": "2024-01-16T00:00:00Z",
            "status": "complete", "partitions": [{"partition_key": staging_key, "revision": 1}],
            "evidence": [{"kind": "reconciliation", "detail": "proof fixture"}],
        }],
        producer="test-repair-source", code_ref="a10-repair-commit-1", source_dataset_identity=IDENTITY,
        partition_manifests=[partition.document],
    )
    return dataset, partition, coverage


def proof(
    dataset_root: Path,
    *,
    dataset_document=None,
    dataset_sha256=None,
    partition_document=None,
    partition_sha256=None,
    coverage_documents=None,
    assessment_status: str = "pass",
    eligibility_state: str = "valid",
) -> CandidateProof:
    dataset, partition, coverage = _real_documents(dataset_root)
    return CandidateProof(
        dataset_document=dataset_document if dataset_document is not None else dataset.document,
        dataset_sha256=dataset_sha256 if dataset_sha256 is not None else dataset.manifest_sha256,
        partition_document=partition_document if partition_document is not None else partition.document,
        partition_sha256=partition_sha256 if partition_sha256 is not None else partition.manifest_sha256,
        coverage_documents=coverage_documents if coverage_documents is not None else (coverage.document,),
        canonical_content_hash_v1="d" * 64,
        assessment_signature="sig-1",
        assessment_status=assessment_status,
        eligibility_state=eligibility_state,
        repair_code_ref="a10-repair-commit-1",
        expected_profile="test-repair-profile",
        expected_check_suite="test-repair-suite",
    )


class CandidateProofV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.dataset_root = Path(self._tempdir.name)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def test_well_formed_proof_constructs(self):
        result = proof(self.dataset_root)
        self.assertEqual("pass", result.assessment_status)
        self.assertEqual("valid", result.eligibility_state)

    def test_empty_documents_are_refused(self):
        with self.assertRaises(RepairError):
            proof(self.dataset_root, dataset_document={})
        with self.assertRaises(RepairError):
            proof(self.dataset_root, partition_document={})
        with self.assertRaises(RepairError):
            proof(self.dataset_root, coverage_documents=())

    # Review finding: candidate proof must require A16 pass/warn, never fail.
    def test_fail_assessment_status_is_ineligible(self):
        with self.assertRaises(RepairIneligible):
            proof(self.dataset_root, assessment_status="fail")

    # Review finding: candidate proof must require S14 valid/degraded, never invalid/closed.
    def test_non_covering_eligibility_state_is_ineligible(self):
        with self.assertRaises(RepairIneligible):
            proof(self.dataset_root, eligibility_state="closed")
        with self.assertRaises(RepairIneligible):
            proof(self.dataset_root, eligibility_state="invalid")

    def test_malformed_hashes_are_refused(self):
        with self.assertRaises(RepairError):
            proof(self.dataset_root, dataset_sha256="not-a-hash")

    # Review finding 1: document hashes are recomputed and cross-verified,
    # never merely format-checked -- a well-formed hex hash for the WRONG
    # document is refused just as loudly as a malformed one.
    def test_wrong_but_well_formed_hash_is_refused(self):
        with self.assertRaises(RepairError):
            proof(self.dataset_root, dataset_sha256="f" * 64)
        with self.assertRaises(RepairError):
            proof(self.dataset_root, partition_sha256="f" * 64)

    # Review finding 1: documents must be schema-valid durable manifests,
    # not arbitrary dicts -- a plausible-looking but malformed document is
    # refused even when its own declared hash is self-consistent.
    def test_malformed_but_hash_consistent_document_is_refused(self):
        from quant_platform.data.repair import _canonical_fingerprint

        forged = {"not": "a manifest"}
        with self.assertRaises(RepairError):
            proof(self.dataset_root, dataset_document=forged, dataset_sha256=_canonical_fingerprint(forged))


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
