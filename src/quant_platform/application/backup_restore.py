"""K08 backup/restore v1: capture, export and restore of a ``RecoverySetV1``.

This is the composition seam over already-CREDITed capabilities: it captures
one identity-bound recovery set from an already-sealed S13 publication
(:mod:`quant_platform.data.publication`), persists it -- together with the
complete ordered predecessor-revision chain required to admit it, each with
its own bound K06 applicability evidence -- as one self-contained, durable,
independently reloadable backup at an explicit destination, and restores
that evidence into an empty isolated target/catalog by re-admitting it
through the existing
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
  revision plus its complete ordered predecessor chain, each carrying its own
  full K06 applicability document -- plus a durable recovery manifest
  recording every member's canonical payload, recovery identity and digests,
  so the backup alone (no separately supplied side parameters, no surviving
  in-memory object) is independently verifiable/restorable from disk after
  process loss, never a mutation of primary state, and never touches
  staging/cache/volatile files;
- restore requires an explicitly empty target and takes one ``restore_catalog``
  object that is *both* the S13 write contract and the authoritative read
  lookups, bound by the caller to one real connection/instance -- structurally
  ruling out a caller wiring the write path to one catalog while an
  independently-constructed inspector approves a different one.  It looks up
  where ``storage_root_id`` actually resolves (never a caller-asserted bare
  string), requires that catalog hold no existing admission for the
  recovered dataset/partition_key family, validates every restored path
  stays inside the target, fully re-validates each member's K06 evidence
  (full document digest and state, not merely an identity string) at this
  final consumption boundary, detects two admitted revisions claiming the
  same physical location with different content, and admits the complete
  predecessor chain plus the target revision as **one atomic transaction** --
  every returned admission's identity, digests, coverage and state are
  verified before the single ``commit()``, so a later failure can never
  leave partial, physically-false catalog state durable;
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
from ..data.publication import SealedCatalogPartition, SealedPartitionEvidence
from ..data.publication_catalog import CatalogPublicationWriter
from ..operations.protection import ProtectionAssessment, ProtectionState
from ..operations.recovery import (
    K06_NOT_APPLICABLE_IDENTITY_DOMAIN,
    K06NotApplicableAssertion,
    RecoveryError,
    RecoverySetV1,
    recovery_set_from_canonical_payload,
)


RECOVERY_MANIFEST_FILENAME = "recovery-manifest.json"
RECOVERY_MANIFEST_SCHEMA_VERSION = "recovery-manifest-v1"


class RestoreCatalog(Protocol):
    """Test-double protocol for the complete catalog capability restore needs.

    This combines the unchanged S13 write contract
    (``seal_partition``/``commit``/``rollback``, matching
    :class:`~quant_platform.data.publication.CertificationCatalogWriter`)
    with authoritative, read-only lookups against that *same* catalog.

    Production callers should use :class:`CatalogRestoreSession`, which
    derives the writer and inspection methods from exactly one catalog
    connection.  This protocol remains only so hermetic tests can exercise
    transaction behavior without a PostgreSQL server.
    """

    def seal_partition(self, *, dataset, partition, coverage_start, coverage_end, storage_root_id) -> SealedCatalogPartition:
        ...

    def commit(self) -> None:
        ...

    def rollback(self) -> None:
        ...

    def resolve_storage_root_abs_path(self, storage_root_id: str) -> str:
        """Return the real registered absolute path for this storage root."""
        ...

    def family_admission_count(self, dataset_identity: DatasetIdentity, partition_key: str) -> int:
        """Return how many rows already exist for this dataset/partition_key family.

        This establishes family-scoped absence only -- restore refuses when
        it is non-zero -- not that the catalog is empty in some broader
        sense; a catalog may legitimately hold unrelated families.
        """
        ...


class CatalogRestoreSession:
    """Concrete restore-catalog session bound to exactly one catalog connection.

    The S13 writer and K08 inspection queries are both constructed from the
    same ``connection`` object.  Production restore therefore cannot validate
    storage-root binding or family absence against one catalog while admitting
    rows into another.
    """

    def __init__(self, connection: Any) -> None:
        self.connection = connection
        self._writer = CatalogPublicationWriter(connection)

    def seal_partition(self, *, dataset, partition, coverage_start, coverage_end, storage_root_id) -> SealedCatalogPartition:
        return self._writer.seal_partition(
            dataset=dataset,
            partition=partition,
            coverage_start=coverage_start,
            coverage_end=coverage_end,
            storage_root_id=storage_root_id,
        )

    def commit(self) -> None:
        self._writer.commit()

    def rollback(self) -> None:
        self._writer.rollback()

    def resolve_storage_root_abs_path(self, storage_root_id: str) -> str:
        with self.connection.cursor() as cursor:
            cursor.execute(
                "SELECT abs_path FROM storage_roots WHERE storage_root_id = %s",
                (storage_root_id,),
            )
            row = cursor.fetchone()
        if row is None:
            raise RuntimeError(f"storage_root_id {storage_root_id!r} is not registered in the restore catalog")
        return row[0]

    def family_admission_count(self, dataset_identity: DatasetIdentity, partition_key: str) -> int:
        with self.connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT count(*) FROM catalog.partitions p
                  JOIN catalog.datasets d ON d.dataset_id = p.dataset_id
                 WHERE d.layer = %s AND d.kind = %s AND d.venue = %s
                   AND d.instrument = %s AND d.schema_id = %s
                   AND p.partition_key = %s
                """,
                (
                    dataset_identity.layer,
                    dataset_identity.dataset_kind,
                    dataset_identity.venue,
                    dataset_identity.instrument,
                    dataset_identity.record_schema_id,
                    partition_key,
                ),
            )
            return cursor.fetchone()[0]


