"""PostgreSQL boundary for S14 publication eligibility and verification.

The boundary starts after S13 has admitted a closed revision and recorded its
quality evidence.  It never creates revisions, changes coverage, or writes a
quality report.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping, Sequence

from .manifests import DATASET_MANIFEST_V1, DATASET_MANIFEST_V2
from .models import DatasetIdentity, Instant, NaturalPartitionIdentity
from .quality_lifecycle import (
    QualityLifecycleRefusal,
    current_partition_report,
    select_current_quality_assessment,
    semantic_assessment_signature,
)


class PublicationEligibilityRefusal(RuntimeError):
    """Raised when current durable evidence cannot authorize eligibility."""


@dataclass(frozen=True, slots=True)
class SelectedCertification:
    signature: str
    status: str
    code_ref: str


@dataclass(frozen=True, slots=True)
class PublicationEligibilityResult:
    partition_id: str
    dataset_id: str
    natural_identity: NaturalPartitionIdentity
    state: str
    ts_start: Instant
    ts_end: Instant
    certification_signature: str
    certification_status: str


class PublicationEligibilityCatalog:
    """Own the one transaction spanning lineage, promotion, and verification."""

    def __init__(self, connection: Any):
        self.connection = connection

    def publish(
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
    ) -> PublicationEligibilityResult:
        try:
            with self.connection.cursor() as cursor:
                verified = self._publish_tx(
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
                )
            self.connection.commit()
            return verified
        except Exception:
            self.connection.rollback()
            raise

    def _publish_tx(
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
    ) -> PublicationEligibilityResult:
        """Transaction-scoped S14 seam: same verification/mutation, caller's cursor.

        Performs the identical accepted logic as :meth:`publish` against a
        cursor the caller already owns, and never commits or rolls back the
        caller's transaction.  A10's atomic cutover reuses this so publication
        eligibility participates in its own single commit instead of the
        standalone commit ``publish`` performs for its own direct callers.
        """
        identity = _identity(dataset)
        target_key = (partition["partition_key"], int(partition["revision"]))
        child = self._resolve_dataset(cursor, identity, dataset, dataset_sha256)
        parents = self._resolve_parents(cursor, dataset, identity)
        relevant_ids = sorted({child[0], *(row[0] for row in parents)})
        self._lock_datasets(cursor, relevant_ids)
        self._verify_locked_datasets(cursor, child, parents, dataset, dataset_sha256)

        topology = self._lock_partition_topology(cursor, child[0], partition["partition_key"])
        target = next((row for row in topology if int(row[3]) == target_key[1]), None)
        current = _live_row(topology)
        if target is None or current is None or str(target[0]) != str(current[0]):
            raise PublicationEligibilityRefusal("target revision is not the current live revision")
        if target[4] not in {"closed", "valid", "degraded"}:
            raise PublicationEligibilityRefusal(f"target state {target[4]!r} is not publishable")
        self._verify_partition(
            target, child, identity, partition, partition_sha256,
            coverage_start, coverage_end, storage_root_id,
        )

        reports = self._quality_reports(cursor, target[0], expected_check_suite)
        selected = _select_authoritative_report(
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
        desired = _eligible_state(selected)

        expected_lineage = self._ensure_lineage(
            cursor, child[0], parents, dataset, identity,
        )
        if target[4] == "closed":
            cursor.execute(
                """
                UPDATE catalog.partitions
                   SET state = %s
                 WHERE partition_id = %s AND state = 'closed'
                RETURNING partition_id::text
                """,
                (desired, target[0]),
            )
            if cursor.fetchone() is None:
                raise PublicationEligibilityRefusal("eligibility state update did not apply")
        elif target[4] != desired:
            raise PublicationEligibilityRefusal(
                f"existing eligibility state {target[4]!r} conflicts with {desired!r}"
            )

        return self._phase5_verify(
            cursor, child, target[0], partition["partition_key"], target_key[1],
            identity, partition, partition_sha256, coverage_start, coverage_end,
            storage_root_id, desired, expected_lineage, expected_check_suite,
            expected_profile, dataset_sha256, coverage_ids, assertion_ids,
            coverage_sha256, selected.signature,
        )

    @staticmethod
    def _resolve_dataset(cursor: Any, identity: DatasetIdentity, document: Mapping[str, Any], digest: str):
        if identity.layer == "features":
            raise PublicationEligibilityRefusal(
                "S14 feature-set catalog mapping is not frozen by the first-vertical contract"
            )
        cursor.execute(
            """
            SELECT d.dataset_id::text, d.layer, d.kind, d.venue, d.instrument,
                   d.schema_id, d.feature_set_def_id::text, d.rel_root, d.manifest_sha256
              FROM catalog.datasets AS d
             WHERE d.layer=%s AND d.kind=%s AND d.venue=%s
               AND d.instrument=%s AND d.schema_id=%s
               AND d.feature_set_def_id IS NULL
            """,
            (identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id),
        )
        rows = cursor.fetchall()
        if len(rows) != 1:
            raise PublicationEligibilityRefusal("child dataset natural identity is missing or duplicated")
        row = rows[0]
        if row[7] != document["rel_root"] or _text(row[8]) != digest:
            raise PublicationEligibilityRefusal("child dataset conflicts with its durable manifest")
        return row

    @staticmethod
    def _resolve_parents(cursor: Any, document: Mapping[str, Any], child: DatasetIdentity):
        lineage = document.get("derived_from")
        version = document.get("schema_version")
        if version == DATASET_MANIFEST_V1:
            if child.layer == "raw":
                if lineage is not None or document.get("transform") is not None:
                    raise PublicationEligibilityRefusal("raw dataset carries derived lineage")
                return []
        elif version == DATASET_MANIFEST_V2:
            if child.layer == "raw":
                if "origin" in document or "derived_from" in document or "transform" in document:
                    raise PublicationEligibilityRefusal("raw dataset-manifest-v2 carries derived topology")
                return []
            origin = document.get("origin")
            if child.layer == "canonical" and origin == "source_acquired":
                if "derived_from" in document:
                    raise PublicationEligibilityRefusal("source_acquired dataset declares lineage")
                transform = document.get("transform")
                if not isinstance(transform, str) or not transform.strip():
                    raise PublicationEligibilityRefusal("source_acquired dataset lacks explicit transform")
                return []
            if child.layer not in {"canonical", "features"} or origin != "dataset_derived":
                raise PublicationEligibilityRefusal("v2 dataset origin is incompatible with its layer")
        else:
            raise PublicationEligibilityRefusal("unsupported dataset manifest schema_version")

        if not isinstance(lineage, list) or not lineage or not isinstance(document.get("transform"), str) or not document["transform"].strip():
            raise PublicationEligibilityRefusal("derived dataset lacks explicit lineage and transform")
        result = []
        seen = set()
        for parent_doc in lineage:
            parent = _identity(parent_doc)
            if parent in seen or parent == child:
                raise PublicationEligibilityRefusal("dataset lineage is duplicated or self-referential")
            seen.add(parent)
            if parent.layer == "features":
                raise PublicationEligibilityRefusal("feature-set parent mapping is not frozen")
            cursor.execute(
                """
                SELECT d.dataset_id::text, d.layer, d.kind, d.venue, d.instrument,
                       d.schema_id, d.feature_set_def_id::text, d.rel_root, d.manifest_sha256
                  FROM catalog.datasets AS d
                 WHERE d.layer=%s AND d.kind=%s AND d.venue=%s
                   AND d.instrument=%s AND d.schema_id=%s
                   AND d.feature_set_def_id IS NULL
                """,
                (parent.layer, parent.dataset_kind, parent.venue, parent.instrument, parent.record_schema_id),
            )
            rows = cursor.fetchall()
            if len(rows) != 1:
                raise PublicationEligibilityRefusal("required parent dataset is missing or duplicated")
            result.append(rows[0])
        return result

    @staticmethod
    def _lock_datasets(cursor: Any, ids: Sequence[str]) -> None:
        placeholders = ", ".join(["%s"] * len(ids))
        cursor.execute(
            f"SELECT dataset_id::text FROM catalog.datasets WHERE dataset_id IN ({placeholders}) ORDER BY dataset_id::text FOR UPDATE",
            tuple(ids),
        )
        locked = [str(row[0]) for row in cursor.fetchall()]
        if locked != list(ids):
            raise PublicationEligibilityRefusal("relevant dataset disappeared during lock acquisition")

    @staticmethod
    def _verify_locked_datasets(cursor: Any, child: Any, parents: Sequence[Any], document: Mapping[str, Any], digest: str) -> None:
        if child[7] != document["rel_root"] or _text(child[8]) != digest:
            raise PublicationEligibilityRefusal("locked child dataset changed")
        if any(row[0] == child[0] for row in parents):
            raise PublicationEligibilityRefusal("child dataset appears as its own parent")

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
    def _verify_partition(row: Any, child: Any, identity: DatasetIdentity, partition: Mapping[str, Any], digest: str,
                          start: Instant, end: Instant, storage_root_id: str) -> None:
        if partition.get("state") != "closed":
            raise PublicationEligibilityRefusal("S14 requires the S13 closed partition manifest")
        if str(row[1]) != str(child[0]) or row[2] != partition["partition_key"] or int(row[3]) != int(partition["revision"]):
            raise PublicationEligibilityRefusal("catalog target natural identity differs from durable partition manifest")
        if row[5] != storage_root_id or row[6] != partition["rel_path"]:
            raise PublicationEligibilityRefusal("catalog target storage identity differs from durable evidence")
        if _instant(row[7]) != start or _instant(row[8]) != end:
            raise PublicationEligibilityRefusal("catalog coverage does not equal reconstructed coverage")
        if int(row[9]) != int(partition["row_count"]) or int(row[10]) != int(partition["file_size_bytes"]):
            raise PublicationEligibilityRefusal("catalog target dimensions differ from durable evidence")
        if _text(row[11]) != partition["sha256"] or _text(row[12]) != digest:
            raise PublicationEligibilityRefusal("catalog target hashes differ from durable evidence")
        # created_at/closed_at are compared at PostgreSQL timestamptz
        # resolution, exactly as S13 Phase 1 already compares them.
        # partition-manifest-v1 permits nanoseconds on these two fields while
        # the column holds microseconds, so a nanosecond comparison would
        # refuse a partition S13 legitimately sealed and certified.
        for index, name in ((13, "created_at"), (14, "closed_at")):
            if _timestamptz(row[index]) != _timestamptz(partition[name]):
                raise PublicationEligibilityRefusal(f"catalog target {name} differs from durable evidence")
        if _number(row[15]) != _number(partition.get("first_sequence")) or _number(row[16]) != _number(partition.get("last_sequence")):
            raise PublicationEligibilityRefusal("catalog target sequence bounds differ from durable evidence")
        if row[17] != partition["producer"] or row[18] != partition["code_ref"]:
            raise PublicationEligibilityRefusal("catalog target producer provenance differs from durable evidence")

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
    def _ensure_lineage(cursor: Any, child_id: str, parents: Sequence[Any], document: Mapping[str, Any], child: DatasetIdentity):
        expected = set()
        transform = document.get("transform")
        if child.layer != "raw":
            if not isinstance(transform, str) or not transform.strip():
                raise PublicationEligibilityRefusal("non-raw dataset transform is missing")
            expected = {(str(row[0]), transform.strip()) for row in parents}
            for parent_id, _ in sorted(expected):
                cursor.execute(
                    """
                    INSERT INTO catalog.dataset_lineage (child_id, parent_id, transform)
                    VALUES (%s, %s, %s) ON CONFLICT (child_id, parent_id, transform) DO NOTHING
                    """,
                    (child_id, parent_id, transform.strip()),
                )
        cursor.execute(
            "SELECT parent_id::text, transform FROM catalog.dataset_lineage WHERE child_id=%s ORDER BY parent_id::text, transform",
            (child_id,),
        )
        actual = {(str(row[0]), str(row[1])) for row in cursor.fetchall()}
        if actual != expected:
            raise PublicationEligibilityRefusal("catalog dataset lineage does not equal durable manifest lineage")
        return frozenset(expected)

    def _phase5_verify(self, cursor: Any, child: Any, partition_id: str, partition_key: str, revision: int,
                       identity: DatasetIdentity, partition: Mapping[str, Any], partition_sha256: str,
                       start: Instant, end: Instant, storage_root_id: str, desired: str,
                       expected_lineage: frozenset[tuple[str, str]], check_suite: str, profile: str,
                       dataset_sha256: str, coverage_ids: Sequence[str], assertion_ids: Sequence[str],
                       coverage_sha256: Sequence[str], signature: str) -> PublicationEligibilityResult:
        topology = self._lock_partition_topology(cursor, child[0], partition_key)
        target = next((row for row in topology if int(row[3]) == revision), None)
        if target is None or target[4] != desired:
            raise PublicationEligibilityRefusal("Phase-5 target state verification failed")
        self._verify_partition(target, child, identity, partition, partition_sha256, start, end, storage_root_id)
        cursor.execute("SELECT parent_id::text, transform FROM catalog.dataset_lineage WHERE child_id=%s ORDER BY parent_id::text, transform", (child[0],))
        if {(str(row[0]), str(row[1])) for row in cursor.fetchall()} != set(expected_lineage):
            raise PublicationEligibilityRefusal("Phase-5 lineage verification failed")
        reports = self._quality_reports(cursor, partition_id, check_suite)
        selected = _select_authoritative_report(
            reports, expected_profile=profile, expected_check_suite=check_suite, identity=identity,
            partition=partition, dataset_sha256=dataset_sha256, partition_sha256=partition_sha256,
            coverage_ids=coverage_ids, assertion_ids=assertion_ids, coverage_sha256=coverage_sha256,
            target=target,
        )
        if selected.signature != signature or _eligible_state(selected) != desired:
            raise PublicationEligibilityRefusal("Phase-5 quality evidence verification failed")
        return PublicationEligibilityResult(
            partition_id=str(target[0]), dataset_id=str(child[0]),
            natural_identity=NaturalPartitionIdentity(identity, partition_key, revision),
            state=desired, ts_start=start, ts_end=end,
            certification_signature=selected.signature, certification_status=selected.status,
        )


def _select_authoritative_report(reports: Sequence[Any], *, expected_profile: str, expected_check_suite: str,
                                 identity: DatasetIdentity, partition: Mapping[str, Any], dataset_sha256: str,
                                 partition_sha256: str, coverage_ids: Sequence[str], assertion_ids: Sequence[str],
                                 coverage_sha256: Sequence[str], target: Any) -> SelectedCertification:
    try:
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
    except QualityLifecycleRefusal as exc:
        raise PublicationEligibilityRefusal(str(exc)) from exc
    return SelectedCertification(selected.signature, selected.status, selected.code_ref)


def _current_report(metrics: Any, status: str, violations: Any, code_ref: Any, profile: str, suite: str,
                    identity: DatasetIdentity, partition: Mapping[str, Any], dataset_sha: str, partition_sha: str,
                    coverage_ids: Sequence[str], assertion_ids: Sequence[str], coverage_sha: Sequence[str], target: Any) -> bool:
    return current_partition_report(
        metrics, status, violations, code_ref, profile, suite, identity, partition,
        dataset_sha, partition_sha, coverage_ids, assertion_ids, coverage_sha, target,
    )


def _eligible_state(selected: SelectedCertification) -> str:
    if selected.status == "pass":
        return "valid"
    if selected.status == "warn":
        return "degraded"
    raise PublicationEligibilityRefusal("fail quality evidence cannot authorize publication eligibility")


def _semantic_signature(suite: str, status: str, metrics: Any, violations: Any, code_ref: str) -> str:
    return semantic_assessment_signature(suite, status, metrics, violations, code_ref)


def _identity(document: Mapping[str, Any]) -> DatasetIdentity:
    return DatasetIdentity(document["layer"], document["dataset_kind"], document["venue"], document["instrument"], document["record_schema_id"], document.get("feature_set_slug"), document.get("feature_set_version"))


def _live_row(rows: Sequence[Any]) -> Any | None:
    live = [row for row in rows if row[4] != "superseded"]
    if len(live) != 1:
        raise PublicationEligibilityRefusal("partition topology does not have exactly one live revision")
    if any(int(row[3]) >= int(live[0][3]) for row in rows if row[4] == "superseded"):
        raise PublicationEligibilityRefusal("partition topology has invalid superseded history")
    return live[0]


def _text(value: Any) -> str | None:
    return None if value is None else str(value).strip()


def _number(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _instant(value: Any) -> Instant | None:
    return None if value is None else Instant.parse(value)


def _timestamptz(value: Any) -> Any:
    """Normalize a durable or catalog timestamp to timestamptz resolution.

    ``catalog.partitions.created_at``/``closed_at`` are PostgreSQL
    ``timestamptz`` (microseconds) while ``partition-manifest-v1`` permits up
    to nanoseconds.  Returning a timezone-aware UTC ``datetime`` truncated to
    microseconds keeps S14 in agreement with S13's Phase-1 comparison instead
    of refusing evidence PostgreSQL can never store more precisely.  Strings
    are parsed, never compared textually.
    """

    return None if value is None else Instant.parse(value).to_datetime()


__all__ = ["PublicationEligibilityCatalog", "PublicationEligibilityRefusal", "PublicationEligibilityResult", "SelectedCertification"]
