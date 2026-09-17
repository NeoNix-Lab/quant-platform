#!/usr/bin/env python3
"""A10 backfill/repair v1: atomic compare-and-cutover control-flow proof.

Real PostgreSQL locking/uniqueness enforcement is CREDITED to
``tests/integration_a10_repair_postgres.py`` and to the existing
``partitions_one_live`` constraint/locking pattern already proven by
``tests/integration_publication_eligibility_postgres.py`` and
``tests/test_quality_lifecycle_v1.py``. This file proves
``RepairCutoverCatalog.cutover``'s own control flow -- which outcome it
picks and what it does/does not mutate -- against a minimal in-memory
double of ``catalog.partitions``/``catalog.artifacts`` that supports exactly
the statements ``cutover`` issues, with real transactional rollback
semantics.

Candidate evidence (staged partition manifest, coverage manifest, physical
artifact bytes) is produced through the REAL, credited
``quant_platform.data.manifests``/``materializer``/``coverage`` machinery --
never hand-typed documents -- so this file also exercises the exact
identity-binding, coverage-refolding and manifest-re-emission control flow
the independent review flagged (findings 1-5), not just a mocked stand-in
for it.
"""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.coverage import reconstruct_catalog_coverage  # noqa: E402
from quant_platform.data.manifests import (  # noqa: E402
    emit_coverage_manifest,
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
    REPAIR_CONVERGENCE_ARTIFACT_KIND,
    RepairCutoverCatalog,
    RepairCutoverRefusal,
    RepairError,
    RepairIntent,
    RepairOutcome,
    provenance_rel_path_for,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
DATASET_KEY = (IDENTITY.layer, IDENTITY.dataset_kind, IDENTITY.venue, IDENTITY.instrument, IDENTITY.record_schema_id)
DATASET_ID = "ds-1"
NATURAL_KEY = "dt=2024-01-15"
REQUIRED = CoverageInterval(Instant.parse("2024-01-15T00:00:00Z"), Instant.parse("2024-01-16T00:00:00Z"))
HASH_A = "a" * 64
HASH_B = "b" * 64
STORAGE_ROOT = "root-1"
CREATED = "2026-08-31T10:00:00Z"
CLOSED = "2026-08-31T10:05:00Z"
SOURCE_SEMANTICS_ID = "bybit-public-trades-sqlite-v1"
MAPPING_ID = "bybit-sqlite-day-extract-v1"


class FakePartitionsTable:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def add(
        self, *, partition_id, partition_key, revision, state,
        content_sha256=None, manifest_sha256=None, storage_root_id=STORAGE_ROOT,
        rel_path=None, ts_start=None, ts_end=None,
    ) -> str:
        self.rows[partition_id] = {
            "partition_id": partition_id,
            "dataset_id": DATASET_ID,
            "partition_key": partition_key,
            "revision": revision,
            "state": state,
            "ts_start": ts_start,
            "ts_end": ts_end,
            "content_sha256": content_sha256 or ("0" * 64),
            "manifest_sha256": manifest_sha256 or ("1" * 64),
            "storage_root_id": storage_root_id,
            "rel_path": rel_path or f"{partition_key}/part-000.parquet",
        }
        return partition_id


class FakeArtifactsTable:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}
        self._next_id = 1

    def insert_or_ignore(self, **fields) -> str | None:
        key = (fields["storage_root_id"], fields["rel_path"])
        if key in self.rows:
            return None
        artifact_id = f"art-{self._next_id}"
        self._next_id += 1
        self.rows[key] = {"artifact_id": artifact_id, **fields}
        return artifact_id

    def select_produced_by(self, *, storage_root_id, kind, produced_by) -> str | None:
        for row in self.rows.values():
            if row["storage_root_id"] == storage_root_id and row["kind"] == kind and row["produced_by"] == produced_by:
                return row["produced_by"]
        return None

    def select_id(self, *, storage_root_id, rel_path) -> str | None:
        row = self.rows.get((storage_root_id, rel_path))
        return None if row is None else row["artifact_id"]


