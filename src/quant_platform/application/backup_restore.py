"""K08 backup/restore v1: capture, export and restore of a ``RecoverySetV1``.

This is the composition seam over already-CREDITed capabilities: it captures
one identity-bound recovery set from an already-sealed S13 publication
(:mod:`quant_platform.data.publication`), persists it as a durable,
independently reloadable backup at an explicit destination, and restores
that evidence into an empty isolated target by re-admitting it through the
existing :class:`~quant_platform.data.publication.CertificationCatalogWriter`
write contract -- never a parallel catalog-write path, never a generalized
backup-provider abstraction.

Only the smallest seam needed for ADR-0039 is implemented here:

- capture cross-checks every durable member (dataset identity, partition
  manifest, folded declared coverage, physical artifact, and -- when
  applicable -- K06 source-protection evidence) against the authoritative
  sealed publication before binding a recovery identity, so a
  mixed/incompatible or foreign generation is refused rather than silently
  captured;
- backup creation is a pure copy of the exact bound evidence plus a durable
  recovery manifest (recording the canonical payload, recovery identity,
  member names/digests and catalog-admission locators), so the backup is
  independently verifiable/restorable from disk after process loss, never a
  mutation of primary state, and never touches staging/cache/volatile files;
- restore requires an explicitly empty target whose registered storage-root
  locator resolves to exactly that target (never a surviving primary
  locator), validates every restored path stays inside the target, fails
  closed on any missing/content-mismatched/foreign member, and verifies the
  returned catalog admission *before* committing it -- never durably admits
  an unverified row;
- restore re-admission reuses ``CertificationCatalogWriter.seal_partition``
  unchanged, so the restored catalog resolves the restored publication using
  the same contract a fresh S13 seal would use -- this module defines no new
  catalog semantics.  A live revision N > 1 is restored by supplying the
  ordered ``predecessor_exports`` chain (revisions 1..N-1) alongside the
  target export: each predecessor is re-admitted (catalog metadata only --
  a superseded revision is never DataGateway-read) before the target
  revision seals, exactly reproducing the contiguous-admission history the
  unmodified catalog contract already requires.  This is not a new
  persistence contract; it is the existing one, driven in order.

Whether a given ``target_root``/``catalog_writer`` pair is genuinely on
storage independent of the tested primary boundary is a deployment-topology
fact this module cannot observe and never asserts -- see ADR-0039 Sec. 2.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any

from ..data.coverage import reconstruct_catalog_coverage
from ..data.materializer import physical_artifact_sha256
from ..data.models import CoverageInterval, DatasetIdentity
from ..data.publication import CertificationCatalogWriter, SealedCatalogPartition, SealedPartitionEvidence
from ..operations.protection import ProtectionAssessment, ProtectionState
from ..operations.recovery import (
    FINALIZED_PARTITION_STATES,
    RecoveryError,
    RecoverySetV1,
    recovery_set_from_canonical_payload,
)


RECOVERY_MANIFEST_FILENAME = "recovery-manifest.json"
RECOVERY_MANIFEST_SCHEMA_VERSION = "recovery-manifest-v1"


@dataclass(frozen=True, slots=True)
class RecoveryBackupExport:
    """Evidence for one recovery set's durable, independently reloadable backup."""

    recovery_set: RecoverySetV1
    destination_root: Path
    dataset_manifest_path: Path
    partition_manifest_path: Path
    coverage_manifest_paths: tuple[Path, ...]
    artifact_path: Path
    k06_protection_document_path: Path | None = None


@dataclass(frozen=True, slots=True)
class RestoredRecoverySet:
    """Evidence for one recovery set successfully restored into an isolated target."""

    recovery_set: RecoverySetV1
    sealed: SealedCatalogPartition
    dataset_manifest_path: Path
    partition_manifest_path: Path
    coverage_manifest_paths: tuple[Path, ...]
    artifact_path: Path
    rel_root: str
    rel_path: str
    k06_protection_document_path: Path | None = None


