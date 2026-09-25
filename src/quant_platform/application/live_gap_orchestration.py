"""ADR-0044 long-gap recording and repair-source orchestration.

This module is application-owned glue. It derives ADR-0044's observable
long-gap state from existing A11/K10 restart evidence, writes explicit
Declared Coverage evidence for the non-complete interval, and classifies repair
source evaluation without creating a second coverage store or a speculative
repair path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from quant_platform.application.bybit_live import RealServerRestartProofReport
from quant_platform.data.manifests import ManifestEmission, emit_coverage_manifest
from quant_platform.data.models import Instant
from quant_platform.source_adapters.bybit_live import (
    BYBIT_RECENT_PUBLIC_TRADES_MAPPING_V1,
    BYBIT_RECENT_PUBLIC_TRADES_SEMANTICS_V1,
    ReconnectStatus,
    bybit_live_dataset_identity,
)


SESSION_CONTINUOUS = "CONTINUOUS"
SESSION_RESTART_RECONCILING = "RESTART_RECONCILING"
SESSION_RESUMED_WITH_EXPLICIT_GAP = "RESUMED_WITH_EXPLICIT_GAP"

GAP_DETECTED = "GAP_DETECTED"
GAP_RECORDED_NON_COMPLETE = "GAP_RECORDED_NON_COMPLETE"
REPAIR_SOURCE_EVALUATION_INCONCLUSIVE = "REPAIR_SOURCE_EVALUATION_INCONCLUSIVE"
REPAIR_SOURCE_UNPROVEN = "REPAIR_SOURCE_UNPROVEN"
REPAIR_CANDIDATE_PENDING = "REPAIR_CANDIDATE_PENDING"
REPAIR_CUTOVER_COMPLETE = "REPAIR_CUTOVER_COMPLETE"

BYBIT_PUBLIC_ARCHIVE_SOURCE_ID = "bybit-public-trading-archive-v1"
LONG_GAP_ORCHESTRATION_VERSION = "live-ingest-long-gap-orchestration-v1"


@dataclass(frozen=True, slots=True)
class TradeKeyBoundary:
    venue: str
    instrument: str
    exchange_ts: Instant
    trade_id: str

    @property
    def order_key(self) -> tuple[Instant, str]:
        return self.exchange_ts, self.trade_id

    def stable_dict(self) -> dict[str, str]:
        return {
            "venue": self.venue,
            "instrument": self.instrument,
            "exchange_ts": self.exchange_ts.isoformat(),
            "trade_id": self.trade_id,
        }


@dataclass(frozen=True, slots=True)
class LongGapInterval:
    start_key: TradeKeyBoundary
    detected_at: Instant
    reason: str
    recent_window_records: int | None
    buffered_ws_records: int | None

    @property
    def coverage_start(self) -> str:
        return _coverage_floor(self.start_key.exchange_ts).isoformat()

    @property
    def coverage_end(self) -> str:
        end = _coverage_ceil(self.detected_at)
        start = Instant.parse(self.coverage_start)
        if end <= start:
            end = Instant(start.epoch_ns + 1_000)
        return end.isoformat()


@dataclass(frozen=True, slots=True)
class RepairSourceEvaluation:
    source_id: str
    interval_state: str
    completed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LongGapRecord:
    session_state: str
    interval_state: str
    interval: LongGapInterval
    coverage_id: str
    assertion_id: str
    coverage_manifest_path: str
    coverage_manifest_sha256: str
    repair_source_evaluation: RepairSourceEvaluation


def long_gap_interval_from_restart_report(
    report: RealServerRestartProofReport,
    *,
    detected_at: Instant | str,
) -> LongGapInterval:
    """Derive ADR-0044's transient GAP_DETECTED interval from K10 evidence."""

    if report.status != "GAP_DETECTED":
        raise ValueError("long-gap interval requires a GAP_DETECTED restart report")
    outcome = report.restart_outcome
    if outcome is None or outcome.reconcile_result is None:
        raise ValueError("GAP_DETECTED restart report requires reconciliation evidence")
    if outcome.reconcile_result.status is not ReconnectStatus.UNRESOLVED_GAP:
        raise ValueError("long-gap interval requires unresolved reconnect evidence")

    evidence = outcome.reconcile_result.evidence
    durable_key = evidence.get("last_durable_key")
    if not isinstance(durable_key, Mapping):
        raise ValueError("GAP_DETECTED evidence must include last_durable_key")
    return LongGapInterval(
        start_key=TradeKeyBoundary(
            venue=_text(durable_key.get("venue"), "last_durable_key.venue"),
            instrument=_text(durable_key.get("instrument"), "last_durable_key.instrument"),
            exchange_ts=Instant.parse(_text(durable_key.get("exchange_ts"), "last_durable_key.exchange_ts")),
            trade_id=_text(durable_key.get("trade_id"), "last_durable_key.trade_id"),
        ),
        detected_at=Instant.parse(detected_at),
        reason=_text(evidence.get("reason"), "reason"),
        recent_window_records=_optional_int(evidence.get("recent_window_records")),
        buffered_ws_records=_optional_int(evidence.get("buffered_ws_records")),
    )


def evaluate_current_bybit_archive_authority(interval: LongGapInterval) -> RepairSourceEvaluation:
    """Apply ADR-0044's current source-authority disposition.

    The public archive was already re-audited by ADR-0044 and failed the S3
    criteria for the current provider behavior. This function records that
    completed negative evaluation for a concrete interval; it does not perform
    network discovery and does not authorize a repair candidate.
    """

    return RepairSourceEvaluation(
        source_id=BYBIT_PUBLIC_ARCHIVE_SOURCE_ID,
        interval_state=REPAIR_SOURCE_UNPROVEN,
        completed=True,
        reasons=(
            "no per-interval completeness attestation or checksum manifest was observed",
            "archive publication lag and recent-trades bounded window do not structurally overlap",
            f"interval_start={interval.coverage_start}",
            f"interval_end={interval.coverage_end}",
        ),
    )


