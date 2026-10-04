"""Provider-neutral S14 Phases 4-5 publication eligibility bridge."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

from .coverage import reconstruct_catalog_coverage
from .manifests import (
    ManifestValidationError,
    _validate_coverage_document,
    _validate_dataset_document,
    _validate_partition_document,
)
from .models import DatasetIdentity
from .publication_eligibility_catalog import (
    PublicationEligibilityCatalog,
    PublicationEligibilityRefusal,
    PublicationEligibilityResult,
)


@dataclass(frozen=True, slots=True)
class PublicationEligibilityEvidence:
    dataset_manifest_path: Path
    partition_manifest_path: Path
    coverage_manifest_paths: tuple[Path, ...]
    storage_root_id: str
    expected_certification_profile: str
    expected_check_suite: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset_manifest_path", Path(self.dataset_manifest_path))
        object.__setattr__(self, "partition_manifest_path", Path(self.partition_manifest_path))
        object.__setattr__(self, "coverage_manifest_paths", tuple(Path(path) for path in self.coverage_manifest_paths))
        if not self.coverage_manifest_paths:
            raise ValueError("at least one coverage manifest is required")
        for name in ("storage_root_id", "expected_certification_profile", "expected_check_suite"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")

    @property
    def expected_profile(self) -> str:
        return self.expected_certification_profile


class PublicationEligibilityBridge:
    """Load durable evidence, fold coverage, and delegate the atomic DB boundary."""

    def __init__(self, catalog: PublicationEligibilityCatalog):
        self.catalog = catalog

    def publish(self, evidence: PublicationEligibilityEvidence) -> PublicationEligibilityResult:
        dataset, dataset_sha = _load_manifest(evidence.dataset_manifest_path, "dataset")
        partition, partition_sha = _load_manifest(evidence.partition_manifest_path, "partition")
        coverage: list[dict[str, Any]] = []
        coverage_sha: list[str] = []
        for path in evidence.coverage_manifest_paths:
            document, digest = _load_manifest(path, "coverage")
            coverage.append(document)
            coverage_sha.append(digest)
        identity = _identity(dataset)
        if _identity(partition) != identity or any(_identity(item) != identity for item in coverage):
            raise PublicationEligibilityRefusal("durable manifests do not share one DatasetIdentity")
        folded, violations = reconstruct_catalog_coverage(coverage, (partition,))
        key = (partition["partition_key"], int(partition["revision"]))
        if violations or key not in folded:
            details = ", ".join(item.code for item in violations)
            raise PublicationEligibilityRefusal(details or "coverage does not resolve to the target revision")
        start, end = folded[key]
        ids = [str(item["coverage_id"]) for item in coverage]
        assertion_ids = [str(assertion["assertion_id"]) for item in coverage for assertion in item["assertions"]]
        return self.catalog.publish(
            dataset=dataset, dataset_sha256=dataset_sha, partition=partition,
            partition_sha256=partition_sha, coverage_start=start, coverage_end=end,
            coverage_ids=ids, assertion_ids=assertion_ids, coverage_sha256=coverage_sha,
            storage_root_id=evidence.storage_root_id.strip(),
            expected_profile=evidence.expected_certification_profile.strip(),
            expected_check_suite=evidence.expected_check_suite.strip(),
        )


def _load_manifest(path: Path, kind: str) -> tuple[dict[str, Any], str]:
    try:
        raw = Path(path).read_bytes()
        document = json.loads(raw.decode("utf-8"))
        if not isinstance(document, dict):
            raise ValueError("manifest must be an object")
        validator = {"dataset": _validate_dataset_document, "partition": _validate_partition_document, "coverage": _validate_coverage_document}[kind]
        validator(document)
    except (OSError, UnicodeError, json.JSONDecodeError, ManifestValidationError, ValueError, TypeError, KeyError) as exc:
        raise PublicationEligibilityRefusal(f"durable {kind} manifest is invalid or unreadable") from exc
    return document, hashlib.sha256(raw).hexdigest()


def _identity(document: dict[str, Any]) -> DatasetIdentity:
    return DatasetIdentity(document["layer"], document["dataset_kind"], document["venue"], document["instrument"], document["record_schema_id"], document.get("feature_set_slug"), document.get("feature_set_version"))


__all__ = ["PublicationEligibilityBridge", "PublicationEligibilityEvidence", "PublicationEligibilityRefusal", "PublicationEligibilityResult"]
