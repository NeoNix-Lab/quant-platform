#!/usr/bin/env python3
"""A10 backfill/repair v1: atomic compare-and-cutover control-flow proof.

Real PostgreSQL locking/uniqueness enforcement is CREDITED to
``tests/integration_a10_repair_postgres.py`` and to the existing
``partitions_one_live`` constraint/locking pattern already proven by
``tests/integration_publication_eligibility_postgres.py`` and
``tests/test_quality_lifecycle_v1.py``. This file proves
``RepairCutoverCatalog.cutover``'s own control flow -- which outcome it
picks and what it does/does not mutate -- against a minimal in-memory
double of ``catalog.partitions`` that supports exactly the statements
``cutover`` issues, with real transactional rollback semantics.
"""

from __future__ import annotations

import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import CoverageInterval, DatasetIdentity, Instant  # noqa: E402
from quant_platform.data.repair import (  # noqa: E402
    CandidateAttempt,
    CoverageGapTrigger,
    InvalidLiveRevisionTrigger,
    PredecessorReference,
    RepairCutoverCatalog,
    RepairCutoverRefusal,
    RepairIntent,
    RepairOutcome,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
DATASET_KEY = (IDENTITY.layer, IDENTITY.dataset_kind, IDENTITY.venue, IDENTITY.instrument, IDENTITY.record_schema_id)
DATASET_ID = "ds-1"
NATURAL_KEY = "dt=2024-01-15"
REQUIRED = CoverageInterval(Instant.parse("2024-01-15T00:00:00Z"), Instant.parse("2024-01-16T00:00:00Z"))
HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


class FakePartitionsTable:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def add(self, *, partition_id, partition_key, revision, state, ts_start=None, ts_end=None) -> str:
        self.rows[partition_id] = {
            "partition_id": partition_id,
            "dataset_id": DATASET_ID,
            "partition_key": partition_key,
            "revision": revision,
            "state": state,
            "ts_start": ts_start,
            "ts_end": ts_end,
        }
        return partition_id


class FakeCursor:
    def __init__(self, table: FakePartitionsTable, fault_on: str | None = None) -> None:
        self.table = table
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
                row for row in self.table.rows.values()
                if row["dataset_id"] == dataset_id and row["partition_key"] == partition_key
            ]
            matches.sort(key=lambda row: row["revision"])
            self._result = [
                (row["partition_id"], row["dataset_id"], row["partition_key"], row["revision"],
                 row["state"], row["ts_start"], row["ts_end"])
                for row in matches
            ]
        elif statement.startswith("UPDATE catalog.partitions") and "SET state = 'superseded'" in statement:
            partition_id, expected_state = params
            row = self.table.rows.get(partition_id)
            if row is not None and row["state"] == expected_state:
                row["state"] = "superseded"
                self._result = [(partition_id,)]
            else:
                self._result = []
        elif statement.startswith("UPDATE catalog.partitions") and "SET partition_key = %s, revision = %s" in statement:
            new_key, new_revision, partition_id, expected_state = params
            row = self.table.rows.get(partition_id)
            if row is not None and row["state"] == expected_state:
                row["partition_key"] = new_key
                row["revision"] = new_revision
                self._result = [(partition_id,)]
            else:
                self._result = []
        else:  # pragma: no cover - guards against an untested new statement shape
            raise AssertionError(f"unexpected statement: {statement}")

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeConnection:
    """A transactional double: rollback restores the pre-transaction snapshot."""

    def __init__(self, table: FakePartitionsTable, fault_on: str | None = None) -> None:
        self.table = table
        self.fault_on = fault_on
        self._snapshot = copy.deepcopy(table.rows)
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.table, self.fault_on)

    def commit(self) -> None:
        self.committed = True
        self._snapshot = copy.deepcopy(self.table.rows)

    def rollback(self) -> None:
        self.rolled_back = True
        self.table.rows = copy.deepcopy(self._snapshot)


def gap(start: str, end: str) -> CoverageInterval:
    return CoverageInterval(Instant.parse(start), Instant.parse(end))


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