def capture_recovery_set(
    evidence: SealedPartitionEvidence,
    sealed: SealedCatalogPartition,
    *,
    partition_state: str = "closed",
    k06_protection: ProtectionAssessment | None = None,
) -> RecoverySetV1:
    """Bind one already-sealed S13 publication's durable evidence to a ``RecoverySetV1``.

    ``sealed`` is CREDITed, already-authoritative S13 seal evidence (never
    re-derived).  Every durable member named by ``evidence`` is cross-checked
    for semantic linkage against ``sealed`` before binding a recovery
    identity -- the dataset manifest's own declared identity, the partition
    manifest's exact bytes, the coverage manifests' folded declared interval
    (via the same CREDITed :func:`~quant_platform.data.coverage.reconstruct_catalog_coverage`
    S13 certification already uses) and the physical artifact's content --
    so a foreign or mixed/incompatible generation can never be captured as
    one recovery set.

    ``partition_state`` records which finalized lifecycle state this backup
    is labeled under (the durable seal evidence itself is always ``closed``;
    a caller backing up an already-promoted live partition should pass the
    catalog-observed ``"valid"``/``"degraded"`` state here instead).
    ``k06_protection``, when the backed-up publication has applicable
    K06-protected source evidence, must be an already-``PROTECTED``
    :class:`~quant_platform.operations.protection.ProtectionAssessment`; its
    identity is bound into the recovery set so that evidence is backed up
    and restored alongside the canonical publication, not merely credited.
    """

    if sealed.state != "closed":
        raise RecoveryError(
            "K08 v1 requires already-sealed (closed) durable evidence as its capture "
            f"input; got {sealed.state!r}"
        )
    if partition_state not in FINALIZED_PARTITION_STATES:
        raise RecoveryError(
            f"partition_state must be a finalized lifecycle state; got {partition_state!r}"
        )

    dataset_manifest_sha256 = _sha256_file(evidence.dataset_manifest_path)
    dataset_document = _load_json(evidence.dataset_manifest_path)
    dataset_identity = _dataset_identity_from_document(dataset_document)
    if dataset_identity != sealed.natural_identity.dataset_identity:
        raise RecoveryError(
            "durable dataset manifest identity does not match the authoritative sealed "
            "publication; refusing to bind a mixed/incompatible generation"
        )

    partition_manifest_sha256 = _sha256_file(evidence.partition_manifest_path)
    if partition_manifest_sha256 != sealed.manifest_sha256:
        raise RecoveryError(
            "durable partition manifest does not match the authoritative sealed "
            "publication; refusing to bind a mixed/incompatible generation"
        )
    partition_document = _load_json(evidence.partition_manifest_path)

    coverage_manifest_sha256 = tuple(
        _sha256_file(path) for path in evidence.coverage_manifest_paths
    )
    if not coverage_manifest_sha256:
        raise RecoveryError("a finalized publication requires at least one durable coverage manifest")
    coverage_documents = tuple(_load_json(path) for path in evidence.coverage_manifest_paths)

    fold_result, violations = reconstruct_catalog_coverage(coverage_documents, (partition_document,))
    if violations:
        raise RecoveryError(
            "declared coverage evidence does not fold to one consistent interval for the "
            "sealed publication; refusing to bind a mixed/incompatible generation: "
            + ", ".join(item.code for item in violations)
        )
    key = (sealed.natural_identity.partition_key, sealed.natural_identity.revision)
    folded = fold_result.get(key)
    if folded is None or folded != (sealed.ts_start, sealed.ts_end):
        raise RecoveryError(
            "declared coverage evidence does not reproduce the sealed publication's "
            "coverage; refusing to bind a mixed/incompatible generation"
        )

    physical_content_sha256 = physical_artifact_sha256(evidence.artifact_path)
    actual_size = Path(evidence.artifact_path).stat().st_size
    if physical_content_sha256 != sealed.content_sha256 or actual_size != sealed.byte_size:
        raise RecoveryError(
            "physical artifact does not match the authoritative sealed publication; "
            "refusing to bind a mixed/incompatible generation"
        )

    k06_protection_identity = None
    if k06_protection is not None:
        if not isinstance(k06_protection, ProtectionAssessment):
            raise RecoveryError("k06_protection must be a ProtectionAssessment")
        if k06_protection.state != ProtectionState.PROTECTED:
            raise RecoveryError(
                "k06_protection evidence must be PROTECTED to bind required source "
                f"evidence into a recovery set; got {k06_protection.state!r}"
            )
        k06_protection_identity = k06_protection.protection_identity

    return RecoverySetV1(
        natural_identity=sealed.natural_identity,
        dataset_manifest_sha256=dataset_manifest_sha256,
        partition_manifest_sha256=partition_manifest_sha256,
        coverage_manifest_sha256=coverage_manifest_sha256,
        physical_content_sha256=physical_content_sha256,
        physical_size_bytes=sealed.byte_size,
        declared_coverage=CoverageInterval(sealed.ts_start, sealed.ts_end),
        partition_state=partition_state,
        catalog_dataset_id=sealed.dataset_id,
        catalog_partition_id=sealed.partition_id,
        k06_protection_identity=k06_protection_identity,
    )


