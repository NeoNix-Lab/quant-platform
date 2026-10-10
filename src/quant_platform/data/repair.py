"""A10 backfill/repair v1: evidence-driven reconciliation over explicit defects.

This module owns only the bounded runtime foundation frozen by the A10
mandate: deterministic repair-intent identity, immutable isolated candidate
attempts, staging isolation sufficient to stop concurrent attempts clobbering
each other, and one dedicated atomic compare-and-cutover transaction that
reuses the existing A16 (:mod:`quality_lifecycle`) and A09/S14
(:mod:`publication_eligibility_catalog`) transaction-scoped seams.

It deliberately does not own acquisition/download, scheduling, A11 live-cursor
semantics, or deletion.  It never imports ``quant_platform.access``: B04
gap/coverage evidence is supplied by the caller as already-frozen
``CoverageInterval`` values from :mod:`quant_platform.data.models`.

Replacement repair cannot reuse the ordinary S13 admission path
(:class:`~quant_platform.data.publication_catalog.CatalogPublicationWriter`)
because that path supersedes the predecessor and commits before quality or
publication evidence is verified.  A10 instead supersedes the predecessor and
admits the candidate as ``closed`` inside its own transaction, then calls the
A16 and S14 transaction-scoped seams against that same connection, and only
commits if both accept the exact final revision -- so a failed candidate never
leaves a half cutover and the predecessor stays authoritative.

Lock ordering matches S14's own internal order (all relevant dataset locks,
child and lineage parents, sorted, before the natural-partition topology
lock) throughout -- including the pre-mutation re-authorization step -- so a
concurrent ordinary S13/S14/A16 call sharing a dataset can only ever block on
this transaction, never deadlock against it.

Successful convergence is recorded durably inside the same commit, in
``catalog.repair_convergence``: ``ALREADY_SATISFIED`` is decided by looking up
that row for the live partition and confirming it names this exact
``repair_intent_id``/``candidate_id`` pair, never by comparing content hashes
alone (a coincidental hash match between two differently-evidenced candidates
must never be treated as the same repair).
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from .coverage import reconstruct_catalog_coverage
from .models import CoverageInterval, DatasetIdentity, Instant, NaturalPartitionIdentity
from .quality_lifecycle import (
    QualityLifecycleCatalog,
    QualityLifecycleRefusal,
    select_current_quality_assessment,
)
from .publication_eligibility_catalog import (
    PublicationEligibilityCatalog,
    PublicationEligibilityRefusal,
)
from quant_platform.canonical import canonical_bytes


REPAIR_SEMANTICS_VERSION = "quant-platform/a10-repair-v1"

# Explicit outcome vocabulary (frozen contract section 13).
REPAIR_REQUIRED = "REPAIR_REQUIRED"
CANDIDATE_PENDING = "CANDIDATE_PENDING"
CONVERGED = "CONVERGED"
ALREADY_SATISFIED = "ALREADY_SATISFIED"
FAILED = "FAILED"
STALE_CONFLICT = "STALE_CONFLICT"

_OUTCOMES = frozenset({
    REPAIR_REQUIRED, CANDIDATE_PENDING, CONVERGED,
    ALREADY_SATISFIED, FAILED, STALE_CONFLICT,
})

_QUALITY_APPLICABLE_STATES = frozenset({"valid", "degraded"})
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_EVIDENCE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")


class RepairRefusal(RuntimeError):
    """Raised when repair evidence or topology cannot authorize an operation."""


def _sha256_hex(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value.strip().lower()):
        raise RepairRefusal(f"{field} must be a lowercase SHA-256 hex digest")
    return value.strip().lower()


# ---------------------------------------------------------------------------
# Trigger evidence (frozen contract sections 1-3).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PredecessorRef:
    """Immutable identity of the exact predecessor a trigger was captured against."""

    revision: int
    content_sha256: str
    state: str

    def __post_init__(self) -> None:
        if self.revision < 1:
            raise RepairRefusal("predecessor revision must be positive")
        object.__setattr__(self, "content_sha256", _sha256_hex(self.content_sha256, "predecessor content_sha256"))
        if self.state not in {"closed", "valid", "degraded", "invalid"}:
            raise RepairRefusal(f"predecessor state {self.state!r} is not a repair-eligible state")

    def stable_dict(self) -> dict[str, Any]:
        return {"revision": self.revision, "content_sha256": self.content_sha256, "state": self.state}


@dataclass(frozen=True, slots=True)
class CoverageGapTrigger:
    """B04 evidence: one or more exact non-empty gaps inside required support.

    A coverage-gap trigger never captures a predecessor: if a row later
    occupies the target slot, that is a different eligibility ground (frozen
    contract section 3) and the intent must re-evaluate to ``ALREADY_SATISFIED``
    or ``STALE_CONFLICT`` rather than silently being reinterpreted.
    """

    required: CoverageInterval
    gaps: tuple[CoverageInterval, ...]

    def __post_init__(self) -> None:
        if not self.gaps:
            raise RepairRefusal("a coverage-gap trigger requires at least one exact gap")
        for gap in self.gaps:
            if gap.start < self.required.start or gap.end > self.required.end:
                raise RepairRefusal("gap evidence must lie inside the required support interval")

    @property
    def kind(self) -> str:
        return "missing_support"

    @property
    def predecessor(self) -> None:
        return None

    def stable_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "required": self.required.stable_dict(),
            "gaps": [gap.stable_dict() for gap in self.gaps],
        }


@dataclass(frozen=True, slots=True)
class InvalidRevisionTrigger:
    """A16 evidence: current authoritative assessment is ``fail`` / ``invalid``."""

    predecessor: PredecessorRef
    assessment_signature: str
    assessment_status: str

    def __post_init__(self) -> None:
        if self.predecessor.state != "invalid":
            raise RepairRefusal("invalid-revision trigger requires an invalid predecessor state")
        if self.assessment_status != "fail":
            raise RepairRefusal("invalid-revision trigger requires a fail assessment status")
        if not isinstance(self.assessment_signature, str) or not self.assessment_signature.strip():
            raise RepairRefusal("invalid-revision trigger requires a non-empty assessment signature")

    @property
    def kind(self) -> str:
        return "invalid_revision"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "predecessor": self.predecessor.stable_dict(),
            "assessment_signature": self.assessment_signature.strip(),
            "assessment_status": self.assessment_status,
        }


RepairTrigger = CoverageGapTrigger | InvalidRevisionTrigger


# ---------------------------------------------------------------------------
# Repair intent identity (frozen contract sections 1, 3).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairIntent:
    """A deterministic, partition-bounded repair obligation.

    Distinct ``DatasetIdentity`` + ``partition_key`` pairs always yield
    distinct intents (section: "multi-partition B04 gap results project into
    independent partition-bounded repair intents").
    """

    dataset_identity: DatasetIdentity
    partition_key: str
    trigger: RepairTrigger
    repair_semantics_version: str = REPAIR_SEMANTICS_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.partition_key, str) or not self.partition_key.strip():
            raise RepairRefusal("repair intent requires a non-empty partition_key")
        if not isinstance(self.repair_semantics_version, str) or not self.repair_semantics_version.strip():
            raise RepairRefusal("repair intent requires a non-empty repair_semantics_version")

    @property
    def predecessor(self) -> PredecessorRef | None:
        return self.trigger.predecessor

    def stable_dict(self) -> dict[str, Any]:
        return {
            "dataset_identity": self.dataset_identity.stable_dict(),
            "partition_key": self.partition_key,
            "trigger": self.trigger.stable_dict(),
            "repair_semantics_version": self.repair_semantics_version,
        }

    @property
    def intent_id(self) -> str:
        return _canonical_hash("a10-repair-intent-v1", self.stable_dict())


# ---------------------------------------------------------------------------
# Candidate acceptance-input evidence identities (amendment section 3;
# review finding: candidate acceptance inputs must bind cryptographically to
# candidate identity, not travel as independent cutover arguments).
# ---------------------------------------------------------------------------


def compute_quality_evidence_id(report: Mapping[str, Any], *, check_suite: str, expected_profile: str) -> str:
    """Deterministic identity of the exact quality-report content a candidate binds.

    ``check_suite`` and ``expected_profile`` are bound in as well as the
    report content: ``cutover()`` files the report under an independently
    supplied ``expected_check_suite`` and selects/verifies it against an
    independently supplied ``expected_profile`` -- without binding both here,
    the same report content could be re-filed under a different suite or
    re-evaluated under a different profile (changing what A16/S14 select and
    accept) while ``candidate_id`` stayed unchanged.  A
    ``CandidateAttempt.quality_evidence_id`` MUST equal this value computed
    over the report, suite and profile that will actually be used at
    cutover; ``cutover()`` re-derives and checks it, so a differently-
    evidenced report, suite or profile can never be substituted under the
    same ``candidate_id``.
    """

    for name in ("status", "metrics", "violations", "code_ref"):
        if name not in report:
            raise RepairRefusal(f"quality report evidence is missing required field {name!r}")
    if not isinstance(check_suite, str) or not check_suite.strip():
        raise RepairRefusal("check_suite must be a non-empty string")
    if not isinstance(expected_profile, str) or not expected_profile.strip():
        raise RepairRefusal("expected_profile must be a non-empty string")
    return _canonical_hash("a10-quality-report-evidence-v1", {
        "check_suite": check_suite.strip(),
        "expected_profile": expected_profile.strip(),
        "status": report["status"],
        "metrics": report["metrics"],
        "violations": report["violations"],
        "code_ref": report["code_ref"],
    })


def compute_coverage_evidence_id(
    coverage_ids: Sequence[str],
    assertion_ids: Sequence[str],
    coverage_sha256: Sequence[str],
    *,
    coverage_start: Instant,
    coverage_end: Instant,
) -> str:
    """Deterministic identity of the exact coverage evidence a candidate binds.

    ``coverage_start``/``coverage_end`` are bound in as well as the coverage
    manifest identifiers: they determine both gap elimination
    (:func:`_verify_gaps_eliminated`) and the admitted partition's catalog
    coverage, so a candidate that fails with one set of bounds must not be
    able to converge later with expanded bounds under the same
    ``candidate_id``.  A ``CandidateAttempt.coverage_evidence_id`` MUST equal
    this value; the same binding-and-recheck discipline as
    :func:`compute_quality_evidence_id`.
    """

    return _canonical_hash("a10-coverage-evidence-v1", {
        "coverage_ids": list(coverage_ids),
        "assertion_ids": list(assertion_ids),
        "coverage_sha256": list(coverage_sha256),
        "coverage_start": Instant.parse(coverage_start).isoformat(),
        "coverage_end": Instant.parse(coverage_end).isoformat(),
    })


# ---------------------------------------------------------------------------
# Candidate attempts (frozen contract sections 5-6).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CandidateAttempt:
    """One immutable, isolated repair candidate attempt.

    Binds every input the acceptance seams (A16/S14) will independently
    re-verify, including the exact quality-report and coverage-evidence
    content via :func:`compute_quality_evidence_id`/
    :func:`compute_coverage_evidence_id` -- ``cutover()`` refuses to run if
    the runtime inputs it is given do not hash to these bound identities, so
    the same ``candidate_id`` can never be retried with different acceptance
    evidence.  Two attempts with identical evidence share one ``candidate_id``
    (idempotent retry); any differing durable/semantic evidence produces a
    distinct id (frozen contract section 6).
    """

    repair_intent_id: str
    natural_identity: NaturalPartitionIdentity
    content_sha256: str
    partition_manifest_sha256: str
    source_evidence_id: str
    materialization_id: str
    coverage_evidence_id: str
    quality_evidence_id: str
    code_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "content_sha256", _sha256_hex(self.content_sha256, "candidate content_sha256"))
        object.__setattr__(
            self, "partition_manifest_sha256",
            _sha256_hex(self.partition_manifest_sha256, "candidate partition_manifest_sha256"),
        )
        for name in ("repair_intent_id", "source_evidence_id", "materialization_id",
                     "coverage_evidence_id", "quality_evidence_id", "code_ref"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise RepairRefusal(f"candidate {name} must be a non-empty string")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "repair_intent_id": self.repair_intent_id,
            "natural_identity": self.natural_identity.stable_dict(),
            "content_sha256": self.content_sha256,
            "partition_manifest_sha256": self.partition_manifest_sha256,
            "source_evidence_id": self.source_evidence_id.strip(),
            "materialization_id": self.materialization_id.strip(),
            "coverage_evidence_id": self.coverage_evidence_id.strip(),
            "quality_evidence_id": self.quality_evidence_id.strip(),
            "code_ref": self.code_ref.strip(),
        }

    @property
    def candidate_id(self) -> str:
        return _canonical_hash("a10-candidate-attempt-v1", self.stable_dict())


# ---------------------------------------------------------------------------
# Staging isolation (frozen contract section 5; amendment section 1/3).
#
# Physical isolation is operational metadata only: it grants no authority and
# is never a semantic partition identity.  Distinct candidate identities are
# content-addressed to distinct directories so concurrent attempts cannot
# clobber one another; the same candidate identity may be written repeatedly
# only if the bytes are unchanged (idempotent retry).
# ---------------------------------------------------------------------------


class CandidateStagingConflict(RepairRefusal):
    """Raised when two distinct candidate attempts would share physical storage."""


def _validate_evidence_name(name: str) -> str:
    """Reject anything but a single safe relative filename.

    No path separators (either OS's), no ``..``/``.``, no leading dot or
    dash, no absolute paths.  A staging root must never be escapable through
    the evidence file name a caller supplies.
    """

    if not isinstance(name, str) or not _SAFE_EVIDENCE_NAME.fullmatch(name):
        raise RepairRefusal(f"evidence name {name!r} is not a safe single relative filename")
    return name


@dataclass(frozen=True, slots=True)
class CandidateStaging:
    """Allocates one physically isolated, content-addressed staging directory per candidate."""

    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))

    def directory_for(self, candidate: CandidateAttempt) -> Path:
        """Return (creating if necessary) the isolated directory for one candidate.

        The directory name is the candidate's own content-addressed identity,
        so two distinct candidates -- even ones targeting the same nominal
        final revision -- can never resolve to the same path.
        """

        directory = self.root / _filesystem_safe(candidate.candidate_id)
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def write_evidence(self, candidate: CandidateAttempt, name: str, payload: bytes) -> Path:
        """Durably write one immutable evidence file for a candidate attempt.

        Writing the same bytes for the same (candidate, name) is idempotent.
        Writing different bytes for an existing (candidate, name) is refused:
        durable candidate evidence, once written, is immutable.  Creation is
        collision-safe: two concurrent writers for the same (candidate, name)
        can never both "win" with different content -- the loser's exclusive
        creation fails and it falls back to the immutability check.  There is
        no non-atomic fallback: if this filesystem cannot provide exclusive
        creation, the write fails closed rather than risking a silent
        clobber between concurrent writers.
        """

        safe_name = _validate_evidence_name(name)
        directory = self.directory_for(candidate)
        target = directory / safe_name
        if target.exists():
            existing = target.read_bytes()
            if existing != payload:
                raise CandidateStagingConflict(
                    f"candidate {candidate.candidate_id} evidence {safe_name!r} is immutable "
                    "and cannot be overwritten with different content"
                )
            return target
        temporary = directory / f".{safe_name}.{uuid4().hex}.tmp"
        temporary.write_bytes(payload)
        try:
            os.link(str(temporary), str(target))
        except FileExistsError:
            existing = target.read_bytes()
            if existing != payload:
                raise CandidateStagingConflict(
                    f"candidate {candidate.candidate_id} evidence {safe_name!r} is immutable "
                    "and cannot be overwritten with different content"
                )
        finally:
            temporary.unlink(missing_ok=True)
        return target

    def occupant(self, candidate_id: str) -> str | None:
        """Return the candidate_id physically occupying its directory, if any.

        Since directories are named by ``candidate_id`` itself, occupancy is
        always exactly the requested id or absent; this exists so callers can
        assert isolation held rather than assume it.
        """

        directory = self.root / _filesystem_safe(candidate_id)
        return candidate_id if directory.exists() else None


# ---------------------------------------------------------------------------
# Predecessor re-authorization evidence (review finding: an invalid-revision
# trigger must re-verify its captured assessment signature is still the
# authoritative leaf, not just that revision/content/state still match).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PredecessorEvidence:
    """Durable evidence needed to re-verify a captured predecessor's assessment.

    Required whenever ``RepairIntent.trigger`` is an :class:`InvalidRevisionTrigger`:
    binds the predecessor's own sealed partition manifest and the coverage
    evidence its authoritative quality report was selected against, so
    ``cutover()`` can re-run the identical A16 selection logic read-only
    against the predecessor and confirm ``trigger.assessment_signature`` is
    still the selected leaf before ever touching topology.
    """

    partition: Mapping[str, Any]
    partition_sha256: str
    coverage_start: Instant
    coverage_end: Instant
    coverage_ids: Sequence[str]
    assertion_ids: Sequence[str]
    coverage_sha256: Sequence[str]


# ---------------------------------------------------------------------------
# Convergence provenance (frozen contract section 14; amendment section 8).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConvergenceProvenance:
    """Immutable evidence bound to the exact final revision that became authoritative.

    Carries the exact coverage IDs/assertion IDs/hashes and the candidate's
    quality-evidence and code identities -- not only the opaque
    ``coverage_evidence_id`` -- so durable audit/reconstruction never depends
    on an external document to know what was actually accepted (review
    finding).
    """

    repair_intent_id: str
    candidate_id: str
    predecessor: PredecessorRef | None
    final_partition_id: str
    final_natural_identity: NaturalPartitionIdentity
    final_partition_manifest_sha256: str
    final_content_sha256: str
    coverage_evidence_id: str
    coverage_ids: tuple[str, ...]
    assertion_ids: tuple[str, ...]
    coverage_sha256: tuple[str, ...]
    quality_evidence_id: str
    quality_assessment_signature: str
    quality_assessment_status: str
    publication_state: str
    code_ref: str
    repair_semantics_version: str = REPAIR_SEMANTICS_VERSION

    def stable_dict(self) -> dict[str, Any]:
        return {
            "repair_intent_id": self.repair_intent_id,
            "candidate_id": self.candidate_id,
            "predecessor": None if self.predecessor is None else self.predecessor.stable_dict(),
            "final_partition_id": self.final_partition_id,
            "final_natural_identity": self.final_natural_identity.stable_dict(),
            "final_partition_manifest_sha256": self.final_partition_manifest_sha256,
            "final_content_sha256": self.final_content_sha256,
            "coverage_evidence_id": self.coverage_evidence_id,
            "coverage_ids": list(self.coverage_ids),
            "assertion_ids": list(self.assertion_ids),
            "coverage_sha256": list(self.coverage_sha256),
            "quality_evidence_id": self.quality_evidence_id,
            "quality_assessment_signature": self.quality_assessment_signature,
            "quality_assessment_status": self.quality_assessment_status,
            "publication_state": self.publication_state,
            "code_ref": self.code_ref,
            "repair_semantics_version": self.repair_semantics_version,
        }

    @property
    def provenance_id(self) -> str:
        return _canonical_hash("a10-convergence-provenance-v1", self.stable_dict())


def write_convergence_provenance(path: Path, provenance: ConvergenceProvenance) -> str:
    """Durably write convergence provenance as a supplementary, hash-addressed
    JSON document alongside the authoritative ``catalog.repair_convergence`` row.

    Returns the sha256 of the written bytes.  Refuses to silently overwrite an
    existing file with different content.  This is diagnostic/portable
    evidence only: ``ALREADY_SATISFIED`` is always decided from the catalog
    transaction, never from this filesystem document.
    """

    payload = canonical_bytes(provenance.stable_dict(), profile="sorted-compact-ascii-v1", allow_nan=True)
    target = Path(path)
    if target.exists():
        existing = target.read_bytes()
        if existing != payload:
            raise RepairRefusal(f"convergence provenance at {target} is immutable and already differs")
        return hashlib.sha256(existing).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(target)
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Cutover result.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CutoverResult:
    outcome: str
    reason: str | None = None
    provenance: ConvergenceProvenance | None = None

    def __post_init__(self) -> None:
        if self.outcome not in _OUTCOMES:
            raise RepairRefusal(f"unsupported repair outcome: {self.outcome!r}")
        if self.outcome == CONVERGED and self.provenance is None:
            raise RepairRefusal("a CONVERGED outcome requires convergence provenance")


class _AlreadySatisfiedSignal(Exception):
    pass


class _StaleConflictSignal(Exception):
    pass


# ---------------------------------------------------------------------------
# The dedicated A10 cutover seam (amendment sections 4-9; frozen contract 9-12).
# ---------------------------------------------------------------------------


class RepairCutoverCatalog:
    """Owns the one atomic compare-and-cutover transaction for A10 repair.

    Lock ordering matches S14's own internal order exactly: all relevant
    dataset locks (child and lineage parents, sorted) are acquired first via
    :class:`PublicationEligibilityCatalog`'s own staticmethods, before the
    natural-partition topology lock -- including for the pre-mutation
    re-authorization step, so an empty-topology backfill is serialized by the
    dataset-row lock even though there is no partition row yet to lock.  A16/S14
    verification and mutation are delegated to their own transaction-scoped
    seams against this same cursor, so a failed candidate rolls back every
    mutation this transaction made, including the predecessor's uncommitted
    supersession.
    """

    def __init__(self, connection: Any):
        self.connection = connection
        self.quality = QualityLifecycleCatalog(connection)
        self.publication = PublicationEligibilityCatalog(connection)

    def cutover(
        self,
        *,
        repair_intent: RepairIntent,
        candidate: CandidateAttempt,
        dataset: Mapping[str, Any],
        dataset_sha256: str,
        candidate_partition: Mapping[str, Any],
        candidate_quality_report: Mapping[str, Any],
        coverage_start: Instant,
        coverage_end: Instant,
        coverage_ids: Sequence[str],
        assertion_ids: Sequence[str],
        coverage_sha256: Sequence[str],
        storage_root_id: str,
        expected_profile: str,
        expected_check_suite: str,
        predecessor_evidence: PredecessorEvidence | None = None,
        coverage_manifests: Sequence[Mapping[str, Any]] | None = None,
    ) -> CutoverResult:
        trigger = repair_intent.trigger
        if candidate.repair_intent_id != repair_intent.intent_id:
            raise RepairRefusal("candidate does not target this repair intent")
        target_natural = NaturalPartitionIdentity(
            repair_intent.dataset_identity, repair_intent.partition_key, int(candidate_partition["revision"]),
        )
        if candidate.natural_identity != target_natural:
            raise RepairRefusal("candidate natural identity does not match candidate_partition")
        if candidate_partition.get("state") != "closed":
            raise RepairRefusal("A10 cutover requires a sealed closed candidate partition manifest")
        if _text(candidate_partition.get("sha256")) != candidate.content_sha256:
            raise RepairRefusal("candidate_partition physical hash differs from candidate identity")
        if _manifest_sha(candidate_partition) != candidate.partition_manifest_sha256:
            raise RepairRefusal("candidate_partition manifest hash differs from candidate identity")
        if _text(candidate_partition.get("code_ref")) != candidate.code_ref.strip():
            raise RepairRefusal("candidate_partition code_ref differs from candidate identity")

        # Candidate acceptance inputs must be exactly the evidence this
        # candidate_id is bound to -- never a substitutable, independently
        # supplied argument (review finding).  expected_check_suite and
        # expected_profile are bound into quality_evidence_id because the
        # report is filed under the suite and selected/verified under the
        # profile; coverage_start/coverage_end are bound into
        # coverage_evidence_id because they determine gap elimination and the
        # admitted partition's catalog coverage.
        if candidate.quality_evidence_id != compute_quality_evidence_id(
            candidate_quality_report, check_suite=expected_check_suite, expected_profile=expected_profile,
        ):
            raise RepairRefusal("candidate_quality_report does not match the candidate's bound quality_evidence_id")
        if candidate.coverage_evidence_id != compute_coverage_evidence_id(
            coverage_ids, assertion_ids, coverage_sha256, coverage_start=coverage_start, coverage_end=coverage_end,
        ):
            raise RepairRefusal("coverage evidence does not match the candidate's bound coverage_evidence_id")

        if isinstance(trigger, InvalidRevisionTrigger):
            if predecessor_evidence is None:
                raise RepairRefusal("predecessor_evidence is required to re-authorize an invalid-revision trigger")
        elif isinstance(trigger, CoverageGapTrigger):
            if coverage_manifests is None:
                raise RepairRefusal("coverage_manifests evidence is required to re-authorize a coverage-gap trigger")
            if [doc.get("coverage_id") for doc in coverage_manifests] != list(coverage_ids):
                raise RepairRefusal("coverage_manifests do not match the candidate's bound coverage_ids evidence")
        else:  # pragma: no cover - exhaustive union
            raise RepairRefusal(f"unsupported repair trigger kind: {trigger!r}")

        try:
            with self.connection.cursor() as cursor:
                identity = repair_intent.dataset_identity
                # Step 1: lock ALL relevant dataset(s) -- child and lineage
                # parents, sorted -- exactly as S14's own publish() does, and
                # strictly before any partition-topology lock.
                child = PublicationEligibilityCatalog._resolve_dataset(cursor, identity, dataset, dataset_sha256)
                parents = PublicationEligibilityCatalog._resolve_parents(cursor, dataset, identity)
                relevant_ids = sorted({child[0], *(row[0] for row in parents)})
                PublicationEligibilityCatalog._lock_datasets(cursor, relevant_ids)
                PublicationEligibilityCatalog._verify_locked_datasets(cursor, child, parents, dataset, dataset_sha256)
                dataset_row = child

                # Step 2: lock/re-read the natural partition topology.  The
                # dataset-row lock above already serializes two concurrent
                # attempts even when this returns zero rows (empty-topology
                # backfill), because both attempts must first acquire the
                # same dataset lock before either can reach this SELECT.
                topology = QualityLifecycleCatalog._lock_partition_topology(
                    cursor, dataset_row[0], repair_intent.partition_key,
                )

                # Step 3: re-evaluate the repair trigger/current authority
                # under lock, against durable convergence provenance -- never
                # against a coincidental content-hash match.  A live row
                # already occupying this partition_key (gap closed, or
                # something else entirely) is caught here regardless of
                # trigger kind.
                predecessor_row = _reevaluate_authority(cursor, repair_intent, candidate, topology)

                # Step 3b: trigger-specific re-authorization.
                if isinstance(trigger, CoverageGapTrigger):
                    _verify_candidate_coverage_via_fold(
                        identity=identity,
                        trigger=trigger,
                        coverage_manifests=coverage_manifests,
                        candidate_partition=candidate_partition,
                        coverage_start=coverage_start,
                        coverage_end=coverage_end,
                    )
                else:
                    _verify_invalid_revision_trigger_still_authoritative(
                        cursor,
                        dataset_row=dataset_row,
                        predecessor_row=predecessor_row,
                        trigger=trigger,
                        identity=identity,
                        dataset_sha256=dataset_sha256,
                        evidence=predecessor_evidence,
                        expected_profile=expected_profile,
                        expected_check_suite=expected_check_suite,
                    )

                # Step 5: if replacement, supersede the predecessor (uncommitted).
                if predecessor_row is not None:
                    cursor.execute(
                        """
                        UPDATE catalog.partitions
                           SET state = 'superseded'
                         WHERE partition_id = %s
                           AND state <> 'superseded'
                        RETURNING partition_id::text
                        """,
                        (predecessor_row[0],),
                    )
                    if cursor.fetchone() is None:
                        raise RepairRefusal("predecessor could not be superseded atomically")

                # Step 6: insert/admit candidate under its FINAL natural identity as closed.
                candidate_partition_id = _insert_candidate(
                    cursor, dataset_row[0], repair_intent.partition_key, candidate_partition,
                    coverage_start, coverage_end,
                )
                # The candidate's quality-report CONTENT (status/metrics/violations/
                # code_ref) is immutable evidence prepared before cutover, but its
                # catalog row can only be written now: catalog.quality_reports is
                # keyed by partition_id, which does not exist before this INSERT.
                _insert_quality_report(cursor, candidate_partition_id, expected_check_suite, candidate_quality_report)

                sealed_partition = dict(candidate_partition)
                sealed_partition["_manifest_sha256"] = candidate.partition_manifest_sha256

                # Step 7: apply the existing A16 lifecycle semantics (tx-scoped
                # seam).  lifecycle_code_ref is the candidate's own bound
                # code_ref -- never an independent, unbound argument.
                quality_result = self.quality._apply_partition_lifecycle_tx(
                    cursor,
                    dataset=dataset,
                    dataset_sha256=dataset_sha256,
                    partition=sealed_partition,
                    partition_sha256=candidate.partition_manifest_sha256,
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                    coverage_ids=coverage_ids,
                    assertion_ids=assertion_ids,
                    coverage_sha256=coverage_sha256,
                    storage_root_id=storage_root_id,
                    expected_profile=expected_profile,
                    expected_check_suite=expected_check_suite,
                    lifecycle_code_ref=candidate.code_ref.strip(),
                )
                if quality_result.resulting_state not in _QUALITY_APPLICABLE_STATES:
                    raise RepairRefusal(
                        f"candidate quality assessment resulted in {quality_result.resulting_state!r}; "
                        "replacement cannot converge"
                    )

                # Step 8: apply the existing A09/S14 publication semantics (tx-scoped seam).
                publication_result = self.publication._publish_tx(
                    cursor,
                    dataset=dataset,
                    dataset_sha256=dataset_sha256,
                    partition=sealed_partition,
                    partition_sha256=candidate.partition_manifest_sha256,
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                    coverage_ids=coverage_ids,
                    assertion_ids=assertion_ids,
                    coverage_sha256=coverage_sha256,
                    storage_root_id=storage_root_id,
                    expected_profile=expected_profile,
                    expected_check_suite=expected_check_suite,
                )
                # Step 9: require the resulting state to be valid/degraded.
                if publication_result.state not in _QUALITY_APPLICABLE_STATES:
                    raise RepairRefusal(
                        f"candidate publication eligibility resulted in {publication_result.state!r}; "
                        "replacement cannot converge"
                    )

                predecessor_ref = repair_intent.predecessor
                provenance = ConvergenceProvenance(
                    repair_intent_id=repair_intent.intent_id,
                    candidate_id=candidate.candidate_id,
                    predecessor=predecessor_ref,
                    final_partition_id=candidate_partition_id,
                    final_natural_identity=target_natural,
                    final_partition_manifest_sha256=candidate.partition_manifest_sha256,
                    final_content_sha256=candidate.content_sha256,
                    coverage_evidence_id=candidate.coverage_evidence_id.strip(),
                    coverage_ids=tuple(coverage_ids),
                    assertion_ids=tuple(assertion_ids),
                    coverage_sha256=tuple(coverage_sha256),
                    quality_evidence_id=candidate.quality_evidence_id.strip(),
                    quality_assessment_signature=quality_result.assessment_signature,
                    quality_assessment_status=quality_result.assessment_status,
                    publication_state=publication_result.state,
                    code_ref=candidate.code_ref.strip(),
                    repair_semantics_version=repair_intent.repair_semantics_version,
                )
                # Step 10: persist integrity-bound convergence provenance
                # inside the SAME committed transaction -- the authoritative
                # source ALREADY_SATISFIED is decided from, not an optional
                # side effect.
                _insert_convergence_provenance(
                    cursor,
                    partition_id=candidate_partition_id,
                    predecessor_partition_id=None if predecessor_row is None else predecessor_row[0],
                    provenance=provenance,
                )
            self.connection.commit()
            return CutoverResult(outcome=CONVERGED, provenance=provenance)
        except _AlreadySatisfiedSignal:
            self.connection.rollback()
            return CutoverResult(outcome=ALREADY_SATISFIED)
        except _StaleConflictSignal as exc:
            self.connection.rollback()
            return CutoverResult(outcome=STALE_CONFLICT, reason=str(exc))
        except (RepairRefusal, QualityLifecycleRefusal, PublicationEligibilityRefusal) as exc:
            self.connection.rollback()
            return CutoverResult(outcome=FAILED, reason=str(exc))
        except Exception:
            self.connection.rollback()
            raise


def _insert_candidate(
    cursor: Any,
    dataset_id: str,
    partition_key: str,
    partition: Mapping[str, Any],
    coverage_start: Instant,
    coverage_end: Instant,
) -> str:
    cursor.execute(
        """
        INSERT INTO catalog.partitions (
            dataset_id, partition_key, revision, storage_root_id,
            rel_path, ts_start, ts_end, row_count, byte_size,
            content_sha256, state, manifest_sha256, created_at,
            closed_at, first_sequence, last_sequence, producer, code_ref
        ) VALUES (
            %(dataset_id)s, %(partition_key)s, %(revision)s, %(storage_root_id)s,
            %(rel_path)s, %(ts_start)s, %(ts_end)s, %(row_count)s, %(byte_size)s,
            %(content_sha256)s, 'closed', %(manifest_sha256)s, %(created_at)s,
            %(closed_at)s, %(first_sequence)s, %(last_sequence)s, %(producer)s, %(code_ref)s
        )
        RETURNING partition_id::text
        """,
        {
            "dataset_id": dataset_id,
            "partition_key": partition_key,
            "revision": int(partition["revision"]),
            "storage_root_id": partition["storage_root_id"],
            "rel_path": partition["rel_path"],
            "ts_start": coverage_start.to_datetime(),
            "ts_end": coverage_end.to_datetime(),
            "row_count": partition["row_count"],
            "byte_size": partition["file_size_bytes"],
            "content_sha256": partition["sha256"],
            "manifest_sha256": _manifest_sha(partition),
            "created_at": _timestamp(partition["created_at"]),
            "closed_at": _timestamp(partition["closed_at"]),
            "first_sequence": partition.get("first_sequence"),
            "last_sequence": partition.get("last_sequence"),
            "producer": partition["producer"],
            "code_ref": partition["code_ref"],
        },
    )
    return str(cursor.fetchone()[0])


def _insert_quality_report(
    cursor: Any,
    partition_id: str,
    check_suite: str,
    report: Mapping[str, Any],
) -> None:
    for name in ("status", "metrics", "violations", "code_ref"):
        if name not in report:
            raise RepairRefusal(f"candidate quality report is missing required field {name!r}")
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:  # pragma: no cover - dependency boundary
        raise RuntimeError("psycopg is required for A10 catalog writes") from exc
    cursor.execute(
        """
        INSERT INTO catalog.quality_reports
            (partition_id, check_suite, status, metrics, violations, code_ref)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            partition_id, check_suite, report["status"],
            Jsonb(dict(report["metrics"])), Jsonb(list(report["violations"])),
            report["code_ref"],
        ),
    )