def inconclusive_repair_source_evaluation(
    *, source_id: str, reason: str
) -> RepairSourceEvaluation:
    """Represent a retryable evaluation failure distinctly from rejection."""

    return RepairSourceEvaluation(
        source_id=_text(source_id, "source_id"),
        interval_state=REPAIR_SOURCE_EVALUATION_INCONCLUSIVE,
        completed=False,
        reasons=(_text(reason, "reason"),),
    )


def repair_candidate_state_for_evaluation(evaluation: RepairSourceEvaluation) -> str:
    """Return the only candidate state reachable from source-authority proof."""

    if evaluation.interval_state == REPAIR_SOURCE_UNPROVEN:
        return REPAIR_SOURCE_UNPROVEN
    if evaluation.interval_state == REPAIR_SOURCE_EVALUATION_INCONCLUSIVE:
        return REPAIR_SOURCE_EVALUATION_INCONCLUSIVE
    return REPAIR_CANDIDATE_PENDING


def record_explicit_long_gap(
    *,
    storage_root: str | Path,
    interval: LongGapInterval,
    producer: str,
    code_ref: str,
    detected_at: Instant | str,
    evaluation: RepairSourceEvaluation | None = None,
) -> LongGapRecord:
    """Persist one explicit non-complete coverage manifest for a long gap."""

    identity = bybit_live_dataset_identity()
    created_at = Instant.parse(detected_at)
    evaluation = evaluation or evaluate_current_bybit_archive_authority(interval)
    dataset_root = Path(storage_root).joinpath(
        identity.layer,
        identity.dataset_kind,
        identity.venue,
        identity.instrument,
        identity.record_schema_id,
    )
    tag = _coverage_tag(created_at)
    coverage_id = f"long-gap-{tag}"
    assertion_id = f"long-gap-assertion-{tag}"
    coverage_manifest_path = dataset_root / f"coverage-manifest-{coverage_id}.json"
    detail = "; ".join(
        (
            f"state={GAP_RECORDED_NON_COMPLETE}",
            f"repair_state={evaluation.interval_state}",
            f"repair_source={evaluation.source_id}",
            f"reason={interval.reason}",
            f"last_durable_key={interval.start_key.stable_dict()}",
            f"recent_window_records={interval.recent_window_records}",
            f"buffered_ws_records={interval.buffered_ws_records}",
            f"evaluation_reasons={list(evaluation.reasons)}",
        )
    )
    emission = emit_coverage_manifest(
        coverage_manifest_path,
        dataset_identity=identity,
        source_dataset_identity=identity,
        coverage_id=coverage_id,
        supersedes=None,
        created_at=created_at,
        acquisition={
            "basis": "reconciliation",
            "intent_start": interval.coverage_start,
            "intent_end": interval.coverage_end,
            "source_semantics": BYBIT_RECENT_PUBLIC_TRADES_SEMANTICS_V1,
            "mapping": BYBIT_RECENT_PUBLIC_TRADES_MAPPING_V1,
        },
        assertions=(
            {
                "assertion_id": assertion_id,
                "start": interval.coverage_start,
                "end": interval.coverage_end,
                "status": "known_gap",
                "partitions": [],
                "evidence": ({"kind": "transport_interruption", "detail": detail},),
            },
        ),
        producer=producer,
        code_ref=code_ref,
    )
    return LongGapRecord(
        session_state=SESSION_RESUMED_WITH_EXPLICIT_GAP,
        interval_state=evaluation.interval_state,
        interval=interval,
        coverage_id=coverage_id,
        assertion_id=assertion_id,
        coverage_manifest_path=str(coverage_manifest_path),
        coverage_manifest_sha256=emission.manifest_sha256,
        repair_source_evaluation=evaluation,
    )


def open_gap_payload(records: tuple[LongGapRecord, ...]) -> dict[str, Any]:
    return {
        "open_gap_count": len(records),
        "open_gap_coverage_ids": tuple(record.coverage_id for record in records),
        "open_gap_states": tuple(record.interval_state for record in records),
    }


def _coverage_floor(instant: Instant) -> Instant:
    return Instant((instant.epoch_ns // 1_000) * 1_000)


def _coverage_ceil(instant: Instant) -> Instant:
    return Instant(((instant.epoch_ns + 999) // 1_000) * 1_000)


def _coverage_tag(instant: Instant) -> str:
    return instant.to_datetime().strftime("%Y%m%d-%H%M%S-%f")


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError("expected integer evidence value")
    return value


__all__ = [
    "BYBIT_PUBLIC_ARCHIVE_SOURCE_ID",
    "GAP_DETECTED",
    "GAP_RECORDED_NON_COMPLETE",
    "LONG_GAP_ORCHESTRATION_VERSION",
    "REPAIR_CANDIDATE_PENDING",
    "REPAIR_CUTOVER_COMPLETE",
    "REPAIR_SOURCE_EVALUATION_INCONCLUSIVE",
    "REPAIR_SOURCE_UNPROVEN",
    "SESSION_CONTINUOUS",
    "SESSION_RESTART_RECONCILING",
    "SESSION_RESUMED_WITH_EXPLICIT_GAP",
    "LongGapInterval",
    "LongGapRecord",
    "RepairSourceEvaluation",
    "TradeKeyBoundary",
    "evaluate_current_bybit_archive_authority",
    "inconclusive_repair_source_evaluation",
    "long_gap_interval_from_restart_report",
    "open_gap_payload",
    "record_explicit_long_gap",
    "repair_candidate_state_for_evaluation",
]