class FakeCursor:
    def __init__(self, partitions: FakePartitionsTable, artifacts: FakeArtifactsTable, fault_on: str | None = None) -> None:
        self.partitions = partitions
        self.artifacts = artifacts
        self.fault_on = fault_on
        self._result: list[tuple] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def execute(self, sql: str, params: tuple | None = None) -> None:
        statement = " ".join(sql.split())
        if self.fault_on and self.fault_on in statement:
            raise RuntimeError("injected fault")
        if "FROM catalog.datasets" in statement:
            key = tuple(params)
            self._result = [(DATASET_ID,)] if key == DATASET_KEY else []
        elif statement.startswith("SELECT partition_id::text, dataset_id::text, partition_key"):
            dataset_id, partition_key = params
            matches = [
                row for row in self.partitions.rows.values()
                if row["dataset_id"] == dataset_id and row["partition_key"] == partition_key
            ]
            matches.sort(key=lambda row: row["revision"])
            self._result = [
                (
                    row["partition_id"], row["dataset_id"], row["partition_key"], row["revision"],
                    row["state"], row["ts_start"], row["ts_end"], row["content_sha256"],
                    row["manifest_sha256"], row["storage_root_id"], row["rel_path"],
                )
                for row in matches
            ]
        elif "SELECT produced_by FROM catalog.artifacts" in statement:
            storage_root_id, kind, produced_by = params
            found = self.artifacts.select_produced_by(storage_root_id=storage_root_id, kind=kind, produced_by=produced_by)
            self._result = [] if found is None else [(found,)]
        elif statement.startswith("UPDATE catalog.partitions") and "SET state = 'superseded'" in statement:
            partition_id, expected_state = params
            row = self.partitions.rows.get(partition_id)
            if row is not None and row["state"] == expected_state:
                row["state"] = "superseded"
                self._result = [(partition_id,)]
            else:
                self._result = []
        elif statement.startswith("UPDATE catalog.partitions") and "SET partition_key = %s, revision = %s, manifest_sha256 = %s" in statement:
            new_key, new_revision, new_manifest_sha256, partition_id, expected_state = params
            row = self.partitions.rows.get(partition_id)
            if row is not None and row["state"] == expected_state:
                row["partition_key"] = new_key
                row["revision"] = new_revision
                row["manifest_sha256"] = new_manifest_sha256
                self._result = [(partition_id,)]
            else:
                self._result = []
        elif statement.startswith("INSERT INTO catalog.artifacts"):
            kind, storage_root_id, rel_path, content_sha256, byte_size, produced_by, code_ref, dataset_id, manifest_sha256 = params
            artifact_id = self.artifacts.insert_or_ignore(
                kind=kind, storage_root_id=storage_root_id, rel_path=rel_path, content_sha256=content_sha256,
                byte_size=byte_size, produced_by=produced_by, code_ref=code_ref, dataset_id=dataset_id,
                manifest_sha256=manifest_sha256,
            )
            self._result = [] if artifact_id is None else [(artifact_id,)]
        elif statement.startswith("SELECT artifact_id::text FROM catalog.artifacts"):
            storage_root_id, rel_path = params
            artifact_id = self.artifacts.select_id(storage_root_id=storage_root_id, rel_path=rel_path)
            self._result = [] if artifact_id is None else [(artifact_id,)]
        else:  # pragma: no cover - guards against an untested new statement shape
            raise AssertionError(f"unexpected statement: {statement}")

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeConnection:
    """A transactional double: rollback restores the pre-transaction snapshot."""

    def __init__(self, partitions: FakePartitionsTable, artifacts: FakeArtifactsTable, fault_on: str | None = None) -> None:
        self.partitions = partitions
        self.artifacts = artifacts
        self.fault_on = fault_on
        self._p_snapshot = copy.deepcopy(partitions.rows)
        self._a_snapshot = copy.deepcopy(artifacts.rows)
        self._a_next_id = artifacts._next_id
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.partitions, self.artifacts, self.fault_on)

    def commit(self) -> None:
        self.committed = True
        self._p_snapshot = copy.deepcopy(self.partitions.rows)
        self._a_snapshot = copy.deepcopy(self.artifacts.rows)
        self._a_next_id = self.artifacts._next_id

    def rollback(self) -> None:
        self.rolled_back = True
        self.partitions.rows = copy.deepcopy(self._p_snapshot)
        self.artifacts.rows = copy.deepcopy(self._a_snapshot)
        self.artifacts._next_id = self._a_next_id