def export_recovery_set(
    recovery_set: RecoverySetV1,
    evidence: SealedPartitionEvidence,
    destination_root: str | Path,
    *,
    k06_protection_document: Mapping[str, Any] | None = None,
) -> RecoveryBackupExport:
    """Copy one finalized recovery set's durable evidence to an explicit backup destination.

    Every source file is opened read-only and copied, never moved, truncated
    or rewritten: backup creation cannot mutate or destroy finalized primary
    state.  Only the exact files bound to ``recovery_set`` are copied -- no
    staging, cache or other volatile runtime state.  A durable recovery
    manifest (:data:`RECOVERY_MANIFEST_FILENAME`) is written last, recording
    the canonical payload, recovery identity and member index, so the export
    is independently reloadable and verifiable via
    :func:`load_recovery_backup_export` without a surviving in-memory
    ``RecoverySetV1``.

    ``k06_protection_document`` (a K06
    :class:`~quant_platform.operations.protection.ProtectionAssessment`'s
    ``stable_dict()``) must be supplied exactly when ``recovery_set`` binds a
    ``k06_protection_identity``, and must match it.
    """

    if (recovery_set.k06_protection_identity is None) != (k06_protection_document is None):
        raise RecoveryError(
            "k06_protection_document must be supplied exactly when recovery_set binds "
            "a k06_protection_identity"
        )
    if k06_protection_document is not None:
        if k06_protection_document.get("protection_identity") != recovery_set.k06_protection_identity:
            raise RecoveryError("k06_protection_document does not match the bound k06_protection_identity")
        if k06_protection_document.get("state") != "PROTECTED":
            raise RecoveryError("k06_protection_document must record a PROTECTED state")

    root = Path(destination_root) / recovery_set.fingerprint_hex
    if root.exists() and any(root.iterdir()):
        raise RecoveryError("backup destination for this recovery identity is not empty")
    root.mkdir(parents=True, exist_ok=True)

    _verify_hash(evidence.dataset_manifest_path, recovery_set.dataset_manifest_sha256)
    _verify_hash(evidence.partition_manifest_path, recovery_set.partition_manifest_sha256)
    _verify_file(evidence.artifact_path, recovery_set.physical_content_sha256, recovery_set.physical_size_bytes)

    dataset_copy = _atomic_copy(Path(evidence.dataset_manifest_path), root / "dataset.json")
    partition_copy = _atomic_copy(Path(evidence.partition_manifest_path), root / "partition.json")
    coverage_copies = tuple(
        _atomic_copy(Path(path), root / f"coverage-{index:03d}.json")
        for index, path in enumerate(evidence.coverage_manifest_paths)
    )
    copied_coverage_hashes = tuple(sorted(_sha256_file(path) for path in coverage_copies))
    if copied_coverage_hashes != recovery_set.coverage_manifest_sha256:
        raise RecoveryError("exported coverage evidence does not match the bound recovery set identity")
    artifact_copy = _atomic_copy(Path(evidence.artifact_path), root / "artifact.parquet")

    k06_copy = None
    if k06_protection_document is not None:
        k06_copy = _atomic_write_json(root / "k06-protection.json", k06_protection_document)

    _write_recovery_manifest(
        root, recovery_set,
        coverage_count=len(coverage_copies),
        has_k06_document=k06_copy is not None,
    )

    return RecoveryBackupExport(
        recovery_set, root, dataset_copy, partition_copy, coverage_copies, artifact_copy, k06_copy,
    )


