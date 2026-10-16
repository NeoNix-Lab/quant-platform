"""S13 publication certification for canonical ``l2-book-event-v1`` partitions.

ADR-0070 §4-§5 (A14c).  A sibling of :class:`PublicationCertification`: ``seal``,
``record_evidence`` and ``run`` are inherited unchanged, so the seal, the catalog
writer, the quality-report shape and the S14 bridge are exactly the ones the
``trade-v1`` path uses.  Only ``certify`` is re-implemented, because the
``trade-v1`` physical and canonical steps are bound to ``TradeRecord``.

The physical and canonical checks run in **one streaming pass** over the
artifact, so memory is bounded by the read batch and the book depth, not by the
size of the day.

A partition publishes ``valid`` only when the A14a certification of its events is
``eligible``.  A ``degraded`` or ``ineligible`` outcome fails the ``canonical``
category: the frozen coverage fold (ADR-0022/ADR-0029) cannot publish a partition
with an internal gap or an unanchored head, so such a partition stays ``closed``
and unpublished (see the A14c issue, "Conflict found while reading the frozen
fold").
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from .l2_parquet import L2ContentHasher, scan_l2_book_event_v1_all
from .models import DatasetIdentity, Instant, L2BookEvent
from .publication import (
    CertificationResult,
    EvidenceCategoryResult,
    PublicationCertification,
    PublicationCertificationError,
    SealedCatalogPartition,
    SealedPartitionEvidence,
    _check,
    _dataset_identity,
    _first_assertion_id,
    _load_evidence,
    _sha256_file,
    _verify_coverage,
    _verify_manifests,
    _violation,
)

_MAX_REPORTED_DISCONTINUITIES = 10


class L2CertificationProfile(Protocol):
    """Source-injected profile; the generic L2 certifier never names a venue."""

    profile_id: str
    check_suite: str
    code_ref: str

    def applies_to(self, identity: DatasetIdentity) -> bool: ...
    def ordering_key(self, event: L2BookEvent) -> tuple[Any, ...]: ...
    def validate_source(
        self, identity: DatasetIdentity, coverage_documents: Sequence[Mapping[str, Any]]
    ) -> Mapping[str, Any]: ...
    def validate_events(self, identity: DatasetIdentity, events: Any) -> Any: ...
    def validate_source_evidence(
        self, coverage_documents: Sequence[Mapping[str, Any]], report: Any
    ) -> Mapping[str, Any]: ...


class L2PublicationCertification(PublicationCertification):
    """Execute S13 Phases 1-3 for an L2 partition and leave it closed."""

    def certify(
        self, evidence: SealedPartitionEvidence, sealed: SealedCatalogPartition
    ) -> CertificationResult:
        loaded = _load_evidence(evidence)
        identity = _dataset_identity(loaded.dataset)
        profile: L2CertificationProfile = self.profile  # type: ignore[assignment]
        violations: list[dict[str, Any]] = []
        categories: list[EvidenceCategoryResult] = []

        manifest_status, manifest_details, manifest_error = _check(
            lambda: _verify_manifests(loaded, identity, sealed)
        )
        categories.append(EvidenceCategoryResult("manifests", manifest_status, manifest_details))
        if manifest_error:
            violations.append(_violation("manifests", manifest_error))

        coverage_status, coverage_details, coverage_error = _check(
            lambda: _verify_coverage(self.coverage_fold, loaded.coverage, loaded.partition, sealed)
        )
        categories.append(EvidenceCategoryResult("coverage", coverage_status, coverage_details))
        if coverage_error:
            violations.append(_violation("coverage", coverage_error))

        # One pass: physical (hash, size, order, bounds, row count), content hash
        # and the A14a certification of the events.
        physical_status, physical_details, physical_error, report, canonical_hash = _stream_artifact(
            evidence.artifact_path, loaded.partition, sealed, profile, identity, self.batch_size
        )
        categories.append(EvidenceCategoryResult("physical", physical_status, physical_details))
        physical_hash = physical_details.get("physical_artifact_hash") if not physical_error else None
        if physical_error:
            violations.append(_violation("physical", physical_error))

        if report is None:
            canonical_status, canonical_details, canonical_error = (
                "fail",
                {},
                "canonical verification cannot run after physical verification failed",
            )
        else:
            canonical_details = {
                "canonical_content_hash_v1": canonical_hash,
                "l2_certification": _summary(report),
            }
            if report.outcome == "eligible":
                canonical_status, canonical_error = "pass", None
            else:
                canonical_status = "fail"
                canonical_error = _canonical_failure(report)
        categories.append(EvidenceCategoryResult("canonical", canonical_status, canonical_details))
        if canonical_error:
            violations.append(_violation("canonical", canonical_error))

        def source_check() -> dict[str, Any]:
            details = dict(profile.validate_source(identity, loaded.coverage))
            if report is not None:
                details.update(profile.validate_source_evidence(loaded.coverage, report))
            return details

        source_status, source_details, source_error = _check(source_check)
        categories.append(EvidenceCategoryResult("source", source_status, source_details))
        if source_error:
            violations.append(_violation("source", source_error))

        categories.append(
            EvidenceCategoryResult(
                "publication",
                "deferred",
                {"owner": "publication-eligibility-bridge-v1", "phases": [4, 5]},
            )
        )
        order = {
            name: index
            for index, name in enumerate(
                ("source", "canonical", "physical", "manifests", "coverage", "publication")
            )
        }
        categories.sort(key=lambda item: order[item.category])

        evidence_map = {item.category: dict(item.evidence, status=item.status) for item in categories}
        evidence_map["publication"] = {
            "status": "deferred",
            "owner": "publication-eligibility-bridge-v1",
            "phases": [4, 5],
        }
        metrics = {
            "certification_profile": profile.profile_id,
            "natural_partition_identity": loaded.partition_identity,
            "dataset_manifest_sha256": loaded.dataset_sha256,
            "partition_manifest_sha256": loaded.partition_sha256,
            "coverage_manifest_id": loaded.coverage[0].get("coverage_id"),
            "coverage_assertion_id": _first_assertion_id(loaded.coverage),
            "coverage_manifest_ids": [item.get("coverage_id") for item in loaded.coverage],
            "coverage_assertion_ids": [
                assertion.get("assertion_id")
                for document in loaded.coverage
                for assertion in document.get("assertions") or ()
            ],
            "coverage_manifest_sha256": list(loaded.coverage_sha256),
            "physical_artifact_hash": physical_hash,
            "canonical_content_hash_v1": canonical_hash,
            "evidence": evidence_map,
        }
        status = "pass" if not violations else "fail"
        return CertificationResult(
            partition_id=sealed.partition_id,
            status=status,
            categories=tuple(categories),
            metrics=metrics,
            violations=tuple(violations),
            code_ref=profile.code_ref,
        )


def _stream_artifact(
    path: Path,
    partition: Mapping[str, Any],
    sealed: SealedCatalogPartition,
    profile: L2CertificationProfile,
    identity: DatasetIdentity,
    batch_size: int,
) -> tuple[str, dict[str, Any], str | None, Any, str | None]:
    """Verify the artifact and certify its events in one pass.

    Returns ``(status, details, error, report, content_hash)``.  ``report`` is
    ``None`` when the physical verification failed before or during the pass.
    """

    try:
        artifact = Path(path)
        if not artifact.is_file():
            raise PublicationCertificationError("physical artifact is missing")
        actual_size = artifact.stat().st_size
        actual_hash = _sha256_file(artifact)
        if actual_size != partition["file_size_bytes"] or actual_size != sealed.byte_size:
            raise PublicationCertificationError(
                "physical artifact file size mismatches authoritative evidence"
            )
        if actual_hash != partition["sha256"] or actual_hash != sealed.content_sha256:
            raise PublicationCertificationError(
                "physical artifact hash mismatches authoritative evidence"
            )

        hasher = L2ContentHasher()
        state: dict[str, Any] = {"previous": None, "first": None, "last": None}

        def events() -> Iterator[L2BookEvent]:
            for batch in scan_l2_book_event_v1_all(artifact, batch_size=batch_size):
                for event in batch:
                    key = profile.ordering_key(event)
                    if state["previous"] is not None and key <= state["previous"]:
                        raise PublicationCertificationError(
                            "physical canonical rows are not strictly ordered"
                        )
                    state["previous"] = key
                    if state["first"] is None:
                        state["first"] = event.exchange_ts
                    state["last"] = event.exchange_ts
                    hasher.update(event)
                    yield event

        report = profile.validate_events(identity, events())
        if hasher.count != partition["row_count"]:
            raise PublicationCertificationError("physical row count mismatches partition manifest")
        first, last = partition.get("first_exchange_ts"), partition.get("last_exchange_ts")
        if hasher.count:
            if (
                first is None
                or last is None
                or state["first"] != Instant.parse(first)
                or state["last"] != Instant.parse(last)
            ):
                raise PublicationCertificationError(
                    "physical observed bounds mismatch partition manifest"
                )
        elif first is not None or last is not None:
            raise PublicationCertificationError("zero-row physical artifact has non-null observed bounds")
        details = {
            "physical_artifact_hash": actual_hash,
            "file_size_bytes": actual_size,
            "row_count": hasher.count,
        }
        return "pass", details, None, report, hasher.hexdigest_identity()
    except Exception as exc:
        return "fail", {}, str(exc), None, None


def _summary(report: Any) -> dict[str, Any]:
    document = report.stable_dict()
    discontinuities = document["discontinuities"]
    return {
        "outcome": document["outcome"],
        "event_count": document["event_count"],
        "segments": len(document["segments"]),
        "discontinuities": len(discontinuities),
        "discontinuity_samples": discontinuities[:_MAX_REPORTED_DISCONTINUITIES],
        "unanchored_head": document["unanchored_head"],
        "unanchored_event_count": document["unanchored_event_count"],
        "deletes_of_absent": document["deletes_of_absent"],
        "crossed_states": document["crossed_states"],
        "violation_counts": document["violation_counts"],
        "parameters": document["parameters"],
    }


def _canonical_failure(report: Any) -> str:
    if report.outcome == "degraded":
        return (
            "L2 events are not one clean continuity segment "
            f"(discontinuities={len(report.discontinuities)}, "
            f"unanchored_head={report.unanchored_head}); such a partition is not publishable "
            "under the frozen coverage fold"
        )
    codes = ", ".join(f"{code}={count}" for code, count in sorted(report.violation_counts.items()))
    return f"L2 events are ineligible: {codes}"


__all__ = ["L2CertificationProfile", "L2PublicationCertification"]