def replacement_intent(predecessor_state: str = "invalid") -> tuple[RepairIntent, str]:
    pred_id = "pred-1"
    trigger = InvalidLiveRevisionTrigger(
        predecessor=PredecessorReference(pred_id, 1, predecessor_state),
        assessment_signature="sig-1",
        assessment_status="fail",
    )
    intent = RepairIntent(
        dataset_identity=IDENTITY,
        partition_key=NATURAL_KEY,
        required_support=REQUIRED,
        predecessor=PredecessorReference(pred_id, 1, predecessor_state),
        trigger=trigger,
    )
    return intent, pred_id


def backfill_intent() -> RepairIntent:
    trigger = CoverageGapTrigger(gaps=(REQUIRED,))
    return RepairIntent(
        dataset_identity=IDENTITY, partition_key=NATURAL_KEY, required_support=REQUIRED,
        predecessor=None, trigger=trigger,
    )


def stage_and_prove(
    intent: RepairIntent,
    dataset_root: Path,
    *,
    content: bytes = b"repair-candidate-payload-1",
    ts: str = "2024-01-15T06:00:00Z",
    assessment_status: str = "pass",
    eligibility_state: str = "valid",
    code_ref: str = "repair-commit-1",
    coverage_start: str | None = None,
    coverage_end: str | None = None,
) -> tuple[CandidateAttempt, CandidateProof]:
    """Build one candidate + its durable proof through the REAL credited
    manifest/coverage machinery: no hand-typed manifest documents.
    """

    content_sha256 = hashlib.sha256(content).hexdigest()
    provisional = CandidateAttempt(
        intent_identity=intent.intent_identity,
        dataset_identity=intent.dataset_identity,
        natural_partition_key=intent.partition_key,
        source_semantics_id=SOURCE_SEMANTICS_ID,
        mapping_id=MAPPING_ID,
        dataset_sha256=HASH_A,
        partition_sha256=HASH_B,
        content_sha256=content_sha256,
        code_ref=code_ref,
    )
    staging_key = provisional.staging_partition_key
    rel_path = f"{staging_key}/part-001.parquet"
    artifact_path = dataset_root / rel_path
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_bytes(content)

    materialization = ParquetMaterialization(
        path=artifact_path,
        dataset_identity=intent.dataset_identity,
        file_size_bytes=len(content),
        row_count=1,
        sha256=content_sha256,
        canonical_content_hash_v1="d" * 64,
        first_exchange_ts=Instant.parse(ts),
        last_exchange_ts=Instant.parse(ts),
    )
    partition_emission = emit_partition_manifest(
        dataset_root / "_staging" / "partition-manifest.json",
        materialization,
        dataset_identity=intent.dataset_identity,
        dataset_root=dataset_root,
        partition_key=staging_key,
        revision=1,
        rel_path=rel_path,
        created_at=CREATED,
        closed_at=CLOSED,
        producer="test-repair-producer",
        code_ref=code_ref,
    )
    candidate = CandidateAttempt(
        intent_identity=intent.intent_identity,
        dataset_identity=intent.dataset_identity,
        natural_partition_key=intent.partition_key,
        source_semantics_id=SOURCE_SEMANTICS_ID,
        mapping_id=MAPPING_ID,
        dataset_sha256=HASH_A,
        partition_sha256=partition_emission.manifest_sha256,
        content_sha256=content_sha256,
        code_ref=code_ref,
    )
    assert candidate.staging_partition_key == staging_key

    window_start = coverage_start or intent.required_support.start.isoformat()
    window_end = coverage_end or intent.required_support.end.isoformat()
    coverage_emission = emit_coverage_manifest(
        dataset_root / "_staging" / "coverage-manifest.json",
        dataset_identity=intent.dataset_identity,
        coverage_id="repair-coverage-1",
        supersedes=None,
        created_at=CREATED,
        acquisition={
            "basis": "reconciliation",
            "intent_start": window_start,
            "intent_end": window_end,
            "source_semantics": SOURCE_SEMANTICS_ID,
            "mapping": MAPPING_ID,
        },
        assertions=[{
            "assertion_id": "repair-complete",
            "start": window_start,
            "end": window_end,
            "status": "complete",
            "partitions": [{"partition_key": staging_key, "revision": 1}],
            "evidence": [{"kind": "reconciliation", "detail": "repair candidate coverage"}],
        }],
        producer="test-repair-producer",
        code_ref=code_ref,
        source_dataset_identity=intent.dataset_identity,
        partition_manifests=[partition_emission.document],
    )
    # Sanity: the real B04 folding independently agrees this candidate
    # closes the target gap before we ever hand it to cutover.
    folded, violations = reconstruct_catalog_coverage(
        (coverage_emission.document,), (partition_emission.document,),
    )
    assert not violations, violations

    proof = CandidateProof(
        dataset_document={"rel_root": "canonical/trades/bybit/BTCUSDT/trade-v1"},
        dataset_sha256=HASH_A,
        partition_document=partition_emission.document,
        partition_sha256=partition_emission.manifest_sha256,
        coverage_documents=(coverage_emission.document,),
        canonical_content_hash_v1="d" * 64,
        assessment_signature="sig-1",
        assessment_status=assessment_status,
        eligibility_state=eligibility_state,
        repair_code_ref=code_ref,
    )
    return candidate, proof


class RepairCutoverTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.dataset_root = Path(self._tempdir.name)
        self.promoted_manifest_path = self.dataset_root / "promoted-partition-manifest.json"

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def stage_candidate_row(self, candidate: CandidateAttempt, proof: CandidateProof, *, partitions: FakePartitionsTable, partition_id: str = "cand-1", state: str = "valid") -> None:
        partitions.add(
            partition_id=partition_id, partition_key=candidate.staging_partition_key, revision=1, state=state,
            content_sha256=candidate.content_sha256, manifest_sha256=proof.partition_sha256,
            rel_path=f"{candidate.staging_partition_key}/part-001.parquet",
        )

    def cutover(self, intent, candidate, proof, connection) -> object:
        return RepairCutoverCatalog(connection).cutover(
            intent=intent, candidate=candidate, proof=proof,
            dataset_root=self.dataset_root, promoted_manifest_path=self.promoted_manifest_path,
        )


class RepairCutoverConvergenceV1Tests(RepairCutoverTestCase):
    # 8. proven replacement -> atomic sole-live cutover; catalog and durable
    # manifest agree afterward (finding 2).
    def test_proven_candidate_converges_and_supersedes_predecessor(self):
        intent, pred_id = replacement_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        partitions = FakePartitionsTable()
        partitions.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="invalid")
        self.stage_candidate_row(candidate, proof, partitions=partitions)
        connection = FakeConnection(partitions, FakeArtifactsTable())

        result = self.cutover(intent, candidate, proof, connection)

        self.assertEqual(RepairOutcome.CONVERGED, result.status)
        self.assertEqual(candidate.candidate_identity, result.candidate_identity)
        self.assertEqual("cand-1", result.live_partition_id)
        self.assertEqual(2, result.live_revision)
        self.assertEqual("valid", result.live_state)
        self.assertTrue(connection.committed)
        self.assertEqual("superseded", partitions.rows[pred_id]["state"])
        self.assertEqual(NATURAL_KEY, partitions.rows["cand-1"]["partition_key"])
        self.assertEqual(2, partitions.rows["cand-1"]["revision"])
        live = [row for row in partitions.rows.values() if row["partition_key"] == NATURAL_KEY and row["state"] != "superseded"]
        self.assertEqual(1, len(live))

        # finding 2: the promoted catalog row's manifest_sha256 agrees with
        # a real, re-emitted natural-identity manifest -- never a bare
        # column flip that leaves the durable manifest still declaring the
        # staging identity.
        self.assertEqual(result.promoted_manifest_sha256, partitions.rows["cand-1"]["manifest_sha256"])
        self.assertTrue(self.promoted_manifest_path.exists())
        promoted_document = __import__("json").loads(self.promoted_manifest_path.read_bytes())
        self.assertEqual(NATURAL_KEY, promoted_document["partition_key"])
        self.assertEqual(2, promoted_document["revision"])
        # the physical artifact is never moved or copied.
        self.assertEqual(f"{candidate.staging_partition_key}/part-001.parquet", promoted_document["rel_path"])

        # finding 5: immutable convergence provenance is durably recorded,
        # both as a catalog.artifacts row and as a durable JSON document.
        self.assertIsNotNone(result.provenance_artifact_id)
        provenance_path = self.dataset_root / provenance_rel_path_for(intent)
        self.assertTrue(provenance_path.exists())
        provenance_document = __import__("json").loads(provenance_path.read_bytes())
        self.assertEqual(intent.intent_identity, provenance_document["intent_identity"])
        self.assertEqual(candidate.candidate_identity, provenance_document["candidate_identity"])

    def test_backfill_without_predecessor_converges_as_revision_one(self):
        intent = backfill_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        partitions = FakePartitionsTable()
        self.stage_candidate_row(candidate, proof, partitions=partitions, state="degraded")
        connection = FakeConnection(partitions, FakeArtifactsTable())

        result = self.cutover(intent, candidate, proof, connection)

        self.assertEqual(RepairOutcome.CONVERGED, result.status)
        self.assertEqual(1, result.live_revision)
        self.assertEqual(NATURAL_KEY, partitions.rows["cand-1"]["partition_key"])

    # 21. ordinary S13-style early supersession path is not used for
    # replacement repair -- predecessor stays exactly authoritative through
    # every step except the one promote/supersede pair inside this method.
    def test_predecessor_state_is_untouched_until_the_single_cutover_call(self):
        intent, pred_id = replacement_intent()
        partitions = FakePartitionsTable()
        partitions.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="invalid")
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        self.stage_candidate_row(candidate, proof, partitions=partitions)
        self.assertEqual("invalid", partitions.rows[pred_id]["state"])
        connection = FakeConnection(partitions, FakeArtifactsTable())
        self.cutover(intent, candidate, proof, connection)
        self.assertEqual("superseded", partitions.rows[pred_id]["state"])


