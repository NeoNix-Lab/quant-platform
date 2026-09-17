#!/usr/bin/env python3
"""Real PostgreSQL proof for the A10 atomic compare-and-cutover seam.

Proves: successful atomic replacement cutover, transaction rollback on a
failure injected mid-cutover, a TRUE concurrent race between two candidate
attempts on separate connections/threads (real row-lock serialization, not a
sequential simulation), one-live preservation throughout, idempotent
ALREADY_SATISFIED retry, physically isolated candidate staging, a
coverage-triggered missing-support backfill, partial-gap refusal, and
quality-pass-with-gap-remaining refusal.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import psycopg  # noqa: E402
from quant_platform.data.models import (  # noqa: E402
    CoverageInterval, DatasetIdentity, Instant, NaturalPartitionIdentity,
)
from quant_platform.data.publication_catalog import CatalogPublicationWriter  # noqa: E402
from quant_platform.data.quality_lifecycle import QualityLifecycleCatalog  # noqa: E402
from quant_platform.data.repair import (  # noqa: E402
    ALREADY_SATISFIED,
    CandidateAttempt,
    CandidateStaging,
    CandidateStagingConflict,
    CONVERGED,
    CoverageGapTrigger,
    FAILED,
    InvalidRevisionTrigger,
    PredecessorEvidence,
    PredecessorRef,
    RepairCutoverCatalog,
    RepairIntent,
    STALE_CONFLICT,
    compute_coverage_evidence_id,
    compute_quality_evidence_id,
)

IDENTITY = DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")
DATASET = {
    "layer": "raw", "dataset_kind": "trades", "venue": "bybit", "instrument": "BTCUSDT",
    "record_schema_id": "trade-v1", "rel_root": "raw/trades/bybit/BTCUSDT/trade-v1",
    "_manifest_sha256": "d" * 64,
}
CHECK_SUITE = "a10-repair-integration-v1"
PROFILE = "a10-repair-integration-profile-v1"
START = Instant.parse("2024-01-15T00:00:00Z")
END = Instant.parse("2024-01-16T00:00:00Z")
REQUIRED = CoverageInterval(START, END)
COVERAGE_IDS = ["a10-coverage-1"]
ASSERTION_IDS = ["a10-assertion-1"]
COVERAGE_SHA = ["e" * 64]


def partition_dict(revision: int, token: str, *, partition_key: str = "dt=2024-01-15") -> dict:
    return {
        "partition_key": partition_key,
        "revision": revision,
        "storage_root_id": "a10-hot",
        "rel_path": f"{partition_key}/part-{revision:03d}-{token}.parquet",
        "row_count": 1,
        "file_size_bytes": 100 + revision,
        "sha256": token * 64,
        # Lowercase, matching `token` exactly: an uppercased variant here
        # previously diverged from CandidateAttempt's lowercase-normalized
        # partition_manifest_sha256 for alphabetic hex tokens, causing A16
        # manifest verification to fail spuriously (review finding).
        "_manifest_sha256": token * 64,
        "state": "closed",
        "created_at": "2026-09-01T10:00:00Z",
        "closed_at": "2026-09-01T10:00:01Z",
        "first_sequence": None,
        "last_sequence": None,
        "producer": "a10-repair-producer",
        "code_ref": f"a10-repair-commit-{token}",
    }


def quality_metrics(
    partition: dict, *, status: str,
    coverage_ids: list[str] = COVERAGE_IDS, assertion_ids: list[str] = ASSERTION_IDS, coverage_sha: list[str] = COVERAGE_SHA,
) -> dict:
    metrics = {
        "certification_profile": PROFILE,
        "natural_partition_identity": {
            "dataset_identity": IDENTITY.stable_dict(),
            "partition_key": partition["partition_key"],
            "revision": partition["revision"],
        },
        "dataset_manifest_sha256": DATASET["_manifest_sha256"],
        "partition_manifest_sha256": partition["_manifest_sha256"],
        "coverage_manifest_id": coverage_ids[0],
        "coverage_assertion_id": assertion_ids[0],
        "coverage_manifest_ids": coverage_ids,
        "coverage_assertion_ids": assertion_ids,
        "coverage_manifest_sha256": coverage_sha,
        "physical_artifact_hash": partition["sha256"],
        "canonical_content_hash_v1": "c" * 64,
        "evidence": {name: {"status": "pass"} for name in ("source", "canonical", "physical", "manifests", "coverage")},
    }
    if status == "fail":
        metrics["evidence"]["source"] = {"status": "fail"}
    return metrics


def report_for(partition: dict, *, status: str, certifier_tag: str) -> dict:
    return {
        "status": status,
        "metrics": quality_metrics(partition, status=status),
        "violations": [] if status == "pass" else [{"category": "source", "message": "controlled failure"}],
        "code_ref": f"a10-repair-certifier-{certifier_tag}",
    }


def candidate_for(
    intent: RepairIntent, partition: dict, report: dict, *, source_tag: str,
    coverage_start: Instant = START, coverage_end: Instant = END,
) -> CandidateAttempt:
    return CandidateAttempt(
        repair_intent_id=intent.intent_id,
        natural_identity=NaturalPartitionIdentity(IDENTITY, partition["partition_key"], partition["revision"]),
        content_sha256=partition["sha256"],
        partition_manifest_sha256=partition["_manifest_sha256"],
        source_evidence_id=f"a10-source-{source_tag}",
        materialization_id=f"a10-materialization-{source_tag}",
        coverage_evidence_id=compute_coverage_evidence_id(
            COVERAGE_IDS, ASSERTION_IDS, COVERAGE_SHA, coverage_start=coverage_start, coverage_end=coverage_end,
        ),
        quality_evidence_id=compute_quality_evidence_id(report, check_suite=CHECK_SUITE, expected_profile=PROFILE),
        code_ref=partition["code_ref"],
    )


def coverage_manifest_for(
    *, coverage_id: str, partition_key: str, revision: int, start: Instant, end: Instant,
    supersedes: str | None = None,
) -> dict:
    """One durable coverage-manifest document, shaped exactly as
    ``quant_platform.data.coverage.reconstruct_catalog_coverage`` (the SAME
    authoritative B04 fold S13/S14 use) expects: identity fields plus one
    'complete' assertion attributing ``[start, end)`` to the target
    partition_key/revision."""

    return {
        **IDENTITY.stable_dict(),
        "coverage_id": coverage_id,
        "supersedes": supersedes,
        "assertions": [{
            "assertion_id": f"{coverage_id}-assertion",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "status": "complete",
            "partitions": [{"partition_key": partition_key, "revision": revision}],
        }],
    }


def do_cutover(
    connection, *, intent: RepairIntent, candidate: CandidateAttempt, partition: dict, report: dict,
    coverage_start: Instant = START, coverage_end: Instant = END,
    predecessor_evidence: PredecessorEvidence | None = None,
    coverage_manifests: list[dict] | None = None,
) -> object:
    return RepairCutoverCatalog(connection).cutover(
        repair_intent=intent,
        candidate=candidate,
        dataset=DATASET,
        dataset_sha256=DATASET["_manifest_sha256"],
        candidate_partition=partition,
        candidate_quality_report=report,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        coverage_ids=COVERAGE_IDS,
        assertion_ids=ASSERTION_IDS,
        coverage_sha256=COVERAGE_SHA,
        storage_root_id="a10-hot",
        expected_profile=PROFILE,
        expected_check_suite=CHECK_SUITE,
        predecessor_evidence=predecessor_evidence,
        coverage_manifests=coverage_manifests,
    )


class FailAfterInsert:
    """Wraps a connection so the candidate INSERT succeeds but a later
    statement fails before commit, proving rollback undoes the whole cutover
    -- including the predecessor's uncommitted supersession."""

    def __init__(self, connection):
        self.connection = connection

    def cursor(self):
        return _FailCursor(self.connection.cursor())

    def commit(self):
        return self.connection.commit()

    def rollback(self):
        return self.connection.rollback()


