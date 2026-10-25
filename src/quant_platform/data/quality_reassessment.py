"""R01 (DG-R): a superseding reassessment of the current live revision of one natural partition (ADR-0074 Amendment 2, A2.3).

``QualityLifecycleCatalog.apply_partition_lifecycle`` (A16) accepts ``valid`` as a source state and maps a current ``fail`` assessment
to ``invalid``, but nothing could write that assessment for a revision that is not ``closed``: ``record_quality_report`` refuses every
other state and the repair path writes reports only for candidates inside its own cutover. This module is the missing writer. It
appends one new report (a ``fail`` that retires the revision from reads, or a ``pass`` that restores it) whose bound evidence is the
superseded assessment's, unchanged, and whose ``metrics.supersedes_assessment_signature`` is the exact semantic signature of the
current leaf; then it runs the existing lifecycle seam. Nothing is deleted or rewritten: the bytes, the manifests and every earlier
report stay as they were, and the old state is recoverable by a later ``pass``.

The writer does not widen the meaning of the lifecycle: it files the report under the same check suite and certification profile as
the assessment it supersedes, it must pass ``current_partition_report`` before anything is written, and it refuses a revision that is
superseded, foreign (another dataset, key or revision), not the live one of its key, ``writing`` or ``closed`` (ordinary S13 owns
``closed``). The report says the partition is incomplete for its day, not that it is corrupt.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .models import Instant
from .quality_lifecycle import (
    QUALITY_LIFECYCLE_CODE_ID,
    SUPERSEDES_ASSESSMENT_SIGNATURE,
    QualityLifecycleCatalog,
    QualityLifecycleRefusal,
    QualityLifecycleResult,
    _identity,
    _identity_from_row,
    _live_row,
    current_partition_report,
    select_current_quality_assessment,
    semantic_assessment_signature,
)

REASON_LEGACY_INCOMPLETE_DAY = "legacy_publication_incomplete_day"
REASSESSMENT_STATUSES = frozenset({"pass", "fail"})
# ``closed`` is S13's (``record_quality_report``); ``writing`` and ``superseded`` are never reassessed.
REASSESSABLE_STATES = frozenset({"valid", "degraded", "invalid"})


class QualityReassessmentRefusal(QualityLifecycleRefusal):
    """The reassessment was refused; nothing was written."""


@dataclass(frozen=True, slots=True)
class LifecycleInputs:
    """The durable evidence of one partition, as the lifecycle seam wants it (read from the three manifest kinds)."""

    dataset: dict[str, Any]
    dataset_sha256: str
    partition: dict[str, Any]
    partition_sha256: str
    coverage_ids: tuple[str, ...]
    assertion_ids: tuple[str, ...]
    coverage_sha256: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReassessmentResult:
    """``report_id`` is the report this call wrote; ``None`` (``inserted`` False) when the current assessment already says it."""

    report_id: str | None
    inserted: bool
    superseded_signature: str
    new_signature: str
    lifecycle: QualityLifecycleResult


def load_lifecycle_inputs(
    dataset_manifest_path: str | Path, partition_manifest_path: str | Path, coverage_manifest_paths: Sequence[str | Path],
) -> LifecycleInputs:
    """Read and validate the durable manifests of one partition (the same loader and hashes S13 uses)."""

    from .publication import SealedPartitionEvidence, _load_evidence

    paths = tuple(Path(item) for item in coverage_manifest_paths)
    loaded = _load_evidence(SealedPartitionEvidence(
        Path(dataset_manifest_path), Path(partition_manifest_path), paths, Path(partition_manifest_path), "unused",
    ))
    return LifecycleInputs(
        dataset=loaded.dataset,
        dataset_sha256=loaded.dataset_sha256,
        partition=loaded.partition,
        partition_sha256=loaded.partition_sha256,
        coverage_ids=tuple(item.get("coverage_id") for item in loaded.coverage),
        assertion_ids=tuple(
            assertion.get("assertion_id") for document in loaded.coverage for assertion in document.get("assertions") or ()
        ),
        coverage_sha256=tuple(loaded.coverage_sha256),
    )


def reassess_live_partition(
    connection: Any,
    *,
    inputs: LifecycleInputs,
    coverage_start: Instant,
    coverage_end: Instant,
    storage_root_id: str,
    expected_profile: str,
    expected_check_suite: str,
    status: str,
    code_ref: str,
    archive_identity: Mapping[str, Any] | None = None,
    lifecycle_code_ref: str = QUALITY_LIFECYCLE_CODE_ID,
) -> ReassessmentResult:
    """Append the reassessment ``status`` (``fail`` retires, ``pass`` restores) for the live revision named by ``inputs``.

    Two short transactions, each committed on its own: the first locks the dataset row and then the key's topology (the order of the
    lifecycle application), checks the target and writes the report; the second is the existing lifecycle seam. A crash between the
    two leaves a current ``fail`` with the state still ``valid``; running the same call again converges (the report is not written
    twice and the seam applies the state). ``archive_identity`` names the archive file the incompleteness is measured against
    (required for ``fail``)."""

    if status not in REASSESSMENT_STATUSES:
        raise QualityReassessmentRefusal(f"a reassessment is a pass or a fail, not {status!r}")
    if not isinstance(code_ref, str) or not code_ref.strip():
        raise QualityReassessmentRefusal("a reassessment requires the producing commit as code_ref")
    if status == "fail" and not archive_identity:
        raise QualityReassessmentRefusal("a failing reassessment must name the archive identity it is measured against")
    code_ref = code_ref.strip()
    violations: list[dict[str, Any]] = [] if status == "pass" else [{
        "category": "coverage",
        "message": REASON_LEGACY_INCOMPLETE_DAY,
        "detail": "the live publication of this partition is incomplete for its day; the archive holds the whole day",
        "archive": dict(archive_identity or {}),
    }]
    try:
        with connection.cursor() as cursor:
            inserted_id, superseded, new_signature = _write_report(
                cursor, inputs, coverage_start, coverage_end, storage_root_id, expected_profile, expected_check_suite,
                status, code_ref, violations,
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    lifecycle = QualityLifecycleCatalog(connection).apply_partition_lifecycle(
        dataset=inputs.dataset, dataset_sha256=inputs.dataset_sha256,
        partition=inputs.partition, partition_sha256=inputs.partition_sha256,
        coverage_start=coverage_start, coverage_end=coverage_end,
        coverage_ids=inputs.coverage_ids, assertion_ids=inputs.assertion_ids, coverage_sha256=inputs.coverage_sha256,
        storage_root_id=storage_root_id, expected_profile=expected_profile, expected_check_suite=expected_check_suite,
        lifecycle_code_ref=lifecycle_code_ref,
    )
    return ReassessmentResult(inserted_id, inserted_id is not None, superseded, new_signature, lifecycle)


def _write_report(
    cursor: Any, inputs: LifecycleInputs, start: Instant, end: Instant, storage_root_id: str, profile: str, suite: str,
    status: str, code_ref: str, violations: list[dict[str, Any]],
) -> tuple[str | None, str, str]:
    identity = _identity(inputs.dataset)
    partition = inputs.partition
    dataset_row = QualityLifecycleCatalog._resolve_dataset(cursor, identity, inputs.dataset, inputs.dataset_sha256)
    topology = QualityLifecycleCatalog._lock_partition_topology(cursor, dataset_row[0], partition["partition_key"])
    target = next((row for row in topology if int(row[3]) == int(partition["revision"])), None)
    if target is None:
        raise QualityReassessmentRefusal("target revision is missing")
    if target[4] == "superseded":
        raise QualityReassessmentRefusal("a superseded revision is never reassessed")
    if target[4] == "writing":
        raise QualityReassessmentRefusal("a writing revision is not quality-eligible")
    _live_row(topology)  # exactly one live revision per key; a target that is not superseded is that one
    if target[4] not in REASSESSABLE_STATES:
        raise QualityReassessmentRefusal(
            "only a valid, degraded or invalid revision is reassessed; a closed one is recorded by the ordinary S13 path")
    QualityLifecycleCatalog._verify_partition(target, dataset_row, partition, inputs.partition_sha256, start, end, storage_root_id)

    reports = QualityLifecycleCatalog._quality_reports(cursor, target[0], suite)
    leaf = select_current_quality_assessment(
        reports, expected_profile=profile, expected_check_suite=suite, identity=_identity_from_row(dataset_row),
        partition=partition, dataset_sha256=inputs.dataset_sha256, partition_sha256=inputs.partition_sha256,
        coverage_ids=inputs.coverage_ids, assertion_ids=inputs.assertion_ids, coverage_sha256=inputs.coverage_sha256,
        target=target,
    )
    if leaf.status == status and (status == "pass" or (leaf.violations == violations and leaf.code_ref == code_ref)):
        return None, leaf.signature, leaf.signature  # the current assessment already says it: nothing to write

    metrics = deepcopy(dict(leaf.metrics))
    evidence = deepcopy(dict(metrics["evidence"]))
    evidence["coverage"] = dict(evidence["coverage"], status=status)
    metrics["evidence"] = evidence
    metrics[SUPERSEDES_ASSESSMENT_SIGNATURE] = leaf.signature
    if not current_partition_report(
        metrics, status, violations, code_ref, profile, suite, _identity_from_row(dataset_row), partition,
        inputs.dataset_sha256, inputs.partition_sha256, inputs.coverage_ids, inputs.assertion_ids, inputs.coverage_sha256, target,
    ):
        raise QualityReassessmentRefusal(
            f"a {status} reassessment cannot be built over the current assessment ({leaf.status}): its evidence does not allow it"
        )
    signature = semantic_assessment_signature(suite, status, metrics, violations, code_ref)
    from psycopg.types.json import Jsonb

    cursor.execute(
        "INSERT INTO catalog.quality_reports (partition_id, check_suite, status, metrics, violations, code_ref) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING report_id::text",
        (target[0], suite, status, Jsonb(metrics), Jsonb(violations), code_ref),
    )
    return str(cursor.fetchone()[0]), leaf.signature, signature


__all__ = [
    "LifecycleInputs",
    "QualityReassessmentRefusal",
    "REASON_LEGACY_INCOMPLETE_DAY",
    "REASSESSABLE_STATES",
    "REASSESSMENT_STATUSES",
    "ReassessmentResult",
    "load_lifecycle_inputs",
    "reassess_live_partition",
]