class RepairCutoverPreservationV1Tests(RepairCutoverTestCase):
    # 6/7. candidate materialized but uncertified / quality fails -> predecessor unchanged
    def test_uncertified_candidate_refuses_and_leaves_predecessor_unchanged(self):
        intent, pred_id = replacement_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        partitions = FakePartitionsTable()
        partitions.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="invalid")
        self.stage_candidate_row(candidate, proof, partitions=partitions, state="closed")
        connection = FakeConnection(partitions, FakeArtifactsTable())
        with self.assertRaises(RepairCutoverRefusal):
            self.cutover(intent, candidate, proof, connection)
        self.assertEqual("invalid", partitions.rows[pred_id]["state"])
        self.assertTrue(connection.rolled_back)

    def test_quality_failed_candidate_refuses_and_leaves_predecessor_unchanged(self):
        intent, pred_id = replacement_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        partitions = FakePartitionsTable()
        partitions.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="invalid")
        self.stage_candidate_row(candidate, proof, partitions=partitions, state="invalid")
        connection = FakeConnection(partitions, FakeArtifactsTable())
        with self.assertRaises(RepairCutoverRefusal):
            self.cutover(intent, candidate, proof, connection)
        self.assertEqual("invalid", partitions.rows[pred_id]["state"])

    # finding 1: exact declared coverage is independently re-folded and
    # verified -- a candidate that only closes part of the targeted gap
    # never converges, even though its staging row is 'valid'.
    def test_candidate_covering_only_part_of_required_support_does_not_converge(self):
        intent, pred_id = replacement_intent()
        candidate, proof = stage_and_prove(
            intent, self.dataset_root,
            coverage_start=REQUIRED.start.isoformat(), coverage_end="2024-01-15T12:00:00Z",
        )
        partitions = FakePartitionsTable()
        partitions.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="invalid")
        self.stage_candidate_row(candidate, proof, partitions=partitions)
        connection = FakeConnection(partitions, FakeArtifactsTable())
        with self.assertRaises(RepairCutoverRefusal):
            self.cutover(intent, candidate, proof, connection)
        self.assertEqual("invalid", partitions.rows[pred_id]["state"])
        self.assertEqual(NATURAL_KEY, partitions.rows[pred_id]["partition_key"])

    # 19. no-predecessor acquisition failure -> support remains missing
    def test_missing_staged_candidate_refuses(self):
        intent = backfill_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        partitions = FakePartitionsTable()
        connection = FakeConnection(partitions, FakeArtifactsTable())
        with self.assertRaises(RepairCutoverRefusal):
            self.cutover(intent, candidate, proof, connection)

    # 9. cutover transaction failure -> predecessor unchanged / no half-switch
    def test_fault_between_supersede_and_promote_leaves_no_half_switch(self):
        intent, pred_id = replacement_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        partitions = FakePartitionsTable()
        partitions.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="invalid")
        self.stage_candidate_row(candidate, proof, partitions=partitions)
        connection = FakeConnection(
            partitions, FakeArtifactsTable(), fault_on="SET partition_key = %s, revision = %s, manifest_sha256 = %s",
        )
        with self.assertRaises(RuntimeError):
            self.cutover(intent, candidate, proof, connection)
        self.assertTrue(connection.rolled_back)
        self.assertEqual("invalid", partitions.rows[pred_id]["state"])
        self.assertEqual(candidate.staging_partition_key, partitions.rows["cand-1"]["partition_key"])


