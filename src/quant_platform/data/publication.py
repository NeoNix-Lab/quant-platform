"""S13 publication-certification runtime: seal, certify, record.

The module is source-neutral.  Source-specific eligibility and source-evidence
meaning are supplied by a ``CertificationProfile``; the future S14 bridge is
intentionally not represented here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Protocol

from .coverage import CoverageViolation, reconstruct_catalog_coverage
from .manifests import (
    ManifestValidationError,
    _validate_coverage_document,
    _validate_dataset_document,
    _validate_partition_document,
)
from .materializer import _validate_record
from .models import (
    DatasetIdentity,
    Instant,
    NaturalPartitionIdentity,
    TradeRecord,
    canonical_content_hash_v1,
)
from .parquet import scan_trade_v1_all


class CertificationProfile(Protocol):
    """Source-injected profile; generic certification never names a venue."""

    profile_id: str
    check_suite: str
    code_ref: str

    def applies_to(self, identity: DatasetIdentity) -> bool: ...
    def ordering_key(self, record: TradeRecord) -> tuple[Any, ...]: ...
    def validate_source(self, identity: DatasetIdentity, coverage_documents: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]: ...
    def validate_records(self, identity: DatasetIdentity, records: Sequence[TradeRecord]) -> Mapping[str, Any]: ...


class PublicationCertificationError(RuntimeError):
    """Raised when a phase cannot establish its frozen invariant."""


@dataclass(frozen=True, slots=True)
class SealedPartitionEvidence:
    dataset_manifest_path: Path
    partition_manifest_path: Path
    coverage_manifest_paths: tuple[Path, ...]
    artifact_path: Path
    storage_root_id: str

    def __post_init__(self) -> None:
        if not self.coverage_manifest_paths:
            raise ValueError("at least one durable coverage manifest is required")


@dataclass(frozen=True, slots=True)
class SealedCatalogPartition:
    partition_id: str
    dataset_id: str
    natural_identity: NaturalPartitionIdentity
    state: str
    ts_start: Instant
    ts_end: Instant
    row_count: int
    byte_size: int
    content_sha256: str
    manifest_sha256: str
    producer: str
    code_ref: str


@dataclass(frozen=True, slots=True)
class QualityReport:
    report_id: str
    partition_id: str
    check_suite: str
    status: str
    metrics: dict[str, Any]
    violations: list[dict[str, Any]]
    code_ref: str


class CertificationCatalogWriter(Protocol):
    """Narrow writer boundary used by S13; no lifecycle-promotion method exists."""

    def seal_partition(
        self,
        *,
        dataset: Mapping[str, Any],
        partition: Mapping[str, Any],
        coverage_start: Instant,
        coverage_end: Instant,
        storage_root_id: str,
    ) -> SealedCatalogPartition: ...

    def record_quality_report(
        self,
        *,
        partition_id: str,
        check_suite: str,
        status: str,
        metrics: Mapping[str, Any],
        violations: list[Mapping[str, Any]],
        code_ref: str,
    ) -> QualityReport: ...

    def commit(self) -> None: ...
    def rollback(self) -> None: ...


class CatalogSealer(Protocol):
    """S13 Phase 1 responsibility: establish the durable closed target."""

    def seal(self, evidence: SealedPartitionEvidence) -> SealedCatalogPartition: ...


class Certifier(Protocol):
    """S13 Phase 2 responsibility: evaluate durable evidence without writes."""

    def certify(self, evidence: SealedPartitionEvidence, sealed: SealedCatalogPartition) -> CertificationResult: ...


class CertificationEvidenceRecorder(Protocol):
    """S13 Phase 3 responsibility: persist the quality report by partition UUID."""

    def record_evidence(self, sealed: SealedCatalogPartition, result: CertificationResult) -> QualityReport: ...


@dataclass(frozen=True, slots=True)
class EvidenceCategoryResult:
    category: str
    status: str
    evidence: dict[str, Any]


@dataclass(frozen=True, slots=True)
class CertificationResult:
    partition_id: str
    status: str
    categories: tuple[EvidenceCategoryResult, ...]
    metrics: dict[str, Any]
    violations: tuple[dict[str, Any], ...]
    code_ref: str

    @property
    def publication_eligibility(self) -> str:
        return "deferred-to-s14-bridge"


@dataclass(frozen=True, slots=True)
class PublicationCertificationRun:
    sealed_partition: SealedCatalogPartition
    certification: CertificationResult
    quality_report: QualityReport | None


@dataclass(frozen=True, slots=True)
class _LoadedEvidence:
    dataset: dict[str, Any]
    partition: dict[str, Any]
    coverage: tuple[dict[str, Any], ...]
    dataset_sha256: str
    partition_sha256: str
    coverage_sha256: tuple[str, ...]

    @property
    def partition_identity(self) -> dict[str, Any]:
        return {
            "dataset_identity": {
                key: self.partition[key]
                for key in ("layer", "dataset_kind", "venue", "instrument", "record_schema_id")
            },
            "partition_key": self.partition["partition_key"],
            "revision": self.partition["revision"],
        }


class PublicationCertification(CatalogSealer, Certifier, CertificationEvidenceRecorder):
    """Execute only S13 Phases 1-3 and leave the partition closed."""

    def __init__(
        self,
        catalog: CertificationCatalogWriter,
        profile: CertificationProfile,
        *,
        batch_size: int = 65_536,
        coverage_fold: Callable[[Iterable[Mapping[str, Any]], Iterable[Mapping[str, Any]]], tuple[dict[tuple[str, int], tuple[Instant, Instant]], Sequence[CoverageViolation]]] = reconstruct_catalog_coverage,
    ) -> None:
        self.catalog = catalog
        self.profile = profile
        self.batch_size = batch_size
        self.coverage_fold = coverage_fold

    def seal(self, evidence: SealedPartitionEvidence) -> SealedCatalogPartition:
        loaded = _load_evidence(evidence)
        identity = _dataset_identity(loaded.dataset)
        if not self.profile.applies_to(identity):
            raise PublicationCertificationError("certification profile does not apply to dataset identity")
        if loaded.partition["state"] != "closed":
            raise PublicationCertificationError("S13 seal requires a closed partition manifest")
        coverage, violations = self.coverage_fold(loaded.coverage, (loaded.partition,))
        key = (loaded.partition["partition_key"], loaded.partition["revision"])
        if violations or key not in coverage:
            raise PublicationCertificationError(
                "declared coverage cannot establish one publishable interval: "
                + ", ".join(item.code for item in violations)
            )
        start, end = coverage[key]
        dataset = dict(loaded.dataset)
        dataset["_manifest_sha256"] = loaded.dataset_sha256
        partition = dict(loaded.partition)
        partition["_manifest_sha256"] = loaded.partition_sha256
        try:
            sealed = self.catalog.seal_partition(
                dataset=dataset,
                partition=partition,
                coverage_start=start,
                coverage_end=end,
                storage_root_id=evidence.storage_root_id,
            )
            self.catalog.commit()
        except Exception:
            self.catalog.rollback()
            raise
        if sealed.state != "closed":
            raise PublicationCertificationError("S13 seal writer returned a non-closed partition")
        if sealed.ts_start != start or sealed.ts_end != end:
            raise PublicationCertificationError("catalog seal did not persist the folded declared coverage")
        return sealed

    def certify(self, evidence: SealedPartitionEvidence, sealed: SealedCatalogPartition) -> CertificationResult:
        loaded = _load_evidence(evidence)
        identity = _dataset_identity(loaded.dataset)
        violations: list[dict[str, Any]] = []
        categories: list[EvidenceCategoryResult] = []
        records: tuple[TradeRecord, ...] = ()
        physical_hash: str | None = None
        canonical_hash: str | None = None
        physical_details: dict[str, Any] = {}

        source_status, source_details, source_error = _check(
            lambda: dict(self.profile.validate_source(identity, loaded.coverage))
        )
        categories.append(EvidenceCategoryResult("source", source_status, source_details))
        if source_error:
            violations.append(_violation("source", source_error))

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

        physical_status, physical_details, physical_error = _check(
            lambda: _read_physical(evidence.artifact_path, loaded.partition, sealed, self.profile, self.batch_size)
        )
        categories.append(EvidenceCategoryResult("physical", physical_status, physical_details))
        if physical_error:
            violations.append(_violation("physical", physical_error))
        else:
            records = physical_details.pop("records")
            physical_hash = physical_details["physical_artifact_hash"]

        if physical_error:
            canonical_status, canonical_details, canonical_error = (
                "fail", {}, "canonical verification cannot run after physical verification failed"
            )
        else:
            canonical_status, canonical_details, canonical_error = _check(
                lambda: _verify_canonical(self.profile, identity, loaded.partition, records)
            )
        categories.append(EvidenceCategoryResult("canonical", canonical_status, canonical_details))
        if canonical_error:
            violations.append(_violation("canonical", canonical_error))
        else:
            canonical_hash = canonical_details["canonical_content_hash_v1"]

        categories.append(EvidenceCategoryResult(
            "publication",
            "deferred",
            {"owner": "publication-eligibility-bridge-v1", "phases": [4, 5]},
        ))
        category_order = {name: index for index, name in enumerate(("source", "canonical", "physical", "manifests", "coverage", "publication"))}
        categories.sort(key=lambda item: category_order[item.category])

        evidence_map = {item.category: dict(item.evidence, status=item.status) for item in categories}
        evidence_map["publication"] = {
            "status": "deferred",
            "owner": "publication-eligibility-bridge-v1",
            "phases": [4, 5],
        }
        metrics = {
            "certification_profile": self.profile.profile_id,
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
            code_ref=self.profile.code_ref,
        )

    def record_evidence(self, sealed: SealedCatalogPartition, result: CertificationResult) -> QualityReport:
        if sealed.state != "closed":
            raise PublicationCertificationError("evidence recording requires a closed sealed partition")
        if not isinstance(result.code_ref, str) or not result.code_ref.strip():
            raise PublicationCertificationError("certification evidence requires a non-empty certifier code_ref")
        if result.partition_id != sealed.partition_id:
            raise PublicationCertificationError("certification result targets a different partition")
        try:
            report = self.catalog.record_quality_report(
                partition_id=sealed.partition_id,
                check_suite=self.profile.check_suite,
                status=result.status,
                metrics=result.metrics,
                violations=list(result.violations),
                code_ref=result.code_ref.strip(),
            )
            self.catalog.commit()
        except Exception:
            self.catalog.rollback()
            raise
        if report.partition_id != sealed.partition_id or report.code_ref.strip() != result.code_ref.strip():
            raise PublicationCertificationError("catalog returned mismatched certification evidence")
        return report

    def run(self, evidence: SealedPartitionEvidence) -> PublicationCertificationRun:
        sealed = self.seal(evidence)
        result = self.certify(evidence, sealed)
        report = self.record_evidence(sealed, result)
        return PublicationCertificationRun(sealed, result, report)


def _load_evidence(evidence: SealedPartitionEvidence) -> _LoadedEvidence:
    dataset, dataset_sha = _load_manifest(evidence.dataset_manifest_path, "dataset")
    partition, partition_sha = _load_manifest(evidence.partition_manifest_path, "partition")
    coverage_values: list[dict[str, Any]] = []
    coverage_hashes: list[str] = []
    for path in evidence.coverage_manifest_paths:
        document, digest = _load_manifest(path, "coverage")
        coverage_values.append(document)
        coverage_hashes.append(digest)
    identity = _dataset_identity(dataset)
    if _dataset_identity(partition) != identity or any(_dataset_identity(item) != identity for item in coverage_values):
        raise PublicationCertificationError("durable manifests do not share one DatasetIdentity")
    return _LoadedEvidence(dataset, partition, tuple(coverage_values), dataset_sha, partition_sha, tuple(coverage_hashes))


def _load_manifest(path: Path, kind: str) -> tuple[dict[str, Any], str]:
    try:
        raw = Path(path).read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise PublicationCertificationError(f"cannot read durable {kind} manifest") from exc
    if not isinstance(document, dict):
        raise PublicationCertificationError(f"durable {kind} manifest must be an object")
    try:
        if kind == "dataset":
            _validate_dataset_document(document)
        elif kind == "partition":
            _validate_partition_document(document)
        else:
            _validate_coverage_document(document)
    except (ManifestValidationError, ValueError, TypeError) as exc:
        raise PublicationCertificationError(f"durable {kind} manifest is invalid") from exc
    return document, hashlib.sha256(raw).hexdigest()


def _dataset_identity(document: Mapping[str, Any]) -> DatasetIdentity:
    return DatasetIdentity(
        document["layer"], document["dataset_kind"], document["venue"],
        document["instrument"], document["record_schema_id"],
        document.get("feature_set_slug"), document.get("feature_set_version"),
    )


def _verify_manifests(loaded: _LoadedEvidence, identity: DatasetIdentity, sealed: SealedCatalogPartition) -> dict[str, Any]:
    natural = NaturalPartitionIdentity(identity, loaded.partition["partition_key"], loaded.partition["revision"])
    if sealed.natural_identity != natural:
        raise PublicationCertificationError("catalog seal natural identity differs from partition manifest")
    if sealed.content_sha256 != loaded.partition["sha256"] or sealed.manifest_sha256 != loaded.partition_sha256:
        raise PublicationCertificationError("catalog seal physical or manifest identity differs from durable manifests")
    if loaded.partition["row_count"] != sealed.row_count or loaded.partition["file_size_bytes"] != sealed.byte_size:
        raise PublicationCertificationError("catalog seal dimensions differ from durable partition manifest")
    return {
        "dataset_schema": loaded.dataset["schema_version"],
        "partition_schema": loaded.partition["schema_version"],
        "coverage_documents": len(loaded.coverage),
        "manifest_sha256": loaded.partition_sha256,
    }


def _verify_coverage(
    fold: Callable[..., Any],
    coverage: Sequence[Mapping[str, Any]],
    partition: Mapping[str, Any],
    sealed: SealedCatalogPartition,
) -> dict[str, Any]:
    result, violations = fold(coverage, (partition,))
    key = (partition["partition_key"], partition["revision"])
    if violations or key not in result:
        raise PublicationCertificationError(", ".join(item.code for item in violations) or "coverage does not resolve to the target partition")
    start, end = result[key]
    if sealed.ts_start != start or sealed.ts_end != end:
        raise PublicationCertificationError("folded coverage differs from the sealed catalog row")
    return {"folded_start": start.isoformat(), "folded_end": end.isoformat()}


def _read_physical(
    path: Path,
    partition: Mapping[str, Any],
    sealed: SealedCatalogPartition,
    profile: CertificationProfile,
    batch_size: int,
) -> dict[str, Any]:
    artifact = Path(path)
    if not artifact.is_file():
        raise PublicationCertificationError("physical artifact is missing")
    actual_size = artifact.stat().st_size
    actual_hash = _sha256_file(artifact)
    if actual_size != partition["file_size_bytes"] or actual_size != sealed.byte_size:
        raise PublicationCertificationError("physical artifact file size mismatches authoritative evidence")
    if actual_hash != partition["sha256"] or actual_hash != sealed.content_sha256:
        raise PublicationCertificationError("physical artifact hash mismatches authoritative evidence")
    records: list[TradeRecord] = []
    previous_key: tuple[Any, ...] | None = None
    for batch in scan_trade_v1_all(artifact, batch_size=batch_size):
        for record in batch:
            key = profile.ordering_key(record)
            if previous_key is not None and key <= previous_key:
                raise PublicationCertificationError("physical canonical rows are not strictly ordered")
            previous_key = key
            records.append(record)
    if len(records) != partition["row_count"]:
        raise PublicationCertificationError("physical row count mismatches partition manifest")
    first = partition.get("first_exchange_ts")
    last = partition.get("last_exchange_ts")
    if records:
        if first is None or last is None or records[0].exchange_ts != Instant.parse(first) or records[-1].exchange_ts != Instant.parse(last):
            raise PublicationCertificationError("physical observed bounds mismatch partition manifest")
    elif first is not None or last is not None:
        raise PublicationCertificationError("zero-row physical artifact has non-null observed bounds")
    return {
        "physical_artifact_hash": actual_hash,
        "file_size_bytes": actual_size,
        "row_count": len(records),
        "records": tuple(records),
    }


def _verify_canonical(profile: CertificationProfile, identity: DatasetIdentity, partition: Mapping[str, Any], records: Sequence[TradeRecord]) -> dict[str, Any]:
    normalized = tuple(_validate_record(record, identity) for record in records)
    profile_details = dict(profile.validate_records(identity, normalized))
    digest = canonical_content_hash_v1(normalized)
    return {**profile_details, "canonical_content_hash_v1": digest}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check(function: Callable[[], dict[str, Any]]) -> tuple[str, dict[str, Any], str | None]:
    try:
        return "pass", function(), None
    except Exception as exc:
        return "fail", {}, str(exc)


def _violation(category: str, message: str) -> dict[str, Any]:
    return {"category": category, "message": message}


def _first_assertion_id(coverage: Sequence[Mapping[str, Any]]) -> str | None:
    for document in coverage:
        assertions = document.get("assertions") or ()
        if assertions:
            return assertions[0].get("assertion_id")
    return None


__all__ = [
    "CatalogSealer",
    "Certifier",
    "CertificationEvidenceRecorder",
    "CertificationCatalogWriter",
    "CertificationProfile",
    "CertificationResult",
    "EvidenceCategoryResult",
    "PublicationCertification",
    "PublicationCertificationError",
    "PublicationCertificationRun",
    "QualityReport",
    "SealedCatalogPartition",
    "SealedPartitionEvidence",
]