def load_recovery_backup_export(destination_root: str | Path) -> RecoveryBackupExport:
    """Reconstruct and re-verify a ``RecoveryBackupExport`` purely from disk.

    No in-memory ``RecoverySetV1`` needs to have survived process loss: every
    field is reloaded from the durable recovery manifest
    (:data:`RECOVERY_MANIFEST_FILENAME`) and every member's content is
    re-hashed against it before anything is trusted, exactly as
    :func:`export_recovery_set` bound it.
    """

    root = Path(destination_root)
    manifest_path = root / RECOVERY_MANIFEST_FILENAME
    document = _load_json(manifest_path)
    if document.get("schema_version") != RECOVERY_MANIFEST_SCHEMA_VERSION:
        raise RecoveryError(f"unsupported recovery manifest schema_version at {manifest_path}")

    catalog_dataset_id = document.get("catalog_dataset_id")
    catalog_partition_id = document.get("catalog_partition_id")
    recovery_set = recovery_set_from_canonical_payload(
        document.get("canonical_payload"),
        catalog_dataset_id=catalog_dataset_id,
        catalog_partition_id=catalog_partition_id,
    )
    if recovery_set.recovery_identity != document.get("recovery_identity"):
        raise RecoveryError("recovery manifest recovery_identity does not match its own canonical payload")

    members = document.get("members")
    if not isinstance(members, Mapping):
        raise RecoveryError("recovery manifest is missing its members index")
    dataset_manifest_path = _member_path(root, members.get("dataset_manifest"), "dataset_manifest")
    partition_manifest_path = _member_path(root, members.get("partition_manifest"), "partition_manifest")
    coverage_names = members.get("coverage_manifests")
    if not isinstance(coverage_names, list) or not coverage_names:
        raise RecoveryError("recovery manifest coverage_manifests must be a non-empty list")
    coverage_manifest_paths = tuple(
        _member_path(root, name, "coverage_manifests") for name in coverage_names
    )
    artifact_path = _member_path(root, members.get("artifact"), "artifact")
    k06_name = members.get("k06_protection")
    k06_protection_document_path = (
        _member_path(root, k06_name, "k06_protection") if k06_name is not None else None
    )
    if (recovery_set.k06_protection_identity is None) != (k06_protection_document_path is None):
        raise RecoveryError("recovery manifest k06 evidence presence does not match its bound identity")

    _verify_hash(dataset_manifest_path, recovery_set.dataset_manifest_sha256)
    _verify_hash(partition_manifest_path, recovery_set.partition_manifest_sha256)
    coverage_hashes = tuple(sorted(_sha256_file(path) for path in coverage_manifest_paths))
    if coverage_hashes != recovery_set.coverage_manifest_sha256:
        raise RecoveryError("backup coverage evidence is missing or corrupt")
    _verify_file(artifact_path, recovery_set.physical_content_sha256, recovery_set.physical_size_bytes)
    if k06_protection_document_path is not None:
        k06_document = _load_json(k06_protection_document_path)
        if k06_document.get("protection_identity") != recovery_set.k06_protection_identity:
            raise RecoveryError("backup k06 protection evidence does not match the bound recovery set identity")

    return RecoveryBackupExport(
        recovery_set, root, dataset_manifest_path, partition_manifest_path,
        coverage_manifest_paths, artifact_path, k06_protection_document_path,
    )


