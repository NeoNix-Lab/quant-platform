"""A16 partition quality assessment selection and lifecycle application.

This module owns the bounded general lifecycle seam after S13 evidence
recording.  It does not publish data, repair data, or create quality reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
from typing import Any, Mapping, Sequence

from .models import DatasetIdentity, Instant, NaturalPartitionIdentity
from quant_platform.canonical import canonical_bytes


QUALITY_LIFECYCLE_CODE_ID = "quant-platform/a16-quality-lifecycle-v1"
SUPERSEDES_ASSESSMENT_SIGNATURE = "supersedes_assessment_signature"


class QualityLifecycleRefusal(RuntimeError):
    """Raised when A16 evidence or topology cannot authorize lifecycle state."""


@dataclass(frozen=True, slots=True)
class SelectedQualityAssessment:
    signature: str
    status: str
    code_ref: str
    assessment_profile: str
    check_suite: str
    supersedes_signature: str | None
    metrics: Mapping[str, Any]
    violations: Any


@dataclass(frozen=True, slots=True)
class QualityLifecycleResult:
    partition_id: str
    dataset_id: str
    natural_identity: NaturalPartitionIdentity
    selected_assessment_scope: dict[str, str]
    assessment_signature: str
    assessment_status: str
    supersedes_assessment_signature: str | None
    prior_state: str
    resulting_state: str
    mutated: bool
    lifecycle_code_ref: str
    bound_evidence: dict[str, Any]


class QualityLifecycleCatalog:
    """Transactional catalog seam for current live partition reassessment."""

    def __init__(self, connection: Any):
        self.connection = connection

    def apply_partition_lifecycle(
        self,
        *,
        dataset: Mapping[str, Any],
        dataset_sha256: str,
        partition: Mapping[str, Any],
        partition_sha256: str,
        coverage_start: Instant,
        coverage_end: Instant,
        coverage_ids: Sequence[str],
        assertion_ids: Sequence[str],
        coverage_sha256: Sequence[str],
        storage_root_id: str,
        expected_profile: str,
        expected_check_suite: str,
        lifecycle_code_ref: str = QUALITY_LIFECYCLE_CODE_ID,
    ) -> QualityLifecycleResult:
        try:
            with self.connection.cursor() as cursor:
                verified = self._apply_partition_lifecycle_tx(
                    cursor,
                    dataset=dataset,
                    dataset_sha256=dataset_sha256,
                    partition=partition,
                    partition_sha256=partition_sha256,
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                    coverage_ids=coverage_ids,
                    assertion_ids=assertion_ids,
                    coverage_sha256=coverage_sha256,
                    storage_root_id=storage_root_id,
                    expected_profile=expected_profile,
                    expected_check_suite=expected_check_suite,
                    lifecycle_code_ref=lifecycle_code_ref,
                )
            self.connection.commit()
            return verified
        except Exception:
            self.connection.rollback()
            raise

    def _apply_partition_lifecycle_tx(
        self,
        cursor: Any,
        *,
        dataset: Mapping[str, Any],
        dataset_sha256: str,
        partition: Mapping[str, Any],
        partition_sha256: str,
        coverage_start: Instant,
        coverage_end: Instant,
        coverage_ids: Sequence[str],
        assertion_ids: Sequence[str],
        coverage_sha256: Sequence[str],
        storage_root_id: str,
        expected_profile: str,
        expected_check_suite: str,
        lifecycle_code_ref: str = QUALITY_LIFECYCLE_CODE_ID,
    ) -> QualityLifecycleResult:
        """Transaction-scoped A16 seam: same verification/mutation, caller's cursor.

        Performs the identical accepted logic as :meth:`apply_partition_lifecycle`
        against a cursor the caller already owns, and never commits or rolls
        back the caller's transaction.  A10's atomic cutover reuses this so
        quality-lifecycle promotion participates in its own single commit
        instead of the standalone commit ``apply_partition_lifecycle`` performs
        for its own direct callers.
        """
        if not isinstance(lifecycle_code_ref, str) or not lifecycle_code_ref.strip():
            raise QualityLifecycleRefusal("lifecycle code identity is required")
        identity = _identity(dataset)
        target_key = (partition["partition_key"], int(partition["revision"]))
        dataset_row = self._resolve_dataset(cursor, identity, dataset, dataset_sha256)
        topology = self._lock_partition_topology(cursor, dataset_row[0], partition["partition_key"])
        target = next((row for row in topology if int(row[3]) == target_key[1]), None)
        if target is None:
            raise QualityLifecycleRefusal("target revision is missing")
        current = _live_row(topology)
        if target[4] == "superseded":
            raise QualityLifecycleRefusal("superseded partition cannot be reactivated")
        if str(current[0]) != str(target[0]):
            raise QualityLifecycleRefusal("target revision is not the current live revision")
        if target[4] == "writing":
            raise QualityLifecycleRefusal("writing partition is not quality-eligible")
        if target[4] not in {"closed", "valid", "degraded", "invalid"}:
            raise QualityLifecycleRefusal(f"target state {target[4]!r} is not quality-applicable")
        self._verify_partition(
            target, dataset_row, partition, partition_sha256,
            coverage_start, coverage_end, storage_root_id,
        )
        reports = self._quality_reports(cursor, target[0], expected_check_suite)
        selected = select_current_quality_assessment(
            reports,
            expected_profile=expected_profile,
            expected_check_suite=expected_check_suite,
            identity=identity,
            partition=partition,
            dataset_sha256=dataset_sha256,
            partition_sha256=partition_sha256,
            coverage_ids=coverage_ids,
            assertion_ids=assertion_ids,
            coverage_sha256=coverage_sha256,
            target=target,
        )
        desired = lifecycle_state_for_status(selected.status)
        prior = str(target[4])
        mutated = False
        if prior != desired:
            cursor.execute(
                """
                UPDATE catalog.partitions
                   SET state = %s
                 WHERE partition_id = %s
                   AND state = %s
                   AND state IN ('closed','valid','degraded','invalid')
                RETURNING partition_id::text
                """,
                (desired, target[0], prior),
            )
            if cursor.fetchone() is None:
                raise QualityLifecycleRefusal("quality lifecycle state update did not apply")
            mutated = True
        return self._verify_result(
            cursor, dataset_row, target[0], partition, partition_sha256,
            coverage_start, coverage_end, storage_root_id, desired,
            expected_profile, expected_check_suite, dataset_sha256,
            coverage_ids, assertion_ids, coverage_sha256, selected,
            prior, mutated, lifecycle_code_ref.strip(),
        )

    @staticmethod
    def _resolve_dataset(cursor: Any, identity: DatasetIdentity, document: Mapping[str, Any], digest: str):
        cursor.execute(
            """
            SELECT d.dataset_id::text, d.layer, d.kind, d.venue, d.instrument,
                   d.schema_id, d.feature_set_def_id::text, d.rel_root, d.manifest_sha256
              FROM catalog.datasets AS d
             WHERE d.layer=%s AND d.kind=%s AND d.venue=%s
               AND d.instrument=%s AND d.schema_id=%s
               AND d.feature_set_def_id IS NULL
             FOR UPDATE
            """,
            (identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id),
        )
        rows = cursor.fetchall()
        if len(rows) != 1:
            raise QualityLifecycleRefusal("dataset natural identity is missing or duplicated")
        row = rows[0]
        if row[7] != document["rel_root"] or _text(row[8]) != digest:
            raise QualityLifecycleRefusal("dataset conflicts with its durable manifest")
        return row

    @staticmethod
    def _lock_partition_topology(cursor: Any, dataset_id: str, partition_key: str):
        cursor.execute(
            """
            SELECT p.partition_id::text, p.dataset_id::text, p.partition_key,
                   p.revision, p.state, p.storage_root_id, p.rel_path,
                   p.ts_start, p.ts_end, p.row_count, p.byte_size,
                   p.content_sha256, p.manifest_sha256, p.created_at,
                   p.closed_at, p.first_sequence, p.last_sequence,
                   p.producer, p.code_ref
              FROM catalog.partitions AS p
             WHERE p.dataset_id=%s AND p.partition_key=%s
             ORDER BY p.revision
             FOR UPDATE
            """,
            (dataset_id, partition_key),
        )
        return cursor.fetchall()

    @staticmethod
    def _quality_reports(cursor: Any, partition_id: str, check_suite: str):
        cursor.execute(
            """
            SELECT report_id::text, status, metrics, violations, code_ref, ran_at
              FROM catalog.quality_reports
             WHERE partition_id=%s AND check_suite=%s
            """,
            (partition_id, check_suite),
        )
        return cursor.fetchall()

    @staticmethod
    def _verify_partition(row: Any, dataset_row: Any, partition: Mapping[str, Any], digest: str,
                          start: Instant, end: Instant, storage_root_id: str) -> None:
        if partition.get("state") != "closed":
            raise QualityLifecycleRefusal("A16 requires the sealed closed partition manifest")
        if str(row[1]) != str(dataset_row[0]) or row[2] != partition["partition_key"] or int(row[3]) != int(partition["revision"]):
            raise QualityLifecycleRefusal("catalog target natural identity differs from durable partition manifest")
        if row[5] != storage_root_id or row[6] != partition["rel_path"]:
            raise QualityLifecycleRefusal("catalog target storage identity differs from durable evidence")
        if _instant(row[7]) != start or _instant(row[8]) != end:
            raise QualityLifecycleRefusal("catalog coverage does not equal durable evidence")
        if int(row[9]) != int(partition["row_count"]) or int(row[10]) != int(partition["file_size_bytes"]):
            raise QualityLifecycleRefusal("catalog target dimensions differ from durable evidence")
        if _text(row[11]) != partition["sha256"] or _text(row[12]) != digest:
            raise QualityLifecycleRefusal("catalog target hashes differ from durable evidence")
        for index, name in ((13, "created_at"), (14, "closed_at")):
            if _timestamptz(row[index]) != _timestamptz(partition[name]):
                raise QualityLifecycleRefusal(f"catalog target {name} differs from durable evidence")
        if _number(row[15]) != _number(partition.get("first_sequence")) or _number(row[16]) != _number(partition.get("last_sequence")):
            raise QualityLifecycleRefusal("catalog target sequence bounds differ from durable evidence")
        if row[17] != partition["producer"] or row[18] != partition["code_ref"]:
            raise QualityLifecycleRefusal("catalog target producer provenance differs from durable evidence")

    def _verify_result(self, cursor: Any, dataset_row: Any, partition_id: str, partition: Mapping[str, Any],
                       partition_sha256: str, start: Instant, end: Instant, storage_root_id: str,
                       desired: str, profile: str, check_suite: str, dataset_sha256: str,
                       coverage_ids: Sequence[str], assertion_ids: Sequence[str],
                       coverage_sha256: Sequence[str], selected: SelectedQualityAssessment,
                       prior: str, mutated: bool, lifecycle_code_ref: str) -> QualityLifecycleResult:
        topology = self._lock_partition_topology(cursor, dataset_row[0], partition["partition_key"])
        target = next((row for row in topology if str(row[0]) == str(partition_id)), None)
        if target is None or target[4] != desired:
            raise QualityLifecycleRefusal("quality lifecycle target state verification failed")
        current = _live_row(topology)
        if str(current[0]) != str(partition_id):
            raise QualityLifecycleRefusal("quality lifecycle target stopped being live")
        self._verify_partition(target, dataset_row, partition, partition_sha256, start, end, storage_root_id)
        identity = _identity_from_row(dataset_row)
        reports = self._quality_reports(cursor, partition_id, check_suite)
        verified = select_current_quality_assessment(
            reports,
            expected_profile=profile,
            expected_check_suite=check_suite,
            identity=identity,
            partition=partition,
            dataset_sha256=dataset_sha256,
            partition_sha256=partition_sha256,
            coverage_ids=coverage_ids,
            assertion_ids=assertion_ids,
            coverage_sha256=coverage_sha256,
            target=target,
        )
        if verified.signature != selected.signature or lifecycle_state_for_status(verified.status) != desired:
            raise QualityLifecycleRefusal("quality lifecycle assessment verification failed")
        return QualityLifecycleResult(
            partition_id=str(partition_id),
            dataset_id=str(dataset_row[0]),
            natural_identity=NaturalPartitionIdentity(identity, partition["partition_key"], int(partition["revision"])),
            selected_assessment_scope={"certification_profile": profile, "check_suite": check_suite},
            assessment_signature=selected.signature,
            assessment_status=selected.status,
            supersedes_assessment_signature=selected.supersedes_signature,
            prior_state=prior,
            resulting_state=desired,
            mutated=mutated,
            lifecycle_code_ref=lifecycle_code_ref,
            bound_evidence=bound_evidence_payload(
                dataset_sha256=dataset_sha256,
                partition_sha256=partition_sha256,
                coverage_ids=coverage_ids,
                assertion_ids=assertion_ids,
                coverage_sha256=coverage_sha256,
                partition_content_sha256=partition["sha256"],
            ),
        )


def select_current_quality_assessment(
    reports: Sequence[Any],
    *,
    expected_profile: str,
    expected_check_suite: str,
    identity: DatasetIdentity,
    partition: Mapping[str, Any],
    dataset_sha256: str,
    partition_sha256: str,
    coverage_ids: Sequence[str],
    assertion_ids: Sequence[str],
    coverage_sha256: Sequence[str],
    target: Any,
) -> SelectedQualityAssessment:
    by_signature: dict[str, SelectedQualityAssessment] = {}
    for row in reports:
        _report_id, status, metrics, violations, code_ref, _ran_at = row
        if not current_partition_report(
            metrics, status, violations, code_ref, expected_profile, expected_check_suite,
            identity, partition, dataset_sha256, partition_sha256, coverage_ids,
            assertion_ids, coverage_sha256, target,
        ):
            continue
        signature = semantic_assessment_signature(expected_check_suite, status, metrics, violations, code_ref)
        link = supersedes_signature(metrics)
        by_signature.setdefault(
            signature,
            SelectedQualityAssessment(
                signature=signature,
                status=status,
                code_ref=code_ref.strip(),
                assessment_profile=expected_profile,
                check_suite=expected_check_suite,
                supersedes_signature=link,
                metrics=metrics,
                violations=violations,
            ),
        )
    if not by_signature:
        raise QualityLifecycleRefusal("no applicable quality assessment matches current durable evidence")
    _validate_supersession_graph(by_signature)
    superseded = {
        item.supersedes_signature
        for item in by_signature.values()
        if item.supersedes_signature is not None
    }
    leaves = [item for signature, item in by_signature.items() if signature not in superseded]
    if len(leaves) != 1:
        raise QualityLifecycleRefusal("quality assessment evidence is ambiguous")
    selected = leaves[0]
    if selected.status not in {"pass", "warn", "fail"}:
        raise QualityLifecycleRefusal("quality assessment status is unsupported")
    return selected


def current_partition_report(
    metrics: Any,
    status: str,
    violations: Any,
    code_ref: Any,
    profile: str,
    suite: str,
    identity: DatasetIdentity,
    partition: Mapping[str, Any],
    dataset_sha: str,
    partition_sha: str,
    coverage_ids: Sequence[str],
    assertion_ids: Sequence[str],
    coverage_sha: Sequence[str],
    target: Any,
) -> bool:
    if status not in {"pass", "warn", "fail"}:
        return False
    if not isinstance(metrics, Mapping) or metrics.get("certification_profile") != profile:
        return False
    natural = metrics.get("natural_partition_identity")
    expected_natural = {"dataset_identity": identity.stable_dict(), "partition_key": partition["partition_key"], "revision": int(partition["revision"])}
    if natural != expected_natural or metrics.get("dataset_manifest_sha256") != dataset_sha:
        return False
    if metrics.get("partition_manifest_sha256") != partition_sha:
        return False
    if _text(metrics.get("physical_artifact_hash")) != _text(partition["sha256"]) or _text(target[11]) != _text(partition["sha256"]):
        return False
    if _text(metrics.get("canonical_content_hash_v1")) is None:
        return False
    if metrics.get("coverage_manifest_id") != (coverage_ids[0] if coverage_ids else None):
        return False
    if metrics.get("coverage_assertion_id") != (assertion_ids[0] if assertion_ids else None):
        return False
    for key, expected in (("coverage_manifest_ids", coverage_ids), ("coverage_assertion_ids", assertion_ids), ("coverage_manifest_sha256", coverage_sha)):
        value = metrics.get(key)
        if not isinstance(value, (list, tuple)) or _set_values(value) != _set_values(expected):
            return False
    evidence = metrics.get("evidence")
    required = {"source", "canonical", "physical", "manifests", "coverage"}
    if not isinstance(evidence, Mapping) or set(evidence).difference(required | {"publication"}) or not required.issubset(evidence):
        return False
    statuses = {name: evidence[name].get("status") if isinstance(evidence[name], Mapping) else None for name in required}
    if any(value not in {"pass", "warn", "fail"} for value in statuses.values()):
        return False
    if not isinstance(code_ref, str) or not code_ref.strip():
        return False
    if status == "pass":
        return not violations and all(value == "pass" for value in statuses.values())
    if status == "warn":
        if statuses["source"] != "warn" or any(statuses[name] != "pass" for name in required - {"source"}):
            return False
        if not isinstance(violations, list):
            return False
        return all(isinstance(item, Mapping) and item.get("category") == "source" for item in violations)
    return status == "fail"


def supersedes_signature(metrics: Any) -> str | None:
    if not isinstance(metrics, Mapping):
        raise QualityLifecycleRefusal("quality assessment metrics are malformed")
    value = metrics.get(SUPERSEDES_ASSESSMENT_SIGNATURE)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise QualityLifecycleRefusal("supersession signature is malformed")
    return value.strip()


def lifecycle_state_for_status(status: str) -> str:
    if status == "pass":
        return "valid"
    if status == "warn":
        return "degraded"
    if status == "fail":
        return "invalid"
    raise QualityLifecycleRefusal("quality assessment status is unsupported")


def bound_evidence_payload(
    *,
    dataset_sha256: str,
    partition_sha256: str,
    coverage_ids: Sequence[str],
    assertion_ids: Sequence[str],
    coverage_sha256: Sequence[str],
    partition_content_sha256: str,
) -> dict[str, Any]:
    return {
        "dataset_manifest_sha256": dataset_sha256,
        "partition_manifest_sha256": partition_sha256,
        "partition_content_sha256": partition_content_sha256,
        "coverage_manifest_ids": list(coverage_ids),
        "coverage_assertion_ids": list(assertion_ids),
        "coverage_manifest_sha256": list(coverage_sha256),
    }


def semantic_assessment_signature(suite: str, status: str, metrics: Any, violations: Any, code_ref: str) -> str:
    normalized = _normalize(metrics)
    if isinstance(normalized, dict):
        for key in ("coverage_manifest_ids", "coverage_assertion_ids", "coverage_manifest_sha256"):
            if key in normalized and isinstance(normalized[key], list):
                normalized[key] = sorted(normalized[key])
    payload = {
        "check_suite": suite,
        "status": status,
        "metrics": normalized,
        "violations": _normalize(violations),
        "code_ref": code_ref.strip(),
    }
    return hashlib.sha256(canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=True)).hexdigest()


def _validate_supersession_graph(assessments: Mapping[str, SelectedQualityAssessment]) -> None:
    signatures = set(assessments)
    for signature, assessment in assessments.items():
        predecessor = assessment.supersedes_signature
        if predecessor is None:
            continue
        if predecessor == signature:
            raise QualityLifecycleRefusal("quality assessment supersession is self-referential")
        if predecessor not in signatures:
            raise QualityLifecycleRefusal("quality assessment supersession reference is unresolved or foreign")
    for signature in signatures:
        seen: set[str] = set()
        current = signature
        while True:
            predecessor = assessments[current].supersedes_signature
            if predecessor is None:
                break
            if predecessor in seen:
                raise QualityLifecycleRefusal("quality assessment supersession graph is cyclic")
            seen.add(predecessor)
            current = predecessor


def _normalize(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _normalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _set_values(value: Any) -> frozenset[Any]:
    if not isinstance(value, (list, tuple)):
        return frozenset()
    return frozenset(_json_key(item) for item in value)


def _json_key(value: Any) -> str:
    return canonical_bytes(_normalize(value), profile="sorted-compact-ascii-v1", allow_nan=True).decode("utf-8")


def _identity(document: Mapping[str, Any]) -> DatasetIdentity:
    return DatasetIdentity(document["layer"], document["dataset_kind"], document["venue"], document["instrument"], document["record_schema_id"], document.get("feature_set_slug"), document.get("feature_set_version"))


def _identity_from_row(row: Any) -> DatasetIdentity:
    return DatasetIdentity(row[1], row[2], row[3], row[4], row[5])


def _live_row(rows: Sequence[Any]) -> Any:
    live = [row for row in rows if row[4] != "superseded"]
    if len(live) != 1:
        raise QualityLifecycleRefusal("partition topology does not have exactly one live revision")
    if any(int(row[3]) >= int(live[0][3]) for row in rows if row[4] == "superseded"):
        raise QualityLifecycleRefusal("partition topology has invalid superseded history")
    return live[0]


def _text(value: Any) -> str | None:
    return None if value is None else str(value).strip()


def _number(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _instant(value: Any) -> Instant | None:
    return None if value is None else Instant.parse(value)


def _timestamptz(value: Any) -> Any:
    return None if value is None else Instant.parse(value).to_datetime()


__all__ = [
    "QUALITY_LIFECYCLE_CODE_ID",
    "SUPERSEDES_ASSESSMENT_SIGNATURE",
    "QualityLifecycleCatalog",
    "QualityLifecycleRefusal",
    "QualityLifecycleResult",
    "SelectedQualityAssessment",
    "bound_evidence_payload",
    "current_partition_report",
    "lifecycle_state_for_status",
    "select_current_quality_assessment",
    "semantic_assessment_signature",
    "supersedes_signature",
]