def make_candidate(intent: RepairIntent, *, content_sha256: str = HASH_C) -> CandidateAttempt:
    return CandidateAttempt(
        intent_identity=intent.intent_identity,
        dataset_identity=IDENTITY,
        natural_partition_key=NATURAL_KEY,
        source_semantics_id="bybit-public-trades-sqlite-v1",
        mapping_id="bybit-sqlite-day-extract-v1",
        dataset_sha256=HASH_A,
        partition_sha256=HASH_B,
        content_sha256=content_sha256,
        code_ref="repair-commit-1",
    )


class RepairCutoverConvergenceV1Tests(unittest.TestCase):
    # 8. proven replacement -> atomic sole-live cutover
    def test_proven_candidate_converges_and_supersedes_predecessor(self):
        intent, pred_id = replacement_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()
        table.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="invalid")
        table.add(
            partition_id="cand-1", partition_key=candidate.staging_partition_key, revision=1,
            state="valid", ts_start=REQUIRED.start.to_datetime(), ts_end=REQUIRED.end.to_datetime(),
        )
        connection = FakeConnection(table)
        result = RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)

        self.assertEqual(RepairOutcome.CONVERGED, result.status)
        self.assertEqual(candidate.candidate_identity, result.candidate_identity)
        self.assertEqual("cand-1", result.live_partition_id)
        self.assertEqual(2, result.live_revision)
        self.assertEqual("valid", result.live_state)
        self.assertTrue(connection.committed)
        self.assertEqual("superseded", table.rows[pred_id]["state"])
        self.assertEqual(NATURAL_KEY, table.rows["cand-1"]["partition_key"])
        self.assertEqual(2, table.rows["cand-1"]["revision"])
        # exactly one non-superseded row remains for the natural partition_key
        live = [row for row in table.rows.values() if row["partition_key"] == NATURAL_KEY and row["state"] != "superseded"]
        self.assertEqual(1, len(live))

    def test_backfill_without_predecessor_converges_as_revision_one(self):
        intent = backfill_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()
        table.add(
            partition_id="cand-1", partition_key=candidate.staging_partition_key, revision=1,
            state="degraded", ts_start=REQUIRED.start.to_datetime(), ts_end=REQUIRED.end.to_datetime(),
        )
        connection = FakeConnection(table)
        result = RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)

        self.assertEqual(RepairOutcome.CONVERGED, result.status)
        self.assertEqual(1, result.live_revision)
        self.assertEqual(NATURAL_KEY, table.rows["cand-1"]["partition_key"])

    # 21. ordinary S13-style early supersession path is not used for
    # replacement repair -- predecessor stays exactly authoritative through
    # every step except the one promote/supersede pair inside this method.
    def test_predecessor_state_is_untouched_until_the_single_cutover_call(self):
        intent, pred_id = replacement_intent()
        table = FakePartitionsTable()
        table.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="invalid")
        # Staging and certifying a candidate never touches the predecessor row:
        # this row simulates the candidate already having gone through
        # seal/quality/eligibility entirely under its own staging_partition_key.
        candidate = make_candidate(intent)
        table.add(
            partition_id="cand-1", partition_key=candidate.staging_partition_key, revision=1,
            state="valid", ts_start=REQUIRED.start.to_datetime(), ts_end=REQUIRED.end.to_datetime(),
        )
        self.assertEqual("invalid", table.rows[pred_id]["state"])
        RepairCutoverCatalog(FakeConnection(table)).cutover(intent=intent, candidate=candidate)
        self.assertEqual("superseded", table.rows[pred_id]["state"])


