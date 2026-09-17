#!/usr/bin/env python3
"""A10 backfill/repair v1: pure intent/candidate identity and eligibility proof."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

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


def proof(
    *,
    dataset_document=None,
    partition_document=None,
    coverage_documents=None,
    assessment_status: str = "pass",
    eligibility_state: str = "valid",
) -> CandidateProof:
    return CandidateProof(
        dataset_document=dataset_document if dataset_document is not None else {"schema_version": "dataset-manifest-v1"},
        dataset_sha256=HASH_A,
        partition_document=partition_document if partition_document is not None else {"schema_version": "partition-manifest-v1"},
        partition_sha256=HASH_B,
        coverage_documents=coverage_documents if coverage_documents is not None else ({"schema_version": "coverage-manifest-v1"},),
        canonical_content_hash_v1=HASH_C,
        assessment_signature="sig-1",
        assessment_status=assessment_status,
        eligibility_state=eligibility_state,
        repair_code_ref="a10-repair-commit-1",
    )


class CandidateProofV1Tests(unittest.TestCase):
    def test_well_formed_proof_constructs(self):
        result = proof()
        self.assertEqual("pass", result.assessment_status)
        self.assertEqual("valid", result.eligibility_state)

    def test_empty_documents_are_refused(self):
        with self.assertRaises(RepairError):
            proof(dataset_document={})
        with self.assertRaises(RepairError):
            proof(partition_document={})
        with self.assertRaises(RepairError):
            proof(coverage_documents=())

    # Review finding: candidate proof must require A16 pass/warn, never fail.
    def test_fail_assessment_status_is_ineligible(self):
        with self.assertRaises(RepairIneligible):
            proof(assessment_status="fail")

    # Review finding: candidate proof must require S14 valid/degraded, never invalid/closed.
    def test_non_covering_eligibility_state_is_ineligible(self):
        with self.assertRaises(RepairIneligible):
            proof(eligibility_state="closed")
        with self.assertRaises(RepairIneligible):
            proof(eligibility_state="invalid")

    def test_malformed_hashes_are_refused(self):
        with self.assertRaises(RepairError):
            CandidateProof(
                dataset_document={"a": 1}, dataset_sha256="not-a-hash",
                partition_document={"a": 1}, partition_sha256=HASH_B,
                coverage_documents=({"a": 1},), canonical_content_hash_v1=HASH_C,
                assessment_signature="sig-1", assessment_status="pass",
                eligibility_state="valid", repair_code_ref="c",
            )


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