class _FailCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        self.cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self.cursor.__exit__(*args)

    def execute(self, statement, params=None):
        self.cursor.execute(statement, params)
        if "INSERT INTO catalog.quality_reports" in statement:
            self.cursor.execute("SELECT 1 / 0")

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


def _live_state(connection, partition_id) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT state FROM catalog.partitions WHERE partition_id=%s", (partition_id,))
        return cursor.fetchone()[0]


def _one_live_count(connection, dataset_id, partition_key) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM catalog.partitions WHERE dataset_id=%s AND partition_key=%s AND state <> 'superseded'",
            (dataset_id, partition_key),
        )
        return cursor.fetchone()[0]


def _row_count(connection, dataset_id, partition_key) -> int:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM catalog.partitions WHERE dataset_id=%s AND partition_key=%s",
            (dataset_id, partition_key),
        )
        return cursor.fetchone()[0]


def _dataset_id(connection) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT dataset_id::text FROM catalog.datasets WHERE layer=%s AND kind=%s AND venue=%s AND instrument=%s AND schema_id=%s",
            (IDENTITY.layer, IDENTITY.dataset_kind, IDENTITY.venue, IDENTITY.instrument, IDENTITY.record_schema_id),
        )
        return cursor.fetchone()[0]


def _establish_invalid_predecessor(connection, writer, *, partition_key: str, token: str) -> tuple:
    """Admit and certify-fail one revision-1 partition, then drive it to
    ``invalid`` via A16, returning (sealed, lifecycle_result, predecessor_evidence)."""

    predecessor = partition_dict(1, token, partition_key=partition_key)
    sealed = writer.seal_partition(
        dataset=DATASET, partition=predecessor, coverage_start=START, coverage_end=END,
        storage_root_id="a10-hot",
    )
    writer.commit()
    writer.record_quality_report(
        partition_id=sealed.partition_id, check_suite=CHECK_SUITE, status="fail",
        metrics=quality_metrics(predecessor, status="fail"),
        violations=[{"category": "source", "message": "predecessor is bad"}],
        code_ref="a10-repair-certifier-predecessor",
    )
    writer.commit()
    lifecycle_result = QualityLifecycleCatalog(connection).apply_partition_lifecycle(
        dataset=DATASET, dataset_sha256=DATASET["_manifest_sha256"],
        partition=predecessor, partition_sha256=predecessor["_manifest_sha256"],
        coverage_start=START, coverage_end=END,
        coverage_ids=COVERAGE_IDS, assertion_ids=ASSERTION_IDS, coverage_sha256=COVERAGE_SHA,
        storage_root_id="a10-hot", expected_profile=PROFILE, expected_check_suite=CHECK_SUITE,
    )
    assert lifecycle_result.resulting_state == "invalid", lifecycle_result.resulting_state
    assert _live_state(connection, sealed.partition_id) == "invalid"
    evidence = PredecessorEvidence(
        partition=predecessor, partition_sha256=predecessor["_manifest_sha256"],
        coverage_start=START, coverage_end=END,
        coverage_ids=COVERAGE_IDS, assertion_ids=ASSERTION_IDS, coverage_sha256=COVERAGE_SHA,
    )
    return sealed, lifecycle_result, evidence


