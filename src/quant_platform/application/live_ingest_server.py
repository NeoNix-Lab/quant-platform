"""ADR-0043: bounded, continuous live-ingest server v1 composition.

This module owns loop composition only (DG-D/C05 boundary): it never reads
argv/environment and never installs OS signal handlers -- that belongs to
``tools/live_ingest_server.py``. It introduces no new acquisition,
canonicalization, publication, certification, cataloging or checkpoint
semantics.

Steady state (ADR-0043 S1) is the bounded live acquisition/publish/checkpoint
composition PR #122 already proved
(``bybit_live.run_real_server_restart_publish_phase``, which drives the same
WebSocket acquisition seam ``run_real_server_publish_proof`` uses and then
advances the checkpoint only after that durable publish). The bounded
recent-trades restart/reconcile composition
(``bybit_live.run_real_server_restart_phase``) is entered only at actual
start/restart or after a cycle whose acquisition could not prove it stayed
connected -- never as the repeating steady-state acquisition source, which
would silently turn this into a REST-reconciliation poller instead of a live
WebSocket ingest server.

Existing functions are called through the ``bybit_live`` module object
(rather than imported by name) so tests can monkeypatch
``bybit_live.run_real_server_restart_phase`` etc. exactly the way
``tests/test_bybit_live_checkpoint_v1.py`` already does.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import time
from typing import Any

from quant_platform.application import bybit_live
from quant_platform.application.live_gap_orchestration import (
    LongGapRecord,
    SESSION_CONTINUOUS,
    SESSION_RESUMED_WITH_EXPLICIT_GAP,
    load_open_long_gap_records,
    long_gap_interval_from_restart_report,
    open_gap_payload,
    record_explicit_long_gap,
)
from quant_platform.data.models import Instant
from quant_platform.operations.capacity import CapacityObservation, StorageRoot, observe_capacity
from quant_platform.operations.checkpoint import CheckpointStore
from quant_platform.operations.observability import (
    EvidenceReference,
    HealthState,
    OperationalSignalV1,
    SignalKind,
    SubjectReference,
)
from quant_platform.operations.pressure import PressureDecision, PressurePolicyDefinition, evaluate_pressure


CAPABILITY_ID = "live-ingest-server-v1"

# Final loop outcomes.
SERVER_STOPPED = "STOPPED"
SERVER_FAILED = "FAILED"
# Retained for API compatibility with the ADR-0043 first slice. Issue #127
# now records the gap explicitly and continues with a new governed live
# segment, so this is no longer a terminal loop status.
SERVER_GAP_DETECTED_AWAITING_REMEDIATION = "GAP_DETECTED_AWAITING_REMEDIATION"


@dataclass(frozen=True, slots=True, kw_only=True)
class LiveIngestServerConfigV1:
    """Typed, immutable composition config (C05: caller resolves values;
    this module never reads CLI/environment itself)."""

    storage_root: str | Path
    storage_root_id: str
    checkpoint_path: str | Path
    pressure_policy: PressurePolicyDefinition
    dsn: str | None = None
    producer: str = CAPABILITY_ID
    code_ref: str = CAPABILITY_ID
    max_messages_per_cycle: int = 1000
    max_seconds_per_cycle: float = 20.0
    recent_limit: int = 1000
    cycle_interval_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class LiveIngestServerReport:
    status: str  # STOPPED | FAILED
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


def _observation_unavailable_signal(subject: SubjectReference, *, reason: str, **payload: Any) -> OperationalSignalV1:
    return OperationalSignalV1(
        subject=subject,
        capability_id=CAPABILITY_ID,
        kind=SignalKind.OBSERVATION_UNAVAILABLE,
        observed_at=_now(),
        payload={"reason": reason, "health": HealthState.UNKNOWN.value, **payload},
    )


def _checkpoint_evidence(checkpoint_identity: str | None) -> tuple[EvidenceReference, ...]:
    if not checkpoint_identity:
        return ()
    return (EvidenceReference(evidence_kind="checkpoint_identity", evidence_identity=checkpoint_identity),)


def _open_gap_evidence(records: tuple[LongGapRecord, ...]) -> tuple[EvidenceReference, ...]:
    return tuple(
        EvidenceReference(evidence_kind="coverage_manifest", evidence_identity=record.coverage_id)
        for record in records
    )


def _session_state(records: tuple[LongGapRecord, ...]) -> str:
    return SESSION_RESUMED_WITH_EXPLICIT_GAP if records else SESSION_CONTINUOUS


def _open_gap_signal_payload(records: tuple[LongGapRecord, ...]) -> dict[str, Any]:
    return open_gap_payload(records) if records else {}


def _merge_open_gap_record(records: list[LongGapRecord], record: LongGapRecord) -> None:
    for index, existing in enumerate(records):
        if existing.interval.start_key.stable_dict() == record.interval.start_key.stable_dict():
            records[index] = record
            return
    records.append(record)


def _pressure_evidence(
    config: LiveIngestServerConfigV1,
) -> tuple[dict[str, Any], tuple[EvidenceReference, ...]]:
    """K04/K05 evidence for one cycle (ADR-0043 §4): observe capacity, fold
    the resulting pressure decision/unavailable identity into the caller's
    HEALTH_SNAPSHOT payload/evidence. Never raises: an observation/decision
    failure is itself explicit K04/K05 evidence, not a loop failure."""

    capacity = observe_capacity(StorageRoot(config.storage_root_id, Path(config.storage_root)))
    decision = evaluate_pressure(policy=config.pressure_policy, capacity=capacity, as_of=datetime.now(timezone.utc))
    payload: dict[str, Any] = {}
    evidence: tuple[EvidenceReference, ...] = ()
    if isinstance(capacity, CapacityObservation):
        payload["capacity_available_bytes"] = capacity.available_bytes
    if isinstance(decision, PressureDecision):
        payload["pressure_state"] = decision.state.value
        payload["pressure_new_writes_allowed"] = decision.restrictions.new_data_producing_writes_allowed
        evidence = (EvidenceReference(evidence_kind="pressure_decision", evidence_identity=decision.decision_identity),)
    else:
        payload["pressure_state"] = "UNAVAILABLE"
        payload["pressure_unavailable_reason"] = decision.reason.value
        evidence = (EvidenceReference(evidence_kind="pressure_decision_unavailable", evidence_identity=decision.decision_identity),)
    return payload, evidence


def _wait_between_cycles(
    seconds: float, *, stop_requested: Callable[[], bool], sleep: Callable[[float], None]
) -> None:
    """Pace steady-state cycles (ADR-0043 §4 stop-check interval) so a fast
    or empty cycle cannot spin against the provider/catalog. Ticks in <=1s
    steps so a stop request is honored promptly regardless of the
    configured interval."""

    remaining = seconds
    tick = 1.0
    while remaining > 0 and not stop_requested():
        step = min(tick, remaining)
        sleep(step)
        remaining -= step


def run_live_ingest_server(
    config: LiveIngestServerConfigV1,
    *,
    stop_requested: Callable[[], bool] = lambda: False,
    on_signal: Callable[[OperationalSignalV1], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> LiveIngestServerReport:
    """Run the bounded live-ingest server loop (ADR-0043 §1).

    ``stop_requested`` is checked before each cycle starts and between the
    ticks of the post-cycle pacing wait, never mid-cycle: a durable publish
    either completes before the next check or hasn't started, so a clean
    stop is always ADR-0042 §3's ordinary "crash after checkpoint advance"
    case, not a new invariant. The executable boundary
    (``tools/live_ingest_server.py``) supplies a predicate backed by its own
    signal handling; this module never touches ``signal``/process control
    itself.

    Any unhandled exception during a cycle (provider, catalog or checkpoint
    failure) is caught, reported as a ``FAILURE`` signal and stops the loop
    -- it never escapes silently past only a ``STARTING``/``RUNNING``
    transition.
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
    gap_load = load_open_long_gap_records(storage_root=config.storage_root)
    open_gaps: list[LongGapRecord] = list(gap_load.records)
    if gap_load.unparseable_manifest_paths:
        # A durable known_gap manifest exists and was confidently classified
        # as ours, but its structured evidence could not be reconstructed.
        # This must never silently read as "no open gap" -- surface it as
        # explicit reduced-observability evidence (K03's own UNKNOWN-never-
        # HEALTHY discipline) rather than dropping it from open_gaps.
        emit(_observation_unavailable_signal(
            subject,
            reason=(
                f"{len(gap_load.unparseable_manifest_paths)} durable long-gap coverage "
                "manifest(s) could not be reconstructed at startup"
            ),
            unparseable_manifest_paths=list(gap_load.unparseable_manifest_paths),
        ))
    # Decided lazily from cycle 1's own protected load, not a separate
    # pre-loop CheckpointStore.load() call: a corrupt/unreadable checkpoint
    # file must surface as a FAILURE signal through the same try/except
    # every other cycle failure does, not raise before the loop even starts.
    needs_reconcile: bool | None = None

    while True:
        if stop_requested():
            break
        cycles += 1

        try:
            checkpoint_exists = store.load() is not None
            if needs_reconcile is None:
                # A checkpoint already on disk on this first cycle means the
                # process is resuming an existing recovery domain --
                # ADR-0043's "entered on start/reconnect" reconcile step,
                # not the steady-state acquisition path.
                needs_reconcile = checkpoint_exists
            pressure_payload, pressure_evidence = _pressure_evidence(config)

            if checkpoint_exists and needs_reconcile:
                report = bybit_live.run_real_server_restart_phase(
                    checkpoint_path=config.checkpoint_path,
                    dsn=config.dsn,
                    storage_root=config.storage_root,
                    storage_root_id=config.storage_root_id,
                    producer=config.producer,
                    code_ref=config.code_ref,
                    recent_limit=config.recent_limit,
                )
                if report.status == "PASS":
                    needs_reconcile = False
                    open_gap_records = tuple(open_gaps)
                    emit(_health_signal(
                        subject, health=HealthState.HEALTHY, session_state=_session_state(open_gap_records),
                        cycle=cycles, phase="reconcile", checkpoint_identity=report.checkpoint_identity,
                        records=report.restart_accepted_records or 0,
                        evidence=(
                            _checkpoint_evidence(report.checkpoint_identity)
                            + pressure_evidence
                            + _open_gap_evidence(open_gap_records)
                        ),
                        **_open_gap_signal_payload(open_gap_records),
                        **pressure_payload,
                    ))
                elif report.status == "REAL_RESTART_PROOF_PENDING":
                    needs_reconcile = False
                    open_gap_records = tuple(open_gaps)
                    emit(_health_signal(
                        subject, health=HealthState.HEALTHY, session_state=_session_state(open_gap_records),
                        cycle=cycles, phase="reconcile",
                        reason="continuity proven; no new records to reconcile",
                        evidence=pressure_evidence + _open_gap_evidence(open_gap_records),
                        **_open_gap_signal_payload(open_gap_records), **pressure_payload,
                    ))
                elif report.status == "GAP_DETECTED":
                    # ADR-0042 S5/S6 + ADR-0044: checkpoint intentionally
                    # stays unadvanced for the interrupted segment. Issue
                    # #127 records the interval as explicit non-complete
                    # coverage and lets the server continue with a new live
                    # segment; only an A10 repair cutover may later close it.
                    detected_at = _now()
                    interval = long_gap_interval_from_restart_report(report, detected_at=detected_at)
                    gap_record = record_explicit_long_gap(
                        storage_root=config.storage_root,
                        interval=interval,
                        producer=config.producer,
                        code_ref=config.code_ref,
                        detected_at=detected_at,
                    )
                    _merge_open_gap_record(open_gaps, gap_record)
                    open_gap_records = tuple(open_gaps)
                    needs_reconcile = False
                    emit(_health_signal(
                        subject, health=HealthState.DEGRADED, session_state=SESSION_RESUMED_WITH_EXPLICIT_GAP,
                        cycle=cycles, phase="reconcile", checkpoint_identity=report.checkpoint_identity,
                        reason=interval.reason,
                        gap_coverage_id=gap_record.coverage_id,
                        gap_assertion_id=gap_record.assertion_id,
                        gap_manifest_path=gap_record.coverage_manifest_path,
                        gap_manifest_sha256=gap_record.coverage_manifest_sha256,
                        gap_supersedes=gap_record.supersedes_coverage_id,
                        gap_state=gap_record.interval_state,
                        repair_source_id=gap_record.repair_source_evaluation.source_id,
                        repair_source_completed=gap_record.repair_source_evaluation.completed,
                        evidence=(
                            _checkpoint_evidence(report.checkpoint_identity)
                            + pressure_evidence
                            + _open_gap_evidence((gap_record,))
                        ),
                        **_open_gap_signal_payload(open_gap_records),
                        **pressure_payload,
                    ))
                else:
                    emit(_failure_signal(
                        subject, failure_code=report.status, cycle=cycles, phase="reconcile",
                    ))
                    status = SERVER_FAILED
                    break
            else:
                report = bybit_live.run_real_server_restart_publish_phase(
                    max_messages=config.max_messages_per_cycle,
                    max_seconds=config.max_seconds_per_cycle,
                    storage_root=config.storage_root,
                    storage_root_id=config.storage_root_id,
                    dsn=config.dsn,
                    checkpoint_path=config.checkpoint_path,
                    producer=config.producer,
                    code_ref=config.code_ref,
                )
                if report.status == "CHECKPOINT_PERSISTED":
                    open_gap_records = tuple(open_gaps)
                    emit(_health_signal(
                        subject, health=HealthState.HEALTHY, session_state=_session_state(open_gap_records),
                        cycle=cycles, phase="acquire", checkpoint_identity=report.checkpoint_identity,
                        evidence=(
                            _checkpoint_evidence(report.checkpoint_identity)
                            + pressure_evidence
                            + _open_gap_evidence(open_gap_records)
                        ),
                        **_open_gap_signal_payload(open_gap_records),
                        **pressure_payload,
                    ))
                elif report.status == "LIVE_PROVIDER_PROOF_PENDING":
                    # Expected, retryable: no messages arrived within this
                    # cycle's bounded acquisition window (ADR-0040 §6/§10 --
                    # at-least-once, not a promise every cycle finds new
                    # trades). Treated as a possible session drop: the next
                    # cycle reconciles before resuming acquisition, exactly
                    # ADR-0043's "if the session dropped: loop back to
                    # resume_live_ingest" -- only meaningful once a
                    # checkpoint exists to reconcile against.
                    if store.load() is not None:
                        needs_reconcile = True
                    open_gap_records = tuple(open_gaps)
                    emit(_health_signal(
                        subject, health=HealthState.DEGRADED, session_state=_session_state(open_gap_records),
                        cycle=cycles, phase="acquire",
                        reason="no acquisition evidence within this cycle's bounded window",
                        evidence=pressure_evidence + _open_gap_evidence(open_gap_records),
                        **_open_gap_signal_payload(open_gap_records), **pressure_payload,
                    ))
                else:
                    emit(_failure_signal(
                        subject, failure_code=report.status, cycle=cycles, phase="acquire",
                    ))
                    status = SERVER_FAILED
                    break
        except Exception as exc:  # noqa: BLE001 - any cycle failure must surface as a FAILURE signal
            emit(_failure_signal(
                subject, failure_code=type(exc).__name__, cycle=cycles, message=str(exc),
            ))
            status = SERVER_FAILED
            break

        _wait_between_cycles(config.cycle_interval_seconds, stop_requested=stop_requested, sleep=sleep)

    emit(_lifecycle_signal(subject, previous_state="RUNNING", resulting_state=status, cycles=cycles))
    return LiveIngestServerReport(status=status, cycles=cycles, signals=tuple(signals))


__all__ = [
    "CAPABILITY_ID",
    "SESSION_CONTINUOUS",
    "SESSION_RESUMED_WITH_EXPLICIT_GAP",
    "SERVER_STOPPED",
    "SERVER_FAILED",
    "SERVER_GAP_DETECTED_AWAITING_REMEDIATION",
    "LiveIngestServerConfigV1",
    "LiveIngestServerReport",
    # Re-exported so the executable boundary (tools/live_ingest_server.py)
    # can build a policy value without importing quant_platform.operations
    # directly -- executable tooling talks to the application seam only.
    "PressurePolicyDefinition",
    "run_live_ingest_server",
]
