"""ADR-0043: bounded, continuous live-ingest server v1 composition.

This module owns loop composition only (DG-D/C05 boundary): it never reads
argv/environment and never installs OS signal handlers -- that belongs to
``tools/live_ingest_server.py``. It introduces no new acquisition,
canonicalization, publication, certification, cataloging or checkpoint
semantics; the loop's steady state is exactly the existing restart/reconcile
cycle PR #122 already proved on the real server
(``bybit_live.run_real_server_restart_publish_phase`` /
``run_real_server_restart_phase``), composed here as an unbounded outer loop
instead of two separate one-shot OS-process invocations.

Existing functions are called through the ``bybit_live`` module object
(rather than imported by name) so tests can monkeypatch
``bybit_live.run_real_server_restart_phase`` etc. exactly the way
``tests/test_bybit_live_checkpoint_v1.py`` already does for the two-phase
restart composition.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from quant_platform.application import bybit_live
from quant_platform.data.models import Instant
from quant_platform.operations.checkpoint import CheckpointStore
from quant_platform.operations.observability import (
    EvidenceReference,
    HealthState,
    OperationalSignalV1,
    SignalKind,
    SubjectReference,
)


CAPABILITY_ID = "live-ingest-server-v1"

# ADR-0044 S2 session-level classification, mirrored here as plain strings
# (this module is application-owned; the state machine itself belongs to
# the long-gap orchestration ADR-0044 introduces for issue #127).
SESSION_CONTINUOUS = "CONTINUOUS"
SESSION_RESUMED_WITH_EXPLICIT_GAP = "RESUMED_WITH_EXPLICIT_GAP"

# Final loop outcomes.
SERVER_STOPPED = "STOPPED"
SERVER_FAILED = "FAILED"
# GAP_DETECTED is a valid ADR-0040 §7 outcome, not an error: the checkpoint
# is intentionally left unadvanced and the gap must be recorded through
# ADR-0044's long-gap orchestration (issue #127) before governed publication
# can safely resume with a new segment. That orchestration does not exist
# yet, so this bounded loop stops rather than inventing a new-segment
# bootstrap procedure ADR-0044 did not specify.
SERVER_GAP_DETECTED_AWAITING_REMEDIATION = "GAP_DETECTED_AWAITING_REMEDIATION"


@dataclass(frozen=True, slots=True, kw_only=True)
class LiveIngestServerConfigV1:
    """Typed, immutable composition config (C05: caller resolves values;
    this module never reads CLI/environment itself)."""

    storage_root: str | Path
    storage_root_id: str
    checkpoint_path: str | Path
    dsn: str | None = None
    producer: str = CAPABILITY_ID
    code_ref: str = CAPABILITY_ID
    max_messages_per_cycle: int = 1000
    max_seconds_per_cycle: float = 20.0
    recent_limit: int = 1000


@dataclass(frozen=True, slots=True)
class LiveIngestServerReport:
    status: str  # STOPPED | FAILED | GAP_DETECTED_AWAITING_REMEDIATION
    cycles: int
    signals: tuple[OperationalSignalV1, ...]


def _now() -> Instant:
    return Instant.parse(datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))


def _subject(config: LiveIngestServerConfigV1) -> SubjectReference:
    return SubjectReference(
        subject_kind="live-ingest-server",
        subject_identity=f"bybit:BTCUSDT:{config.storage_root_id}",
    )


def _lifecycle_signal(
    subject: SubjectReference, *, previous_state: str, resulting_state: str, **context: Any
) -> OperationalSignalV1:
    return OperationalSignalV1(
        subject=subject,
        capability_id=CAPABILITY_ID,
        kind=SignalKind.LIFECYCLE_TRANSITION,
        observed_at=_now(),
        payload={
            "previous_state": previous_state,
            "resulting_state": resulting_state,
            "context": dict(context) or {"reason": "loop lifecycle transition"},
        },
    )


def _health_signal(
    subject: SubjectReference,
    *,
    health: HealthState,
    session_state: str,
    evidence: tuple[EvidenceReference, ...] = (),
    **payload: Any,
) -> OperationalSignalV1:
    return OperationalSignalV1(
        subject=subject,
        capability_id=CAPABILITY_ID,
        kind=SignalKind.HEALTH_SNAPSHOT,
        observed_at=_now(),
        payload={"health": health.value, "session_state": session_state, **payload},
        evidence=evidence,
    )


def _failure_signal(subject: SubjectReference, *, failure_code: str, **context: Any) -> OperationalSignalV1:
    return OperationalSignalV1(
        subject=subject,
        capability_id=CAPABILITY_ID,
        kind=SignalKind.FAILURE,
        observed_at=_now(),
        payload={"failure_code": failure_code, "context": dict(context)},
    )


def run_live_ingest_server(
    config: LiveIngestServerConfigV1,
    *,
    stop_requested: Callable[[], bool] = lambda: False,
    on_signal: Callable[[OperationalSignalV1], None] | None = None,
) -> LiveIngestServerReport:
    """Run the bounded live-ingest server loop (ADR-0043 §1).

    ``stop_requested`` is checked once *before* each cycle starts, never
    mid-cycle: a durable publish either completes before the next check or
    hasn't started, so a clean stop is always ADR-0042 §3's ordinary
    "crash after checkpoint advance" case, not a new invariant. The
    executable boundary (``tools/live_ingest_server.py``) supplies a
    predicate backed by its own signal handling; this module never touches
    ``signal``/process control itself.
    """

    subject = _subject(config)
    signals: list[OperationalSignalV1] = []

    def emit(signal: OperationalSignalV1) -> None:
        signals.append(signal)
        if on_signal is not None:
            on_signal(signal)

    emit(_lifecycle_signal(subject, previous_state="STARTING", resulting_state="RUNNING"))

    store = CheckpointStore(config.checkpoint_path)
    cycles = 0
    status = SERVER_STOPPED

    while True:
        if stop_requested():
            break
        cycles += 1
        checkpoint = store.load()

        if checkpoint is None:
            publish_report = bybit_live.run_real_server_restart_publish_phase(
                max_messages=config.max_messages_per_cycle,
                max_seconds=config.max_seconds_per_cycle,
                storage_root=config.storage_root,
                storage_root_id=config.storage_root_id,
                dsn=config.dsn,
                checkpoint_path=config.checkpoint_path,
                producer=config.producer,
                code_ref=config.code_ref,
            )
            if publish_report.status == "CHECKPOINT_PERSISTED":
                emit(_health_signal(
                    subject, health=HealthState.HEALTHY, session_state=SESSION_CONTINUOUS,
                    cycle=cycles, checkpoint_identity=publish_report.checkpoint_identity,
                    evidence=_checkpoint_evidence(publish_report.checkpoint_identity),
                ))
                continue
            if publish_report.status == "LIVE_PROVIDER_PROOF_PENDING":
                # Expected, retryable: no messages arrived within this
                # cycle's bounded acquisition window (ADR-0040 §6/§10 -- at
                # least once, not a promise every cycle finds new trades).
                emit(_health_signal(
                    subject, health=HealthState.DEGRADED, session_state=SESSION_CONTINUOUS,
                    cycle=cycles, reason="no acquisition evidence within this cycle's bounded window",
                ))
                continue
            emit(_failure_signal(
                subject, failure_code=publish_report.status, cycle=cycles, phase="first-acquisition",
            ))
            status = SERVER_FAILED
            break

        restart_report = bybit_live.run_real_server_restart_phase(
            checkpoint_path=config.checkpoint_path,
            dsn=config.dsn,
            storage_root=config.storage_root,
            storage_root_id=config.storage_root_id,
            producer=config.producer,
            code_ref=config.code_ref,
            recent_limit=config.recent_limit,
        )
        if restart_report.status == "PASS":
            emit(_health_signal(
                subject, health=HealthState.HEALTHY, session_state=SESSION_CONTINUOUS,
                cycle=cycles, checkpoint_identity=restart_report.checkpoint_identity,
                records=restart_report.restart_accepted_records or 0,
                evidence=_checkpoint_evidence(restart_report.checkpoint_identity),
            ))
            continue
        if restart_report.status == "REAL_RESTART_PROOF_PENDING":
            # Continuity was proven but there was nothing new to durably
            # publish this cycle -- a normal no-op cycle, not a failure.
            emit(_health_signal(
                subject, health=HealthState.HEALTHY, session_state=SESSION_CONTINUOUS,
                cycle=cycles, reason="continuity proven; no new records this cycle",
            ))
            continue
        if restart_report.status == "GAP_DETECTED":
            # ADR-0042 S5/S6 + ADR-0044: the checkpoint is intentionally left
            # unadvanced. Recording this durably and resuming with a new
            # governed segment is issue #127's job (ADR-0044); this bounded
            # slice stops rather than inventing that procedure.
            outcome = restart_report.restart_outcome
            reason = (
                outcome.reconcile_result.evidence.get("reason")
                if outcome is not None and outcome.reconcile_result is not None
                else None
            )
            emit(_health_signal(
                subject, health=HealthState.DEGRADED, session_state=SESSION_RESUMED_WITH_EXPLICIT_GAP,
                cycle=cycles, checkpoint_identity=restart_report.checkpoint_identity,
                reason=reason or "durable anchor outside bounded reconciliation window",
            ))
            status = SERVER_GAP_DETECTED_AWAITING_REMEDIATION
            break

        emit(_failure_signal(
            subject, failure_code=restart_report.status, cycle=cycles, phase="restart-reconcile",
        ))
        status = SERVER_FAILED
        break

    emit(_lifecycle_signal(subject, previous_state="RUNNING", resulting_state=status, cycles=cycles))
    return LiveIngestServerReport(status=status, cycles=cycles, signals=tuple(signals))


def _checkpoint_evidence(checkpoint_identity: str | None) -> tuple[EvidenceReference, ...]:
    if not checkpoint_identity:
        return ()
    return (EvidenceReference(evidence_kind="checkpoint_identity", evidence_identity=checkpoint_identity),)


__all__ = [
    "CAPABILITY_ID",
    "SESSION_CONTINUOUS",
    "SESSION_RESUMED_WITH_EXPLICIT_GAP",
    "SERVER_STOPPED",
    "SERVER_FAILED",
    "SERVER_GAP_DETECTED_AWAITING_REMEDIATION",
    "LiveIngestServerConfigV1",
    "LiveIngestServerReport",
    "run_live_ingest_server",
]