def _run_replacement_and_coverage_scenarios(connection, writer, dsn: str) -> None:
    sealed, lifecycle_result, predecessor_evidence = _establish_invalid_predecessor(
        connection, writer, partition_key="dt=2024-01-15", token="1",
    )
    dataset_id = _dataset_id(connection)
    predecessor_ref = PredecessorRef(revision=1, content_sha256=predecessor_evidence.partition["sha256"], state="invalid")
    trigger = InvalidRevisionTrigger(
        predecessor=predecessor_ref,
        assessment_signature=lifecycle_result.assessment_signature,
        assessment_status="fail",
    )
    intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)

    # === Vector: cutover transaction failure leaves predecessor authoritative,
    # no half-switch (frozen contract section 9-10; adversarial vector 9). ===
    failing_partition = partition_dict(2, "2")
    failing_report = report_for(failing_partition, status="pass", certifier_tag="injected-failure")
    failing_candidate = candidate_for(intent, failing_partition, failing_report, source_tag="injected-failure")
    failing_connection = FailAfterInsert(connection)
    try:
        do_cutover(
            failing_connection, intent=intent, candidate=failing_candidate, partition=failing_partition,
            report=failing_report, predecessor_evidence=predecessor_evidence,
        )
    except Exception:
        pass
    else:
        raise AssertionError("injected mid-cutover DB failure was not raised")
    assert _live_state(connection, sealed.partition_id) == "invalid", "predecessor must remain authoritative after rollback"
    assert _one_live_count(connection, dataset_id, "dt=2024-01-15") == 1
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM catalog.partitions WHERE dataset_id=%s AND partition_key=%s AND revision=2",
            (dataset_id, "dt=2024-01-15"),
        )
        assert cursor.fetchone()[0] == 0, "the failed candidate row must not survive rollback"

    # === Vector: candidate quality fails -> predecessor unchanged
    # (adversarial vector 7). ===
    failing_quality_partition = partition_dict(2, "3")
    failing_quality_report = report_for(failing_quality_partition, status="fail", certifier_tag="failing-quality")
    failing_quality_candidate = candidate_for(intent, failing_quality_partition, failing_quality_report, source_tag="failing-quality")
    outcome = do_cutover(
        connection, intent=intent, candidate=failing_quality_candidate, partition=failing_quality_partition,
        report=failing_quality_report, predecessor_evidence=predecessor_evidence,
    )
    assert outcome.outcome == FAILED, outcome
    assert _live_state(connection, sealed.partition_id) == "invalid"
    assert _one_live_count(connection, dataset_id, "dt=2024-01-15") == 1

    # === Vector: candidate acceptance inputs are bound to candidate identity --
    # a mismatched report/coverage bundle for an otherwise-valid candidate must
    # be refused before any mutation (review finding). ===
    bound_partition = partition_dict(2, "4")
    bound_report = report_for(bound_partition, status="pass", certifier_tag="bound")
    bound_candidate = candidate_for(intent, bound_partition, bound_report, source_tag="bound")
    tampered_report = dict(bound_report, code_ref="a10-repair-certifier-tampered")
    try:
        do_cutover(
            connection, intent=intent, candidate=bound_candidate, partition=bound_partition,
            report=tampered_report, predecessor_evidence=predecessor_evidence,
        )
    except Exception:
        pass
    else:
        raise AssertionError("a report not matching the candidate's bound quality_evidence_id was accepted")
    assert _live_state(connection, sealed.partition_id) == "invalid"

    # === Vector: proven replacement -> atomic sole-live cutover
    # (adversarial vector 3, 8). Staging isolation proof alongside: two
    # DISTINCT candidates for the same nominal revision use physically
    # isolated evidence directories that cannot clobber each other. ===
    with tempfile.TemporaryDirectory() as staging_root:
        staging = CandidateStaging(Path(staging_root))
        winner_partition = partition_dict(2, "5")
        loser_partition = partition_dict(2, "6")
        winner_report = report_for(winner_partition, status="pass", certifier_tag="winner")
        loser_report = report_for(loser_partition, status="pass", certifier_tag="loser")
        winner_candidate = candidate_for(intent, winner_partition, winner_report, source_tag="winner")
        loser_candidate = candidate_for(intent, loser_partition, loser_report, source_tag="loser")

        winner_dir = staging.directory_for(winner_candidate)
        loser_dir = staging.directory_for(loser_candidate)
        assert winner_dir != loser_dir
        staging.write_evidence(winner_candidate, "partition.json", b"winner-bytes")
        staging.write_evidence(loser_candidate, "partition.json", b"loser-bytes")
        assert (winner_dir / "partition.json").read_bytes() == b"winner-bytes"
        assert (loser_dir / "partition.json").read_bytes() == b"loser-bytes"
        try:
            staging.write_evidence(winner_candidate, "partition.json", b"tampered-bytes")
        except CandidateStagingConflict:
            pass
        else:
            raise AssertionError("differing content for the same candidate identity was accepted")
        try:
            staging.write_evidence(winner_candidate, "../escape.json", b"payload")
        except Exception:
            pass
        else:
            raise AssertionError("a path-traversal evidence name was accepted")

        converged = do_cutover(
            connection, intent=intent, candidate=winner_candidate, partition=winner_partition,
            report=winner_report, predecessor_evidence=predecessor_evidence,
        )
        assert converged.outcome == CONVERGED, converged
        assert converged.provenance.publication_state in {"valid", "degraded"}
        assert _live_state(connection, sealed.partition_id) == "superseded"
        assert _one_live_count(connection, dataset_id, "dt=2024-01-15") == 1
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT state FROM catalog.partitions WHERE partition_id=%s", (converged.provenance.final_partition_id,),
            )
            assert cursor.fetchone()[0] == "valid"
            cursor.execute(
                "SELECT repair_intent_id, candidate_id FROM catalog.repair_convergence WHERE partition_id=%s",
                (converged.provenance.final_partition_id,),
            )
            recorded = cursor.fetchone()
            assert recorded == (intent.intent_id, winner_candidate.candidate_id), recorded

        # A fresh connection must see the same committed state (no
        # phantom uncommitted mutation from this process alone).
        with psycopg.connect(dsn) as fresh:
            assert _live_state(fresh, sealed.partition_id) == "superseded"
            assert _live_state(fresh, converged.provenance.final_partition_id) == "valid"

        # === Vector: identical retry is idempotent -> ALREADY_SATISFIED,
        # no gratuitous new revision (adversarial vector 10, 13). ===
        retry = do_cutover(
            connection, intent=intent, candidate=winner_candidate, partition=winner_partition,
            report=winner_report, predecessor_evidence=predecessor_evidence,
        )
        assert retry.outcome == ALREADY_SATISFIED, retry
        assert _one_live_count(connection, dataset_id, "dt=2024-01-15") == 1
        assert _row_count(connection, dataset_id, "dt=2024-01-15") == 2, "retry must not create a gratuitous new revision"

        # === Vector: a distinct (loser) candidate racing for the same now-stale
        # predecessor is STALE_CONFLICT, never silently wins by insertion/
        # timestamp order, and never reactivates the superseded predecessor
        # (adversarial vector 11, 12, 17, 20). ===
        loser_outcome = do_cutover(
            connection, intent=intent, candidate=loser_candidate, partition=loser_partition,
            report=loser_report, predecessor_evidence=predecessor_evidence,
        )
        assert loser_outcome.outcome == STALE_CONFLICT, loser_outcome
        assert _live_state(connection, sealed.partition_id) == "superseded", "superseded predecessor must never reactivate"
        assert _one_live_count(connection, dataset_id, "dt=2024-01-15") == 1

    with connection.cursor() as cursor:
        cursor.execute("SELECT indexdef FROM pg_indexes WHERE schemaname='catalog' AND indexname='partitions_one_live'")
        assert "UNIQUE INDEX partitions_one_live" in cursor.fetchone()[0]

    # === Vector: multi-partition B04 gaps project to independent
    # partition-bounded repair intents; missing-support backfill via
    # CoverageGapTrigger on a NEVER-admitted partition_key (adversarial
    # vector 1, 23). ===
    backfill_key = "dt=2024-02-01"
    backfill_required = CoverageInterval(Instant.parse("2024-02-01T00:00:00Z"), Instant.parse("2024-02-02T00:00:00Z"))
    backfill_gap = backfill_required
    backfill_trigger = CoverageGapTrigger(required=backfill_required, gaps=(backfill_gap,))
    backfill_intent = RepairIntent(IDENTITY, backfill_key, backfill_trigger)
    backfill_partition = partition_dict(1, "7", partition_key=backfill_key)
    backfill_report = report_for(backfill_partition, status="pass", certifier_tag="backfill")
    backfill_candidate = candidate_for(
        backfill_intent, backfill_partition, backfill_report, source_tag="backfill",
        coverage_start=backfill_required.start, coverage_end=backfill_required.end,
    )
    backfill_coverage_manifests = [coverage_manifest_for(
        coverage_id=COVERAGE_IDS[0], partition_key=backfill_key, revision=1,
        start=backfill_required.start, end=backfill_required.end,
    )]
    backfill_outcome = do_cutover(
        connection, intent=backfill_intent, candidate=backfill_candidate, partition=backfill_partition,
        report=backfill_report, coverage_start=backfill_required.start, coverage_end=backfill_required.end,
        coverage_manifests=backfill_coverage_manifests,
    )
    assert backfill_outcome.outcome == CONVERGED, backfill_outcome
    assert backfill_outcome.provenance.predecessor is None
    assert _one_live_count(connection, dataset_id, backfill_key) == 1
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT state FROM catalog.partitions WHERE partition_id=%s", (backfill_outcome.provenance.final_partition_id,),
        )
        assert cursor.fetchone()[0] == "valid"

    # Identical retry of the SAME candidate -> ALREADY_SATISFIED via durable
    # convergence provenance naming this exact repair_intent_id/candidate_id,
    # no gratuitous new revision (adversarial vector 10, 13).
    already = do_cutover(
        connection, intent=backfill_intent, candidate=backfill_candidate, partition=backfill_partition,
        report=backfill_report, coverage_start=backfill_required.start, coverage_end=backfill_required.end,
        coverage_manifests=backfill_coverage_manifests,
    )
    assert already.outcome == ALREADY_SATISFIED, already
    assert _row_count(connection, dataset_id, backfill_key) == 1

    # A DISTINCT candidate racing for the same now-already-satisfied slot is
    # STALE_CONFLICT, never ALREADY_SATISFIED merely because the obligation
    # happens to be met: ALREADY_SATISFIED requires durable provenance naming
    # THIS exact repair_intent_id/candidate_id (review finding), not just a
    # coincidental content/state match on the live row.
    backfill_distinct_partition = partition_dict(2, "8", partition_key=backfill_key)
    backfill_distinct_report = report_for(backfill_distinct_partition, status="pass", certifier_tag="backfill-distinct")
    backfill_distinct_candidate = candidate_for(
        backfill_intent, backfill_distinct_partition, backfill_distinct_report, source_tag="backfill-distinct",
        coverage_start=backfill_required.start, coverage_end=backfill_required.end,
    )
    distinct_outcome = do_cutover(
        connection, intent=backfill_intent, candidate=backfill_distinct_candidate, partition=backfill_distinct_partition,
        report=backfill_distinct_report, coverage_start=backfill_required.start, coverage_end=backfill_required.end,
        coverage_manifests=backfill_coverage_manifests,
    )
    assert distinct_outcome.outcome == STALE_CONFLICT, distinct_outcome
    assert _row_count(connection, dataset_id, backfill_key) == 1

    # === Vector: partial gap fill -> not converged (adversarial vector 14). ===
    partial_key = "dt=2024-03-01"
    partial_required = CoverageInterval(Instant.parse("2024-03-01T00:00:00Z"), Instant.parse("2024-03-02T00:00:00Z"))
    partial_gap = partial_required
    partial_trigger = CoverageGapTrigger(required=partial_required, gaps=(partial_gap,))
    partial_intent = RepairIntent(IDENTITY, partial_key, partial_trigger)
    partial_partition = partition_dict(1, "9", partition_key=partial_key)
    partial_report = report_for(partial_partition, status="pass", certifier_tag="partial")
    half = Instant.parse("2024-03-01T12:00:00Z")
    partial_candidate = candidate_for(
        partial_intent, partial_partition, partial_report, source_tag="partial",
        coverage_start=partial_required.start, coverage_end=half,
    )
    # The candidate can only durably prove HALF the required interval --
    # its own coverage-manifest evidence declares completion only up to
    # `half`, exactly matching what it will claim as coverage_end.
    partial_coverage_manifests = [coverage_manifest_for(
        coverage_id=COVERAGE_IDS[0], partition_key=partial_key, revision=1,
        start=partial_required.start, end=half,
    )]
    partial_outcome = do_cutover(
        connection, intent=partial_intent, candidate=partial_candidate, partition=partial_partition,
        report=partial_report, coverage_start=partial_required.start, coverage_end=half,
        coverage_manifests=partial_coverage_manifests,
    )
    assert partial_outcome.outcome == FAILED, partial_outcome
    assert _row_count(connection, dataset_id, partial_key) == 0, "a rejected partial-fill candidate must not admit a row"

    # === Vector: quality passes but target gap remains -> not converged
    # (adversarial vector 16).  Same mechanism as partial fill: the
    # candidate's own declared coverage does not eliminate the gap even
    # though its quality report would otherwise pass. ===
    remaining_key = "dt=2024-04-01"
    remaining_required = CoverageInterval(Instant.parse("2024-04-01T00:00:00Z"), Instant.parse("2024-04-03T00:00:00Z"))
    remaining_gap = CoverageInterval(Instant.parse("2024-04-02T00:00:00Z"), Instant.parse("2024-04-03T00:00:00Z"))
    remaining_trigger = CoverageGapTrigger(required=remaining_required, gaps=(remaining_gap,))
    remaining_intent = RepairIntent(IDENTITY, remaining_key, remaining_trigger)
    remaining_partition = partition_dict(1, "0", partition_key=remaining_key)
    remaining_report = report_for(remaining_partition, status="pass", certifier_tag="remaining")
    # Candidate only declares coverage for 04-01, leaving the 04-02 gap open.
    covers_only_first_day_end = Instant.parse("2024-04-02T00:00:00Z")
    remaining_candidate = candidate_for(
        remaining_intent, remaining_partition, remaining_report, source_tag="remaining",
        coverage_start=remaining_required.start, coverage_end=covers_only_first_day_end,
    )
    remaining_coverage_manifests = [coverage_manifest_for(
        coverage_id=COVERAGE_IDS[0], partition_key=remaining_key, revision=1,
        start=remaining_required.start, end=covers_only_first_day_end,
    )]
    remaining_outcome = do_cutover(
        connection, intent=remaining_intent, candidate=remaining_candidate, partition=remaining_partition,
        report=remaining_report, coverage_start=remaining_required.start, coverage_end=covers_only_first_day_end,
        coverage_manifests=remaining_coverage_manifests,
    )
    assert remaining_outcome.outcome == FAILED, remaining_outcome
    assert _row_count(connection, dataset_id, remaining_key) == 0


