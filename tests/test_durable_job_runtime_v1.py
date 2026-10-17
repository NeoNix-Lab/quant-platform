from __future__ import annotations

from dataclasses import replace
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

    def test_terminal_job_allows_one_bounded_diagnostic_evidence_append(self) -> None:
        store = self.store()
        job = store.admit(admission(effect_safety=EffectSafety.NO_EFFECT))
        cancelled = store.transition(
            job.job_id,
            JobState.CANCELLED,
            reason_code="operator_cancelled",
        )
        evidence = store.transition(
            job.job_id,
            JobState.CANCELLED,
            reason_code="operator_cancelled",
            diagnostic_reference="evidence:operator-cancellation-ack",
        )

        self.assertIsNone(cancelled.diagnostic_reference)
        self.assertEqual("evidence:operator-cancellation-ack", evidence.diagnostic_reference)
        self.assertEqual(evidence, store.transition(
            job.job_id,
            JobState.CANCELLED,
            reason_code="operator_cancelled",
            diagnostic_reference="evidence:operator-cancellation-ack",
        ))
        with self.assertRaises(DurableJobConflict):
            store.transition(
                job.job_id,
                JobState.CANCELLED,
                reason_code="operator_cancelled",
                diagnostic_reference="evidence:replacement-is-forbidden",
            )

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

    def test_free_text_failure_evidence_is_lossless_and_idempotent(self) -> None:
        vectors = (
            ("failed\x00detail", None),
            ("handler_failed", "evidence\x00ref"),
            ("failed\x00detail", "evidence\x00ref"),
            (r"failed\u0000detail", r'evidence\x00ref "quoted"'),
            ("null", '"null"'),
            ("r" * 511 + "\x00", "日本語\n\x00é"),
        )
        for index, (reason, diagnostic) in enumerate(vectors):
            with self.subTest(reason=reason, diagnostic=diagnostic):
                store = self.store()
                self.addCleanup(store.connection.close)
                job = store.admit(replace(admission(), request_identity=f"failure-evidence:{index}"))
                self.assertIsNone(job.reason_code)
                self.assertIsNone(job.diagnostic_reference)
                store.transition(job.job_id, JobState.QUEUED)
                store.transition(job.job_id, JobState.RUNNING)
                self.assertIsNone(store.attempts(job.job_id)[0].failure_code)
                self.assertIsNone(store.attempts(job.job_id)[0].evidence_reference)
                failed = store.transition(
                    job.job_id, JobState.FAILED, reason_code=reason,
                    diagnostic_reference=diagnostic,
                )
                self.assertEqual(JobState.FAILED, failed.state)
                self.assertEqual((reason, diagnostic), (failed.reason_code, failed.diagnostic_reference))
                attempt = store.attempts(job.job_id)[0]
                self.assertEqual(JobState.FAILED, attempt.state)
                self.assertEqual((reason, diagnostic), (attempt.failure_code, attempt.evidence_reference))
                self.assertEqual(failed, store.get(job.job_id))
                self.assertEqual(failed, store.transition(
                    job.job_id, JobState.FAILED, reason_code=reason,
                    diagnostic_reference=diagnostic,
                ))
                self.assertEqual((attempt,), store.attempts(job.job_id))

    def test_terminal_diagnostic_append_preserves_nul_and_literal_escapes(self) -> None:
        store = self.store()
        self.addCleanup(store.connection.close)
        job = store.admit(admission())
        store.transition(job.job_id, JobState.QUEUED)
        store.transition(job.job_id, JobState.RUNNING)
        reason = "handler_failed"
        diagnostic = 'evidence\x00ref' + r'\u0000\x00 "quoted"'
        store.transition(job.job_id, JobState.FAILED, reason_code=reason)
        attempt_before_append = store.attempts(job.job_id)[0]
        appended = store.transition(
            job.job_id, JobState.FAILED, reason_code=reason,
            diagnostic_reference=diagnostic,
        )
        self.assertEqual((reason, diagnostic), (appended.reason_code, appended.diagnostic_reference))
        self.assertEqual(appended, store.transition(
            job.job_id, JobState.FAILED, reason_code=reason,
            diagnostic_reference=diagnostic,
        ))
        # Existing terminal append changes only Job evidence, not the closed attempt.
        self.assertEqual((attempt_before_append,), store.attempts(job.job_id))
        with self.assertRaises(DurableJobConflict):
            store.transition(job.job_id, JobState.FAILED, reason_code=reason,
                             diagnostic_reference="different\x00evidence")


if __name__ == "__main__":
    unittest.main()