@dataclass(frozen=True, slots=True)
class PredecessorEvidence:
    """One predecessor revision's own sealed evidence and K06 applicability document.

    Every predecessor is captured (via :func:`capture_recovery_set`) exactly
    like the target revision, including its own mandatory K06 applicability
    declaration; its corresponding document is supplied here so the export
    can persist it, exactly as the target's is -- a protected predecessor
    backup is never silently missing its required evidence.
    """

    recovery_set: RecoverySetV1
    evidence: SealedPartitionEvidence
    k06_document: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RecoveryBackupExport:
    """Evidence for one recovery set's durable, independently reloadable backup.

    ``k06_evidence_path`` points at whichever K06 applicability document is
    bound -- the full protection assessment or the full not-applicable
    assertion -- never both.

    ``predecessors`` -- when the recovered revision is not the first admitted
    one -- holds the complete ordered backup (including each one's own K06
    evidence) for every prior revision (1..N-1) of the same
    dataset/partition_key required to admit this one into an empty catalog,
    nested under the same ``destination_root``.  A revision-N export
    therefore needs no separately supplied or discovered side information to
    be restored.
    """

    recovery_set: RecoverySetV1
    destination_root: Path
    dataset_manifest_path: Path
    partition_manifest_path: Path
    coverage_manifest_paths: tuple[Path, ...]
    artifact_path: Path
    k06_evidence_path: Path | None = None
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
    k06_evidence_path: Path | None = None


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
    decision this module cannot independently derive from a source-semantics
    policy it does not own (mirroring K06's own
    ``SafetyRelevanceAssertion``/``ProtectionObligationEvidence`` discipline
    under ADR-0032: a claim that cannot be independently verified must be
    attributed to an explicit accountable party, never resolved by a
    self-referential local policy).  ``k06_protection``, when the backed-up
    publication has applicable K06-protected source evidence, must be an
    already-``PROTECTED`` :class:`~quant_platform.operations.protection.ProtectionAssessment`;
    its *complete* assessment document (state, per-artifact outcomes,
    verifier, instant) is digest-bound into the recovery set, not merely its
    unit identity, so that evidence is backed up and restored alongside the
    canonical publication with tamper-evidence on its full content.
    ``k06_not_applicable``, when no such evidence is required to reconstruct
    this accepted state, is an attributed assertion recording who determined
    that and why; its complete document is likewise digest-bound and, via
    :func:`export_recovery_set`'s ``k06_not_applicable_document``, persisted
    in full -- not merely as an opaque fingerprint.
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
    k06_not_applicable_document: Mapping[str, Any] | None = None,
    predecessors: Sequence[PredecessorEvidence] = (),
) -> RecoveryBackupExport:
    """Copy one finalized recovery set's durable evidence to an explicit backup destination.

    Every source file is opened read-only and copied, never moved, truncated
    or rewritten: backup creation cannot mutate or destroy finalized primary
    state.  Only the exact files bound to ``recovery_set`` (and, when
    supplied, each ``predecessors`` entry, including its own K06 evidence)
    are copied -- no staging, cache or other volatile runtime state.  A
    durable recovery manifest (:data:`RECOVERY_MANIFEST_FILENAME`) is written
    last, recording the canonical payload, recovery identity and member
    index for the target revision *and* every predecessor, so the export is
    independently reloadable and verifiable via
    :func:`load_recovery_backup_export` without a surviving in-memory
    ``RecoverySetV1`` or any separately supplied predecessor information.

    ``predecessors`` must be the ordered :class:`PredecessorEvidence` entries
    for revisions 1..N-1 of the same dataset/partition_key, where N is
    ``recovery_set.natural_identity.revision``; this is validated the same
    way restore validates it (fail closed on a wrong count or a
    non-contiguous/foreign family).

    Exactly one of ``k06_protection_document`` (a K06
    :class:`~quant_platform.operations.protection.ProtectionAssessment`'s
    ``stable_dict()``) or ``k06_not_applicable_document`` (a
    :class:`~quant_platform.operations.recovery.K06NotApplicableAssertion`'s
    ``canonical_payload()``) must be supplied, matching which branch
    ``recovery_set`` bound; its *complete* content must hash to the
    corresponding bound digest.
    """

    predecessor_sets = [item.recovery_set for item in predecessors]
    _validate_predecessor_chain(recovery_set.natural_identity, predecessor_sets)

    # Pure validation (no filesystem mutation) happens before anything is
    # created, so a failed export call -- including a rejected K06 document
    # -- never leaves a partially populated destination behind for a retry
    # to trip over.
    target_member = _prepare_export_member(
        recovery_set, evidence,
        k06_protection_document=k06_protection_document,
        k06_not_applicable_document=k06_not_applicable_document,
    )
    predecessor_members = tuple(
        _prepare_predecessor_export_member(member) for member in predecessors
    )

    root = Path(destination_root) / recovery_set.fingerprint_hex
    if root.exists() and any(root.iterdir()):
        raise RecoveryError("backup destination for this recovery identity is not empty")
    root.mkdir(parents=True, exist_ok=True)

    dataset_copy = _atomic_copy(Path(evidence.dataset_manifest_path), root / "dataset.json")
    partition_copy = _atomic_copy(Path(evidence.partition_manifest_path), root / "partition.json")
    coverage_copies = tuple(
        _atomic_copy(Path(path), root / f"coverage-{index:03d}.json")
        for index, path in enumerate(evidence.coverage_manifest_paths)
    )
    artifact_copy = _atomic_copy(Path(evidence.artifact_path), root / "artifact.parquet")

    k06_copy = _atomic_write_json(root / _K06_FILENAMES[target_member.k06_kind], target_member.k06_document)

    predecessor_exports = tuple(
        _export_predecessor_member(root, index + 1, member)
        for index, member in enumerate(predecessor_members)
    )

    _write_recovery_manifest(
        root, recovery_set,
        coverage_count=len(coverage_copies),
        k06_kind=target_member.k06_kind,
        predecessor_exports=predecessor_exports,
    )

    return RecoveryBackupExport(
        recovery_set, root, dataset_copy, partition_copy, coverage_copies, artifact_copy,
        k06_copy, predecessor_exports,
    )