def _run_true_concurrency_race(dsn: str) -> None:
    """Two DISTINCT candidates race for the same stale predecessor on two
    SEPARATE connections/threads.  Real PostgreSQL row locking -- not
    sequential simulation -- must serialize them: exactly one converges, the
    other observes STALE_CONFLICT after blocking on the same locks this
    module's own dataset-then-topology ordering acquires."""

    race_key = "dt=2024-05-01"
    with psycopg.connect(dsn) as setup_connection:
        writer = CatalogPublicationWriter(setup_connection)
        sealed, lifecycle_result, predecessor_evidence = _establish_invalid_predecessor(
            setup_connection, writer, partition_key=race_key, token="a",
        )
        predecessor_ref = PredecessorRef(revision=1, content_sha256=predecessor_evidence.partition["sha256"], state="invalid")
        trigger = InvalidRevisionTrigger(
            predecessor=predecessor_ref,
            assessment_signature=lifecycle_result.assessment_signature,
            assessment_status="fail",
        )
        intent = RepairIntent(IDENTITY, race_key, trigger)
        dataset_id = _dataset_id(setup_connection)

    partition_a = partition_dict(2, "b", partition_key=race_key)
    partition_b = partition_dict(2, "c", partition_key=race_key)
    report_a = report_for(partition_a, status="pass", certifier_tag="race-a")
    report_b = report_for(partition_b, status="pass", certifier_tag="race-b")
    candidate_a = candidate_for(intent, partition_a, report_a, source_tag="race-a")
    candidate_b = candidate_for(intent, partition_b, report_b, source_tag="race-b")

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}

    def _attempt(name: str, candidate, partition, report) -> None:
        try:
            with psycopg.connect(dsn) as own_connection:
                barrier.wait(timeout=10)
                results[name] = do_cutover(
                    own_connection, intent=intent, candidate=candidate, partition=partition,
                    report=report, predecessor_evidence=predecessor_evidence,
                )
        except BaseException as exc:  # noqa: BLE001 - surfaced to the main thread below
            errors[name] = exc

    thread_a = threading.Thread(target=_attempt, args=("a", candidate_a, partition_a, report_a))
    thread_b = threading.Thread(target=_attempt, args=("b", candidate_b, partition_b, report_b))
    thread_a.start()
    thread_b.start()
    thread_a.join(timeout=30)
    thread_b.join(timeout=30)

    if errors:
        raise AssertionError(f"concurrent cutover attempt raised: {errors}")
    assert set(results) == {"a", "b"}
    outcomes = {name: result.outcome for name, result in results.items()}
    winners = [name for name, outcome in outcomes.items() if outcome == CONVERGED]
    losers = [name for name, outcome in outcomes.items() if outcome == STALE_CONFLICT]
    assert len(winners) == 1 and len(losers) == 1, outcomes

    with psycopg.connect(dsn) as verify:
        assert _one_live_count(verify, dataset_id, race_key) == 1
        assert _live_state(verify, sealed.partition_id) == "superseded"
        winner_result = results[winners[0]]
        assert _live_state(verify, winner_result.provenance.final_partition_id) in {"valid", "degraded"}


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("POSTGRESQL A10 REPAIR CUTOVER GATE NOT EXECUTED LOCALLY")
        return 0

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO catalog.schema_registry (schema_id,name,version,json_sha256,body) "
                "VALUES ('trade-v1','trade',1,%s,%s::jsonb) ON CONFLICT DO NOTHING",
                ("0" * 64, "{}"),
            )
            cursor.execute(
                "INSERT INTO catalog.storage_roots (storage_root_id,abs_path,tier) VALUES ('a10-hot',%s,'hot') ON CONFLICT DO NOTHING",
                (str(Path(tempfile.gettempdir()) / "a10-repair-integration"),),
            )
        connection.commit()
        writer = CatalogPublicationWriter(connection)
        _run_replacement_and_coverage_scenarios(connection, writer, dsn)

    _run_true_concurrency_race(dsn)

    print("A10 PostgreSQL repair cutover integration PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