def _insert_convergence_provenance(
    cursor: Any,
    *,
    partition_id: str,
    predecessor_partition_id: str | None,
    provenance: ConvergenceProvenance,
) -> None:
    cursor.execute(
        """
        INSERT INTO catalog.repair_convergence (
            partition_id, repair_intent_id, candidate_id, provenance_id, predecessor_partition_id,
            final_partition_manifest_sha256, final_content_sha256, coverage_evidence_id,
            coverage_ids, assertion_ids, coverage_sha256, quality_evidence_id,
            quality_assessment_signature, quality_assessment_status, publication_state,
            code_ref, repair_semantics_version
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            partition_id, provenance.repair_intent_id, provenance.candidate_id, provenance.provenance_id,
            predecessor_partition_id,
            provenance.final_partition_manifest_sha256, provenance.final_content_sha256,
            provenance.coverage_evidence_id,
            list(provenance.coverage_ids), list(provenance.assertion_ids), list(provenance.coverage_sha256),
            provenance.quality_evidence_id,
            provenance.quality_assessment_signature, provenance.quality_assessment_status,
            provenance.publication_state, provenance.code_ref, provenance.repair_semantics_version,
        ),
    )


def _provenance_marks_self(cursor: Any, live_partition_id: str, repair_intent: RepairIntent, candidate: CandidateAttempt) -> bool:
    """True iff durable convergence provenance proves the live row is exactly
    this repair_intent's own prior successful convergence with this exact
    candidate.

    Cross-checks multiple independently-stored fields (final content/manifest
    hashes and coverage/quality evidence ids) against the candidate's own
    corresponding fields -- not only the two opaque ``repair_intent_id``/
    ``candidate_id`` strings -- so a write-path defect that stored
    inconsistent values under a matching ``candidate_id`` is detected rather
    than silently authorizing ``ALREADY_SATISFIED``.  Never inferred from a
    coincidental content-hash match alone.
    """

    cursor.execute(
        """
        SELECT repair_intent_id, candidate_id, final_content_sha256,
               final_partition_manifest_sha256, coverage_evidence_id, quality_evidence_id
          FROM catalog.repair_convergence
         WHERE partition_id = %s
        """,
        (live_partition_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return False
    (
        stored_intent_id, stored_candidate_id, stored_content_sha256,
        stored_manifest_sha256, stored_coverage_evidence_id, stored_quality_evidence_id,
    ) = row
    return (
        stored_intent_id == repair_intent.intent_id
        and stored_candidate_id == candidate.candidate_id
        and _text(stored_content_sha256) == candidate.content_sha256
        and _text(stored_manifest_sha256) == candidate.partition_manifest_sha256
        and stored_coverage_evidence_id == candidate.coverage_evidence_id.strip()
        and stored_quality_evidence_id == candidate.quality_evidence_id.strip()
    )


def _matches_predecessor(row: Any, predecessor_ref: PredecessorRef) -> bool:
    return (
        int(row[3]) == predecessor_ref.revision
        and _text(row[11]) == predecessor_ref.content_sha256
        and row[4] == predecessor_ref.state
    )


def _reevaluate_authority(
    cursor: Any,
    repair_intent: RepairIntent,
    candidate: CandidateAttempt,
    topology: Sequence[Any],
) -> Any | None:
    """Re-check current catalog authority under lock; raise the matching signal.

    Returns the locked predecessor row to supersede (or ``None`` for a
    genuine no-predecessor backfill), or raises :class:`_AlreadySatisfiedSignal`
    / :class:`_StaleConflictSignal` to short-circuit the transaction without
    any mutation.  "Already this candidate's own convergence" is decided only
    from durable ``catalog.repair_convergence`` provenance (see
    :func:`_provenance_marks_self`), never from revision/content/state alone:
    a distinct candidate that happens to share a content hash with the live
    row must never be treated as already satisfied.
    """

    live = [row for row in topology if row[4] != "superseded"]
    if len(live) > 1:
        raise RepairRefusal("partition topology does not have exactly one live revision")
    live_row = live[0] if live else None
    predecessor_ref = repair_intent.predecessor

    if predecessor_ref is None:
        # Missing-support backfill: no admitted/eligible revision expected yet.
        if live_row is None:
            return None
        if _provenance_marks_self(cursor, live_row[0], repair_intent, candidate):
            raise _AlreadySatisfiedSignal()
        raise _StaleConflictSignal(
            f"a revision now occupies the target slot in state {live_row[4]!r}; "
            "re-evaluate as a newly derived repair intent"
        )

    # Replacement repair: a captured predecessor identity/state must still match.
    if live_row is None:
        raise _StaleConflictSignal("predecessor no longer exists in current topology")
    if _matches_predecessor(live_row, predecessor_ref):
        return live_row
    if _provenance_marks_self(cursor, live_row[0], repair_intent, candidate):
        raise _AlreadySatisfiedSignal()
    raise _StaleConflictSignal(
        "current authority no longer matches the captured predecessor; repair intent must re-evaluate"
    )


def _verify_gaps_eliminated(
    gaps: Sequence[CoverageInterval],
    coverage_start: Instant,
    coverage_end: Instant,
) -> None:
    """Every targeted gap must fall fully inside the candidate's own resulting
    declared coverage; partial fill is operational progress only (frozen
    contract section 8) and must never converge."""

    for gap in gaps:
        if gap.start < coverage_start or gap.end > coverage_end:
            raise RepairRefusal(
                "candidate declared coverage does not eliminate every targeted gap; partial fill cannot converge"
            )


def _verify_candidate_coverage_via_fold(
    *,
    identity: DatasetIdentity,
    trigger: CoverageGapTrigger,
    coverage_manifests: Sequence[Mapping[str, Any]],
    candidate_partition: Mapping[str, Any],
    coverage_start: Instant,
    coverage_end: Instant,
) -> None:
    """Re-derive the candidate's declared coverage through the SAME
    authoritative B04 fold S13/S14 use (``reconstruct_catalog_coverage``),
    rather than trusting caller-supplied ``coverage_start``/``coverage_end``
    or the originally captured ``trigger.gaps`` at face value.

    Folds the caller's coverage-manifest evidence against the candidate's own
    about-to-be-admitted partition (never a synthetic/fabricated partition,
    and never the current live row, which ``_reevaluate_authority`` has
    already proven does not exist for a coverage-gap trigger): the fold can
    only attribute completed coverage to a partition manifest that is
    actually present, so this is a real, non-tautological, integrity-bound
    verification -- not a restatement of the topology check.  Every targeted
    gap must fall inside the folded interval, and the folded interval must
    exactly equal the caller's ``coverage_start``/``coverage_end``, proving
    those bounds are not independently fabricated relative to the durable
    coverage manifests admission will actually use.
    """

    manifest_for_fold = {
        **identity.stable_dict(),
        "partition_key": candidate_partition["partition_key"],
        "revision": int(candidate_partition["revision"]),
        "state": "closed",
        "row_count": candidate_partition.get("row_count", 0),
        "first_exchange_ts": candidate_partition.get("first_exchange_ts"),
        "last_exchange_ts": candidate_partition.get("last_exchange_ts"),
    }
    folded, violations = reconstruct_catalog_coverage(coverage_manifests, (manifest_for_fold,))
    if violations:
        raise RepairRefusal(
            "candidate coverage evidence does not fold to one publishable interval: "
            + ", ".join(item.code for item in violations)
        )
    key = (manifest_for_fold["partition_key"], manifest_for_fold["revision"])
    if key not in folded:
        raise RepairRefusal("candidate coverage evidence does not resolve to the target revision")
    folded_start, folded_end = folded[key]
    if folded_start != coverage_start or folded_end != coverage_end:
        raise RepairRefusal(
            "supplied coverage_start/coverage_end do not match the authoritative coverage fold"
        )
    _verify_gaps_eliminated(trigger.gaps, folded_start, folded_end)


def _verify_invalid_revision_trigger_still_authoritative(
    cursor: Any,
    *,
    dataset_row: Any,
    predecessor_row: Any,
    trigger: InvalidRevisionTrigger,
    identity: DatasetIdentity,
    dataset_sha256: str,
    evidence: PredecessorEvidence,
    expected_profile: str,
    expected_check_suite: str,
) -> None:
    """Re-authorize an invalid-revision trigger against the predecessor's
    CURRENT authoritative quality assessment, not merely its topology tuple.

    Re-runs the identical A16 selection logic read-only against the
    predecessor's own durable manifest evidence and confirms
    ``trigger.assessment_signature`` is still the selected leaf; a newer
    quality report that supersedes it with a different signature (even one
    that leaves the topology state unchanged at ``invalid``) must never
    silently authorize cutover under the stale signature.
    """

    QualityLifecycleCatalog._verify_partition(
        predecessor_row, dataset_row, evidence.partition, evidence.partition_sha256,
        evidence.coverage_start, evidence.coverage_end, evidence.partition["storage_root_id"],
    )
    reports = QualityLifecycleCatalog._quality_reports(cursor, predecessor_row[0], expected_check_suite)
    try:
        selected = select_current_quality_assessment(
            reports,
            expected_profile=expected_profile,
            expected_check_suite=expected_check_suite,
            identity=identity,
            partition=evidence.partition,
            dataset_sha256=dataset_sha256,
            partition_sha256=evidence.partition_sha256,
            coverage_ids=evidence.coverage_ids,
            assertion_ids=evidence.assertion_ids,
            coverage_sha256=evidence.coverage_sha256,
            target=predecessor_row,
        )
    except QualityLifecycleRefusal as exc:
        raise _StaleConflictSignal(
            f"predecessor assessment evidence could not be re-authorized: {exc}"
        ) from exc
    if selected.signature != trigger.assessment_signature.strip() or selected.status != "fail":
        raise _StaleConflictSignal(
            "captured predecessor assessment signature is no longer authoritative; repair intent must re-evaluate"
        )


# ---------------------------------------------------------------------------
# Pure eligibility evaluation (frozen contract section 2), independent of any
# DB connection so callers can decide REPAIR_REQUIRED vs no-trigger before
# ever constructing a candidate.
# ---------------------------------------------------------------------------


def evaluate_missing_support_eligibility(
    required: CoverageInterval,
    gaps: Sequence[CoverageInterval],
) -> str:
    """REPAIR_REQUIRED if B04 proves at least one non-empty gap, else ALREADY_SATISFIED."""

    return REPAIR_REQUIRED if gaps else ALREADY_SATISFIED


def evaluate_invalid_revision_eligibility(assessment_status: str, live_state: str) -> str:
    """REPAIR_REQUIRED only for an authoritative fail assessment on an invalid live revision.

    ``warn``/``degraded`` is never an automatic trigger (frozen contract
    section 2); neither is ``pass``/``valid`` merely because other evidence is
    newer or different.
    """

    if assessment_status == "fail" and live_state == "invalid":
        return REPAIR_REQUIRED
    return ALREADY_SATISFIED


# ---------------------------------------------------------------------------
# Shared helpers.
# ---------------------------------------------------------------------------


def _filesystem_safe(identity: str) -> str:
    """Project a content-addressed identity string to a portable directory name.

    Identities carry ``:`` separators (e.g. ``a10-candidate-attempt-v1:sha256:<hex>``),
    which is not a legal path character on Windows.  The projection is
    injective for the identities this module produces: only the fixed
    ``<domain>:sha256:<hex>`` shape is ever generated, so replacing the
    separator cannot collide two distinct identities.
    """

    return identity.replace(":", "_")


def _canonical_hash(domain: str, payload: Mapping[str, Any]) -> str:
    document = canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=True).decode("utf-8")
    digest = hashlib.sha256(f"{domain}\x00{document}".encode("utf-8")).hexdigest()
    return f"{domain}:sha256:{digest}"


def _text(value: Any) -> str | None:
    return None if value is None else str(value).strip()


def _timestamp(value: Any) -> Any:
    if isinstance(value, str):
        return Instant.parse(value).to_datetime()
    if isinstance(value, Instant):
        return value.to_datetime()
    return value


def _manifest_sha(document: Mapping[str, Any]) -> str:
    value = document.get("_manifest_sha256")
    if value is None:
        raise RepairRefusal("durable candidate manifest SHA is missing from catalog write input")
    # Normalized (lowercase hex) at this single choke point: both the row
    # this module writes to catalog.partitions and the equality check against
    # candidate.partition_manifest_sha256 must agree regardless of the
    # caller-supplied document's original casing.
    return _sha256_hex(value, "candidate_partition manifest hash")


__all__ = [
    "REPAIR_SEMANTICS_VERSION",
    "REPAIR_REQUIRED",
    "CANDIDATE_PENDING",
    "CONVERGED",
    "ALREADY_SATISFIED",
    "FAILED",
    "STALE_CONFLICT",
    "RepairRefusal",
    "PredecessorRef",
    "PredecessorEvidence",
    "CoverageGapTrigger",
    "InvalidRevisionTrigger",
    "RepairTrigger",
    "RepairIntent",
    "CandidateAttempt",
    "CandidateStaging",
    "CandidateStagingConflict",
    "ConvergenceProvenance",
    "write_convergence_provenance",
    "CutoverResult",
    "RepairCutoverCatalog",
    "compute_quality_evidence_id",
    "compute_coverage_evidence_id",
    "evaluate_missing_support_eligibility",
    "evaluate_invalid_revision_eligibility",
]