@dataclass(frozen=True, slots=True)
class _PreparedExportMember:
    recovery_set: RecoverySetV1
    evidence: SealedPartitionEvidence
    k06_kind: str
    k06_document: Mapping[str, Any]


def _prepare_predecessor_export_member(member: PredecessorEvidence) -> _PreparedExportMember:
    recovery_set = member.recovery_set
    if recovery_set.k06_protection_identity is not None:
        return _prepare_export_member(
            recovery_set, member.evidence,
            k06_protection_document=member.k06_document,
            k06_not_applicable_document=None,
        )
    return _prepare_export_member(
        recovery_set, member.evidence,
        k06_protection_document=None,
        k06_not_applicable_document=member.k06_document,
    )


def _prepare_export_member(
    recovery_set: RecoverySetV1,
    evidence: SealedPartitionEvidence,
    *,
    k06_protection_document: Mapping[str, Any] | None,
    k06_not_applicable_document: Mapping[str, Any] | None,
) -> _PreparedExportMember:
    _verify_hash(evidence.dataset_manifest_path, recovery_set.dataset_manifest_sha256)
    _verify_hash(evidence.partition_manifest_path, recovery_set.partition_manifest_sha256)
    coverage_hashes = tuple(sorted(_sha256_file(path) for path in evidence.coverage_manifest_paths))
    if coverage_hashes != recovery_set.coverage_manifest_sha256:
        raise RecoveryError("backup coverage evidence does not match its bound recovery set identity")
    _verify_file(evidence.artifact_path, recovery_set.physical_content_sha256, recovery_set.physical_size_bytes)
    k06_kind, k06_document = _resolve_k06_export_document(
        recovery_set, k06_protection_document, k06_not_applicable_document,
    )
    return _PreparedExportMember(recovery_set, evidence, k06_kind, k06_document)


