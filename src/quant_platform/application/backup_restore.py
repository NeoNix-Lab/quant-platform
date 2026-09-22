"""K08 backup/restore v1: capture, export and restore of a ``RecoverySetV1``.

This is the composition seam over already-CREDITed capabilities: it captures
one identity-bound recovery set from an already-sealed S13 publication
(:mod:`quant_platform.data.publication`), copies its exact durable evidence
to an explicit backup destination, and restores that evidence into an empty
isolated target by re-admitting it through the existing
:class:`~quant_platform.data.publication.CertificationCatalogWriter` write
contract -- never a parallel catalog-write path, never a generalized
backup-provider abstraction.

Only the smallest seam needed for ADR-0039 is implemented here:

- backup creation is a pure copy of the exact bound evidence, never a mutation
  of primary state, and never touches staging/cache/volatile files;
- restore requires an explicitly empty target that cannot alias a supplied
  primary/backup locator, and fails closed on any missing or
  content-mismatched member;
- restore re-admission reuses ``CertificationCatalogWriter.seal_partition``
  unchanged, so the restored catalog resolves the restored publication using
  the same contract a fresh S13 seal would use -- this module defines no new
  catalog semantics.

v1 targets one finalized (closed/valid/degraded) publication generation.  A
live successor revision (``revision > 1``) restored alone into a brand-new
catalog target will be refused by ``CertificationCatalogWriter`` itself,
which requires revision 1 to admit first in an empty topology -- restoring a
multi-revision history is a deliberate v1 non-goal (K09/relocation
territory), not an identity or proof defect; replay the ordered recovery
sets for every prior revision first if that is ever required.

Whether a given ``target_root``/``catalog_writer`` pair is genuinely on
storage independent of the tested primary boundary is a deployment-topology
fact this module cannot observe and never asserts -- see ADR-0039 Sec. 2.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile

from ..data.materializer import physical_artifact_sha256
from ..data.models import CoverageInterval
from ..data.publication import CertificationCatalogWriter, SealedCatalogPartition, SealedPartitionEvidence
from ..operations.recovery import RecoveryError, RecoverySetV1


@dataclass(frozen=True, slots=True)
class RecoveryBackupExport:
    """Evidence for one recovery set's exact copy at an explicit backup destination."""

    recovery_set: RecoverySetV1
    destination_root: Path
    dataset_manifest_path: Path
    partition_manifest_path: Path
    coverage_manifest_paths: tuple[Path, ...]
    artifact_path: Path


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


def capture_recovery_set(
    evidence: SealedPartitionEvidence,
    sealed: SealedCatalogPartition,
) -> RecoverySetV1:
    """Bind one already-sealed S13 publication's durable evidence to a ``RecoverySetV1``.

    ``sealed`` is CREDITed, already-authoritative S13 seal evidence (never
    re-derived); this function only hashes the exact durable files named by
    ``evidence`` and cross-checks them against ``sealed``, failing closed if
    they diverge -- a mixed/incompatible generation (a stale or foreign
    manifest/artifact) can never be captured as a recovery set.
    """

    if sealed.state != "closed":
        raise RecoveryError(
            "K08 v1 protects only a finalized (closed) publication generation; "
            f"got {sealed.state!r}"
        )
    dataset_manifest_sha256 = _sha256_file(evidence.dataset_manifest_path)
    partition_manifest_sha256 = _sha256_file(evidence.partition_manifest_path)
    if partition_manifest_sha256 != sealed.manifest_sha256:
        raise RecoveryError(
            "durable partition manifest does not match the authoritative sealed "
            "publication; refusing to bind a mixed/incompatible generation"
        )
    coverage_manifest_sha256 = tuple(
        _sha256_file(path) for path in evidence.coverage_manifest_paths
    )
    if not coverage_manifest_sha256:
        raise RecoveryError("a finalized publication requires at least one durable coverage manifest")
    physical_content_sha256 = physical_artifact_sha256(evidence.artifact_path)
    actual_size = Path(evidence.artifact_path).stat().st_size
    if physical_content_sha256 != sealed.content_sha256 or actual_size != sealed.byte_size:
        raise RecoveryError(
            "physical artifact does not match the authoritative sealed publication; "
            "refusing to bind a mixed/incompatible generation"
        )
    return RecoverySetV1(
        natural_identity=sealed.natural_identity,
        dataset_manifest_sha256=dataset_manifest_sha256,
        partition_manifest_sha256=partition_manifest_sha256,
        coverage_manifest_sha256=coverage_manifest_sha256,
        physical_content_sha256=physical_content_sha256,
        physical_size_bytes=sealed.byte_size,
        declared_coverage=CoverageInterval(sealed.ts_start, sealed.ts_end),
        partition_state=sealed.state,
        catalog_dataset_id=sealed.dataset_id,
        catalog_partition_id=sealed.partition_id,
    )