class RepairCutoverPreservationV1Tests(unittest.TestCase):
    # 6/7. candidate materialized but uncertified / quality fails -> predecessor unchanged
    def test_uncertified_candidate_refuses_and_leaves_predecessor_unchanged(self):
        intent, pred_id = replacement_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()
        table.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="invalid")
        table.add(partition_id="cand-1", partition_key=candidate.staging_partition_key, revision=1, state="closed")
        connection = FakeConnection(table)
        with self.assertRaises(RepairCutoverRefusal):
            RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)
        self.assertEqual("invalid", table.rows[pred_id]["state"])
        self.assertTrue(connection.rolled_back)

    def test_quality_failed_candidate_refuses_and_leaves_predecessor_unchanged(self):
        intent, pred_id = replacement_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()
        table.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="invalid")
        table.add(partition_id="cand-1", partition_key=candidate.staging_partition_key, revision=1, state="invalid")
        connection = FakeConnection(table)
        with self.assertRaises(RepairCutoverRefusal):
            RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)
        self.assertEqual("invalid", table.rows[pred_id]["state"])

    # 16. quality passes but target gap remains -> not converged
    def test_candidate_covering_only_part_of_required_support_does_not_converge(self):
        intent, pred_id = replacement_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()
        table.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="invalid")
        table.add(
            partition_id="cand-1", partition_key=candidate.staging_partition_key, revision=1, state="valid",
            ts_start=REQUIRED.start.to_datetime(), ts_end=Instant.parse("2024-01-15T12:00:00Z").to_datetime(),
        )
        connection = FakeConnection(table)
        with self.assertRaises(RepairCutoverRefusal):
            RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)
        self.assertEqual("invalid", table.rows[pred_id]["state"])
        self.assertEqual(NATURAL_KEY, table.rows[pred_id]["partition_key"])

    # 19. no-predecessor acquisition failure -> support remains missing
    def test_missing_staged_candidate_refuses(self):
        intent = backfill_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()
        connection = FakeConnection(table)
        with self.assertRaises(RepairCutoverRefusal):
            RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)

    # 9. cutover transaction failure -> predecessor unchanged / no half-switch
    def test_fault_between_supersede_and_promote_leaves_no_half_switch(self):
        intent, pred_id = replacement_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()
        table.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="invalid")
        table.add(
            partition_id="cand-1", partition_key=candidate.staging_partition_key, revision=1, state="valid",
            ts_start=REQUIRED.start.to_datetime(), ts_end=REQUIRED.end.to_datetime(),
        )
        connection = FakeConnection(table, fault_on="SET partition_key = %s, revision = %s")
        with self.assertRaises(RuntimeError):
            RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)
        self.assertTrue(connection.rolled_back)
        # No half-switch: predecessor is back to its pre-transaction state,
        # never left superseded while the candidate is not authoritative.
        self.assertEqual("invalid", table.rows[pred_id]["state"])
        self.assertEqual(candidate.staging_partition_key, table.rows["cand-1"]["partition_key"])


