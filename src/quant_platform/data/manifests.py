"""Durable dataset, partition, and coverage manifest emission for conformity v1.

This module owns only the generic manifest seam.  Source-specific code supplies
the acquisition evidence; it is deliberately not imported here.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import gc
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any

from .materializer import ParquetMaterialization, physical_artifact_sha256
from .models import DataIntegrityError, DatasetIdentity, Instant, InvalidRequest


class ManifestValidationError(DataIntegrityError):
    """Raised when a manifest cannot be published under a frozen v1 contract."""


@dataclass(frozen=True, slots=True)
class ManifestEmission:
    """Evidence for one atomically persisted manifest document."""

    path: Path
    manifest_sha256: str
    persisted_bytes: bytes
    physical_artifact_hash: str | None = None
    canonical_content_hash_v1: str | None = None

    @property
    def document(self) -> dict[str, Any]:
        """Return a fresh document decoded from the immutable persisted bytes."""

        return json.loads(self.persisted_bytes.decode("utf-8"))

_IDENTIFIER = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_VENUE = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")
_PARTITION_KEY = re.compile(r"^[a-z_]+=[A-Za-z0-9._-]+(?:/[a-z_]+=[A-Za-z0-9._-]+)*$")
_REL_PATH = re.compile(r"^[A-Za-z0-9._=-]+(?:/[A-Za-z0-9._=-]+)*$")
_UTC_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?Z$"
)
_COVERAGE_TIMESTAMP = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_DIGITS = re.compile(r"^(0|[1-9][0-9]*)$")
_DATASET_KINDS = {
    "raw": {"trades", "l2"},
    "canonical": {"trades", "footprint", "l2"},
    "features": {"trade_microstructure", "footprint_microstructure", "l2_microstructure"},
}
_COVERAGE_BASES = {
    "source_archive",
    "api_request",
    "source_extract",
    "source_session",
    "live_stream",
    "backfill",
    "reconciliation",
}
_EVIDENCE_KINDS = {
    "archive_completeness",
    "pagination_complete",
    "deterministic_source_extract",
    "connection_continuity",
    "transport_interruption",
    "sequence_continuity",
    "sequence_discontinuity",
    "reconciliation",
}
_COMPLETE_CONTRADICTORY_EVIDENCE = {
    "transport_interruption",
    "sequence_discontinuity",
}
_ELIGIBLE_PARTITION_STATES = {"closed", "valid", "degraded"}
_TEMPORARY_CLEANUP_RETRIES = 10
_TEMPORARY_CLEANUP_DELAY_SECONDS = 0.01


def emit_dataset_manifest(
    path: str | Path,
    *,
    dataset_identity: DatasetIdentity,
    created_at: Instant | str,
    derived_from: Sequence[DatasetIdentity | Mapping[str, Any]] | None = None,
    transform: str | None = None,
) -> ManifestEmission:
    """Validate and atomically emit one deterministic ``dataset-manifest-v1``."""

    identity = _identity_document(dataset_identity)
    if dataset_identity.layer == "raw":
        if derived_from:
            raise ManifestValidationError("raw dataset manifests cannot have derived_from")
        if transform is not None:
            raise ManifestValidationError("raw dataset manifests cannot have transform")
    else:
        if not derived_from:
            raise ManifestValidationError("non-raw dataset manifests require explicit derived_from")
        if transform is None:
            raise ManifestValidationError("non-raw dataset manifests require explicit transform")

    lineage = None if derived_from is None else [_identity_document(item) for item in derived_from]
    if lineage is not None:
        identities = [_identity_tuple(item) for item in lineage]
        if len(set(identities)) != len(identities):
            raise ManifestValidationError("derived_from identities must be unique")
        if _identity_tuple(identity) in identities:
            raise ManifestValidationError("dataset manifest cannot derive from itself")

    document: dict[str, Any] = {
        "schema_version": "dataset-manifest-v1",
        **identity,
        "rel_root": _derive_rel_root(identity),
        "created_at": _utc_timestamp(created_at, "created_at"),
    }
    if lineage is not None:
        document["derived_from"] = lineage
    if transform is not None:
        document["transform"] = _canonical_identifier(transform, "transform")

    _validate_dataset_document(document)
    return _persist_manifest(path, document)


def emit_partition_manifest(
    path: str | Path,
    materialization: ParquetMaterialization,
    *,
    dataset_identity: DatasetIdentity,
    dataset_root: str | Path,
    partition_key: str,
    revision: int,
    rel_path: str,
    created_at: Instant | str,
    closed_at: Instant | str,
    producer: str,
    code_ref: str,
) -> ManifestEmission:
    """Emit a ``closed`` partition manifest from sealed materialization evidence.

    The physical artifact is re-hashed and re-statted before publication.  No
    sequence, receive timestamp, or source evidence is invented here.
    """

    identity = _identity_document(dataset_identity)
    _validate_partition_key(partition_key)
    _validate_revision(revision)
    _validate_rel_path(rel_path, partition_key)
    _nonblank(producer, "producer")
    _nonblank(code_ref, "code_ref")
    if not isinstance(materialization, ParquetMaterialization):
        raise ManifestValidationError("materialization must be ParquetMaterialization")
    if materialization.dataset_identity != dataset_identity:
        raise ManifestValidationError(
            "materialization dataset identity does not match partition identity"
        )
    _validate_artifact_path(dataset_root, rel_path, materialization)

    artifact = Path(materialization.path)
    if not artifact.is_file():
        raise ManifestValidationError("materialization artifact does not exist")
    actual_size = artifact.stat().st_size
    actual_hash = physical_artifact_sha256(artifact)
    if actual_size != materialization.file_size_bytes:
        raise ManifestValidationError("materialization file size does not match artifact")
    if actual_hash != materialization.physical_artifact_hash:
        raise ManifestValidationError("materialization physical hash does not match artifact")
    if materialization.row_count < 0:
        raise ManifestValidationError("materialization row_count must be non-negative")
    if materialization.row_count == 0:
        if materialization.first_exchange_ts is not None or materialization.last_exchange_ts is not None:
            raise ManifestValidationError("zero-row materialization must have null observed bounds")
    elif materialization.first_exchange_ts is None or materialization.last_exchange_ts is None:
        raise ManifestValidationError("non-empty materialization requires observed bounds")
    if not _SHA256.fullmatch(actual_hash):
        raise ManifestValidationError("materialization physical hash is not lowercase SHA-256")

    document: dict[str, Any] = {
        "schema_version": "partition-manifest-v1",
        **identity,
        "partition_key": partition_key,
        "revision": revision,
        "state": "closed",
        "rel_path": rel_path,
        "file_size_bytes": actual_size,
        "row_count": materialization.row_count,
        "sha256": actual_hash,
        "first_exchange_ts": _optional_utc_timestamp(
            materialization.first_exchange_ts, "first_exchange_ts"
        ),
        "last_exchange_ts": _optional_utc_timestamp(
            materialization.last_exchange_ts, "last_exchange_ts"
        ),
        "created_at": _utc_timestamp(created_at, "created_at"),
        "closed_at": _utc_timestamp(closed_at, "closed_at"),
        "producer": producer,
        "code_ref": code_ref,
    }
    _validate_partition_document(document)
    emission = _persist_manifest(path, document)
    return ManifestEmission(
        path=emission.path,
        manifest_sha256=emission.manifest_sha256,
        persisted_bytes=emission.persisted_bytes,
        physical_artifact_hash=actual_hash,
        canonical_content_hash_v1=materialization.canonical_content_hash_v1,
    )


def emit_coverage_manifest(
    path: str | Path,
    *,
    dataset_identity: DatasetIdentity,
    coverage_id: str,
    supersedes: str | None,
    created_at: Instant | str,
    acquisition: Mapping[str, Any],
    assertions: Sequence[Mapping[str, Any]],
    producer: str,
    code_ref: str,
    source_dataset_identity: DatasetIdentity,
    partition_manifests: Iterable[Mapping[str, Any]] | None = None,
) -> ManifestEmission:
    """Validate and atomically emit explicit source-owned coverage evidence.

    A ``complete`` assertion is accepted only when its single natural
    partition reference resolves to exactly one supplied eligible partition
    manifest. Final observed-bounds containment belongs to catalog
    reconstruction across compatible coverage documents.
    """

    identity = _identity_document(dataset_identity)
    source_identity = _identity_document(source_dataset_identity)
    if _identity_tuple(source_identity) != _identity_tuple(identity):
        raise ManifestValidationError(
            "source evidence dataset identity does not match coverage identity"
        )
    coverage_id = _canonical_identifier(coverage_id, "coverage_id")
    if supersedes is not None:
        supersedes = _canonical_identifier(supersedes, "supersedes")
        if supersedes == coverage_id:
            raise ManifestValidationError("coverage manifest cannot supersede itself")
    _nonblank(producer, "producer")
    _nonblank(code_ref, "code_ref")
    acquisition_doc = _coverage_acquisition(acquisition)
    assertions_doc = _coverage_assertions(assertions, acquisition_doc)
    document: dict[str, Any] = {
        "schema_version": "coverage-manifest-v1",
        **identity,
        "coverage_id": coverage_id,
        "supersedes": supersedes,
        "created_at": _utc_timestamp(created_at, "created_at"),
        "acquisition": acquisition_doc,
        "assertions": assertions_doc,
        "producer": producer,
        "code_ref": code_ref,
    }
    _validate_coverage_document(document)

    partitions = list(partition_manifests or ())
    complete_refs = [
        ref
        for assertion in assertions_doc
        if assertion["status"] == "complete"
        for ref in assertion["partitions"]
    ]
    if complete_refs:
        _validate_complete_partition_refs(document, assertions_doc, partitions)

    return _persist_manifest(path, document)


def _coverage_acquisition(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ManifestValidationError("acquisition must be an explicit mapping")
    expected = {"basis", "intent_start", "intent_end", "source_semantics", "mapping"}
    if set(value) != expected:
        raise ManifestValidationError("acquisition must provide exactly the frozen v1 fields")
    basis = value["basis"]
    if basis not in _COVERAGE_BASES:
        raise ManifestValidationError(f"unsupported acquisition basis: {basis!r}")
    source_semantics = _versioned_identifier(value["source_semantics"], "source_semantics")
    mapping = _versioned_identifier(value["mapping"], "mapping")
    start = _coverage_timestamp(value["intent_start"], "acquisition.intent_start")
    end = _coverage_timestamp(value["intent_end"], "acquisition.intent_end")
    if Instant.parse(start) >= Instant.parse(end):
        raise ManifestValidationError("acquisition interval must be non-degenerate half-open [start, end)")
    return {
        "basis": basis,
        "intent_start": start,
        "intent_end": end,
        "source_semantics": source_semantics,
        "mapping": mapping,
    }


def _coverage_assertions(
    values: Sequence[Mapping[str, Any]], acquisition: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
        raise ManifestValidationError("assertions must be a non-empty sequence")
    intent_start = Instant.parse(acquisition["intent_start"])
    intent_end = Instant.parse(acquisition["intent_end"])
    result: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    spans: list[tuple[Instant, Instant]] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            raise ManifestValidationError(f"assertions[{index}] must be a mapping")
        expected = {"assertion_id", "start", "end", "status", "partitions", "evidence"}
        if set(value) != expected:
            raise ManifestValidationError(f"assertions[{index}] has fields outside coverage v1")
        assertion_id = _canonical_identifier(value["assertion_id"], f"assertions[{index}].assertion_id")
        if assertion_id in seen_ids:
            raise ManifestValidationError(f"duplicate assertion_id: {assertion_id!r}")
        seen_ids.add(assertion_id)
        start = _coverage_timestamp(value["start"], f"assertions[{index}].start")
        end = _coverage_timestamp(value["end"], f"assertions[{index}].end")
        start_i, end_i = Instant.parse(start), Instant.parse(end)
        if start_i >= end_i:
            raise ManifestValidationError(f"assertions[{index}] interval must be non-degenerate")
        if start_i < intent_start or end_i > intent_end:
            raise ManifestValidationError(f"assertions[{index}] interval is outside acquisition intent")
        if any(start_i < previous_end and previous_start < end_i for previous_start, previous_end in spans):
            raise ManifestValidationError("coverage assertions may not overlap")
        spans.append((start_i, end_i))

        status = value["status"]
        if status not in {"complete", "uncertain", "known_gap"}:
            raise ManifestValidationError(f"unsupported assertion status: {status!r}")
        partition_values = value["partitions"]
        if not isinstance(partition_values, Sequence) or isinstance(partition_values, (str, bytes)):
            raise ManifestValidationError(f"assertions[{index}].partitions must be a sequence")
        if status == "complete" and len(partition_values) != 1:
            raise ManifestValidationError("complete assertions must name exactly one partition")
        partitions = [_partition_ref(ref, index, ref_index) for ref_index, ref in enumerate(partition_values)]
        evidence_values = value["evidence"]
        if not isinstance(evidence_values, Sequence) or isinstance(evidence_values, (str, bytes)) or not evidence_values:
            raise ManifestValidationError(f"assertions[{index}] requires explicit evidence")
        evidence = [_evidence_item(item, index, evidence_index) for evidence_index, item in enumerate(evidence_values)]
        if status == "complete" and _COMPLETE_CONTRADICTORY_EVIDENCE.intersection(
            item["kind"] for item in evidence
        ):
            raise ManifestValidationError("complete assertion contradicts its evidence")
        result.append({
            "assertion_id": assertion_id,
            "start": start,
            "end": end,
            "status": status,
            "partitions": partitions,
            "evidence": evidence,
        })
    return result


def _validate_complete_partition_refs(
    coverage: Mapping[str, Any],
    assertions: Sequence[Mapping[str, Any]],
    partition_manifests: Sequence[Mapping[str, Any]],
) -> None:
    by_ref: dict[tuple[str, int], list[Mapping[str, Any]]] = {}
    for index, partition in enumerate(partition_manifests):
        if not isinstance(partition, Mapping):
            raise ManifestValidationError(f"partition_manifests[{index}] must be a mapping")
        _validate_partition_document(partition)
        if _identity_tuple(partition) != _identity_tuple(coverage):
            raise ManifestValidationError("partition manifest dataset identity does not match coverage")
        ref = (partition["partition_key"], partition["revision"])
        by_ref.setdefault(ref, []).append(partition)

    for assertion in assertions:
        if assertion["status"] != "complete":
            continue
        ref = assertion["partitions"][0]
        key = (ref["partition_key"], ref["revision"])
        matches = by_ref.get(key, [])
        if len(matches) != 1:
            raise ManifestValidationError(
                f"complete assertion must resolve to exactly one partition manifest: {key!r}"
            )
        partition = matches[0]
        if partition["state"] not in _ELIGIBLE_PARTITION_STATES:
            raise ManifestValidationError("complete assertion references an ineligible partition")


def _partition_ref(value: Mapping[str, Any], assertion_index: int, ref_index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"partition_key", "revision"}:
        raise ManifestValidationError(f"assertions[{assertion_index}].partitions[{ref_index}] is invalid")
    _validate_partition_key(value["partition_key"])
    _validate_revision(value["revision"])
    return {"partition_key": value["partition_key"], "revision": value["revision"]}


def _evidence_item(value: Mapping[str, Any], assertion_index: int, evidence_index: int) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "detail"}:
        raise ManifestValidationError(f"assertions[{assertion_index}].evidence[{evidence_index}] is invalid")
    if value["kind"] not in _EVIDENCE_KINDS:
        raise ManifestValidationError(f"unsupported evidence kind: {value['kind']!r}")
    detail = _nonblank(value["detail"], "evidence.detail")
    return {"kind": value["kind"], "detail": detail}


def _identity_document(value: DatasetIdentity | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, DatasetIdentity):
        result = value.stable_dict()
    elif isinstance(value, Mapping):
        result = dict(value)
    else:
        raise ManifestValidationError("dataset identity must be DatasetIdentity or a mapping")
    expected = {"layer", "dataset_kind", "venue", "instrument", "record_schema_id"}
    if result.get("layer") == "features":
        expected |= {"feature_set_slug", "feature_set_version"}
    if set(result) != expected:
        raise ManifestValidationError("dataset identity does not match the frozen v1 shape")
    layer = result.get("layer")
    if layer not in _DATASET_KINDS or result.get("dataset_kind") not in _DATASET_KINDS[layer]:
        raise ManifestValidationError("dataset identity layer/dataset_kind combination is invalid")
    if not isinstance(result.get("venue"), str) or not _VENUE.fullmatch(result["venue"]):
        raise ManifestValidationError("dataset identity venue is not path-safe")
    _nonblank(result.get("instrument"), "instrument")
    _canonical_identifier(result.get("record_schema_id"), "record_schema_id")
    if layer == "features":
        _canonical_identifier(result["feature_set_slug"], "feature_set_slug")
        if not isinstance(result["feature_set_version"], int) or isinstance(result["feature_set_version"], bool) or result["feature_set_version"] < 1:
            raise ManifestValidationError("feature_set_version must be positive")
    return result


def _identity_tuple(value: Mapping[str, Any]) -> tuple[Any, ...]:
    fields = ("layer", "dataset_kind", "venue", "instrument", "record_schema_id")
    result = tuple(value.get(field) for field in fields)
    if value.get("layer") == "features":
        result += (value.get("feature_set_slug"), value.get("feature_set_version"))
    return result


def _derive_rel_root(identity: Mapping[str, Any]) -> str:
    parts = [identity["layer"], identity["dataset_kind"], identity["venue"], _encode_instrument(identity["instrument"])]
    if identity["layer"] == "features":
        parts.extend([identity["feature_set_slug"], f"v{identity['feature_set_version']}"])
    parts.append(identity["record_schema_id"])
    return "/".join(parts)


def _encode_instrument(value: str) -> str:
    encoded: list[str] = []
    for byte in value.encode("utf-8"):
        char = chr(byte)
        if ("A" <= char <= "Z") or ("a" <= char <= "z") or ("0" <= char <= "9") or char in "._-":
            encoded.append(char)
        else:
            encoded.append(f"%{byte:02X}")
    return "".join(encoded)


def _canonical_identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ManifestValidationError(f"{field} must be a canonical identifier")
    return value


def _versioned_identifier(value: Any, field: str) -> str:
    result = _canonical_identifier(value, field)
    if not re.search(r"-v[1-9][0-9]*$", result):
        raise ManifestValidationError(f"{field} must carry an explicit -v<N> suffix")
    return result


def _nonblank(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or not value.strip():
        raise ManifestValidationError(f"{field} must be a non-empty string")
    return value


def _validate_revision(value: Any) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ManifestValidationError("revision must be an integer >= 1")


def _validate_partition_key(value: Any) -> None:
    if not isinstance(value, str) or not _PARTITION_KEY.fullmatch(value):
        raise ManifestValidationError("partition_key does not match the frozen v1 grammar")


def _validate_rel_path(value: Any, partition_key: str) -> None:
    if not isinstance(value, str) or not _REL_PATH.fullmatch(value) or "%" in value:
        raise ManifestValidationError("rel_path is not a safe relative path")
    if any(part in {".", ".."} for part in value.split("/")) or not value.startswith(partition_key + "/"):
        raise ManifestValidationError("rel_path must be inside its explicit partition_key")


def _validate_artifact_path(
    dataset_root: str | Path,
    rel_path: str,
    materialization: ParquetMaterialization,
) -> None:
    try:
        root = Path(dataset_root).resolve(strict=True)
        actual = Path(materialization.path).resolve(strict=True)
        expected = (root / Path(rel_path)).resolve(strict=True)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise ManifestValidationError("dataset root and materialization path must resolve") from exc
    try:
        expected.relative_to(root)
    except ValueError as exc:
        raise ManifestValidationError("rel_path resolves outside dataset_root") from exc
    if expected != actual:
        raise ManifestValidationError(
            "rel_path does not identify the materialization artifact under dataset_root"
        )


def _utc_timestamp(value: Instant | str, field: str) -> str:
    if isinstance(value, str) and (value != value.strip() or not _UTC_TIMESTAMP.fullmatch(value)):
        raise ManifestValidationError(f"{field} must be a UTC RFC 3339 timestamp")
    try:
        result = Instant.parse(value).isoformat()
    except (InvalidRequest, TypeError) as exc:
        raise ManifestValidationError(f"{field} must be a valid UTC timestamp") from exc
    if not _UTC_TIMESTAMP.fullmatch(result):
        raise ManifestValidationError(f"{field} must be a UTC RFC 3339 timestamp")
    return result


def _optional_utc_timestamp(value: Instant | None, field: str) -> str | None:
    return None if value is None else _utc_timestamp(value, field)


def _coverage_timestamp(value: Instant | str, field: str) -> str:
    if isinstance(value, str) and (value != value.strip() or not _COVERAGE_TIMESTAMP.fullmatch(value)):
        raise ManifestValidationError(f"{field} must have at most microsecond precision")
    try:
        instant = Instant.parse(value)
    except (InvalidRequest, TypeError) as exc:
        raise ManifestValidationError(f"{field} must be a valid UTC timestamp") from exc
    if instant.epoch_ns % 1_000 != 0:
        raise ManifestValidationError(f"{field} has sub-microsecond precision")
    result = instant.isoformat()
    if not _COVERAGE_TIMESTAMP.fullmatch(result):
        raise ManifestValidationError(f"{field} has sub-microsecond precision")
    return result


def _validate_dataset_document(document: Mapping[str, Any]) -> None:
    required = {
        "schema_version", "layer", "dataset_kind", "venue", "instrument",
        "record_schema_id", "rel_root", "created_at",
    }
    allowed = required | {"feature_set_slug", "feature_set_version", "derived_from", "transform"}
    if not required.issubset(document) or not set(document).issubset(allowed):
        raise ManifestValidationError("dataset manifest fields do not match the frozen v1 shape")
    _identity_document({key: document[key] for key in document if key in {
        "layer", "dataset_kind", "venue", "instrument", "record_schema_id", "feature_set_slug", "feature_set_version"
    }})
    if document.get("schema_version") != "dataset-manifest-v1":
        raise ManifestValidationError("invalid dataset manifest schema_version")
    if document.get("rel_root") != _derive_rel_root(document):
        raise ManifestValidationError("dataset manifest rel_root is not derived from identity")
    _utc_timestamp(document["created_at"], "created_at")
    if document.get("layer") == "raw" and document.get("derived_from"):
        raise ManifestValidationError("raw dataset manifest cannot declare lineage")
    if document.get("layer") == "raw" and "transform" in document:
        raise ManifestValidationError("raw dataset manifest cannot declare transform")
    if document.get("layer") != "raw":
        if not document.get("derived_from"):
            raise ManifestValidationError("derived dataset manifest requires lineage")
        if "transform" not in document:
            raise ManifestValidationError("derived dataset manifest requires transform")
    if "derived_from" in document:
        if not isinstance(document["derived_from"], list):
            raise ManifestValidationError("derived_from must be a list")
        identities = []
        for parent in document["derived_from"]:
            parent_identity = _identity_document(parent)
            identities.append(_identity_tuple(parent_identity))
        if len(set(identities)) != len(identities):
            raise ManifestValidationError("derived_from identities must be unique")
        if _identity_tuple(document) in identities:
            raise ManifestValidationError("dataset manifest cannot derive from itself")
    if "transform" in document:
        _canonical_identifier(document["transform"], "transform")


def _validate_partition_document(document: Mapping[str, Any]) -> None:
    required = {
        "schema_version", "layer", "dataset_kind", "venue", "instrument",
        "record_schema_id", "partition_key", "revision", "state", "rel_path",
        "file_size_bytes", "row_count", "first_exchange_ts", "last_exchange_ts",
        "created_at", "producer", "code_ref",
    }
    optional = {
        "feature_set_slug", "feature_set_version", "sha256", "first_sequence",
        "last_sequence", "closed_at",
    }
    if not required.issubset(document) or not set(document).issubset(required | optional):
        raise ManifestValidationError("partition manifest fields do not match the frozen v1 shape")
    if document.get("schema_version") != "partition-manifest-v1":
        raise ManifestValidationError("invalid partition manifest schema_version")
    identity_fields = {key: document[key] for key in document if key in {
        "layer", "dataset_kind", "venue", "instrument", "record_schema_id", "feature_set_slug", "feature_set_version"
    }}
    _identity_document(identity_fields)
    _validate_partition_key(document.get("partition_key"))
    _validate_revision(document.get("revision"))
    if document.get("state") not in {"writing", "closed", "valid", "degraded", "invalid", "superseded"}:
        raise ManifestValidationError("partition state is not in the frozen v1 vocabulary")
    _validate_rel_path(document.get("rel_path"), document["partition_key"])
    if not isinstance(document.get("file_size_bytes"), int) or isinstance(document["file_size_bytes"], bool) or document["file_size_bytes"] < 0:
        raise ManifestValidationError("file_size_bytes must be a non-negative integer")
    if not isinstance(document.get("row_count"), int) or isinstance(document["row_count"], bool) or document["row_count"] < 0:
        raise ManifestValidationError("row_count must be a non-negative integer")
    state = document["state"]
    if state == "writing":
        if document.get("sha256") is not None and (
            not isinstance(document["sha256"], str) or not _SHA256.fullmatch(document["sha256"])
        ):
            raise ManifestValidationError("writing partition sha256 must be lowercase SHA-256 or null")
    elif not isinstance(document.get("sha256"), str) or not _SHA256.fullmatch(document["sha256"]):
        raise ManifestValidationError("sealed partition requires lowercase SHA-256")
    first, last = document.get("first_exchange_ts"), document.get("last_exchange_ts")
    if document["row_count"] == 0 and (first is not None or last is not None):
        raise ManifestValidationError("zero-row partition must have null observed bounds")
    if document["row_count"] > 0:
        if not isinstance(first, str) or not isinstance(last, str):
            raise ManifestValidationError("non-empty partition requires observed bounds")
        if Instant.parse(first) > Instant.parse(last):
            raise ManifestValidationError("observed bounds are out of order")
    for field in ("first_exchange_ts", "last_exchange_ts", "created_at", "closed_at"):
        value = document.get(field)
        if value is not None:
            try:
                _utc_timestamp(value, field)
            except ManifestValidationError:
                raise
        if field == "closed_at" and state != "writing" and value is None:
            raise ManifestValidationError("closed partition requires closed_at")
    _nonblank(document.get("producer"), "producer")
    _nonblank(document.get("code_ref"), "code_ref")
    first_sequence = document.get("first_sequence")
    last_sequence = document.get("last_sequence")
    if first_sequence is not None and (
        not isinstance(first_sequence, str) or not _DIGITS.fullmatch(first_sequence)
    ):
        raise ManifestValidationError("first_sequence must be a canonical digit string")
    if first_sequence is not None and last_sequence is None:
        raise ManifestValidationError("first_sequence and last_sequence must be paired")
    if first_sequence is None and last_sequence is not None:
        raise ManifestValidationError("first_sequence and last_sequence must be paired")
    if last_sequence is not None and (
        not isinstance(last_sequence, str) or not _DIGITS.fullmatch(last_sequence)
    ):
        raise ManifestValidationError("last_sequence must be a canonical digit string")
    if first_sequence is not None and int(first_sequence) > int(last_sequence):
        raise ManifestValidationError("first_sequence must not exceed last_sequence numerically")


def _validate_coverage_document(document: Mapping[str, Any]) -> None:
    required = {
        "schema_version", "layer", "dataset_kind", "venue", "instrument",
        "record_schema_id", "coverage_id", "supersedes", "created_at",
        "acquisition", "assertions", "producer", "code_ref",
    }
    optional = {"feature_set_slug", "feature_set_version"}
    if not required.issubset(document) or not set(document).issubset(required | optional):
        raise ManifestValidationError("coverage manifest fields do not match the frozen v1 shape")
    if document.get("schema_version") != "coverage-manifest-v1":
        raise ManifestValidationError("invalid coverage manifest schema_version")
    identity_fields = {key: document[key] for key in document if key in {
        "layer", "dataset_kind", "venue", "instrument", "record_schema_id", "feature_set_slug", "feature_set_version"
    }}
    _identity_document(identity_fields)
    _canonical_identifier(document.get("coverage_id"), "coverage_id")
    supersedes = document.get("supersedes")
    if supersedes is not None:
        _canonical_identifier(supersedes, "supersedes")
    _utc_timestamp(document["created_at"], "created_at")
    acquisition = _coverage_acquisition(document.get("acquisition"))
    if not isinstance(document.get("assertions"), list) or not document["assertions"]:
        raise ManifestValidationError("coverage assertions must be non-empty")
    _coverage_assertions(document["assertions"], acquisition)
    _nonblank(document.get("producer"), "producer")
    _nonblank(document.get("code_ref"), "code_ref")


def _persist_manifest(path: str | Path, document: dict[str, Any]) -> ManifestEmission:
    target = Path(path)
    if target.suffix.lower() != ".json":
        raise ManifestValidationError("manifest path must have a .json suffix")
    persisted = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    digest = hashlib.sha256(persisted).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as handle:
            handle.write(persisted)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except Exception:
        _remove_temporary_file(temporary)
        raise
    return ManifestEmission(target, digest, persisted)


def _remove_temporary_file(path: Path) -> None:
    for attempt in range(_TEMPORARY_CLEANUP_RETRIES + 1):
        try:
            path.unlink(missing_ok=True)
            return
        except OSError:
            gc.collect()
            if attempt == _TEMPORARY_CLEANUP_RETRIES:
                return
            time.sleep(_TEMPORARY_CLEANUP_DELAY_SECONDS)


__all__ = [
    "ManifestEmission",
    "ManifestValidationError",
    "emit_coverage_manifest",
    "emit_dataset_manifest",
    "emit_partition_manifest",
]
