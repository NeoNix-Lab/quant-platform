#!/usr/bin/env python3
"""Real PostgreSQL proof for the A10 atomic compare-and-cutover seam.

Proves: successful atomic replacement cutover, transaction rollback on a
failure injected mid-cutover, a stale-predecessor race between two candidate
attempts, one-live preservation throughout, idempotent ALREADY_SATISFIED
retry, and physically isolated candidate staging.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

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
    FAILED,
    InvalidRevisionTrigger,
    PredecessorRef,
    RepairCutoverCatalog,
    RepairIntent,
    STALE_CONFLICT,
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


def partition_dict(revision: int, token: str) -> dict:
    return {
        "partition_key": "dt=2024-01-15",
        "revision": revision,
        "storage_root_id": "a10-hot",
        "rel_path": f"dt=2024-01-15/part-{revision:03d}-{token}.parquet",
        "row_count": 1,
        "file_size_bytes": 100 + revision,
        "sha256": token * 64,
        "_manifest_sha256": (token.upper() * 64),
        "state": "closed",
        "created_at": "2026-09-01T10:00:00Z",
        "closed_at": "2026-09-01T10:00:01Z",
        "first_sequence": None,
        "last_sequence": None,
        "producer": "a10-repair-producer",
        "code_ref": f"a10-repair-commit-{token}",
    }


def quality_metrics(partition: dict, *, status: str) -> dict:
    metrics = {
        "certification_profile": PROFILE,
        "natural_partition_identity": {
            "dataset_identity": IDENTITY.stable_dict(),
            "partition_key": partition["partition_key"],
            "revision": partition["revision"],
        },
        "dataset_manifest_sha256": DATASET["_manifest_sha256"],
        "partition_manifest_sha256": partition["_manifest_sha256"],
        "coverage_manifest_id": COVERAGE_IDS[0],
        "coverage_assertion_id": ASSERTION_IDS[0],
        "coverage_manifest_ids": COVERAGE_IDS,
        "coverage_assertion_ids": ASSERTION_IDS,
        "coverage_manifest_sha256": COVERAGE_SHA,
        "physical_artifact_hash": partition["sha256"],
        "canonical_content_hash_v1": "c" * 64,
        "evidence": {name: {"status": "pass"} for name in ("source", "canonical", "physical", "manifests", "coverage")},
    }
    if status == "fail":
        metrics["evidence"]["source"] = {"status": "fail"}
    return metrics


def candidate_for(intent: RepairIntent, partition: dict, *, source_tag: str) -> CandidateAttempt:
    return CandidateAttempt(
        repair_intent_id=intent.intent_id,
        natural_identity=NaturalPartitionIdentity(IDENTITY, partition["partition_key"], partition["revision"]),
        content_sha256=partition["sha256"],
        partition_manifest_sha256=partition["_manifest_sha256"],
        source_evidence_id=f"a10-source-{source_tag}",
        materialization_id=f"a10-materialization-{source_tag}",
        coverage_evidence_id=COVERAGE_IDS[0],
        quality_evidence_id=f"a10-quality-{source_tag}",
        code_ref=partition["code_ref"],
    )


def do_cutover(connection, *, intent: RepairIntent, candidate: CandidateAttempt, partition: dict, status: str) -> object:
    report = {
        "status": status,
        "metrics": quality_metrics(partition, status=status),
        "violations": [] if status == "pass" else [{"category": "source", "message": "controlled failure"}],
        "code_ref": "a10-repair-certifier",
    }
    return RepairCutoverCatalog(connection).cutover(
        repair_intent=intent,
        candidate=candidate,
        dataset=DATASET,
        dataset_sha256=DATASET["_manifest_sha256"],
        candidate_partition=partition,
        candidate_quality_report=report,
        coverage_start=START,
        coverage_end=END,
        coverage_ids=COVERAGE_IDS,
        assertion_ids=ASSERTION_IDS,
        coverage_sha256=COVERAGE_SHA,
        storage_root_id="a10-hot",
        expected_profile=PROFILE,
        expected_check_suite=CHECK_SUITE,
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


def _dataset_id(connection) -> str:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT dataset_id::text FROM catalog.datasets WHERE layer=%s AND kind=%s AND venue=%s AND instrument=%s AND schema_id=%s",
            (IDENTITY.layer, IDENTITY.dataset_kind, IDENTITY.venue, IDENTITY.instrument, IDENTITY.record_schema_id),
        )
        return cursor.fetchone()[0]


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

        # --- Establish an authoritative predecessor, then drive it invalid via A16. ---
        predecessor = partition_dict(1, "p")
        sealed = writer.seal_partition(
            dataset=DATASET, partition=predecessor, coverage_start=START, coverage_end=END,
            storage_root_id="a10-hot",
        )
        writer.commit()
        writer.record_quality_report(
            partition_id=sealed.partition_id, check_suite=CHECK_SUITE, status="fail",
            metrics=quality_metrics(predecessor, status="fail"),
            violations=[{"category": "source", "message": "predecessor is bad"}],
            code_ref="a10-repair-certifier",
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

        dataset_id = _dataset_id(connection)
        predecessor_ref = PredecessorRef(revision=1, content_sha256=predecessor["sha256"], state="invalid")
        trigger = InvalidRevisionTrigger(
            predecessor=predecessor_ref,
            assessment_signature=lifecycle_result.assessment_signature,
            assessment_status="fail",
        )
        intent = RepairIntent(IDENTITY, "dt=2024-01-15", trigger)

        # === Vector: cutover transaction failure leaves predecessor authoritative,
        # no half-switch (frozen contract section 9-10; adversarial vector 9). ===
        failing_partition = partition_dict(2, "x")
        failing_candidate = candidate_for(intent, failing_partition, source_tag="injected-failure")
        failing_connection = FailAfterInsert(connection)
        try:
            do_cutover(failing_connection, intent=intent, candidate=failing_candidate, partition=failing_partition, status="pass")
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
        failing_quality_partition = partition_dict(2, "y")
        failing_quality_candidate = candidate_for(intent, failing_quality_partition, source_tag="failing-quality")
        outcome = do_cutover(connection, intent=intent, candidate=failing_quality_candidate, partition=failing_quality_partition, status="fail")
        assert outcome.outcome == FAILED, outcome
        assert _live_state(connection, sealed.partition_id) == "invalid"
        assert _one_live_count(connection, dataset_id, "dt=2024-01-15") == 1

        # === Vector: proven replacement -> atomic sole-live cutover
        # (adversarial vector 3, 8). Staging isolation proof alongside: two
        # DISTINCT candidates for the same nominal revision use physically
        # isolated evidence directories that cannot clobber each other. ===
        with tempfile.TemporaryDirectory() as staging_root:
            staging = CandidateStaging(Path(staging_root))
            winner_partition = partition_dict(2, "w")
            loser_partition = partition_dict(2, "z")
            winner_candidate = candidate_for(intent, winner_partition, source_tag="winner")
            loser_candidate = candidate_for(intent, loser_partition, source_tag="loser")

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

            converged = do_cutover(connection, intent=intent, candidate=winner_candidate, partition=winner_partition, status="pass")
            assert converged.outcome == CONVERGED, converged
            assert converged.provenance.publication_state in {"valid", "degraded"}
            assert _live_state(connection, sealed.partition_id) == "superseded"
            assert _one_live_count(connection, dataset_id, "dt=2024-01-15") == 1
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT state FROM catalog.partitions WHERE partition_id=%s", (converged.provenance.final_partition_id,),
                )
                assert cursor.fetchone()[0] == "valid"

            # A fresh connection must see the same committed state (no
            # phantom uncommitted mutation from this process alone).
            with psycopg.connect(dsn) as fresh:
                assert _live_state(fresh, sealed.partition_id) == "superseded"
                assert _live_state(fresh, converged.provenance.final_partition_id) == "valid"

            # === Vector: identical retry is idempotent -> ALREADY_SATISFIED,
            # no gratuitous new revision (adversarial vector 10, 13). ===
            retry = do_cutover(connection, intent=intent, candidate=winner_candidate, partition=winner_partition, status="pass")
            assert retry.outcome == ALREADY_SATISFIED, retry
            assert _one_live_count(connection, dataset_id, "dt=2024-01-15") == 1
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM catalog.partitions WHERE dataset_id=%s AND partition_key=%s",
                    (dataset_id, "dt=2024-01-15"),
                )
                assert cursor.fetchone()[0] == 2, "retry must not create a gratuitous new revision"

            # === Vector: two candidate attempts racing for the same stale
            # predecessor -> the loser is STALE_CONFLICT, never silently wins
            # by insertion/timestamp order, and never reactivates the
            # superseded predecessor (adversarial vector 12, 17, 20). ===
            loser_outcome = do_cutover(connection, intent=intent, candidate=loser_candidate, partition=loser_partition, status="pass")
            assert loser_outcome.outcome == STALE_CONFLICT, loser_outcome
            assert _live_state(connection, sealed.partition_id) == "superseded", "superseded predecessor must never reactivate"
            assert _one_live_count(connection, dataset_id, "dt=2024-01-15") == 1

        with connection.cursor() as cursor:
            cursor.execute("SELECT indexdef FROM pg_indexes WHERE schemaname='catalog' AND indexname='partitions_one_live'")
            assert "UNIQUE INDEX partitions_one_live" in cursor.fetchone()[0]

    print("A10 PostgreSQL repair cutover integration PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