def _export_predecessor_member(root: Path, revision: int, member: _PreparedExportMember) -> RecoveryBackupExport:
    recovery_set = member.recovery_set
    evidence = member.evidence
    sub = root / "predecessors" / f"{revision:03d}"
    if sub.exists() and any(sub.iterdir()):
        raise RecoveryError("backup destination for this predecessor revision is not empty")
    sub.mkdir(parents=True, exist_ok=True)

    dataset_copy = _atomic_copy(Path(evidence.dataset_manifest_path), sub / "dataset.json")
    partition_copy = _atomic_copy(Path(evidence.partition_manifest_path), sub / "partition.json")
    coverage_copies = tuple(
        _atomic_copy(Path(path), sub / f"coverage-{index:03d}.json")
        for index, path in enumerate(evidence.coverage_manifest_paths)
    )
    artifact_copy = _atomic_copy(Path(evidence.artifact_path), sub / "artifact.parquet")

    k06_copy = _atomic_write_json(sub / _K06_FILENAMES[member.k06_kind], member.k06_document)

    return RecoveryBackupExport(
        recovery_set, sub, dataset_copy, partition_copy, coverage_copies, artifact_copy, k06_copy,
    )


_K06_FILENAMES = {
    "k06_protection": "k06-protection.json",
    "k06_not_applicable": "k06-not-applicable.json",
}


def _resolve_k06_export_document(
    recovery_set: RecoverySetV1,
    k06_protection_document: Mapping[str, Any] | None,
    k06_not_applicable_document: Mapping[str, Any] | None,
) -> tuple[str, Mapping[str, Any]]:
    """Validate and select the one K06 document matching ``recovery_set``'s bound branch."""

    if recovery_set.k06_protection_identity is not None:
        if k06_protection_document is None or k06_not_applicable_document is not None:
            raise RecoveryError(
                "k06_protection_document must be supplied (and k06_not_applicable_document "
                "omitted) when recovery_set binds a k06_protection_identity"
            )
        _verify_k06_protection_document(k06_protection_document, recovery_set)
        return "k06_protection", k06_protection_document
    if k06_not_applicable_document is None or k06_protection_document is not None:
        raise RecoveryError(
            "k06_not_applicable_document must be supplied (and k06_protection_document "
            "omitted) when recovery_set binds a k06_not_applicable_fingerprint"
        )
    _verify_k06_not_applicable_document(k06_not_applicable_document, recovery_set)
    return "k06_not_applicable", k06_not_applicable_document


def _verify_k06_protection_document(document: Mapping[str, Any], recovery_set: RecoverySetV1) -> None:
    if document.get("protection_identity") != recovery_set.k06_protection_identity:
        raise RecoveryError("k06_protection_document does not match the bound k06_protection_identity")
    if document.get("state") != "PROTECTED":
        raise RecoveryError("k06_protection_document must record a PROTECTED state")
    if _canonical_document_sha256(document) != recovery_set.k06_assessment_sha256:
        raise RecoveryError(
            "k06_protection_document content does not match the bound k06_assessment_sha256"
        )