def restore_recovery_set(
    export: RecoveryBackupExport,
    target_root: str | Path,
    *,
    forbidden_roots: Iterable[str | Path],
    catalog_writer: CertificationCatalogWriter,
    storage_root_id: str,
    storage_root_abs_path: str | Path,
    predecessor_exports: Sequence[RecoveryBackupExport] = (),
) -> RestoredRecoverySet:
    """Restore one exported recovery set into an empty isolated target.

    ``target_root`` must not already hold content and must not resolve to or
    inside any of ``forbidden_roots`` (the primary/backup locators this
    restore is proving independent of).  ``storage_root_abs_path`` -- the
    absolute path the catalog has *already* registered for
    ``storage_root_id`` -- must resolve to exactly ``target_root``; otherwise
    the restored catalog row could resolve reads to a surviving primary (or
    any other) location instead of the isolated target, which this function
    refuses rather than silently permit.  Every restored member path is
    proven to stay inside ``target_root`` before any directory is created or
    file copied.

    A live revision N > 1 requires ``predecessor_exports`` to be the ordered,
    contiguous exports for revisions 1..N-1 of the same dataset/partition_key
    family: each is re-admitted (catalog metadata only) before the target
    revision itself seals, exactly reproducing the contiguous-admission
    history the unmodified ``catalog_writer`` contract already requires --
    this is not a new persistence contract, just that existing one driven in
    order.  ``catalog_writer`` re-admits the restored evidence under
    ``storage_root_id`` through the unchanged S13
    :class:`~quant_platform.data.publication.CertificationCatalogWriter`
    write contract.  The returned admission's identity, digests and coverage
    are verified *before* ``commit()`` is called -- any mismatch rolls back
    rather than leaving unverified state durable.  A missing or
    content-mismatched backup member fails the restore rather than silently
    substituting.
    """

    recovery_set = export.recovery_set
    target = Path(target_root)
    _refuse_alias(target, forbidden_roots)
    resolved_target = _resolve_maybe(target)
    if _resolve_maybe(Path(storage_root_abs_path)) != resolved_target:
        raise RecoveryError(
            "storage_root_abs_path must resolve to exactly target_root, or the restored "
            "catalog could resolve reads to a location other than the isolated restore target"
        )
    if target.exists():
        if not target.is_dir():
            raise RecoveryError("restore target must be a directory")
        if any(target.iterdir()):
            raise RecoveryError("restore target must be empty")
    else:
        target.mkdir(parents=True)

    family = recovery_set.natural_identity
    expected_revision = len(predecessor_exports) + 1
    if family.revision != expected_revision:
        raise RecoveryError(
            f"restoring revision {family.revision} requires exactly "
            f"{family.revision - 1} ordered predecessor_exports (got "
            f"{len(predecessor_exports)}); the unmodified catalog admission contract "
            "requires every prior revision to admit first in an empty topology"
        )
    for index, predecessor in enumerate(predecessor_exports, start=1):
        predecessor_identity = predecessor.recovery_set.natural_identity
        if (
            predecessor_identity.dataset_identity != family.dataset_identity
            or predecessor_identity.partition_key != family.partition_key
            or predecessor_identity.revision != index
        ):
            raise RecoveryError(
                "predecessor_exports must be the contiguous ordered revision chain for "
                "the same dataset/partition_key, starting at revision 1"
            )
        _admit_predecessor(predecessor, catalog_writer, storage_root_id)

    dataset_bytes = _read_verified(export.dataset_manifest_path, recovery_set.dataset_manifest_sha256)
    partition_bytes = _read_verified(export.partition_manifest_path, recovery_set.partition_manifest_sha256)
    coverage_hashes = tuple(sorted(_sha256_file(path) for path in export.coverage_manifest_paths))
    if coverage_hashes != recovery_set.coverage_manifest_sha256:
        raise RecoveryError("backup coverage evidence is missing or corrupt")
    _verify_file(export.artifact_path, recovery_set.physical_content_sha256, recovery_set.physical_size_bytes)
    if export.k06_protection_document_path is not None:
        k06_document = _load_json(export.k06_protection_document_path)
        if k06_document.get("protection_identity") != recovery_set.k06_protection_identity:
            raise RecoveryError("backup k06 protection evidence does not match the bound recovery set identity")

    dataset_document = json.loads(dataset_bytes)
    partition_document = json.loads(partition_bytes)
    rel_root = _validate_safe_relative(dataset_document.get("rel_root"), "dataset manifest rel_root")
    rel_path = _validate_safe_relative(partition_document.get("rel_path"), "partition manifest rel_path")

    artifact_target = target / rel_root / rel_path
    if not _is_relative_to(_resolve_maybe(artifact_target), resolved_target):
        raise RecoveryError("restored artifact destination resolves outside the isolated restore target")

    dataset_target = _atomic_copy(export.dataset_manifest_path, target / "dataset.json")
    partition_target = _atomic_copy(export.partition_manifest_path, target / "partition.json")
    coverage_targets = tuple(
        _atomic_copy(source, target / f"coverage-{index:03d}.json")
        for index, source in enumerate(export.coverage_manifest_paths)
    )
    artifact_target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_copy(export.artifact_path, artifact_target)
    k06_target = None
    if export.k06_protection_document_path is not None:
        k06_target = _atomic_copy(export.k06_protection_document_path, target / "k06-protection.json")

    dataset_document = dict(dataset_document, _manifest_sha256=recovery_set.dataset_manifest_sha256)
    partition_document = dict(partition_document, _manifest_sha256=recovery_set.partition_manifest_sha256)
    try:
        sealed = catalog_writer.seal_partition(
            dataset=dataset_document,
            partition=partition_document,
            coverage_start=recovery_set.declared_coverage.start,
            coverage_end=recovery_set.declared_coverage.end,
            storage_root_id=storage_root_id,
        )
        if sealed.natural_identity != recovery_set.natural_identity:
            raise RecoveryError("restored catalog admission produced a different publication identity")
        if (
            sealed.content_sha256 != recovery_set.physical_content_sha256
            or sealed.manifest_sha256 != recovery_set.partition_manifest_sha256
        ):
            raise RecoveryError("restored catalog admission does not match the bound recovery set identity")
        if (
            sealed.ts_start != recovery_set.declared_coverage.start
            or sealed.ts_end != recovery_set.declared_coverage.end
        ):
            raise RecoveryError("restored catalog admission does not reproduce declared coverage")
        catalog_writer.commit()
    except Exception:
        catalog_writer.rollback()
        raise

    return RestoredRecoverySet(
        recovery_set, sealed, dataset_target, partition_target, coverage_targets,
        artifact_target, rel_root, rel_path, k06_target,
    )


