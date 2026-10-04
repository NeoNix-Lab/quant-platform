"""Durable J03 admission, lifecycle, and recovery coordination.

This module records Job lifecycle evidence before any future dispatcher may
execute a handler.  It deliberately does not schedule, invoke, or retry a
handler: the owning Application capability remains responsible for operation
semantics and an operator must explicitly choose any recovery.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import sqlite3
from typing import Any


class DurableJobError(RuntimeError):
    """Base class for durable Job admission and lifecycle refusals."""


class DurableJobConflict(DurableJobError):
    """An immutable admission or lifecycle transition conflicts."""


class DurableJobNotFound(DurableJobError):
    """A requested durable Job record is absent."""


class JobState(StrEnum):
    ADMITTED = "ADMITTED"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCELLATION_REQUESTED = "CANCELLATION_REQUESTED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EffectSafety(StrEnum):
    NO_EFFECT = "NO_EFFECT"
    PURE_REPLAYABLE = "PURE_REPLAYABLE"
    IDEMPOTENCY_EVIDENCE = "IDEMPOTENCY_EVIDENCE"


TERMINAL_JOB_STATES = frozenset({JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED})
_ALLOWED_TRANSITIONS = frozenset({
    (JobState.ADMITTED, JobState.QUEUED),
    (JobState.ADMITTED, JobState.CANCELLED),
    (JobState.QUEUED, JobState.RUNNING),
    (JobState.QUEUED, JobState.CANCELLED),
    (JobState.RUNNING, JobState.SUCCEEDED),
    (JobState.RUNNING, JobState.FAILED),
    (JobState.RUNNING, JobState.CANCELLATION_REQUESTED),
    (JobState.RUNNING, JobState.RECOVERY_REQUIRED),
    (JobState.CANCELLATION_REQUESTED, JobState.CANCELLED),
    (JobState.CANCELLATION_REQUESTED, JobState.FAILED),
    (JobState.CANCELLATION_REQUESTED, JobState.RECOVERY_REQUIRED),
    (JobState.RECOVERY_REQUIRED, JobState.QUEUED),
    (JobState.RECOVERY_REQUIRED, JobState.FAILED),
    (JobState.RECOVERY_REQUIRED, JobState.CANCELLED),
})


@dataclass(frozen=True, slots=True)
class JobAdmission:
    operation_kind: str
    contract_version: str
    implementation_identity: str
    request_identity: str
    canonical_parameters: Mapping[str, Any]
    input_identities: tuple[str, ...]
    effect_safety: EffectSafety
    effect_safety_proof_reference: str | None = None

    def __post_init__(self) -> None:
        for field in ("operation_kind", "contract_version", "implementation_identity", "request_identity"):
            if not isinstance(getattr(self, field), str) or not getattr(self, field).strip():
                raise ValueError(f"{field} must be a non-empty string")
        if not isinstance(self.canonical_parameters, Mapping):
            raise TypeError("canonical_parameters must be a mapping")
        if any(not isinstance(item, str) or not item.strip() for item in self.input_identities):
            raise ValueError("input_identities must contain non-empty strings")
        if len(set(self.input_identities)) != len(self.input_identities):
            raise ValueError("input_identities must be unique")
        if not isinstance(self.effect_safety, EffectSafety):
            raise TypeError("effect_safety must be an EffectSafety")
        _bounded_optional_text(self.effect_safety_proof_reference, "effect_safety_proof_reference")
        if (
            self.effect_safety == EffectSafety.IDEMPOTENCY_EVIDENCE
            and self.effect_safety_proof_reference is None
        ):
            raise ValueError(
                "IDEMPOTENCY_EVIDENCE requires an idempotency or durable-effect proof reference"
            )
        if (
            self.effect_safety != EffectSafety.IDEMPOTENCY_EVIDENCE
            and self.effect_safety_proof_reference is not None
        ):
            raise ValueError(
                "effect_safety_proof_reference is only valid for IDEMPOTENCY_EVIDENCE"
            )
        _canonical_json(self.payload())

    def payload(self) -> dict[str, Any]:
        return {
            "operation_kind": self.operation_kind,
            "contract_version": self.contract_version,
            "implementation_identity": self.implementation_identity,
            "request_identity": self.request_identity,
            "canonical_parameters": dict(self.canonical_parameters),
            "input_identities": sorted(self.input_identities),
            "effect_safety": self.effect_safety.value,
            "effect_safety_proof_reference": self.effect_safety_proof_reference,
        }

    @property
    def job_id(self) -> str:
        digest = hashlib.sha256(_canonical_json(self.payload()).encode("utf-8")).hexdigest()
        return f"job-v1:sha256:{digest}"


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    admission: JobAdmission
    state: JobState
    reason_code: str | None
    result_references: tuple[str, ...]
    diagnostic_reference: str | None


@dataclass(frozen=True, slots=True)
class JobAttempt:
    job_id: str
    attempt_no: int
    state: JobState
    failure_code: str | None
    evidence_reference: str | None


class DurableJobStore:
    """SQLite-backed server-local Job storage, separate from I02 persistence."""

    def __init__(self, connection: sqlite3.Connection):
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be a sqlite3.Connection")
        self.connection = connection
        self._initialize()

    def admit(self, admission: JobAdmission) -> JobRecord:
        if not isinstance(admission, JobAdmission):
            raise TypeError("admission must be a JobAdmission")
        payload = _canonical_json(admission.payload())
        with self.connection:
            row = self.connection.execute(
                "SELECT payload FROM durable_jobs WHERE job_id = ?", (admission.job_id,)
            ).fetchone()
            if row is None:
                self.connection.execute(
                    "INSERT INTO durable_jobs (job_id, payload, state) VALUES (?, ?, ?)",
                    (admission.job_id, payload, JobState.ADMITTED.value),
                )
            elif str(row[0]) != payload:
                raise DurableJobConflict("job_id admission payload conflicts")
        return self.get(admission.job_id)

    def get(self, job_id: str) -> JobRecord:
        row = self.connection.execute(
            "SELECT job_id, payload, state, reason_code, result_references, diagnostic_reference "
            "FROM durable_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise DurableJobNotFound("job is not admitted")
        return _job_record(row)

    def transition(
        self,
        job_id: str,
        state: JobState,
        *,
        reason_code: str | None = None,
        result_references: tuple[str, ...] = (),
        diagnostic_reference: str | None = None,
    ) -> JobRecord:
        if not isinstance(state, JobState):
            raise TypeError("state must be a JobState")
        _bounded_optional_text(reason_code, "reason_code")
        _bounded_optional_text(diagnostic_reference, "diagnostic_reference")
        if any(not isinstance(item, str) or not item.strip() for item in result_references):
            raise ValueError("result_references must contain non-empty strings")
        with self.connection:
            current = self.get(job_id)
            if current.state == state:
                if (
                    current.reason_code != reason_code
                    or current.result_references != tuple(result_references)
                    or current.diagnostic_reference != diagnostic_reference
                ):
                    raise DurableJobConflict("job state evidence conflicts")
                return current
            if current.state in TERMINAL_JOB_STATES:
                raise DurableJobConflict("terminal job state is absorbing")
            if (current.state, state) not in _ALLOWED_TRANSITIONS:
                raise DurableJobConflict(f"illegal job transition {current.state.value} -> {state.value}")
            if state == JobState.SUCCEEDED and not result_references:
                raise DurableJobConflict("successful job requires result references")
            if state in frozenset({JobState.FAILED, JobState.CANCELLED}) and not reason_code:
                raise DurableJobConflict("failed or cancelled job requires a reason code")
            self.connection.execute(
                "UPDATE durable_jobs SET state = ?, reason_code = ?, result_references = ?, "
                "diagnostic_reference = ? WHERE job_id = ?",
                (state.value, reason_code, _canonical_json(list(result_references)), diagnostic_reference, job_id),
            )
            if current.state in frozenset({JobState.RUNNING, JobState.CANCELLATION_REQUESTED}):
                self.connection.execute(
                    "UPDATE durable_job_attempts SET state = ?, failure_code = ?, evidence_reference = ? "
                    "WHERE job_id = ? AND attempt_no = (SELECT MAX(attempt_no) FROM durable_job_attempts WHERE job_id = ?)",
                    (state.value, reason_code, diagnostic_reference, job_id, job_id),
                )
            if state == JobState.RUNNING:
                self.connection.execute(
                    "INSERT INTO durable_job_attempts (job_id, attempt_no, state) "
                    "VALUES (?, COALESCE((SELECT MAX(attempt_no) + 1 FROM durable_job_attempts WHERE job_id = ?), 1), ?)",
                    (job_id, job_id, JobState.RUNNING.value),
                )
        return self.get(job_id)

    def recover_after_restart(self) -> tuple[JobRecord, ...]:
        """Mark interrupted work recoverable; never infer liveness or redispatch."""
        recovered: list[JobRecord] = []
        with self.connection:
            rows = self.connection.execute(
                "SELECT job_id FROM durable_jobs WHERE state IN (?, ?) ORDER BY job_id",
                (JobState.RUNNING.value, JobState.CANCELLATION_REQUESTED.value),
            ).fetchall()
            for (job_id,) in rows:
                current = self.get(str(job_id))
                self.connection.execute(
                    "UPDATE durable_jobs SET state = ?, reason_code = ? WHERE job_id = ?",
                    (JobState.RECOVERY_REQUIRED.value, "restart_recovery_required", current.job_id),
                )
                self.connection.execute(
                    "UPDATE durable_job_attempts SET state = ?, failure_code = ? "
                    "WHERE job_id = ? AND attempt_no = (SELECT MAX(attempt_no) FROM durable_job_attempts WHERE job_id = ?)",
                    (JobState.RECOVERY_REQUIRED.value, "restart_recovery_required", current.job_id, current.job_id),
                )
                recovered.append(self.get(current.job_id))
        return tuple(recovered)

    def retry_or_resume(self, job_id: str) -> JobRecord:
        """Explicitly queue a recovered Job only with admitted effect-safety proof."""
        current = self.get(job_id)
        if current.state != JobState.RECOVERY_REQUIRED:
            raise DurableJobConflict("retry or resume requires RECOVERY_REQUIRED")
        if not _has_effect_safety_proof(current.admission):
            raise DurableJobConflict("handler effect safety proof is unavailable")
        return self.transition(job_id, JobState.QUEUED)

    def attempts(self, job_id: str) -> tuple[JobAttempt, ...]:
        self.get(job_id)
        rows = self.connection.execute(
            "SELECT job_id, attempt_no, state, failure_code, evidence_reference "
            "FROM durable_job_attempts WHERE job_id = ? ORDER BY attempt_no", (job_id,)
        ).fetchall()
        return tuple(JobAttempt(str(row[0]), int(row[1]), JobState(str(row[2])), row[3], row[4]) for row in rows)

    def _initialize(self) -> None:
        with self.connection:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS durable_jobs ("
                "job_id TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL, "
                "reason_code TEXT, result_references TEXT NOT NULL DEFAULT '[]', diagnostic_reference TEXT)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS durable_job_attempts ("
                "job_id TEXT NOT NULL, attempt_no INTEGER NOT NULL, state TEXT NOT NULL, "
                "failure_code TEXT, evidence_reference TEXT, PRIMARY KEY (job_id, attempt_no), "
                "FOREIGN KEY (job_id) REFERENCES durable_jobs(job_id))"
            )


def _job_record(row: Any) -> JobRecord:
    payload = json.loads(str(row[1]))
    admission = JobAdmission(
        operation_kind=payload["operation_kind"], contract_version=payload["contract_version"],
        implementation_identity=payload["implementation_identity"], request_identity=payload["request_identity"],
        canonical_parameters=payload["canonical_parameters"], input_identities=tuple(payload["input_identities"]),
        effect_safety=EffectSafety(payload["effect_safety"]),
        effect_safety_proof_reference=payload.get("effect_safety_proof_reference"),
    )
    return JobRecord(
        job_id=str(row[0]), admission=admission, state=JobState(str(row[2])), reason_code=row[3],
        result_references=tuple(json.loads(str(row[4]))), diagnostic_reference=row[5],
    )


def _bounded_optional_text(value: str | None, field: str) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 512):
        raise ValueError(f"{field} must be a bounded non-empty string when supplied")


def _has_effect_safety_proof(admission: JobAdmission) -> bool:
    """Return whether the immutable admission contains a permitted retry proof."""
    return admission.effect_safety in frozenset({
        EffectSafety.NO_EFFECT,
        EffectSafety.PURE_REPLAYABLE,
    }) or (
        admission.effect_safety == EffectSafety.IDEMPOTENCY_EVIDENCE
        and admission.effect_safety_proof_reference is not None
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


__all__ = [
    "DurableJobConflict", "DurableJobError", "DurableJobNotFound", "DurableJobStore", "EffectSafety",
    "JobAdmission", "JobAttempt", "JobRecord", "JobState", "TERMINAL_JOB_STATES",
]