def _verify_k06_not_applicable_document(document: Mapping[str, Any], recovery_set: RecoverySetV1) -> None:
    if document.get("identity_domain") != K06_NOT_APPLICABLE_IDENTITY_DOMAIN:
        raise RecoveryError("k06_not_applicable_document has an unsupported identity_domain")
    if _canonical_document_sha256(document) != recovery_set.k06_not_applicable_fingerprint:
        raise RecoveryError(
            "k06_not_applicable_document content does not match the bound "
            "k06_not_applicable_fingerprint"
        )


def load_recovery_backup_export(destination_root: str | Path) -> RecoveryBackupExport:
    """Reconstruct and re-verify a ``RecoveryBackupExport`` purely from disk.

    No in-memory ``RecoverySetV1`` (or separately discovered predecessor
    chain) needs to have survived process loss: every field -- the target
    revision and its complete ordered predecessor chain, each with its own
    K06 evidence -- is reloaded from the durable recovery manifest
    (:data:`RECOVERY_MANIFEST_FILENAME`) and every member's content
    (including the full K06 document, not merely an identity string) is
    re-verified against it before anything is trusted, exactly as
    :func:`export_recovery_set` bound it.
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
    dataset_manifest_path, partition_manifest_path, coverage_manifest_paths, artifact_path, k06_path = (
        _load_member_paths(root, entry, recovery_set, "predecessor entry")
    )
    return RecoveryBackupExport(
        recovery_set, root, dataset_manifest_path, partition_manifest_path,
        coverage_manifest_paths, artifact_path, k06_path,
    )


def _load_member_recovery_set(document: Mapping[str, Any], catalog_dataset_id: Any, catalog_partition_id: Any) -> RecoverySetV1:
    return recovery_set_from_canonical_payload(
        document.get("canonical_payload"),
        catalog_dataset_id=catalog_dataset_id,
        catalog_partition_id=catalog_partition_id,
    )


def _load_member_paths(
    root: Path, document: Mapping[str, Any], recovery_set: RecoverySetV1, label: str,
) -> tuple[Path, Path, tuple[Path, ...], Path, Path]:
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

    k06_path = _load_and_verify_k06_member(root, members, recovery_set, label)

    _verify_hash(dataset_manifest_path, recovery_set.dataset_manifest_sha256)
    _verify_hash(partition_manifest_path, recovery_set.partition_manifest_sha256)
    coverage_hashes = tuple(sorted(_sha256_file(path) for path in coverage_manifest_paths))
    if coverage_hashes != recovery_set.coverage_manifest_sha256:
        raise RecoveryError("backup coverage evidence is missing or corrupt")
    _verify_file(artifact_path, recovery_set.physical_content_sha256, recovery_set.physical_size_bytes)

    return dataset_manifest_path, partition_manifest_path, coverage_manifest_paths, artifact_path, k06_path


def _load_and_verify_k06_member(
    root: Path, members: Mapping[str, Any], recovery_set: RecoverySetV1, label: str,
) -> Path:
    """Load whichever K06 member matches ``recovery_set``'s bound branch and fully verify it."""

    protection_name = members.get("k06_protection")
    not_applicable_name = members.get("k06_not_applicable")
    if recovery_set.k06_protection_identity is not None:
        if protection_name is None or not_applicable_name is not None:
            raise RecoveryError(f"{label} k06 evidence presence does not match its bound identity")
        path = _member_path(root, protection_name, "k06_protection")
        _verify_k06_protection_document(_load_json(path), recovery_set)
        return path
    if not_applicable_name is None or protection_name is not None:
        raise RecoveryError(f"{label} k06 evidence presence does not match its bound identity")
    path = _member_path(root, not_applicable_name, "k06_not_applicable")
    _verify_k06_not_applicable_document(_load_json(path), recovery_set)
    return path


