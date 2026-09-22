"""K08 backup/restore v1: capture, export and restore of a ``RecoverySetV1``.

This is the composition seam over already-CREDITed capabilities: it captures
one identity-bound recovery set from an already-sealed S13 publication
(:mod:`quant_platform.data.publication`), persists it -- together with the
complete ordered predecessor-revision chain required to admit it -- as one
self-contained, durable, independently reloadable backup at an explicit
destination, and restores that evidence into an empty isolated target/catalog
by re-admitting it through the existing
:class:`~quant_platform.data.publication.CertificationCatalogWriter` write
contract -- never a parallel catalog-write path, never a generalized
backup-provider abstraction.

Only the smallest seam needed for ADR-0039 is implemented here:

- capture cross-checks every durable member (dataset identity, partition
  manifest, folded declared coverage, physical artifact, and the complete K06
  applicability decision -- protected evidence or an attributed
  not-applicable assertion, never a silent omission) against the
  authoritative sealed publication before binding a recovery identity, so a
  mixed/incompatible or foreign generation is refused rather than silently
  captured;
- backup creation is a pure copy of the exact bound evidence -- target
  revision plus its complete ordered predecessor chain -- plus a durable
  recovery manifest recording every member's canonical payload, recovery
  identity and digests, so the backup alone (no separately supplied side
  parameters, no surviving in-memory object) is independently
  verifiable/restorable from disk after process loss, never a mutation of
  primary state, and never touches staging/cache/volatile files;
- restore requires an explicitly empty target whose registered storage-root
  locator is looked up *authoritatively* from the same catalog
  ``catalog_writer`` admits into (never trusted as a caller-supplied bare
  string) and resolves to exactly that target, requires that catalog to hold
  no existing admission for the recovered dataset/partition_key family,
  validates every restored path stays inside the target, detects two
  admitted revisions claiming the same physical location with different
  content, and admits the complete predecessor chain plus the target
  revision as **one atomic transaction** -- every returned admission's
  identity, digests, coverage and state are verified before the single
  ``commit()``, so a later failure can never leave partial, physically-false
  catalog state durable;
- restore re-admission reuses ``CertificationCatalogWriter.seal_partition``
  unchanged for every revision, so the restored catalog resolves the
  restored publication using the same contract a fresh S13 seal would use --
  this module defines no new catalog semantics, just drives the existing one
  in order.

Whether a given ``target_root``/catalog instance is genuinely on storage
independent of the tested primary boundary is a deployment-topology fact
this module cannot observe and never asserts -- see ADR-0039 Sec. 2.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any, Protocol

from ..data.coverage import reconstruct_catalog_coverage
from ..data.materializer import physical_artifact_sha256
from ..data.models import CoverageInterval, DatasetIdentity
from ..data.publication import CertificationCatalogWriter, SealedCatalogPartition, SealedPartitionEvidence
from ..operations.protection import ProtectionAssessment, ProtectionState
from ..operations.recovery import (
    K06NotApplicableAssertion,
    RecoveryError,
    RecoverySetV1,
    recovery_set_from_canonical_payload,
)


RECOVERY_MANIFEST_FILENAME = "recovery-manifest.json"
RECOVERY_MANIFEST_SCHEMA_VERSION = "recovery-manifest-v1"


class RestoreCatalogInspector(Protocol):
    """Authoritative, read-only lookups against the exact catalog ``catalog_writer`` admits into.

    K08 never opens its own catalog connection or crawls a catalog schema;
    the caller binds these lookups to the same connection/catalog instance
    used by ``catalog_writer`` so restore can verify claims about it rather
    than trust a caller-supplied locator string or an unverified "it's
    empty" assumption.
    """

    def resolve_storage_root_abs_path(self, storage_root_id: str) -> str:
        """Return the real registered absolute path for this storage root."""
        ...

    def family_admission_count(self, dataset_identity: DatasetIdentity, partition_key: str) -> int:
        """Return how many catalog rows already exist for this dataset/partition_key family."""
        ...


@dataclass(frozen=True, slots=True)
class RecoveryBackupExport:
    """Evidence for one recovery set's durable, independently reloadable backup.

    ``predecessors`` -- when the recovered revision is not the first admitted
    one -- holds the complete ordered backup for every prior revision
    (1..N-1) of the same dataset/partition_key required to admit this one
    into an empty catalog, nested under the same ``destination_root``.  A
    revision-N export therefore needs no separately supplied or discovered
    side information to be restored.
    """

    recovery_set: RecoverySetV1
    destination_root: Path
    dataset_manifest_path: Path
    partition_manifest_path: Path
    coverage_manifest_paths: tuple[Path, ...]
    artifact_path: Path
    k06_protection_document_path: Path | None = None
    predecessors: tuple["RecoveryBackupExport", ...] = ()


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
    k06_protection: ProtectionAssessment | None = None,
    k06_not_applicable: K06NotApplicableAssertion | None = None,
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

    Exactly one of ``k06_protection`` or ``k06_not_applicable`` is required
    (never both, never neither): K06 applicability is a mandatory, attributed
    decision.  ``k06_protection``, when the backed-up publication has
    applicable K06-protected source evidence, must be an already-``PROTECTED``
    :class:`~quant_platform.operations.protection.ProtectionAssessment`; its
    *complete* assessment document (state, per-artifact outcomes, verifier,
    instant) is digest-bound into the recovery set, not merely its unit
    identity, so that evidence is backed up and restored alongside the
    canonical publication with tamper-evidence on its full content.
    ``k06_not_applicable``, when no such evidence is required to reconstruct
    this accepted state, is an attributed assertion recording who determined
    that and why.
    """

    if sealed.state != "closed":
        raise RecoveryError(
            "K08 v1 requires already-sealed (closed) durable evidence as its capture "
            f"input; got {sealed.state!r}"
        )
    if (k06_protection is None) == (k06_not_applicable is None):
        raise RecoveryError(
            "capture requires exactly one of k06_protection or k06_not_applicable: K06 "
            "applicability must be an explicit, attributed decision, never a silent omission"
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
    k06_assessment_sha256 = None
    k06_not_applicable_fingerprint = None
    if k06_protection is not None:
        if not isinstance(k06_protection, ProtectionAssessment):
            raise RecoveryError("k06_protection must be a ProtectionAssessment")
        if k06_protection.state != ProtectionState.PROTECTED:
            raise RecoveryError(
                "k06_protection evidence must be PROTECTED to bind required source "
                f"evidence into a recovery set; got {k06_protection.state!r}"
            )
        k06_protection_identity = k06_protection.protection_identity
        k06_assessment_sha256 = _canonical_document_sha256(k06_protection.stable_dict())
    else:
        if not isinstance(k06_not_applicable, K06NotApplicableAssertion):
            raise RecoveryError("k06_not_applicable must be a K06NotApplicableAssertion")
        k06_not_applicable_fingerprint = k06_not_applicable.fingerprint

    return RecoverySetV1(
        natural_identity=sealed.natural_identity,
        dataset_manifest_sha256=dataset_manifest_sha256,
        partition_manifest_sha256=partition_manifest_sha256,
        coverage_manifest_sha256=coverage_manifest_sha256,
        physical_content_sha256=physical_content_sha256,
        physical_size_bytes=sealed.byte_size,
        declared_coverage=CoverageInterval(sealed.ts_start, sealed.ts_end),
        catalog_dataset_id=sealed.dataset_id,
        catalog_partition_id=sealed.partition_id,
        k06_protection_identity=k06_protection_identity,
        k06_assessment_sha256=k06_assessment_sha256,
        k06_not_applicable_fingerprint=k06_not_applicable_fingerprint,
    )


def export_recovery_set(
    recovery_set: RecoverySetV1,
    evidence: SealedPartitionEvidence,
    destination_root: str | Path,
    *,
    k06_protection_document: Mapping[str, Any] | None = None,
    predecessors: Sequence[tuple[RecoverySetV1, SealedPartitionEvidence]] = (),
) -> RecoveryBackupExport:
    """Copy one finalized recovery set's durable evidence to an explicit backup destination.

    Every source file is opened read-only and copied, never moved, truncated
    or rewritten: backup creation cannot mutate or destroy finalized primary
    state.  Only the exact files bound to ``recovery_set`` (and, when
    supplied, each ``predecessors`` entry) are copied -- no staging, cache or
    other volatile runtime state.  A durable recovery manifest
    (:data:`RECOVERY_MANIFEST_FILENAME`) is written last, recording the
    canonical payload, recovery identity and member index for the target
    revision *and* every predecessor, so the export is independently
    reloadable and verifiable via :func:`load_recovery_backup_export` without
    a surviving in-memory ``RecoverySetV1`` or any separately supplied
    predecessor information.

    ``predecessors`` must be the ordered ``(recovery_set, evidence)`` pairs
    for revisions 1..N-1 of the same dataset/partition_key, where N is
    ``recovery_set.natural_identity.revision``; this is validated the same
    way restore validates it (fail closed on a wrong count or a
    non-contiguous/foreign family).

    ``k06_protection_document`` (a K06
    :class:`~quant_platform.operations.protection.ProtectionAssessment`'s
    ``stable_dict()``) must be supplied exactly when ``recovery_set`` binds a
    ``k06_protection_identity``, and its *complete* content must hash to
    ``recovery_set.k06_assessment_sha256``.
    """

    predecessor_sets = [item[0] for item in predecessors]
    _validate_predecessor_chain(recovery_set.natural_identity, predecessor_sets)

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
        if _canonical_document_sha256(k06_protection_document) != recovery_set.k06_assessment_sha256:
            raise RecoveryError(
                "k06_protection_document content does not match the bound k06_assessment_sha256"
            )

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

    predecessor_exports = tuple(
        _export_predecessor_member(root, index + 1, p_recovery_set, p_evidence)
        for index, (p_recovery_set, p_evidence) in enumerate(predecessors)
    )

    _write_recovery_manifest(
        root, recovery_set,
        coverage_count=len(coverage_copies),
        has_k06_document=k06_copy is not None,
        predecessor_exports=predecessor_exports,
    )

    return RecoveryBackupExport(
        recovery_set, root, dataset_copy, partition_copy, coverage_copies, artifact_copy,
        k06_copy, predecessor_exports,
    )


def _export_predecessor_member(
    root: Path, revision: int, recovery_set: RecoverySetV1, evidence: SealedPartitionEvidence,
) -> RecoveryBackupExport:
    sub = root / "predecessors" / f"{revision:03d}"
    if sub.exists() and any(sub.iterdir()):
        raise RecoveryError("backup destination for this predecessor revision is not empty")
    sub.mkdir(parents=True, exist_ok=True)

    _verify_hash(evidence.dataset_manifest_path, recovery_set.dataset_manifest_sha256)
    _verify_hash(evidence.partition_manifest_path, recovery_set.partition_manifest_sha256)
    _verify_file(evidence.artifact_path, recovery_set.physical_content_sha256, recovery_set.physical_size_bytes)

    dataset_copy = _atomic_copy(Path(evidence.dataset_manifest_path), sub / "dataset.json")
    partition_copy = _atomic_copy(Path(evidence.partition_manifest_path), sub / "partition.json")
    coverage_copies = tuple(
        _atomic_copy(Path(path), sub / f"coverage-{index:03d}.json")
        for index, path in enumerate(evidence.coverage_manifest_paths)
    )
    copied_hashes = tuple(sorted(_sha256_file(path) for path in coverage_copies))
    if copied_hashes != recovery_set.coverage_manifest_sha256:
        raise RecoveryError(
            "exported predecessor coverage evidence does not match its bound recovery set identity"
        )
    artifact_copy = _atomic_copy(Path(evidence.artifact_path), sub / "artifact.parquet")
    return RecoveryBackupExport(recovery_set, sub, dataset_copy, partition_copy, coverage_copies, artifact_copy)


def load_recovery_backup_export(destination_root: str | Path) -> RecoveryBackupExport:
    """Reconstruct and re-verify a ``RecoveryBackupExport`` purely from disk.

    No in-memory ``RecoverySetV1`` (or separately discovered predecessor
    chain) needs to have survived process loss: every field -- the target
    revision and its complete ordered predecessor chain -- is reloaded from
    the durable recovery manifest (:data:`RECOVERY_MANIFEST_FILENAME`) and
    every member's content is re-hashed against it before anything is
    trusted, exactly as :func:`export_recovery_set` bound it.
    """

    root = Path(destination_root)
    manifest_path = root / RECOVERY_MANIFEST_FILENAME
    document = _load_json(manifest_path)
    if document.get("schema_version") != RECOVERY_MANIFEST_SCHEMA_VERSION:
        raise RecoveryError(f"unsupported recovery manifest schema_version at {manifest_path}")

    recovery_set = _load_member_recovery_set(
        document, document.get("catalog_dataset_id"), document.get("catalog_partition_id"),
    )
    if recovery_set.recovery_identity != document.get("recovery_identity"):
        raise RecoveryError("recovery manifest recovery_identity does not match its own canonical payload")

    dataset_manifest_path, partition_manifest_path, coverage_manifest_paths, artifact_path, k06_path = (
        _load_member_paths(root, document, recovery_set, "recovery manifest")
    )

    predecessor_entries = document.get("predecessors") or []
    if not isinstance(predecessor_entries, list):
        raise RecoveryError("recovery manifest predecessors must be a list")
    predecessors = tuple(
        _load_predecessor_export(root, entry, index + 1)
        for index, entry in enumerate(predecessor_entries)
    )
    _validate_predecessor_chain(recovery_set.natural_identity, [item.recovery_set for item in predecessors])

    return RecoveryBackupExport(
        recovery_set, root, dataset_manifest_path, partition_manifest_path,
        coverage_manifest_paths, artifact_path, k06_path, predecessors,
    )


def _load_predecessor_export(root: Path, entry: Any, expected_revision: int) -> RecoveryBackupExport:
    if not isinstance(entry, Mapping):
        raise RecoveryError("recovery manifest predecessor entry is malformed")
    recovery_set = _load_member_recovery_set(
        entry, entry.get("catalog_dataset_id"), entry.get("catalog_partition_id"),
    )
    if recovery_set.recovery_identity != entry.get("recovery_identity"):
        raise RecoveryError("predecessor recovery_identity does not match its own canonical payload")
    if recovery_set.natural_identity.revision != expected_revision:
        raise RecoveryError(
            f"predecessor chain is not contiguous: expected revision {expected_revision}, "
            f"got {recovery_set.natural_identity.revision}"
        )
    dataset_manifest_path, partition_manifest_path, coverage_manifest_paths, artifact_path, _k06 = (
        _load_member_paths(root, entry, recovery_set, "predecessor entry")
    )
    return RecoveryBackupExport(
        recovery_set, root, dataset_manifest_path, partition_manifest_path,
        coverage_manifest_paths, artifact_path,
    )


def _load_member_recovery_set(document: Mapping[str, Any], catalog_dataset_id: Any, catalog_partition_id: Any) -> RecoverySetV1:
    return recovery_set_from_canonical_payload(
        document.get("canonical_payload"),
        catalog_dataset_id=catalog_dataset_id,
        catalog_partition_id=catalog_partition_id,
    )


def _load_member_paths(
    root: Path, document: Mapping[str, Any], recovery_set: RecoverySetV1, label: str,
) -> tuple[Path, Path, tuple[Path, ...], Path, Path | None]:
    members = document.get("members")
    if not isinstance(members, Mapping):
        raise RecoveryError(f"{label} is missing its members index")
    dataset_manifest_path = _member_path(root, members.get("dataset_manifest"), "dataset_manifest")
    partition_manifest_path = _member_path(root, members.get("partition_manifest"), "partition_manifest")
    coverage_names = members.get("coverage_manifests")
    if not isinstance(coverage_names, list) or not coverage_names:
        raise RecoveryError(f"{label} coverage_manifests must be a non-empty list")
    coverage_manifest_paths = tuple(
        _member_path(root, name, "coverage_manifests") for name in coverage_names
    )
    artifact_path = _member_path(root, members.get("artifact"), "artifact")
    k06_name = members.get("k06_protection")
    k06_protection_document_path = _member_path(root, k06_name, "k06_protection") if k06_name is not None else None
    if (recovery_set.k06_protection_identity is None) != (k06_protection_document_path is None):
        raise RecoveryError(f"{label} k06 evidence presence does not match its bound identity")

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
        if _canonical_document_sha256(k06_document) != recovery_set.k06_assessment_sha256:
            raise RecoveryError("backup k06 protection evidence content does not match its bound digest")

    return dataset_manifest_path, partition_manifest_path, coverage_manifest_paths, artifact_path, k06_protection_document_path


def restore_recovery_set(
    export: RecoveryBackupExport,
    target_root: str | Path,
    *,
    forbidden_roots: Iterable[str | Path],
    catalog_writer: CertificationCatalogWriter,
    catalog_inspector: RestoreCatalogInspector,
    storage_root_id: str,
) -> RestoredRecoverySet:
    """Restore one exported recovery set into an empty isolated target and catalog.

    ``target_root`` must not already hold content and must not resolve to or
    inside any of ``forbidden_roots`` (the primary/backup locators this
    restore is proving independent of).  ``catalog_inspector`` -- bound by
    the caller to the *same* catalog/connection instance as ``catalog_writer``
    -- is used to authoritatively look up where ``storage_root_id`` actually
    resolves (never a caller-asserted bare path) and to require that catalog
    hold no existing admission for the recovered dataset/partition_key family
    before restore begins; either check failing refuses the restore rather
    than silently permit a primary-resolving or non-isolated admission.

    ``export.predecessors`` -- the complete ordered revision-1..N-1 chain
    bound in the backup itself -- is re-validated for contiguity and, for
    every member (each predecessor, then the target revision), its physical
    artifact and manifest evidence are restored into the target with a
    collision check (two admitted revisions must never claim the same
    physical location with different content).  All admissions are then
    sealed through the unchanged ``catalog_writer`` contract inside **one**
    transaction: every returned admission's identity, digests, coverage and
    state are verified before a single ``commit()`` -- any mismatch anywhere
    in the chain rolls back the entire attempt, never leaving partial,
    physically-false catalog state durable.
    """

    recovery_set = export.recovery_set
    target = Path(target_root)
    _refuse_alias(target, forbidden_roots)
    resolved_target = _resolve_maybe(target)

    storage_root_abs_path = catalog_inspector.resolve_storage_root_abs_path(storage_root_id)
    if _resolve_maybe(Path(storage_root_abs_path)) != resolved_target:
        raise RecoveryError(
            "storage_root_id does not authoritatively resolve to target_root in the "
            "restore catalog; the restored catalog could resolve reads to a location "
            "other than the isolated restore target"
        )

    family = recovery_set.natural_identity
    if catalog_inspector.family_admission_count(family.dataset_identity, family.partition_key):
        raise RecoveryError(
            "the restore catalog already contains admitted rows for this "
            "dataset/partition_key family; restore requires an empty isolated catalog"
        )

    _validate_predecessor_chain(family, [item.recovery_set for item in export.predecessors])

    if target.exists():
        if not target.is_dir():
            raise RecoveryError("restore target must be a directory")
        if any(target.iterdir()):
            raise RecoveryError("restore target must be empty")
    else:
        target.mkdir(parents=True)

    placed_artifacts: dict[Path, str] = {}
    staged: list[_StagedAdmission] = []
    for predecessor in export.predecessors:
        revision = predecessor.recovery_set.natural_identity.revision
        manifest_dir = target / "_recovery" / "predecessors" / f"{revision:03d}"
        staged.append(_stage_member(target, resolved_target, predecessor, manifest_dir, placed_artifacts))
    staged.append(_stage_member(target, resolved_target, export, target, placed_artifacts))

    try:
        sealed: SealedCatalogPartition | None = None
        for admission in staged:
            sealed = catalog_writer.seal_partition(
                dataset=admission.dataset_document,
                partition=admission.partition_document,
                coverage_start=admission.recovery_set.declared_coverage.start,
                coverage_end=admission.recovery_set.declared_coverage.end,
                storage_root_id=storage_root_id,
            )
            if sealed.natural_identity != admission.recovery_set.natural_identity:
                raise RecoveryError("restored catalog admission produced a different publication identity")
            if sealed.state != "closed":
                raise RecoveryError("restored catalog admission did not return a closed partition")
            if (
                sealed.content_sha256 != admission.recovery_set.physical_content_sha256
                or sealed.manifest_sha256 != admission.recovery_set.partition_manifest_sha256
            ):
                raise RecoveryError("restored catalog admission does not match the bound recovery set identity")
            if (
                sealed.ts_start != admission.recovery_set.declared_coverage.start
                or sealed.ts_end != admission.recovery_set.declared_coverage.end
            ):
                raise RecoveryError("restored catalog admission does not reproduce declared coverage")
        catalog_writer.commit()
    except Exception:
        catalog_writer.rollback()
        raise

    target_admission = staged[-1]
    return RestoredRecoverySet(
        recovery_set, sealed,
        target_admission.dataset_target, target_admission.partition_target, target_admission.coverage_targets,
        target_admission.artifact_target, target_admission.rel_root, target_admission.rel_path,
        target_admission.k06_target,
    )


@dataclass(frozen=True, slots=True)
class _StagedAdmission:
    recovery_set: RecoverySetV1
    dataset_document: dict[str, Any]
    partition_document: dict[str, Any]
    dataset_target: Path
    partition_target: Path
    coverage_targets: tuple[Path, ...]
    artifact_target: Path
    rel_root: str
    rel_path: str
    k06_target: Path | None


def _stage_member(
    target: Path,
    resolved_target: Path,
    member: RecoveryBackupExport,
    manifest_dir: Path,
    placed_artifacts: dict[Path, str],
) -> _StagedAdmission:
    recovery_set = member.recovery_set
    dataset_bytes = _read_verified(member.dataset_manifest_path, recovery_set.dataset_manifest_sha256)
    partition_bytes = _read_verified(member.partition_manifest_path, recovery_set.partition_manifest_sha256)
    coverage_hashes = tuple(sorted(_sha256_file(path) for path in member.coverage_manifest_paths))
    if coverage_hashes != recovery_set.coverage_manifest_sha256:
        raise RecoveryError("backup coverage evidence is missing or corrupt")
    _verify_file(member.artifact_path, recovery_set.physical_content_sha256, recovery_set.physical_size_bytes)

    dataset_document = json.loads(dataset_bytes)
    partition_document = json.loads(partition_bytes)
    rel_root = _validate_safe_relative(dataset_document.get("rel_root"), "dataset manifest rel_root")
    rel_path = _validate_safe_relative(partition_document.get("rel_path"), "partition manifest rel_path")
    artifact_target = target / rel_root / rel_path
    if not _is_relative_to(_resolve_maybe(artifact_target), resolved_target):
        raise RecoveryError("restored artifact destination resolves outside the isolated restore target")

    previous_hash = placed_artifacts.get(artifact_target)
    if previous_hash is not None and previous_hash != recovery_set.physical_content_sha256:
        raise RecoveryError(
            "two admitted revisions in this restore claim the same physical artifact "
            f"location with different content: {artifact_target}"
        )
    placed_artifacts[artifact_target] = recovery_set.physical_content_sha256
    if not artifact_target.exists():
        artifact_target.parent.mkdir(parents=True, exist_ok=True)
        _atomic_copy(member.artifact_path, artifact_target)

    dataset_target = _atomic_copy(member.dataset_manifest_path, manifest_dir / "dataset.json")
    partition_target = _atomic_copy(member.partition_manifest_path, manifest_dir / "partition.json")
    coverage_targets = tuple(
        _atomic_copy(source, manifest_dir / f"coverage-{index:03d}.json")
        for index, source in enumerate(member.coverage_manifest_paths)
    )
    k06_target = None
    if member.k06_protection_document_path is not None:
        k06_document = _load_json(member.k06_protection_document_path)
        if k06_document.get("protection_identity") != recovery_set.k06_protection_identity:
            raise RecoveryError("backup k06 protection evidence does not match the bound recovery set identity")
        k06_target = _atomic_copy(member.k06_protection_document_path, manifest_dir / "k06-protection.json")

    dataset_document = dict(dataset_document, _manifest_sha256=recovery_set.dataset_manifest_sha256)
    partition_document = dict(partition_document, _manifest_sha256=recovery_set.partition_manifest_sha256)

    return _StagedAdmission(
        recovery_set, dataset_document, partition_document,
        dataset_target, partition_target, coverage_targets, artifact_target,
        rel_root, rel_path, k06_target,
    )


def _validate_predecessor_chain(family: Any, predecessors: Sequence[RecoverySetV1]) -> None:
    expected_revision = len(predecessors) + 1
    if family.revision != expected_revision:
        raise RecoveryError(
            f"restoring/exporting revision {family.revision} requires exactly "
            f"{family.revision - 1} ordered predecessor revisions (got {len(predecessors)}); "
            "the unmodified catalog admission contract requires every prior revision to "
            "admit first in an empty topology"
        )
    for index, predecessor in enumerate(predecessors, start=1):
        identity = predecessor.natural_identity
        if (
            identity.dataset_identity != family.dataset_identity
            or identity.partition_key != family.partition_key
            or identity.revision != index
        ):
            raise RecoveryError(
                "predecessor chain must be the contiguous ordered revision history for "
                "the same dataset/partition_key, starting at revision 1"
            )


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
    predecessor_exports: tuple[RecoveryBackupExport, ...] = (),
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
        "predecessors": [
            _predecessor_manifest_entry(index + 1, member)
            for index, member in enumerate(predecessor_exports)
        ],
    }
    return _atomic_write_json(root / RECOVERY_MANIFEST_FILENAME, document)


def _predecessor_manifest_entry(revision: int, member: RecoveryBackupExport) -> dict[str, Any]:
    prefix = PurePosixPath("predecessors", f"{revision:03d}")
    return {
        "recovery_identity": member.recovery_set.recovery_identity,
        "canonical_payload": member.recovery_set.canonical_payload(),
        "catalog_dataset_id": member.recovery_set.catalog_dataset_id,
        "catalog_partition_id": member.recovery_set.catalog_partition_id,
        "members": {
            "dataset_manifest": str(prefix / "dataset.json"),
            "partition_manifest": str(prefix / "partition.json"),
            "coverage_manifests": [
                str(prefix / f"coverage-{index:03d}.json")
                for index in range(len(member.coverage_manifest_paths))
            ],
            "artifact": str(prefix / "artifact.parquet"),
        },
    }


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


def _canonical_document_sha256(document: Mapping[str, Any]) -> str:
    payload = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


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
    "RestoreCatalogInspector",
    "RestoredRecoverySet",
    "capture_recovery_set",
    "export_recovery_set",
    "load_recovery_backup_export",
    "restore_recovery_set",
]