class RepairCutoverStaleAndAlreadySatisfiedV1Tests(RepairCutoverTestCase):
    # finding 6: mere coverage sufficiency on a mismatch is NEVER
    # authoritative by itself -- an unrelated, merely-covering replacement
    # (a race loser's own view, or someone else's independent fix) is
    # STALE_CONFLICT, not ALREADY_SATISFIED.
    def test_unrelated_covering_live_revision_is_stale_conflict_not_already_satisfied(self):
        intent, pred_id = replacement_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        partitions = FakePartitionsTable()
        partitions.add(
            partition_id="other-live", partition_key=NATURAL_KEY, revision=2, state="valid",
            ts_start=REQUIRED.start.to_datetime(), ts_end=REQUIRED.end.to_datetime(),
        )
        self.stage_candidate_row(candidate, proof, partitions=partitions)
        connection = FakeConnection(partitions, FakeArtifactsTable())
        result = self.cutover(intent, candidate, proof, connection)
        self.assertEqual(RepairOutcome.STALE_CONFLICT, result.status)
        self.assertIsNone(result.candidate_identity)
        self.assertEqual("valid", partitions.rows["other-live"]["state"])
        self.assertEqual(2, partitions.rows["other-live"]["revision"])
        self.assertEqual(candidate.staging_partition_key, partitions.rows["cand-1"]["partition_key"])

    # ALREADY_SATISFIED is reserved for a durable-provenance-confirmed
    # idempotent retry of the exact candidate that already converged here.
    def test_idempotent_retry_of_the_actual_winner_is_already_satisfied(self):
        intent, pred_id = replacement_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        partitions = FakePartitionsTable()
        partitions.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="invalid")
        self.stage_candidate_row(candidate, proof, partitions=partitions)
        artifacts = FakeArtifactsTable()
        connection = FakeConnection(partitions, artifacts)
        first = self.cutover(intent, candidate, proof, connection)
        self.assertEqual(RepairOutcome.CONVERGED, first.status)

        # Retry with the identical intent/candidate/proof: the captured
        # predecessor no longer matches current topology (it is now
        # superseded), but the retry is the actual winner, durably proven
        # by the provenance artifact recorded on the first attempt.
        retry = self.cutover(intent, candidate, proof, connection)
        self.assertEqual(RepairOutcome.ALREADY_SATISFIED, retry.status)
        self.assertEqual(candidate.candidate_identity, retry.candidate_identity)
        # no gratuitous new revision or mutation on the idempotent retry.
        self.assertEqual(2, partitions.rows["cand-1"]["revision"])
        self.assertEqual("superseded", partitions.rows[pred_id]["state"])

    # 12/17. old trigger/predecessor changes -> stale conflict, no silent retarget
    def test_changed_incompatible_predecessor_is_stale_conflict(self):
        intent, pred_id = replacement_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        partitions = FakePartitionsTable()
        partitions.add(partition_id="other-live", partition_key=NATURAL_KEY, revision=2, state="invalid")
        self.stage_candidate_row(candidate, proof, partitions=partitions)
        connection = FakeConnection(partitions, FakeArtifactsTable())
        result = self.cutover(intent, candidate, proof, connection)
        self.assertEqual(RepairOutcome.STALE_CONFLICT, result.status)
        self.assertEqual("invalid", partitions.rows["other-live"]["state"])
        self.assertEqual(2, partitions.rows["other-live"]["revision"])

    # 18. higher revision/newer timestamp -> no authority by ordering alone
    def test_higher_revision_number_alone_grants_no_authority(self):
        intent, pred_id = replacement_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        partitions = FakePartitionsTable()
        partitions.add(partition_id="other-live", partition_key=NATURAL_KEY, revision=9, state="invalid")
        self.stage_candidate_row(candidate, proof, partitions=partitions)
        connection = FakeConnection(partitions, FakeArtifactsTable())
        result = self.cutover(intent, candidate, proof, connection)
        self.assertEqual(RepairOutcome.STALE_CONFLICT, result.status)

    def test_vanished_predecessor_is_stale_conflict_not_deletion(self):
        intent, pred_id = replacement_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        partitions = FakePartitionsTable()
        self.stage_candidate_row(candidate, proof, partitions=partitions)
        connection = FakeConnection(partitions, FakeArtifactsTable())
        result = self.cutover(intent, candidate, proof, connection)
        self.assertEqual(RepairOutcome.STALE_CONFLICT, result.status)

    # 20. superseded predecessor never reactivated
    def test_cutover_never_reactivates_an_already_superseded_row(self):
        intent, pred_id = replacement_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        partitions = FakePartitionsTable()
        partitions.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="superseded")
        partitions.add(partition_id="other-live", partition_key=NATURAL_KEY, revision=2, state="invalid")
        self.stage_candidate_row(candidate, proof, partitions=partitions)
        connection = FakeConnection(partitions, FakeArtifactsTable())
        result = self.cutover(intent, candidate, proof, connection)
        self.assertEqual(RepairOutcome.STALE_CONFLICT, result.status)
        self.assertEqual("superseded", partitions.rows[pred_id]["state"])