class RepairCutoverStaleAndAlreadySatisfiedV1Tests(unittest.TestCase):
    # 13. already repaired -> ALREADY_SATISFIED, no gratuitous new revision
    def test_already_covering_live_revision_is_already_satisfied(self):
        intent, pred_id = replacement_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()
        # Someone else already replaced the invalid predecessor with a valid,
        # fully-covering revision before this cutover attempt runs.
        table.add(
            partition_id="other-live", partition_key=NATURAL_KEY, revision=2, state="valid",
            ts_start=REQUIRED.start.to_datetime(), ts_end=REQUIRED.end.to_datetime(),
        )
        table.add(partition_id="cand-1", partition_key=candidate.staging_partition_key, revision=1, state="valid",
                   ts_start=REQUIRED.start.to_datetime(), ts_end=REQUIRED.end.to_datetime())
        connection = FakeConnection(table)
        result = RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)
        self.assertEqual(RepairOutcome.ALREADY_SATISFIED, result.status)
        self.assertIsNone(result.candidate_identity)
        # no gratuitous new revision: topology is exactly as it was.
        self.assertEqual("valid", table.rows["other-live"]["state"])
        self.assertEqual(2, table.rows["other-live"]["revision"])
        self.assertEqual(candidate.staging_partition_key, table.rows["cand-1"]["partition_key"])

    def test_backfill_already_satisfied_when_a_live_revision_now_covers_it(self):
        intent = backfill_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()
        table.add(
            partition_id="other-live", partition_key=NATURAL_KEY, revision=1, state="degraded",
            ts_start=REQUIRED.start.to_datetime(), ts_end=REQUIRED.end.to_datetime(),
        )
        connection = FakeConnection(table)
        result = RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)
        self.assertEqual(RepairOutcome.ALREADY_SATISFIED, result.status)

    # 12/17. old trigger/predecessor changes -> stale conflict, no silent retarget
    def test_changed_incompatible_predecessor_is_stale_conflict(self):
        intent, pred_id = replacement_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()
        # A different, still-non-covering revision now occupies the natural key.
        table.add(
            partition_id="other-live", partition_key=NATURAL_KEY, revision=2, state="invalid",
        )
        table.add(partition_id="cand-1", partition_key=candidate.staging_partition_key, revision=1, state="valid",
                   ts_start=REQUIRED.start.to_datetime(), ts_end=REQUIRED.end.to_datetime())
        connection = FakeConnection(table)
        result = RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)
        self.assertEqual(RepairOutcome.STALE_CONFLICT, result.status)
        # no mutation at all on a stale-conflict outcome
        self.assertEqual("invalid", table.rows["other-live"]["state"])
        self.assertEqual(2, table.rows["other-live"]["revision"])

    # 18. higher revision/newer timestamp -> no authority by ordering alone
    def test_higher_revision_number_alone_grants_no_authority(self):
        intent, pred_id = replacement_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()
        # revision 9 is numerically far ahead of the captured predecessor's
        # revision 1, but it neither matches identity nor covers required
        # support, so ordering alone must not be treated as authoritative.
        table.add(partition_id="other-live", partition_key=NATURAL_KEY, revision=9, state="invalid")
        table.add(partition_id="cand-1", partition_key=candidate.staging_partition_key, revision=1, state="valid",
                   ts_start=REQUIRED.start.to_datetime(), ts_end=REQUIRED.end.to_datetime())
        connection = FakeConnection(table)
        result = RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)
        self.assertEqual(RepairOutcome.STALE_CONFLICT, result.status)

    def test_vanished_predecessor_is_stale_conflict_not_deletion(self):
        intent, pred_id = replacement_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()
        table.add(partition_id="cand-1", partition_key=candidate.staging_partition_key, revision=1, state="valid",
                   ts_start=REQUIRED.start.to_datetime(), ts_end=REQUIRED.end.to_datetime())
        connection = FakeConnection(table)
        result = RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)
        self.assertEqual(RepairOutcome.STALE_CONFLICT, result.status)

    # 20. superseded predecessor never reactivated
    def test_cutover_never_reactivates_an_already_superseded_row(self):
        intent, pred_id = replacement_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()
        table.add(partition_id=pred_id, partition_key=NATURAL_KEY, revision=1, state="superseded")
        table.add(
            partition_id="other-live", partition_key=NATURAL_KEY, revision=2, state="invalid",
        )
        table.add(partition_id="cand-1", partition_key=candidate.staging_partition_key, revision=1, state="valid",
                   ts_start=REQUIRED.start.to_datetime(), ts_end=REQUIRED.end.to_datetime())
        connection = FakeConnection(table)
        result = RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)
        self.assertEqual(RepairOutcome.STALE_CONFLICT, result.status)
        self.assertEqual("superseded", table.rows[pred_id]["state"])


class RepairCutoverMisuseV1Tests(unittest.TestCase):
    def test_candidate_for_a_different_intent_is_refused(self):
        intent, pred_id = replacement_intent()
        other_intent = backfill_intent()
        candidate = make_candidate(other_intent)
        table = FakePartitionsTable()
        connection = FakeConnection(table)
        with self.assertRaises(Exception):
            RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)

    def test_unknown_dataset_refuses(self):
        intent = backfill_intent()
        candidate = make_candidate(intent)
        table = FakePartitionsTable()

        class EmptyDatasetCursor(FakeCursor):
            def execute(self, sql, params=None):
                statement = " ".join(sql.split())
                if "FROM catalog.datasets" in statement:
                    self._result = []
                else:
                    super().execute(sql, params)

        class EmptyDatasetConnection(FakeConnection):
            def cursor(self):
                return EmptyDatasetCursor(self.table)

        connection = EmptyDatasetConnection(table)
        with self.assertRaises(RepairCutoverRefusal):
            RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate)


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