def _admit_predecessor(
    export: RecoveryBackupExport,
    catalog_writer: CertificationCatalogWriter,
    storage_root_id: str,
) -> None:
    """Re-admit one already-restored predecessor revision's catalog metadata only.

    A superseded revision is never read by DataGateway (no ``LifecyclePolicy``
    includes ``superseded``), so its physical artifact never needs to occupy
    the isolated target -- only its manifest evidence, verified the same way
    as every other member, and admitted through the same unmodified catalog
    contract.
    """

    recovery_set = export.recovery_set
    dataset_bytes = _read_verified(export.dataset_manifest_path, recovery_set.dataset_manifest_sha256)
    partition_bytes = _read_verified(export.partition_manifest_path, recovery_set.partition_manifest_sha256)
    coverage_hashes = tuple(sorted(_sha256_file(path) for path in export.coverage_manifest_paths))
    if coverage_hashes != recovery_set.coverage_manifest_sha256:
        raise RecoveryError("predecessor backup coverage evidence is missing or corrupt")
    _verify_file(export.artifact_path, recovery_set.physical_content_sha256, recovery_set.physical_size_bytes)

    dataset_document = dict(json.loads(dataset_bytes), _manifest_sha256=recovery_set.dataset_manifest_sha256)
    partition_document = dict(json.loads(partition_bytes), _manifest_sha256=recovery_set.partition_manifest_sha256)
    try:
        sealed = catalog_writer.seal_partition(
            dataset=dataset_document,
            partition=partition_document,
            coverage_start=recovery_set.declared_coverage.start,
            coverage_end=recovery_set.declared_coverage.end,
            storage_root_id=storage_root_id,
        )
        if sealed.natural_identity != recovery_set.natural_identity:
            raise RecoveryError("predecessor catalog admission produced a different publication identity")
        catalog_writer.commit()
    except Exception:
        catalog_writer.rollback()
        raise