class RepairCutoverMisuseV1Tests(RepairCutoverTestCase):
    def test_candidate_for_a_different_intent_is_refused(self):
        intent, pred_id = replacement_intent()
        other_intent = backfill_intent()
        candidate, proof = stage_and_prove(other_intent, self.dataset_root)
        partitions = FakePartitionsTable()
        connection = FakeConnection(partitions, FakeArtifactsTable())
        with self.assertRaises(RepairError):
            self.cutover(intent, candidate, proof, connection)

    # finding 4: candidate.dataset_identity / natural_partition_key must be
    # bound to the intent, not merely intent_identity.
    def test_candidate_for_a_different_dataset_identity_is_refused(self):
        intent = backfill_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        other_identity = DatasetIdentity("canonical", "trades", "kraken", "XBTUSD", "trade-v1")
        forged = CandidateAttempt(
            intent_identity=candidate.intent_identity, dataset_identity=other_identity,
            natural_partition_key=candidate.natural_partition_key,
            source_semantics_id=candidate.source_semantics_id, mapping_id=candidate.mapping_id,
            dataset_sha256=candidate.dataset_sha256, partition_sha256=candidate.partition_sha256,
            content_sha256=candidate.content_sha256, code_ref=candidate.code_ref,
        )
        partitions = FakePartitionsTable()
        connection = FakeConnection(partitions, FakeArtifactsTable())
        with self.assertRaises(RepairError):
            self.cutover(intent, forged, proof, connection)

    def test_candidate_for_a_different_partition_key_is_refused(self):
        intent = backfill_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        forged = CandidateAttempt(
            intent_identity=candidate.intent_identity, dataset_identity=candidate.dataset_identity,
            natural_partition_key="dt=2024-01-16",
            source_semantics_id=candidate.source_semantics_id, mapping_id=candidate.mapping_id,
            dataset_sha256=candidate.dataset_sha256, partition_sha256=candidate.partition_sha256,
            content_sha256=candidate.content_sha256, code_ref=candidate.code_ref,
        )
        partitions = FakePartitionsTable()
        connection = FakeConnection(partitions, FakeArtifactsTable())
        with self.assertRaises(RepairError):
            self.cutover(intent, forged, proof, connection)

    # finding 3: proof must be exactly bound to the candidate's own
    # declared evidence -- an unrelated proof (arbitrary hashes) is refused.
    def test_proof_with_unrelated_dataset_sha256_is_refused(self):
        intent = backfill_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        from dataclasses import replace
        forged_proof = replace(proof, dataset_sha256="f" * 64)
        partitions = FakePartitionsTable()
        connection = FakeConnection(partitions, FakeArtifactsTable())
        with self.assertRaises(RepairError):
            self.cutover(intent, candidate, forged_proof, connection)

    def test_proof_with_unrelated_partition_sha256_is_refused(self):
        intent = backfill_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        from dataclasses import replace
        forged_proof = replace(proof, partition_sha256="f" * 64)
        partitions = FakePartitionsTable()
        connection = FakeConnection(partitions, FakeArtifactsTable())
        with self.assertRaises(RepairError):
            self.cutover(intent, candidate, forged_proof, connection)

    def test_proof_partition_document_pointing_at_a_different_staging_key_is_refused(self):
        intent = backfill_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        from dataclasses import replace
        forged_document = dict(proof.partition_document)
        forged_document["partition_key"] = "dt=2024-01-15/repair=not-this-candidate"
        forged_proof = replace(proof, partition_document=forged_document)
        partitions = FakePartitionsTable()
        connection = FakeConnection(partitions, FakeArtifactsTable())
        with self.assertRaises(RepairError):
            self.cutover(intent, candidate, forged_proof, connection)

    def test_proof_partition_document_declaring_a_non_revision_one_is_refused(self):
        intent = backfill_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        from dataclasses import replace
        forged_document = dict(proof.partition_document)
        forged_document["revision"] = 2
        forged_proof = replace(proof, partition_document=forged_document)
        partitions = FakePartitionsTable()
        connection = FakeConnection(partitions, FakeArtifactsTable())
        with self.assertRaises(RepairError):
            self.cutover(intent, candidate, forged_proof, connection)

    def test_unknown_dataset_refuses(self):
        intent = backfill_intent()
        candidate, proof = stage_and_prove(intent, self.dataset_root)
        partitions = FakePartitionsTable()

        class EmptyDatasetCursor(FakeCursor):
            def execute(self, sql, params=None):
                statement = " ".join(sql.split())
                if "FROM catalog.datasets" in statement:
                    self._result = []
                else:
                    super().execute(sql, params)

        class EmptyDatasetConnection(FakeConnection):
            def cursor(self):
                return EmptyDatasetCursor(self.partitions, self.artifacts)

        connection = EmptyDatasetConnection(partitions, FakeArtifactsTable())
        with self.assertRaises(RepairCutoverRefusal):
            self.cutover(intent, candidate, proof, connection)


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
