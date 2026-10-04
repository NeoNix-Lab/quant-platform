from __future__ import annotations

import sqlite3
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.durable_jobs import (  # noqa: E402
    DurableJobConflict,
    DurableJobStore,
    EffectSafety,
    JobAdmission,
    JobState,
)


def admission(
    *,
    effect_safety: EffectSafety = EffectSafety.PURE_REPLAYABLE,
    effect_safety_proof_reference: str | None = None,
) -> JobAdmission:
    return JobAdmission(
        operation_kind="supervised-train-evaluate-v1",
        contract_version="v1",
        implementation_identity="commit:abc123",
        request_identity="request:sha256:123",
        canonical_parameters={"fold": 2, "seed": 7},
        input_identities=("dataset:sha256:aaa", "policy:sha256:bbb"),
        effect_safety=effect_safety,
        effect_safety_proof_reference=effect_safety_proof_reference,
    )


class DurableJobRuntimeV1Tests(unittest.TestCase):
    def store(self) -> DurableJobStore:
        return DurableJobStore(sqlite3.connect(":memory:"))

    def test_admission_is_deterministic_and_persisted_before_queueing(self) -> None:
        store = self.store()
        first = store.admit(admission())
        second = store.admit(admission())
        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(JobState.ADMITTED, first.state)
        self.assertEqual(first, store.get(first.job_id))

    def test_only_declared_transitions_and_terminal_states_are_immutable(self) -> None:
        store = self.store()
        job = store.admit(admission())
        with self.assertRaises(DurableJobConflict):
            store.transition(job.job_id, JobState.RUNNING)
        store.transition(job.job_id, JobState.QUEUED)
        store.transition(job.job_id, JobState.RUNNING)
        succeeded = store.transition(
            job.job_id, JobState.SUCCEEDED, reason_code="completed", result_references=("result:sha256:1",)
        )
        self.assertEqual(JobState.SUCCEEDED, succeeded.state)
        with self.assertRaises(DurableJobConflict):
            store.transition(job.job_id, JobState.QUEUED)

    def test_restart_marks_running_job_recovery_required_without_redispatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "jobs.sqlite"
            first = DurableJobStore(sqlite3.connect(database))
            job = first.admit(admission())
            first.transition(job.job_id, JobState.QUEUED)
            first.transition(job.job_id, JobState.RUNNING)
            first.connection.close()
            restarted = DurableJobStore(sqlite3.connect(database))
            recovered = restarted.recover_after_restart()
            self.assertEqual((job.job_id,), tuple(item.job_id for item in recovered))
            self.assertEqual(JobState.RECOVERY_REQUIRED, restarted.get(job.job_id).state)
            self.assertEqual((JobState.RECOVERY_REQUIRED,), tuple(item.state for item in restarted.attempts(job.job_id)))
            restarted.connection.close()

    def test_retry_is_explicit_and_creates_a_new_durable_attempt(self) -> None:
        store = self.store()
        job = store.admit(admission())
        store.transition(job.job_id, JobState.QUEUED)
        store.transition(job.job_id, JobState.RUNNING)
        store.recover_after_restart()
        self.assertEqual(JobState.QUEUED, store.retry_or_resume(job.job_id).state)
        store.transition(job.job_id, JobState.RUNNING)
        self.assertEqual((1, 2), tuple(item.attempt_no for item in store.attempts(job.job_id)))
        with self.assertRaises(DurableJobConflict):
            store.retry_or_resume(job.job_id)

    def test_idempotency_retry_requires_immutable_proof_reference(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires an idempotency"):
            admission(effect_safety=EffectSafety.IDEMPOTENCY_EVIDENCE)

        store = self.store()
        job = store.admit(
            admission(
                effect_safety=EffectSafety.IDEMPOTENCY_EVIDENCE,
                effect_safety_proof_reference="idempotency-key:training-request-123",
            )
        )
        store.transition(job.job_id, JobState.QUEUED)
        store.transition(job.job_id, JobState.RUNNING)
        store.recover_after_restart()

        self.assertEqual(JobState.QUEUED, store.retry_or_resume(job.job_id).state)


if __name__ == "__main__":
    unittest.main()