def _dataset_identity_from_document(document: Mapping[str, Any]) -> DatasetIdentity:
    try:
        return DatasetIdentity(
            layer=document["layer"],
            dataset_kind=document["dataset_kind"],
            venue=document["venue"],
            instrument=document["instrument"],
            record_schema_id=document["record_schema_id"],
            feature_set_slug=document.get("feature_set_slug"),
            feature_set_version=document.get("feature_set_version"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RecoveryError("durable dataset manifest does not carry a valid DatasetIdentity") from exc


def _write_recovery_manifest(
    root: Path,
    recovery_set: RecoverySetV1,
    *,
    coverage_count: int,
    has_k06_document: bool,
) -> Path:
    document = {
        "schema_version": RECOVERY_MANIFEST_SCHEMA_VERSION,
        "recovery_identity": recovery_set.recovery_identity,
        "canonical_payload": recovery_set.canonical_payload(),
        "catalog_dataset_id": recovery_set.catalog_dataset_id,
        "catalog_partition_id": recovery_set.catalog_partition_id,
        "members": {
            "dataset_manifest": "dataset.json",
            "partition_manifest": "partition.json",
            "coverage_manifests": [f"coverage-{index:03d}.json" for index in range(coverage_count)],
            "artifact": "artifact.parquet",
            "k06_protection": "k06-protection.json" if has_k06_document else None,
        },
    }
    return _atomic_write_json(root / RECOVERY_MANIFEST_FILENAME, document)


def _member_path(root: Path, name: Any, field_name: str) -> Path:
    if not isinstance(name, str):
        raise RecoveryError(f"recovery manifest member {field_name!r} is missing or malformed")
    safe = _validate_safe_relative(name, f"members.{field_name}")
    return root / safe


def _validate_safe_relative(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise RecoveryError(f"{field_name} must be a non-empty relative path")
    if "\\" in value or ":" in value:
        raise RecoveryError(f"{field_name} must be a POSIX-style relative path")
    pure = PurePosixPath(value)
    if pure.is_absolute():
        raise RecoveryError(f"{field_name} must not be an absolute path")
    parts = pure.parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise RecoveryError(f"{field_name} must not contain '.', '..' or empty segments")
    return value


def _refuse_alias(target: Path, forbidden_roots: Iterable[str | Path]) -> None:
    resolved_target = _resolve_maybe(target)
    for raw in forbidden_roots:
        resolved_forbidden = _resolve_maybe(Path(raw))
        if (
            resolved_target == resolved_forbidden
            or _is_relative_to(resolved_target, resolved_forbidden)
            or _is_relative_to(resolved_forbidden, resolved_target)
        ):
            raise RecoveryError(
                "restore target must not alias a primary or backup storage locator"
            )


def _resolve_maybe(path: Path) -> Path:
    try:
        return path.resolve(strict=True)
    except OSError:
        return path.resolve(strict=False)


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def _sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: str | Path) -> dict[str, Any]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise RecoveryError(f"evidence is missing or unreadable: {path}") from exc
    try:
        document = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RecoveryError(f"evidence is not valid JSON: {path}") from exc
    if not isinstance(document, dict):
        raise RecoveryError(f"evidence must be a JSON object: {path}")
    return document


def _verify_hash(path: str | Path, expected_sha256: str) -> None:
    try:
        actual = _sha256_file(path)
    except OSError as exc:
        raise RecoveryError(f"evidence is missing or unreadable: {path}") from exc
    if actual != expected_sha256:
        raise RecoveryError(f"evidence content does not match the bound recovery set: {path}")


def _verify_file(path: str | Path, expected_sha256: str, expected_size: int) -> None:
    try:
        actual_size = Path(path).stat().st_size
    except OSError as exc:
        raise RecoveryError(f"evidence is missing: {path}") from exc
    if actual_size != expected_size:
        raise RecoveryError(f"evidence size does not match the bound recovery set: {path}")
    _verify_hash(path, expected_sha256)


def _read_verified(path: Path, expected_sha256: str) -> bytes:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise RecoveryError(f"backup member is missing or unreadable: {path}") from exc
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise RecoveryError(f"backup member content does not match the bound recovery set: {path}")
    return data


def _atomic_copy(source: Path, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=str(destination.parent)
    )
    os.close(fd)
    temporary = Path(temp_name)
    try:
        with Path(source).open("rb") as src, temporary.open("wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return destination


def _atomic_write_json(path: Path, document: Mapping[str, Any]) -> Path:
    payload = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    temporary = Path(temp_name)
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return path


__all__ = [
    "RECOVERY_MANIFEST_FILENAME",
    "RECOVERY_MANIFEST_SCHEMA_VERSION",
    "RecoveryBackupExport",
    "RestoredRecoverySet",
    "capture_recovery_set",
    "export_recovery_set",
    "load_recovery_backup_export",
    "restore_recovery_set",
]