def restore_recovery_set(
    export: RecoveryBackupExport,
    target_root: str | Path,
    *,
    forbidden_roots: Iterable[str | Path],
    restore_catalog: RestoreCatalog,
    storage_root_id: str,
) -> RestoredRecoverySet:
    """Restore one exported recovery set into an empty isolated target and catalog.

    ``target_root`` must not already hold content and must not resolve to or
    inside any of ``forbidden_roots`` (the primary/backup locators this
    restore is proving independent of).  ``restore_catalog`` is the single
    object the caller has bound to one real connection/instance for both
    admission and authoritative lookups (see :class:`RestoreCatalog`); it is
    used to look up where ``storage_root_id`` actually resolves (never a
    caller-asserted bare path) and to require that catalog hold no existing
    admission for the recovered dataset/partition_key family before restore
    begins; either check failing refuses the restore rather than silently
    permit a primary-resolving or non-isolated admission.

    ``export.predecessors`` -- the complete ordered revision-1..N-1 chain
    bound in the backup itself -- is re-validated for contiguity and, for
    every member (each predecessor, then the target revision), its physical
    artifact and manifest evidence are restored into the target with a
    collision check (two admitted revisions must never claim the same
    physical location with different content), and its K06 applicability
    document is *fully* re-verified here -- the complete digest and (for
    protected evidence) its ``PROTECTED`` state, not merely an identity
    string -- at this final consumption boundary, so evidence modified after
    export/load and passed straight to restore is still caught.  All
    admissions are then sealed through the unchanged ``restore_catalog``
    contract inside **one** transaction: every returned admission's
    identity, digests, coverage and state are verified before a single
    ``commit()`` -- any mismatch anywhere in the chain rolls back the entire
    attempt, never leaving partial, physically-false catalog state durable.
    """

    recovery_set = export.recovery_set
    target = Path(target_root)
    _refuse_alias(target, forbidden_roots)
    resolved_target = _resolve_maybe(target)

    storage_root_abs_path = restore_catalog.resolve_storage_root_abs_path(storage_root_id)
    if _resolve_maybe(Path(storage_root_abs_path)) != resolved_target:
        raise RecoveryError(
            "storage_root_id does not authoritatively resolve to target_root in the "
            "restore catalog; the restored catalog could resolve reads to a location "
            "other than the isolated restore target"
        )

    family = recovery_set.natural_identity
    if restore_catalog.family_admission_count(family.dataset_identity, family.partition_key):
        raise RecoveryError(
            "the restore catalog already contains admitted rows for this "
            "dataset/partition_key family; restore requires no existing admission for it"
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
            sealed = restore_catalog.seal_partition(
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
        restore_catalog.commit()
    except Exception:
        restore_catalog.rollback()
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

    # Full re-verification at restore's final consumption boundary: a
    # protection_identity/fingerprint match alone is not enough -- the
    # complete document (state, outcomes, verifier, instant, or the
    # not-applicable assertion's authority/rationale) must still hash to the
    # bound digest, catching evidence modified after export/load and passed
    # straight into restore.
    if member.k06_evidence_path is None:
        raise RecoveryError("backup member is missing its required K06 applicability evidence")
    k06_document = _load_json(member.k06_evidence_path)
    if recovery_set.k06_protection_identity is not None:
        _verify_k06_protection_document(k06_document, recovery_set)
    else:
        _verify_k06_not_applicable_document(k06_document, recovery_set)

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
    k06_filename = "k06-protection.json" if recovery_set.k06_protection_identity is not None else "k06-not-applicable.json"
    k06_target = _atomic_copy(member.k06_evidence_path, manifest_dir / k06_filename)

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
    k06_kind: str,
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
            "k06_protection": "k06-protection.json" if k06_kind == "k06_protection" else None,
            "k06_not_applicable": "k06-not-applicable.json" if k06_kind == "k06_not_applicable" else None,
        },
        "predecessors": [
            _predecessor_manifest_entry(index + 1, member)
            for index, member in enumerate(predecessor_exports)
        ],
    }
    return _atomic_write_json(root / RECOVERY_MANIFEST_FILENAME, document)


def _predecessor_manifest_entry(revision: int, member: RecoveryBackupExport) -> dict[str, Any]:
    prefix = PurePosixPath("predecessors", f"{revision:03d}")
    is_protected = member.recovery_set.k06_protection_identity is not None
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
            "k06_protection": str(prefix / "k06-protection.json") if is_protected else None,
            "k06_not_applicable": str(prefix / "k06-not-applicable.json") if not is_protected else None,
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
    "CatalogRestoreSession",
    "PredecessorEvidence",
    "RecoveryBackupExport",
    "RestoreCatalog",
    "RestoredRecoverySet",
    "capture_recovery_set",
    "export_recovery_set",
    "load_recovery_backup_export",
    "restore_recovery_set",
]
