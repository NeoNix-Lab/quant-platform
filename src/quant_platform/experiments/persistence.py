"""PostgreSQL persistence boundary for Experiment identities and runs.

This module persists the I01 semantic identity family without redefining it.
The database owns restart-safe state; it does not infer worker liveness, retry
policy, job scheduling or artifact payload storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Sequence

from .identities import (
    ArtifactContentIdentity,
    ArtifactIdentity,
    RunIdentity,
    RunSpecIdentity,
    StudyIdentity,
    TrialIdentity,
)
from quant_platform.canonical import canonical_bytes


class ExperimentPersistenceError(RuntimeError):
    """Base class for Experiment persistence refusals."""


class ExperimentPersistenceConflict(ExperimentPersistenceError):
    """An existing durable fact conflicts with the submitted fact."""


class ExperimentPersistenceNotFound(ExperimentPersistenceError):
    """A required durable Experiment fact is missing."""


class RunState(StrEnum):
    REGISTERED = "REGISTERED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


TERMINAL_RUN_STATES = frozenset({RunState.SUCCEEDED, RunState.FAILED})
_ALLOWED_TRANSITIONS = frozenset({
    (RunState.REGISTERED, RunState.RUNNING),
    (RunState.REGISTERED, RunState.FAILED),
    (RunState.RUNNING, RunState.SUCCEEDED),
    (RunState.RUNNING, RunState.FAILED),
})


@dataclass(frozen=True, slots=True)
class ArtifactRegistration:
    identity: ArtifactIdentity
    locator: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    run_spec_fingerprint: str
    execution_id: str
    artifact_role: str
    artifact_content_fingerprint: str
    locator: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_spec_fingerprint: str
    execution_id: str
    state: RunState
    failure_details: Mapping[str, Any] | None = None

    @property
    def running_is_liveness_evidence(self) -> bool:
        return False


class ExperimentRepository:
    """Transactional PostgreSQL repository for I02 durable Experiment facts."""

    def __init__(self, connection: Any):
        self.connection = connection

    def register_study(self, study: StudyIdentity) -> str:
        try:
            with self.connection.cursor() as cursor:
                self._ensure_study(cursor, study)
            self.connection.commit()
            return study.fingerprint
        except Exception:
            self.connection.rollback()
            raise

    def register_trial(self, trial: TrialIdentity) -> str:
        try:
            with self.connection.cursor() as cursor:
                self._ensure_trial(cursor, trial)
            self.connection.commit()
            return trial.fingerprint
        except Exception:
            self.connection.rollback()
            raise

    def register_run_spec(self, run_spec: RunSpecIdentity) -> str:
        try:
            with self.connection.cursor() as cursor:
                self._ensure_run_spec(cursor, run_spec)
            self.connection.commit()
            return run_spec.fingerprint
        except Exception:
            self.connection.rollback()
            raise

    def register_artifact_content(self, content: ArtifactContentIdentity) -> str:
        try:
            with self.connection.cursor() as cursor:
                self._ensure_artifact_content(cursor, content)
            self.connection.commit()
            return content.fingerprint
        except Exception:
            self.connection.rollback()
            raise

    def register_run(self, run: RunIdentity) -> RunRecord:
        try:
            with self.connection.cursor() as cursor:
                self._ensure_run_spec(cursor, run.run_spec_identity)
                self._ensure_run_registered(cursor, run)
                record = self._get_run_locked(cursor, run, lock=False)
            self.connection.commit()
            return record
        except Exception:
            self.connection.rollback()
            raise

    def transition_run(
        self,
        run: RunIdentity,
        state: RunState | str,
        *,
        failure_details: Mapping[str, Any] | None = None,
    ) -> RunRecord:
        desired = _run_state(state)
        try:
            with self.connection.cursor() as cursor:
                record = self._transition_existing_run(
                    cursor,
                    run,
                    desired,
                    failure_details=failure_details,
                )
            self.connection.commit()
            return record
        except Exception:
            self.connection.rollback()
            raise

    def succeed_run_with_artifacts(
        self,
        run: RunIdentity,
        artifacts: Sequence[ArtifactRegistration],
    ) -> RunRecord:
        try:
            with self.connection.cursor() as cursor:
                record = self._get_run_locked(cursor, run, lock=True)
                if record.state == RunState.SUCCEEDED:
                    for artifact in artifacts:
                        self._ensure_artifact(cursor, run, artifact)
                    result = self._get_run_locked(cursor, run, lock=False)
                else:
                    result = self._transition_existing_run(cursor, run, RunState.SUCCEEDED)
                    for artifact in artifacts:
                        self._ensure_artifact(cursor, run, artifact)
            self.connection.commit()
            return result
        except Exception:
            self.connection.rollback()
            raise

    def get_run(self, run: RunIdentity) -> RunRecord:
        with self.connection.cursor() as cursor:
            return self._get_run_locked(cursor, run, lock=False)

    def list_runs_for_run_spec(self, run_spec: RunSpecIdentity | str) -> tuple[RunRecord, ...]:
        fingerprint = run_spec if isinstance(run_spec, str) else run_spec.fingerprint
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT run_spec_fingerprint, execution_id, state, failure_details
                  FROM experiment.runs
                 WHERE run_spec_fingerprint = %s
                 ORDER BY execution_id
                """,
                (fingerprint,),
            )
            return tuple(_run_record(row) for row in cursor.fetchall())

    def list_artifacts(self, run: RunIdentity) -> tuple[ArtifactRecord, ...]:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT run_spec_fingerprint, execution_id, artifact_role,
                       artifact_content_fingerprint, locator
                  FROM experiment.artifacts
                 WHERE run_spec_fingerprint = %s AND execution_id = %s
                 ORDER BY artifact_role, artifact_content_fingerprint
                """,
                (run.run_spec_identity.fingerprint, run.execution_id),
            )
            return tuple(
                ArtifactRecord(
                    run_spec_fingerprint=str(row[0]),
                    execution_id=str(row[1]),
                    artifact_role=str(row[2]),
                    artifact_content_fingerprint=str(row[3]),
                    locator=_mapping_or_none(row[4]),
                )
                for row in cursor.fetchall()
            )

    def _ensure_study(self, cursor: Any, study: StudyIdentity) -> None:
        if not isinstance(study, StudyIdentity):
            raise TypeError("study must be a StudyIdentity")
        self._ensure_payload(
            cursor,
            table="experiment.studies",
            key_column="study_fingerprint",
            key_value=study.fingerprint,
            payload=study.fingerprint_payload(),
        )

    def _ensure_trial(self, cursor: Any, trial: TrialIdentity) -> None:
        if not isinstance(trial, TrialIdentity):
            raise TypeError("trial must be a TrialIdentity")
        self._ensure_study(cursor, trial.study_identity)
        self._ensure_payload(
            cursor,
            table="experiment.trials",
            key_column="trial_fingerprint",
            key_value=trial.fingerprint,
            payload=trial.fingerprint_payload(),
            extra_columns=("study_fingerprint",),
            extra_values=(trial.study_identity.fingerprint,),
        )

    def _ensure_run_spec(self, cursor: Any, run_spec: RunSpecIdentity) -> None:
        if not isinstance(run_spec, RunSpecIdentity):
            raise TypeError("run_spec must be a RunSpecIdentity")
        self._ensure_trial(cursor, run_spec.trial_identity)
        self._ensure_payload(
            cursor,
            table="experiment.run_specs",
            key_column="run_spec_fingerprint",
            key_value=run_spec.fingerprint,
            payload=run_spec.fingerprint_payload(),
            extra_columns=("trial_fingerprint",),
            extra_values=(run_spec.trial_identity.fingerprint,),
        )

    def _ensure_artifact_content(self, cursor: Any, content: ArtifactContentIdentity) -> None:
        if not isinstance(content, ArtifactContentIdentity):
            raise TypeError("content must be an ArtifactContentIdentity")
        self._ensure_payload(
            cursor,
            table="experiment.artifact_contents",
            key_column="artifact_content_fingerprint",
            key_value=content.fingerprint,
            payload=content.fingerprint_payload(),
            extra_columns=("artifact_kind", "artifact_schema_version", "content_identity"),
            extra_values=(content.artifact_kind, content.artifact_schema_version, content.content_identity),
        )

    def _ensure_payload(
        self,
        cursor: Any,
        *,
        table: str,
        key_column: str,
        key_value: str,
        payload: Mapping[str, Any],
        extra_columns: tuple[str, ...] = (),
        extra_values: tuple[Any, ...] = (),
    ) -> None:
        columns = (key_column, *extra_columns, "payload")
        placeholders = ", ".join(["%s"] * len(columns))
        cursor.execute(
            f"""
            INSERT INTO {table} ({", ".join(columns)})
            VALUES ({placeholders})
            ON CONFLICT ({key_column}) DO NOTHING
            RETURNING {key_column}
            """,
            (key_value, *extra_values, _jsonb(payload)),
        )
        if cursor.fetchone() is not None:
            return
        cursor.execute(
            f"""
            SELECT {", ".join((*extra_columns, "payload"))}
              FROM {table}
             WHERE {key_column} = %s
            """,
            (key_value,),
        )
        row = cursor.fetchone()
        if row is None:
            raise ExperimentPersistenceConflict(f"{table} write did not converge")
        existing_extras = row[:len(extra_columns)]
        existing_payload = row[len(extra_columns)]
        if tuple(str(value) for value in existing_extras) != tuple(str(value) for value in extra_values):
            raise ExperimentPersistenceConflict(f"{key_column} parent relationship conflicts")
        if _canonical_json(existing_payload) != _canonical_json(payload):
            raise ExperimentPersistenceConflict(f"{key_column} payload conflicts")

    def _ensure_run_registered(self, cursor: Any, run: RunIdentity) -> None:
        if not isinstance(run, RunIdentity):
            raise TypeError("run must be a RunIdentity")
        cursor.execute(
            """
            INSERT INTO experiment.runs
                (run_spec_fingerprint, execution_id, state)
            VALUES (%s, %s, 'REGISTERED')
            ON CONFLICT (run_spec_fingerprint, execution_id) DO NOTHING
            RETURNING execution_id
            """,
            (run.run_spec_identity.fingerprint, run.execution_id),
        )
        cursor.fetchone()

    def _get_run_locked(self, cursor: Any, run: RunIdentity, *, lock: bool) -> RunRecord:
        suffix = " FOR UPDATE" if lock else ""
        cursor.execute(
            f"""
            SELECT run_spec_fingerprint, execution_id, state, failure_details
              FROM experiment.runs
             WHERE run_spec_fingerprint = %s AND execution_id = %s
            {suffix}
            """,
            (run.run_spec_identity.fingerprint, run.execution_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise ExperimentPersistenceNotFound("run is not registered")
        return _run_record(row)

    def _transition_existing_run(
        self,
        cursor: Any,
        run: RunIdentity,
        state: RunState,
        *,
        failure_details: Mapping[str, Any] | None = None,
    ) -> RunRecord:
        current = self._get_run_locked(cursor, run, lock=True)
        details = _normalize_failure_details(failure_details) if state == RunState.FAILED else None
        if current.state == state:
            if _canonical_json(current.failure_details) != _canonical_json(details):
                raise ExperimentPersistenceConflict("run state evidence conflicts")
            return current
        if current.state in TERMINAL_RUN_STATES:
            raise ExperimentPersistenceConflict("terminal run state is absorbing")
        if (current.state, state) not in _ALLOWED_TRANSITIONS:
            raise ExperimentPersistenceConflict(f"illegal run transition {current.state.value} -> {state.value}")
        cursor.execute(
            """
            UPDATE experiment.runs
               SET state = %s,
                   started_at = CASE
                       WHEN %s = 'RUNNING' THEN COALESCE(started_at, now())
                       ELSE started_at
                   END,
                   completed_at = CASE
                       WHEN %s IN ('SUCCEEDED','FAILED') THEN now()
                       ELSE completed_at
                   END,
                   failure_details = %s,
                   updated_at = now()
             WHERE run_spec_fingerprint = %s AND execution_id = %s
             RETURNING run_spec_fingerprint, execution_id, state, failure_details
            """,
            (
                state.value,
                state.value,
                state.value,
                _jsonb(details) if details is not None else None,
                run.run_spec_identity.fingerprint,
                run.execution_id,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            raise ExperimentPersistenceNotFound("run disappeared during transition")
        return _run_record(row)

    def _ensure_artifact(
        self,
        cursor: Any,
        run: RunIdentity,
        registration: ArtifactRegistration,
    ) -> None:
        if not isinstance(registration, ArtifactRegistration):
            raise TypeError("artifact registration must be an ArtifactRegistration")
        artifact = registration.identity
        if artifact.run_identity.stable_dict() != run.stable_dict():
            raise ExperimentPersistenceConflict("artifact run identity does not match transition run")
        self._ensure_artifact_content(cursor, artifact.artifact_content_identity)
        cursor.execute(
            """
            SELECT artifact_content_fingerprint, locator
              FROM experiment.artifacts
             WHERE run_spec_fingerprint = %s
               AND execution_id = %s
               AND artifact_role = %s
             FOR UPDATE
            """,
            (run.run_spec_identity.fingerprint, run.execution_id, artifact.artifact_role),
        )
        row = cursor.fetchone()
        if row is not None:
            if str(row[0]) != artifact.artifact_content_identity.fingerprint:
                raise ExperimentPersistenceConflict("artifact role already claims different content")
            return
        cursor.execute(
            """
            INSERT INTO experiment.artifacts
                (run_spec_fingerprint, execution_id, artifact_role,
                 artifact_content_fingerprint, locator)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                run.run_spec_identity.fingerprint,
                run.execution_id,
                artifact.artifact_role,
                artifact.artifact_content_identity.fingerprint,
                _jsonb(registration.locator) if registration.locator is not None else None,
            ),
        )


def _run_state(value: RunState | str) -> RunState:
    if isinstance(value, RunState):
        return value
    try:
        return RunState(value)
    except ValueError as exc:
        raise ExperimentPersistenceConflict("unknown run state") from exc


def _run_record(row: Any) -> RunRecord:
    return RunRecord(
        run_spec_fingerprint=str(row[0]),
        execution_id=str(row[1]),
        state=_run_state(str(row[2])),
        failure_details=_mapping_or_none(row[3]),
    )


def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ExperimentPersistenceConflict("stored JSON payload is not an object")
    return dict(value)


def _normalize_failure_details(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ExperimentPersistenceConflict("failure_details must be a mapping")
    return dict(value)


def _canonical_json(value: Any) -> str:
    return canonical_bytes(value, profile="sorted-compact-ascii-v1", allow_nan=False).decode("utf-8")


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError:
        return value
    return Jsonb(value)


__all__ = [
    "ArtifactRecord",
    "ArtifactRegistration",
    "ExperimentPersistenceConflict",
    "ExperimentPersistenceError",
    "ExperimentPersistenceNotFound",
    "ExperimentRepository",
    "RunRecord",
    "RunState",
    "TERMINAL_RUN_STATES",
]