def export_recovery_set(
    recovery_set: RecoverySetV1,
    evidence: SealedPartitionEvidence,
    destination_root: str | Path,
) -> RecoveryBackupExport:
    """Copy one finalized recovery set's durable evidence to an explicit backup destination.

    Every source file is opened read-only and copied, never moved, truncated
    or rewritten: backup creation cannot mutate or destroy finalized primary
    state.  Only the exact files bound to ``recovery_set`` are copied -- no
    staging, cache or other volatile runtime state.
    """

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

    return RecoveryBackupExport(
        recovery_set, root, dataset_copy, partition_copy, coverage_copies, artifact_copy,
    )


def restore_recovery_set(
    export: RecoveryBackupExport,
    target_root: str | Path,
    *,
    forbidden_roots: Iterable[str | Path],
    catalog_writer: CertificationCatalogWriter,
    storage_root_id: str,
) -> RestoredRecoverySet:
    """Restore one exported recovery set into an empty isolated target.

    ``target_root`` must not already hold content and must not resolve to or
    inside any of ``forbidden_roots`` (the primary/backup locators this
    restore is proving independent of) -- restore never silently reuses a
    primary artifact locator.  ``catalog_writer`` re-admits the restored
    evidence under ``storage_root_id`` through the unchanged S13
    :class:`~quant_platform.data.publication.CertificationCatalogWriter`
    write contract (the same ``seal_partition``/``commit``/``rollback``
    boundary a fresh S13 seal uses), so restored catalog resolution uses
    exactly that existing admission semantics.  A missing or
    content-mismatched backup member fails the restore rather than silently
    substituting.
    """

    recovery_set = export.recovery_set
    target = Path(target_root)
    _refuse_alias(target, forbidden_roots)
    if target.exists():
        if not target.is_dir():
            raise RecoveryError("restore target must be a directory")
        if any(target.iterdir()):
            raise RecoveryError("restore target must be empty")
    else:
        target.mkdir(parents=True)

    dataset_bytes = _read_verified(export.dataset_manifest_path, recovery_set.dataset_manifest_sha256)
    partition_bytes = _read_verified(export.partition_manifest_path, recovery_set.partition_manifest_sha256)
    coverage_hashes = tuple(sorted(_sha256_file(path) for path in export.coverage_manifest_paths))
    if coverage_hashes != recovery_set.coverage_manifest_sha256:
        raise RecoveryError("backup coverage evidence is missing or corrupt")
    _verify_file(export.artifact_path, recovery_set.physical_content_sha256, recovery_set.physical_size_bytes)

    dataset_document = json.loads(dataset_bytes)
    partition_document = json.loads(partition_bytes)
    rel_root = dataset_document["rel_root"]
    rel_path = partition_document["rel_path"]

    dataset_target = _atomic_copy(export.dataset_manifest_path, target / "dataset.json")
    partition_target = _atomic_copy(export.partition_manifest_path, target / "partition.json")
    coverage_targets = tuple(
        _atomic_copy(source, target / f"coverage-{index:03d}.json")
        for index, source in enumerate(export.coverage_manifest_paths)
    )
    artifact_target = target / rel_root / rel_path
    artifact_target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_copy(export.artifact_path, artifact_target)

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
        catalog_writer.commit()
    except Exception:
        catalog_writer.rollback()
        raise
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

    return RestoredRecoverySet(
        recovery_set, sealed, dataset_target, partition_target, coverage_targets,
        artifact_target, rel_root, rel_path,
    )


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


__all__ = [
    "RecoveryBackupExport",
    "RestoredRecoverySet",
    "capture_recovery_set",
    "export_recovery_set",
    "restore_recovery_set",
]
