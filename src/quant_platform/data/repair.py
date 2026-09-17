"""A10 backfill/repair v1: deterministic repair intent, isolated candidates,
and the dedicated atomic replacement cutover.

Repair is reconciliation over explicit defects, never a heuristic. This
module owns only the bounded seam described by the frozen A10 contract:

- deterministic, evidence-bound repair-intent identity (no event-spacing,
  file-age, row-count, wall-clock or "latest source" heuristic ever creates
  a repair target);
- immutable, content-addressed candidate-attempt identity, whose derived
  staging ``partition_key`` gives distinct attempts physically isolated
  storage/catalog rows before convergence, purely by being a different
  natural-key value -- no new schema, table or column is introduced;
- one dedicated transactional compare-and-cutover seam that, after the
  candidate is already proven through the existing S13/A16/S14 machinery
  under its own isolated staging partition-key family, re-verifies that
  proof is bound to the exact candidate, re-derives declared coverage from
  canonical evidence, supersedes the predecessor, re-emits a
  natural-identity manifest so the promoted catalog row and its durable
  manifest agree, and records immutable convergence provenance -- all in
  one commit.

Candidate sealing, quality assessment and publication eligibility are
deliberately NOT reimplemented here: a caller certifies a candidate by
calling the existing ``CatalogPublicationWriter.seal_partition``,
``QualityLifecycleCatalog.apply_partition_lifecycle`` and
``PublicationEligibilityCatalog.publish`` with the candidate's own
``staging_partition_key`` as the partition manifest's ``partition_key``.
Because that staging key is never the natural partition_key, none of those
existing writers ever touches -- let alone supersedes -- the live
predecessor while the candidate is still being proven. Only
:class:`RepairCutoverCatalog.cutover` ever changes the natural partition's
topology, and it does so in one transaction, never before candidate proof
is already durable and re-verified.

The staging partition_key is deliberately chosen as ``"{natural_key}/repair=
{digest}"``: a sub-path of the natural key, so the staged artifact's
``rel_path`` already satisfies the existing, unmodified
``rel_path_inside_partition`` constraint under the natural key too. This
means promotion never needs to move or copy the physical artifact -- only a
new manifest that re-declares natural identity over the same unmoved bytes.

This module must not import ``quant_platform.access``: callers pass already
computed B04 gap evidence and A16 assessment evidence in as canonical
``CoverageInterval``/string values.  It does import the sibling
``quant_platform.data.coverage``/``.manifests``/``.materializer`` modules
(same "producer" package owner) to reuse their credited folding and
manifest-emission logic rather than reimplementing it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .coverage import reconstruct_catalog_coverage
from .manifests import (
    ManifestEmission,
    ManifestValidationError,
    emit_partition_manifest,
    _persist_manifest,
    _validate_coverage_document,
    _validate_dataset_document,
    _validate_partition_document,
)
from .materializer import ParquetMaterialization
from .models import CoverageInterval, DatasetIdentity, Instant
from .quality_lifecycle import QualityLifecycleRefusal, select_current_quality_assessment


REPAIR_SEMANTICS_VERSION = "a10-repair-v1"
REPAIR_INTENT_IDENTITY_DOMAIN = "a10-repair-intent-v1"
CANDIDATE_ATTEMPT_IDENTITY_DOMAIN = "a10-candidate-attempt-v1"
REPAIR_CONVERGENCE_ARTIFACT_KIND = "a10_repair_convergence"

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")
_ELIGIBLE_LIVE_STATES = frozenset({"closed", "valid", "degraded", "invalid"})
_COVERING_STATES = frozenset({"valid", "degraded"})


class RepairError(RuntimeError):
    """Base for A10 repair refusals."""


class RepairIneligible(RepairError):
    """Raised when supplied evidence does not establish genuine repair eligibility."""


class RepairCutoverRefusal(RepairError):
    """Raised when the atomic compare-and-cutover transaction cannot proceed."""


class RepairOutcome(StrEnum):
    """The frozen A10 v1 outcome vocabulary."""

    REPAIR_REQUIRED = "REPAIR_REQUIRED"
    CANDIDATE_PENDING = "CANDIDATE_PENDING"
    CONVERGED = "CONVERGED"
    ALREADY_SATISFIED = "ALREADY_SATISFIED"
    FAILED = "FAILED"
    STALE_CONFLICT = "STALE_CONFLICT"


@dataclass(frozen=True, slots=True)
class PredecessorReference:
    """The exact durable identity of a live revision captured at intent build time.

    Trigger evidence is target-bound: this is the immutable snapshot the
    cutover later compares against current topology to detect staleness.
    """

    partition_id: str
    revision: int
    state: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "partition_id", _non_empty_text(self.partition_id, "partition_id"))
        if type(self.revision) is not int or isinstance(self.revision, bool) or self.revision < 1:
            raise RepairError("predecessor revision must be a positive integer")
        if self.state not in _ELIGIBLE_LIVE_STATES:
            raise RepairError(f"predecessor state {self.state!r} is not a live catalog state")

    def stable_dict(self) -> dict[str, Any]:
        return {"partition_id": self.partition_id, "revision": self.revision, "state": self.state}


@dataclass(frozen=True, slots=True)
class CoverageGapTrigger:
    """Immutable B04-style gap evidence: one or more exact non-empty gaps.

    Also represents "explicitly unavailable required support" when the sole
    gap equals the entire required support and no predecessor exists -- B04's
    own ``gaps_for`` naturally returns exactly that when nothing is covered,
    so no distinct trigger type is needed.
    """

    gaps: tuple[CoverageInterval, ...]

    def __post_init__(self) -> None:
        gaps = tuple(self.gaps)
        if not gaps:
            raise RepairIneligible("missing-support trigger requires at least one non-empty gap")
        for index, gap in enumerate(gaps):
            if not isinstance(gap, CoverageInterval):
                raise RepairError(f"gaps[{index}] must be a CoverageInterval")
            if not gap.start < gap.end:
                raise RepairIneligible(f"gaps[{index}] must be a non-degenerate half-open interval")
        ordered = tuple(sorted(gaps, key=lambda item: (item.start.epoch_ns, item.end.epoch_ns)))
        for left, right in zip(ordered, ordered[1:]):
            if left.end > right.start:
                raise RepairError("gaps must not overlap")
        object.__setattr__(self, "gaps", ordered)

    def stable_dict(self) -> dict[str, Any]:
        return {"kind": "coverage_gap", "gaps": [gap.stable_dict() for gap in self.gaps]}


@dataclass(frozen=True, slots=True)
class InvalidLiveRevisionTrigger:
    """Immutable A16 evidence that the current live revision is ``fail``/``invalid``."""

    predecessor: PredecessorReference
    assessment_signature: str
    assessment_status: str

    def __post_init__(self) -> None:
        if not isinstance(self.predecessor, PredecessorReference):
            raise RepairError("predecessor must be a PredecessorReference")
        if self.predecessor.state != "invalid":
            raise RepairIneligible(
                "invalid-live-revision trigger requires the captured predecessor state to be 'invalid'"
            )
        object.__setattr__(
            self, "assessment_signature", _non_empty_text(self.assessment_signature, "assessment_signature")
        )
        if self.assessment_status != "fail":
            raise RepairIneligible(
                "invalid-live-revision trigger requires an A16 'fail' assessment status"
            )
        object.__setattr__(self, "assessment_status", self.assessment_status)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "kind": "invalid_live_revision",
            "predecessor": self.predecessor.stable_dict(),
            "assessment_signature": self.assessment_signature,
            "assessment_status": self.assessment_status,
        }


RepairTrigger = CoverageGapTrigger | InvalidLiveRevisionTrigger


@dataclass(frozen=True, slots=True)
class RepairIntent:
    """One deterministic, partition-bounded, evidence-driven repair target.

    ``warn``/``degraded`` current state and a merely newer/different
    candidate source are never sufficient: construction only succeeds when
    ``trigger`` already establishes genuine eligibility under the frozen A10
    rules (enforced by :class:`CoverageGapTrigger` /
    :class:`InvalidLiveRevisionTrigger` themselves), and every field
    participates in ``intent_identity`` so a changed predecessor/trigger can
    never silently retarget an existing intent.
    """

    dataset_identity: DatasetIdentity
    partition_key: str
    required_support: CoverageInterval
    predecessor: PredecessorReference | None
    trigger: RepairTrigger
    semantics_version: str = REPAIR_SEMANTICS_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_identity, DatasetIdentity):
            raise RepairError("dataset_identity must be a DatasetIdentity")
        object.__setattr__(self, "partition_key", _non_empty_text(self.partition_key, "partition_key"))
        if not isinstance(self.required_support, CoverageInterval):
            raise RepairError("required_support must be a CoverageInterval")
        if not self.required_support.start < self.required_support.end:
            raise RepairError("required_support must be a non-degenerate half-open interval")
        if self.predecessor is not None and not isinstance(self.predecessor, PredecessorReference):
            raise RepairError("predecessor must be a PredecessorReference or None")
        if self.predecessor is not None and self.predecessor.state == "superseded":
            raise RepairError("a superseded row is history, not a repair predecessor")
        if not isinstance(self.trigger, (CoverageGapTrigger, InvalidLiveRevisionTrigger)):
            raise RepairError("trigger must be CoverageGapTrigger or InvalidLiveRevisionTrigger")
        if isinstance(self.trigger, CoverageGapTrigger):
            for index, gap in enumerate(self.trigger.gaps):
                if gap.start < self.required_support.start or gap.end > self.required_support.end:
                    raise RepairError(f"gaps[{index}] is not contained in required_support")
        else:
            if self.predecessor is None or self.predecessor != self.trigger.predecessor:
                raise RepairError(
                    "invalid-live-revision trigger predecessor must equal the intent's own predecessor"
                )
        if self.semantics_version != REPAIR_SEMANTICS_VERSION:
            raise RepairError("unsupported repair semantics_version")

    @property
    def outcome(self) -> RepairOutcome:
        return RepairOutcome.REPAIR_REQUIRED

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": REPAIR_INTENT_IDENTITY_DOMAIN,
            "dataset_identity": self.dataset_identity.stable_dict(),
            "partition_key": self.partition_key,
            "required_support": self.required_support.stable_dict(),
            "predecessor": None if self.predecessor is None else self.predecessor.stable_dict(),
            "trigger": self.trigger.stable_dict(),
            "semantics_version": self.semantics_version,
        }

    @property
    def intent_identity(self) -> str:
        return f"{REPAIR_INTENT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"

    def stable_dict(self) -> dict[str, Any]:
        return {"intent_identity": self.intent_identity, "canonical_payload": self.canonical_payload()}


@dataclass(frozen=True, slots=True)
class CandidateAttempt:
    """One immutable, content-addressed non-authoritative repair candidate.

    ``candidate_identity`` is a deterministic fingerprint of every field:
    semantically identical retries (same intent, same source/transform/
    durable evidence) compute the exact same identity and the exact same
    ``staging_partition_key``, so they naturally reuse/credit one candidate
    row (idempotent retry).  Different content/evidence computes a distinct
    identity and a distinct staging key, so it is a distinct, independently
    proven candidate that can never collide with -- or clobber -- another
    attempt's staging row.
    """

    intent_identity: str
    dataset_identity: DatasetIdentity
    natural_partition_key: str
    source_semantics_id: str
    mapping_id: str
    dataset_sha256: str
    partition_sha256: str
    content_sha256: str
    manifest_metadata_sha256: str
    code_ref: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "intent_identity", _non_empty_text(self.intent_identity, "intent_identity"))
        if not isinstance(self.dataset_identity, DatasetIdentity):
            raise RepairError("dataset_identity must be a DatasetIdentity")
        object.__setattr__(
            self, "natural_partition_key", _non_empty_text(self.natural_partition_key, "natural_partition_key")
        )
        object.__setattr__(
            self, "source_semantics_id", _non_empty_text(self.source_semantics_id, "source_semantics_id")
        )
        object.__setattr__(self, "mapping_id", _non_empty_text(self.mapping_id, "mapping_id"))
        object.__setattr__(self, "dataset_sha256", _sha256_hex(self.dataset_sha256, "dataset_sha256"))
        object.__setattr__(self, "partition_sha256", _sha256_hex(self.partition_sha256, "partition_sha256"))
        object.__setattr__(self, "content_sha256", _sha256_hex(self.content_sha256, "content_sha256"))
        object.__setattr__(
            self, "manifest_metadata_sha256", _sha256_hex(self.manifest_metadata_sha256, "manifest_metadata_sha256")
        )
        object.__setattr__(self, "code_ref", _non_empty_text(self.code_ref, "code_ref"))

    @property
    def outcome(self) -> RepairOutcome:
        return RepairOutcome.CANDIDATE_PENDING

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": CANDIDATE_ATTEMPT_IDENTITY_DOMAIN,
            "intent_identity": self.intent_identity,
            "dataset_identity": self.dataset_identity.stable_dict(),
            "natural_partition_key": self.natural_partition_key,
            "source_semantics_id": self.source_semantics_id,
            "mapping_id": self.mapping_id,
            "dataset_sha256": self.dataset_sha256,
            "partition_sha256": self.partition_sha256,
            "content_sha256": self.content_sha256,
            "manifest_metadata_sha256": self.manifest_metadata_sha256,
            "code_ref": self.code_ref,
        }

    @property
    def candidate_identity(self) -> str:
        return f"{CANDIDATE_ATTEMPT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"

    @property
    def staging_partition_key(self) -> str:
        """The physically/logically isolated natural-key value for this attempt.

        Includes every field of :meth:`canonical_payload` that can be known
        BEFORE the staged partition manifest is emitted: ``intent_identity``,
        ``natural_partition_key``, ``dataset_sha256``, ``source_semantics_id``,
        ``mapping_id``, ``content_sha256``, ``manifest_metadata_sha256`` and
        ``code_ref``. Two attempts that agree on all of these are the same
        evidence under the same lineage and source identity (vectors
        10/11/22): they may safely share one staging row. Two attempts that
        differ in ANY of them -- including distinct dataset lineage, distinct
        source/mapping identity, or distinct manifest metadata such as
        ``created_at``/``closed_at``/``producer`` -- get distinct staging
        keys and can never collide on, or clobber, each other's row.

        ``partition_sha256`` is deliberately the ONE candidate field excluded
        here, and only because it is impossible to include without literal
        self-reference: it is the hash of the staged partition MANIFEST
        DOCUMENT, and that document's own ``partition_key`` field must equal
        this very key -- so a key derived from a hash that already contains
        the key could never be computed. This is not a residual isolation
        gap: every input that actually DETERMINES ``partition_sha256`` for a
        fixed ``(dataset_identity, natural_partition_key, revision=1, state=
        'closed')`` is now bound here -- the physical bytes
        (``content_sha256``, which alone determines ``file_size_bytes``,
        ``row_count``, ``sha256`` and the observed exchange-timestamp bounds
        for a deterministic materializer), the code identity (``code_ref``),
        the lineage/source identity, AND the remaining group of
        manifest-defining fields the physical bytes do NOT determine --
        ``created_at``/``closed_at``/``producer``/``rel_path`` -- via
        ``manifest_metadata_sha256`` (see :func:`manifest_metadata_fingerprint`).
        Two attempts that agree on every field here therefore necessarily
        produce a byte-identical staged partition manifest, so
        ``partition_sha256`` is fully determined even though it cannot
        itself appear in this key.

        A different ``partition_key`` value is a different row family under
        ``partitions_one_live``, and because it is deliberately a *sub-path*
        of the natural key, its ``rel_path`` already satisfies
        ``rel_path_inside_partition`` under the natural key too -- so
        promotion never has to move the physical artifact.
        """

        payload = {
            "identity_domain": f"{CANDIDATE_ATTEMPT_IDENTITY_DOMAIN}-staging-key",
            "intent_identity": self.intent_identity,
            "natural_partition_key": self.natural_partition_key,
            "dataset_sha256": self.dataset_sha256,
            "source_semantics_id": self.source_semantics_id,
            "mapping_id": self.mapping_id,
            "content_sha256": self.content_sha256,
            "manifest_metadata_sha256": self.manifest_metadata_sha256,
            "code_ref": self.code_ref,
        }
        digest = _canonical_fingerprint(payload)
        return f"{self.natural_partition_key}/repair={digest}"

    def stable_dict(self) -> dict[str, Any]:
        return {
            "candidate_identity": self.candidate_identity,
            "staging_partition_key": self.staging_partition_key,
            "canonical_payload": self.canonical_payload(),
        }


@dataclass(frozen=True, slots=True)
class CandidateProof:
    """Durable evidence binding one staged candidate to its S13/A16/S14 proof.

    Every document here must be a WELL-FORMED, schema-valid durable manifest
    (verified against the same frozen validators ``quant_platform.data``
    already uses to load manifests off disk), and every declared hash must
    be the ACTUAL canonical hash of its own document -- both independently
    recomputed here, never merely format-checked. ``cutover`` additionally
    re-derives ``assessment_signature``/``assessment_status``/
    ``canonical_content_hash_v1`` from the durable ``catalog.quality_reports``
    row itself (the same credited A16 selection machinery S14 already uses),
    and re-folds ``coverage_documents`` through the credited B04
    reconstruction, so nothing here is trusted as caller-asserted fact: it is
    proof to be verified, not a claim to be recorded.
    """

    dataset_document: Mapping[str, Any]
    dataset_sha256: str
    partition_document: Mapping[str, Any]
    partition_sha256: str
    coverage_documents: tuple[Mapping[str, Any], ...]
    canonical_content_hash_v1: str
    assessment_signature: str
    assessment_status: str
    eligibility_state: str
    repair_code_ref: str
    expected_profile: str
    expected_check_suite: str

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_document, Mapping) or not self.dataset_document:
            raise RepairError("dataset_document must be a non-empty mapping")
        try:
            _validate_dataset_document(self.dataset_document)
        except ManifestValidationError as exc:
            raise RepairError(f"dataset_document is not a valid durable manifest: {exc}") from exc
        object.__setattr__(self, "dataset_sha256", _sha256_hex(self.dataset_sha256, "dataset_sha256"))
        if _canonical_fingerprint(self.dataset_document) != self.dataset_sha256:
            raise RepairError("dataset_sha256 is not the actual canonical hash of dataset_document")

        if not isinstance(self.partition_document, Mapping) or not self.partition_document:
            raise RepairError("partition_document must be a non-empty mapping")
        try:
            _validate_partition_document(self.partition_document)
        except ManifestValidationError as exc:
            raise RepairError(f"partition_document is not a valid durable manifest: {exc}") from exc
        object.__setattr__(self, "partition_sha256", _sha256_hex(self.partition_sha256, "partition_sha256"))
        if _canonical_fingerprint(self.partition_document) != self.partition_sha256:
            raise RepairError("partition_sha256 is not the actual canonical hash of partition_document")

        coverage_documents = tuple(self.coverage_documents)
        if not coverage_documents:
            raise RepairError("coverage_documents must be non-empty")
        for index, document in enumerate(coverage_documents):
            if not isinstance(document, Mapping) or not document:
                raise RepairError(f"coverage_documents[{index}] must be a non-empty mapping")
            try:
                _validate_coverage_document(document)
            except ManifestValidationError as exc:
                raise RepairError(f"coverage_documents[{index}] is not a valid durable manifest: {exc}") from exc
        object.__setattr__(self, "coverage_documents", coverage_documents)

        object.__setattr__(
            self, "canonical_content_hash_v1", _sha256_hex(self.canonical_content_hash_v1, "canonical_content_hash_v1")
        )
        object.__setattr__(
            self, "assessment_signature", _non_empty_text(self.assessment_signature, "assessment_signature")
        )
        if self.assessment_status not in {"pass", "warn"}:
            raise RepairIneligible("candidate proof requires an A16 'pass' or 'warn' assessment status")
        if self.eligibility_state not in _COVERING_STATES:
            raise RepairIneligible("candidate proof requires an S14 'valid' or 'degraded' eligibility state")
        object.__setattr__(self, "repair_code_ref", _non_empty_text(self.repair_code_ref, "repair_code_ref"))
        object.__setattr__(self, "expected_profile", _non_empty_text(self.expected_profile, "expected_profile"))
        object.__setattr__(
            self, "expected_check_suite", _non_empty_text(self.expected_check_suite, "expected_check_suite")
        )

    def coverage_ids_and_assertion_ids(self) -> tuple[list[str], list[str]]:
        """Coverage/assertion identity DERIVED from the documents themselves
        (never caller-asserted), matching :meth:`PublicationEligibilityBridge.publish`.
        """

        ids = [str(document.get("coverage_id")) for document in self.coverage_documents]
        assertion_ids = [
            str(assertion.get("assertion_id"))
            for document in self.coverage_documents
            for assertion in (document.get("assertions") or ())
        ]
        return ids, assertion_ids

    def coverage_sha256(self) -> tuple[str, ...]:
        """The actual canonical hash of each coverage document, DERIVED here
        rather than caller-asserted -- matches what S14 durably recorded
        for these exact documents when it computed the same hash off the
        persisted manifest bytes.
        """

        return tuple(_canonical_fingerprint(document) for document in self.coverage_documents)


@dataclass(frozen=True, slots=True)
class RepairCutoverResult:
    """Immutable convergence provenance for one cutover attempt.

    ``status`` is restricted to the three outcomes a cutover transaction can
    itself decide: :attr:`RepairOutcome.CONVERGED`,
    :attr:`RepairOutcome.ALREADY_SATISFIED` or
    :attr:`RepairOutcome.STALE_CONFLICT`.  On ``CONVERGED`` every field in
    frozen contract item 14 is populated; on the other two outcomes only the
    fields the topology comparison itself could establish are.
    """

    status: RepairOutcome
    intent_identity: str
    dataset_id: str
    natural_partition_key: str
    required_support: CoverageInterval
    predecessor: PredecessorReference | None
    candidate_identity: str | None
    live_partition_id: str | None
    live_revision: int | None
    live_state: str | None
    assessment_signature: str | None = None
    assessment_status: str | None = None
    eligibility_state: str | None = None
    repair_code_ref: str | None = None
    promoted_manifest_sha256: str | None = None
    provenance_artifact_id: str | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        if self.status not in (
            RepairOutcome.CONVERGED,
            RepairOutcome.ALREADY_SATISFIED,
            RepairOutcome.STALE_CONFLICT,
        ):
            raise RepairError("RepairCutoverResult.status must be CONVERGED, ALREADY_SATISFIED or STALE_CONFLICT")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "intent_identity": self.intent_identity,
            "dataset_id": self.dataset_id,
            "natural_partition_key": self.natural_partition_key,
            "required_support": self.required_support.stable_dict(),
            "predecessor": None if self.predecessor is None else self.predecessor.stable_dict(),
            "candidate_identity": self.candidate_identity,
            "live_partition_id": self.live_partition_id,
            "live_revision": self.live_revision,
            "live_state": self.live_state,
            "assessment_signature": self.assessment_signature,
            "assessment_status": self.assessment_status,
            "eligibility_state": self.eligibility_state,
            "repair_code_ref": self.repair_code_ref,
            "promoted_manifest_sha256": self.promoted_manifest_sha256,
            "provenance_artifact_id": self.provenance_artifact_id,
            "detail": self.detail,
        }


def manifest_metadata_fingerprint(
    *, created_at: str, closed_at: str, producer: str, rel_path_suffix: str
) -> str:
    """The deterministic hash of a staged partition manifest's
    ``created_at``/``closed_at``/``producer``/rel-path-suffix fields.

    These are the only ``partition-manifest-v1`` fields NOT already
    determined by physical content (``content_sha256``) or code/lineage
    identity: a deterministic materializer computes ``file_size_bytes``,
    ``row_count``, ``sha256`` and the observed exchange-timestamp bounds
    purely from the physical bytes. ``created_at``/``closed_at``/``producer``
    and the manifest's ``rel_path`` are all chosen by the caller at seal
    time -- but ``rel_path`` itself is required (by the credited
    ``rel_path_inside_partition`` constraint) to be a sub-path of the
    candidate's own :attr:`CandidateAttempt.staging_partition_key`, so only
    the portion of ``rel_path`` AFTER that prefix -- ``rel_path_suffix`` --
    is an independent choice knowable before the staging key exists; passing
    the full ``rel_path`` here would be circular. Callers compute this
    BEFORE the staged manifest is emitted, and pass it as
    :attr:`CandidateAttempt.manifest_metadata_sha256` so it can participate
    in :attr:`CandidateAttempt.staging_partition_key` -- closing the
    residual staging-isolation gap: two attempts identical in content, code
    and lineage but sealed under a different rel-path suffix (e.g. a
    different staged filename) must never collide.
    """

    payload = {
        "identity_domain": "a10-candidate-attempt-manifest-metadata-v1",
        "created_at": _non_empty_text(created_at, "created_at"),
        "closed_at": _non_empty_text(closed_at, "closed_at"),
        "producer": _non_empty_text(producer, "producer"),
        "rel_path_suffix": _non_empty_text(rel_path_suffix, "rel_path_suffix"),
    }
    return _canonical_fingerprint(payload)


def provenance_rel_path_for(intent: RepairIntent, candidate: CandidateAttempt) -> str:
    """The deterministic, dataset-root-relative path of one (intent,
    candidate) pair's immutable convergence provenance record.

    Keyed by BOTH ``intent_identity`` AND ``candidate_identity`` -- never by
    intent alone. ``cutover`` persists this document BEFORE its database
    mutation commits (so it survives as durable evidence even if the
    transaction later fails), which means the path must already be unique
    per candidate: were it keyed by intent alone, a failed attempt by one
    candidate would occupy the same path a LATER, different candidate for
    the same intent must also write to, forcing either a spurious conflict
    or an overwrite of what the module's own contract calls immutable
    evidence. Keyed per candidate, a failed attempt's document is simply
    orphaned at its own path -- never touched, never in anyone's way.
    """

    digest = _canonical_fingerprint({
        "identity_domain": "a10-repair-provenance-v1",
        "intent_identity": intent.intent_identity,
        "candidate_identity": candidate.candidate_identity,
    })
    return f"_repair/{digest}.json"


def _provenance_paths(
    proof: CandidateProof, intent: RepairIntent, candidate: CandidateAttempt,
) -> tuple[str, str]:
    """Return ``(dataset-root-relative path, storage-root-relative catalog
    rel_path)`` for one (intent, candidate) pair's provenance record.
    """

    local_rel_path = provenance_rel_path_for(intent, candidate)
    dataset_rel_root = _non_empty_text(
        proof.dataset_document.get("rel_root"), "proof.dataset_document.rel_root"
    )
    return local_rel_path, f"{dataset_rel_root}/{local_rel_path}"


class RepairCutoverCatalog:
    """The one dedicated A10 atomic compare-and-cutover seam.

    Deliberately does not seal, certify or assess the candidate: by the time
    ``cutover`` is called, the candidate must already be a ``valid``/
    ``degraded`` row under its own isolated ``staging_partition_key``,
    proven through the ordinary, unmodified S13/A16/S14 machinery. This
    method re-verifies that proof is exactly bound to ``candidate`` and
    ``intent``, independently re-folds declared coverage, then -- in one
    commit -- supersedes the predecessor, re-emits a natural-identity
    manifest over the unmoved physical artifact, promotes the candidate row,
    and records immutable convergence provenance.
    """

    def __init__(self, connection: Any):
        self.connection = connection

    def cutover(
        self,
        *,
        intent: RepairIntent,
        candidate: CandidateAttempt,
        proof: CandidateProof,
        dataset_root: Path,
        promoted_manifest_path: Path,
    ) -> RepairCutoverResult:
        if not isinstance(intent, RepairIntent):
            raise RepairError("intent must be a RepairIntent")
        if not isinstance(candidate, CandidateAttempt):
            raise RepairError("candidate must be a CandidateAttempt")
        if not isinstance(proof, CandidateProof):
            raise RepairError("proof must be a CandidateProof")
        if candidate.intent_identity != intent.intent_identity:
            raise RepairError("candidate does not target this repair intent")
        if candidate.dataset_identity != intent.dataset_identity:
            raise RepairError("candidate.dataset_identity does not match the repair intent's dataset")
        if candidate.natural_partition_key != intent.partition_key:
            raise RepairError("candidate.natural_partition_key does not match the repair intent's partition_key")
        if proof.dataset_sha256 != candidate.dataset_sha256:
            raise RepairError("proof.dataset_sha256 does not match the candidate's declared evidence")
        if proof.partition_sha256 != candidate.partition_sha256:
            raise RepairError("proof.partition_sha256 does not match the candidate's declared evidence")
        if proof.partition_document.get("sha256") != candidate.content_sha256:
            raise RepairError("proof.partition_document physical hash does not match the candidate's declared evidence")
        if proof.partition_document.get("partition_key") != candidate.staging_partition_key:
            raise RepairError("proof.partition_document does not target the candidate's own staging_partition_key")
        if proof.partition_document.get("revision") != 1:
            raise RepairError("proof.partition_document must describe the staged candidate's revision 1")
        if proof.partition_document.get("code_ref") != candidate.code_ref:
            raise RepairError("proof.partition_document code_ref does not match the candidate's declared code identity")
        if proof.repair_code_ref != candidate.code_ref:
            raise RepairError("proof.repair_code_ref does not match the candidate's declared code identity")
        proof_rel_path = proof.partition_document.get("rel_path") or ""
        staging_prefix = f"{candidate.staging_partition_key}/"
        if not proof_rel_path.startswith(staging_prefix):
            raise RepairError(
                "proof.partition_document rel_path is not a sub-path of the candidate's own staging_partition_key"
            )
        if candidate.manifest_metadata_sha256 != manifest_metadata_fingerprint(
            created_at=proof.partition_document.get("created_at"),
            closed_at=proof.partition_document.get("closed_at"),
            producer=proof.partition_document.get("producer"),
            rel_path_suffix=proof_rel_path[len(staging_prefix):],
        ):
            raise RepairError(
                "candidate.manifest_metadata_sha256 does not match the durable partition manifest's own metadata"
            )
        for index, coverage_document in enumerate(proof.coverage_documents):
            acquisition = coverage_document.get("acquisition") or {}
            if acquisition.get("source_semantics") != candidate.source_semantics_id:
                raise RepairError(
                    f"proof.coverage_documents[{index}] source_semantics does not match the candidate's declared evidence"
                )
            if acquisition.get("mapping") != candidate.mapping_id:
                raise RepairError(
                    f"proof.coverage_documents[{index}] mapping does not match the candidate's declared evidence"
                )

        identity = intent.dataset_identity
        dataset_root = Path(dataset_root)
        try:
            with self.connection.cursor() as cursor:
                dataset_id = self._resolve_dataset(cursor, identity)
                natural = self._lock_topology(cursor, dataset_id, intent.partition_key)
                current_live = _live_row(natural)

                verdict, detail = self._authority_verdict(
                    cursor, intent, candidate, proof, dataset_root, dataset_id, current_live,
                )
                if verdict is not None:
                    self.connection.commit()
                    return _result(
                        verdict, intent, dataset_id, candidate_identity=(
                            candidate.candidate_identity if verdict is RepairOutcome.ALREADY_SATISFIED else None
                        ),
                        live=current_live, detail=detail,
                    )

                staging = self._lock_topology(cursor, dataset_id, candidate.staging_partition_key)
                staged = _single_row(staging, "candidate staging partition")
                if str(staged[1]) != str(dataset_id):
                    raise RepairCutoverRefusal("staged candidate belongs to a different dataset")
                if staged[4] not in _COVERING_STATES:
                    raise RepairCutoverRefusal(
                        f"candidate is not proven: staging state is {staged[4]!r}, expected valid/degraded"
                    )
                if _text(staged[7]) != candidate.content_sha256:
                    raise RepairCutoverRefusal("staged content_sha256 does not match the candidate's declared evidence")
                if _text(staged[8]) != proof.partition_sha256:
                    raise RepairCutoverRefusal("staged manifest_sha256 does not match the proven candidate manifest")
                if proof.eligibility_state != staged[4]:
                    raise RepairCutoverRefusal(
                        "proof.eligibility_state does not match the staged candidate's actual catalog state"
                    )

                self._verify_durable_assessment(cursor, proof, staged, identity)

                folded_start, folded_end = _verify_candidate_coverage(candidate, proof, intent)

                next_revision = 1 if intent.predecessor is None else intent.predecessor.revision + 1

                # Durable evidence is written before any DB mutation: on
                # failure it is orphaned, isolated, non-authoritative
                # diagnostic evidence -- never a half-switch.
                promoted = _emit_promoted_manifest(
                    proof=proof, intent=intent, next_revision=next_revision,
                    dataset_root=dataset_root, manifest_output_path=Path(promoted_manifest_path),
                    staged_rel_path=staged[10],
                )
                # Relative to dataset_root for the physical write; relative to
                # the storage root (dataset_root's own parent tree) for the
                # catalog.artifacts row, which is keyed across all datasets
                # sharing one storage root. Keyed by (intent, candidate): see
                # provenance_rel_path_for for why intent alone is not enough.
                provenance_local_rel_path, provenance_rel_path = _provenance_paths(proof, intent, candidate)
                provenance_payload = _provenance_payload(
                    intent=intent, candidate=candidate, proof=proof,
                    folded_start=folded_start, folded_end=folded_end, next_revision=next_revision,
                    promoted_partition_id=str(staged[0]), promoted_state=staged[4],
                    promoted_manifest_sha256=promoted.manifest_sha256,
                )
                provenance_emission = _persist_manifest(
                    dataset_root / provenance_local_rel_path, provenance_payload,
                )

                if intent.predecessor is not None:
                    cursor.execute(
                        """
                        UPDATE catalog.partitions
                           SET state = 'superseded'
                         WHERE partition_id = %s AND state = %s
                        RETURNING partition_id::text
                        """,
                        (intent.predecessor.partition_id, intent.predecessor.state),
                    )
                    if cursor.fetchone() is None:
                        raise RepairCutoverRefusal("predecessor could not be superseded atomically")

                cursor.execute(
                    """
                    UPDATE catalog.partitions
                       SET partition_key = %s, revision = %s, manifest_sha256 = %s
                     WHERE partition_id = %s AND state = %s
                    RETURNING partition_id::text
                    """,
                    (intent.partition_key, next_revision, promoted.manifest_sha256, staged[0], staged[4]),
                )
                if cursor.fetchone() is None:
                    raise RepairCutoverRefusal("candidate could not be promoted atomically")

                cursor.execute(
                    """
                    INSERT INTO catalog.artifacts
                        (kind, storage_root_id, rel_path, content_sha256, byte_size, produced_by, code_ref, dataset_id, manifest_sha256)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (storage_root_id, rel_path) DO NOTHING
                    RETURNING artifact_id::text
                    """,
                    (
                        REPAIR_CONVERGENCE_ARTIFACT_KIND, staged[9], provenance_rel_path,
                        provenance_emission.manifest_sha256, len(provenance_emission.persisted_bytes),
                        candidate.candidate_identity, proof.repair_code_ref, dataset_id,
                        provenance_emission.manifest_sha256,
                    ),
                )
                provenance_row_id = cursor.fetchone()
                if provenance_row_id is None:
                    # ON CONFLICT DO NOTHING fired: a row already exists at
                    # this path (a prior attempt, e.g. an idempotent retry
                    # that reached this point before). It is accepted as
                    # OUR provenance record only if its content genuinely
                    # matches what we intended to write -- never merely
                    # because a row exists at the same path.
                    cursor.execute(
                        "SELECT artifact_id::text, content_sha256, manifest_sha256, produced_by, code_ref, dataset_id::text "
                        "FROM catalog.artifacts WHERE storage_root_id=%s AND rel_path=%s",
                        (staged[9], provenance_rel_path),
                    )
                    existing = cursor.fetchone()
                    if existing is not None and (
                        _text(existing[1]) != provenance_emission.manifest_sha256
                        or _text(existing[2]) != provenance_emission.manifest_sha256
                        or existing[3] != candidate.candidate_identity
                        or existing[4] != proof.repair_code_ref
                        or str(existing[5]) != str(dataset_id)
                    ):
                        raise RepairCutoverRefusal(
                            "an unrelated convergence provenance record already occupies this path"
                        )
                    provenance_row_id = None if existing is None else (existing[0],)
                if provenance_row_id is None:
                    raise RepairCutoverRefusal("convergence provenance could not be durably recorded")

                final = self._lock_topology(cursor, dataset_id, intent.partition_key)
                promoted_row = _live_row(final)
                if (
                    promoted_row is None
                    or str(promoted_row[0]) != str(staged[0])
                    or int(promoted_row[3]) != next_revision
                    or promoted_row[4] != staged[4]
                    or _text(promoted_row[8]) != promoted.manifest_sha256
                ):
                    raise RepairCutoverRefusal("post-cutover topology verification failed")
                if intent.predecessor is not None:
                    predecessor_row = next(
                        (row for row in final if str(row[0]) == intent.predecessor.partition_id), None
                    )
                    if predecessor_row is None or predecessor_row[4] != "superseded":
                        raise RepairCutoverRefusal("predecessor did not verify as superseded post-cutover")

            self.connection.commit()
            return _result(
                RepairOutcome.CONVERGED, intent, dataset_id, candidate_identity=candidate.candidate_identity,
                live=promoted_row, detail="",
                assessment_signature=proof.assessment_signature, assessment_status=proof.assessment_status,
                eligibility_state=proof.eligibility_state, repair_code_ref=proof.repair_code_ref,
                promoted_manifest_sha256=promoted.manifest_sha256, provenance_artifact_id=str(provenance_row_id[0]),
            )
        except Exception:
            self.connection.rollback()
            raise

    def _authority_verdict(
        self, cursor: Any, intent: RepairIntent, candidate: CandidateAttempt, proof: CandidateProof,
        dataset_root: Path, dataset_id: str, current_live: Any,
    ) -> tuple[RepairOutcome | None, str]:
        """Decide ALREADY_SATISFIED / STALE_CONFLICT / proceed (``None``).

        Coverage completeness never substitutes for exact identity: the only
        way a mismatch resolves to ``ALREADY_SATISFIED`` rather than
        ``STALE_CONFLICT`` is durable provenance proof that THIS candidate is
        the one that already converged here -- an idempotent retry of the
        winner, never a loser inferring success from someone else's coverage,
        and never a candidate that DID win at some point in the past but has
        since been legitimately superseded by a further repair.
        """

        predecessor = intent.predecessor
        if predecessor is None:
            if current_live is None:
                return None, ""
            return self._resolve_mismatch(cursor, candidate, proof, intent, dataset_root, dataset_id, current_live)

        if current_live is None:
            return RepairOutcome.STALE_CONFLICT, "captured predecessor no longer exists"
        if (
            str(current_live[0]) == predecessor.partition_id
            and int(current_live[3]) == predecessor.revision
            and current_live[4] == predecessor.state
        ):
            return None, ""
        return self._resolve_mismatch(cursor, candidate, proof, intent, dataset_root, dataset_id, current_live)

    def _resolve_mismatch(
        self, cursor: Any, candidate: CandidateAttempt, proof: CandidateProof, intent: RepairIntent,
        dataset_root: Path, dataset_id: str, current_live: Any,
    ) -> tuple[RepairOutcome, str]:
        """ALREADY_SATISFIED requires more than "this candidate converged
        SOMEWHERE, SOMETIME": the durable provenance record this candidate
        itself produced (at its own (intent, candidate)-keyed path -- see
        :func:`provenance_rel_path_for`) must still describe EXACTLY the
        current live row: same promoted partition id, revision, state and
        manifest hash. A candidate that once won but was since legitimately
        superseded by a further repair no longer satisfies this, and
        correctly resolves to STALE_CONFLICT, not a stale ALREADY_SATISFIED.
        """

        local_rel_path, catalog_rel_path = _provenance_paths(proof, intent, candidate)
        cursor.execute(
            "SELECT produced_by FROM catalog.artifacts WHERE storage_root_id=%s AND rel_path=%s AND kind=%s AND dataset_id=%s",
            (current_live[9], catalog_rel_path, REPAIR_CONVERGENCE_ARTIFACT_KIND, dataset_id),
        )
        row = cursor.fetchone()
        if row is None or row[0] != candidate.candidate_identity:
            return (
                RepairOutcome.STALE_CONFLICT,
                "current live revision no longer matches the captured predecessor/trigger authority",
            )

        provenance_path = Path(dataset_root) / local_rel_path
        if not provenance_path.is_file():
            raise RepairCutoverRefusal(
                "convergence provenance is durably recorded in the catalog but its document is missing"
            )
        try:
            provenance_document = json.loads(provenance_path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RepairCutoverRefusal("convergence provenance document could not be read") from exc

        if (
            provenance_document.get("intent_identity") != intent.intent_identity
            or provenance_document.get("candidate_identity") != candidate.candidate_identity
            or provenance_document.get("promoted_partition_id") != str(current_live[0])
            or provenance_document.get("resulting_revision") != int(current_live[3])
            or provenance_document.get("promoted_state") != current_live[4]
            or _text(provenance_document.get("promoted_manifest_sha256")) != _text(current_live[8])
        ):
            return (
                RepairOutcome.STALE_CONFLICT,
                "this candidate previously converged here, but the natural key has since moved past it",
            )
        return (
            RepairOutcome.ALREADY_SATISFIED,
            "this exact candidate already converged here (idempotent retry of the winner)",
        )

    @staticmethod
    def _resolve_dataset(cursor: Any, identity: DatasetIdentity) -> str:
        cursor.execute(
            """
            SELECT dataset_id::text
              FROM catalog.datasets
             WHERE layer=%s AND kind=%s AND venue=%s
               AND instrument=%s AND schema_id=%s
               AND feature_set_def_id IS NULL
             FOR UPDATE
            """,
            (identity.layer, identity.dataset_kind, identity.venue, identity.instrument, identity.record_schema_id),
        )
        rows = cursor.fetchall()
        if len(rows) != 1:
            raise RepairCutoverRefusal("dataset natural identity is missing or duplicated")
        return str(rows[0][0])

    @staticmethod
    def _lock_topology(cursor: Any, dataset_id: str, partition_key: str):
        cursor.execute(
            """
            SELECT partition_id::text, dataset_id::text, partition_key,
                   revision, state, ts_start, ts_end,
                   content_sha256, manifest_sha256, storage_root_id, rel_path
              FROM catalog.partitions
             WHERE dataset_id=%s AND partition_key=%s
             ORDER BY revision
             FOR UPDATE
            """,
            (dataset_id, partition_key),
        )
        return cursor.fetchall()

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

    def _verify_durable_assessment(
        self, cursor: Any, proof: CandidateProof, staged: Any, identity: DatasetIdentity,
    ) -> None:
        """Independently re-derive the staged candidate's A16 assessment from
        ``catalog.quality_reports`` -- the SAME credited selection machinery
        (:func:`select_current_quality_assessment`) S14 itself uses to
        compute ``PublicationEligibilityResult.certification_signature`` /
        ``.certification_status`` -- and refuse unless ``proof`` matches that
        durable truth exactly. Nothing about assessment status, signature or
        ``canonical_content_hash_v1`` is ever accepted merely because the
        caller asserted it.
        """

        reports = self._quality_reports(cursor, staged[0], proof.expected_check_suite)
        coverage_ids, assertion_ids = proof.coverage_ids_and_assertion_ids()
        # select_current_quality_assessment / current_partition_report only
        # ever read index 11 (content_sha256) of their `target` row; this
        # minimal synthetic tuple carries the staged row's REAL, already-
        # locked content_sha256 at that exact index -- it is not a stand-in
        # for the rest of that row's shape, only for the one field read.
        synthetic_target = tuple([None] * 11 + [staged[7]])
        try:
            selected = select_current_quality_assessment(
                reports,
                expected_profile=proof.expected_profile,
                expected_check_suite=proof.expected_check_suite,
                identity=identity,
                partition=proof.partition_document,
                dataset_sha256=proof.dataset_sha256,
                partition_sha256=proof.partition_sha256,
                coverage_ids=coverage_ids,
                assertion_ids=assertion_ids,
                coverage_sha256=proof.coverage_sha256(),
                target=synthetic_target,
            )
        except QualityLifecycleRefusal as exc:
            raise RepairCutoverRefusal(f"durable A16 assessment could not be re-derived: {exc}") from exc
        if selected.status != proof.assessment_status:
            raise RepairCutoverRefusal("proof.assessment_status does not match the durable A16 assessment")
        if selected.signature != proof.assessment_signature:
            raise RepairCutoverRefusal("proof.assessment_signature does not match the durable A16 assessment")
        durable_canonical_hash = _text(selected.metrics.get("canonical_content_hash_v1")) if isinstance(selected.metrics, Mapping) else None
        if durable_canonical_hash != proof.canonical_content_hash_v1:
            raise RepairCutoverRefusal(
                "proof.canonical_content_hash_v1 does not match the durably recorded A16 metrics"
            )


def _verify_candidate_coverage(
    candidate: CandidateAttempt, proof: CandidateProof, intent: RepairIntent,
) -> tuple[Instant, Instant]:
    """Independently re-fold declared coverage through the credited B04
    reconstruction and prove it closes every targeted gap (contract item 8).
    """

    result, violations = reconstruct_catalog_coverage(proof.coverage_documents, (proof.partition_document,))
    if violations:
        raise RepairCutoverRefusal(
            "candidate coverage evidence has violations: " + ", ".join(item.code for item in violations)
        )
    key = (candidate.staging_partition_key, 1)
    if key not in result:
        raise RepairCutoverRefusal("candidate coverage does not resolve to one complete declared interval")
    start, end = result[key]
    if start > intent.required_support.start or end < intent.required_support.end:
        raise RepairCutoverRefusal("candidate coverage does not close every targeted gap; a target gap remains")
    return start, end


def _emit_promoted_manifest(
    *,
    proof: CandidateProof,
    intent: RepairIntent,
    next_revision: int,
    dataset_root: Path,
    manifest_output_path: Path,
    staged_rel_path: str,
) -> ManifestEmission:
    """Re-declare natural identity over the same, unmoved physical artifact.

    ``staged_rel_path`` is a sub-path of ``intent.partition_key`` by
    construction (see :attr:`CandidateAttempt.staging_partition_key`), so it
    already satisfies ``rel_path_inside_partition`` under the natural key:
    no artifact copy is needed, only a new manifest.
    """

    partition = proof.partition_document
    materialization = ParquetMaterialization(
        path=(dataset_root / staged_rel_path),
        dataset_identity=intent.dataset_identity,
        file_size_bytes=partition["file_size_bytes"],
        row_count=partition["row_count"],
        sha256=partition["sha256"],
        canonical_content_hash_v1=proof.canonical_content_hash_v1,
        first_exchange_ts=None if partition.get("first_exchange_ts") is None else Instant.parse(partition["first_exchange_ts"]),
        last_exchange_ts=None if partition.get("last_exchange_ts") is None else Instant.parse(partition["last_exchange_ts"]),
    )
    return emit_partition_manifest(
        manifest_output_path, materialization,
        dataset_identity=intent.dataset_identity,
        dataset_root=dataset_root,
        partition_key=intent.partition_key,
        revision=next_revision,
        rel_path=staged_rel_path,
        created_at=partition["created_at"],
        closed_at=partition["closed_at"],
        producer=partition["producer"],
        code_ref=partition["code_ref"],
    )


def _provenance_payload(
    *, intent: RepairIntent, candidate: CandidateAttempt, proof: CandidateProof,
    folded_start: Instant, folded_end: Instant, next_revision: int,
    promoted_partition_id: str, promoted_state: str, promoted_manifest_sha256: str,
) -> dict[str, Any]:
    return {
        "schema_version": "a10-repair-convergence-v1",
        "repair_semantics_version": REPAIR_SEMANTICS_VERSION,
        "intent": intent.canonical_payload(),
        "intent_identity": intent.intent_identity,
        "candidate": candidate.canonical_payload(),
        "candidate_identity": candidate.candidate_identity,
        "repaired_coverage": {"start": folded_start.isoformat(), "end": folded_end.isoformat()},
        "resulting_revision": next_revision,
        "promoted_partition_id": promoted_partition_id,
        "promoted_state": promoted_state,
        "promoted_manifest_sha256": promoted_manifest_sha256,
        "assessment_signature": proof.assessment_signature,
        "assessment_status": proof.assessment_status,
        "eligibility_state": proof.eligibility_state,
        "repair_code_ref": proof.repair_code_ref,
    }


def _live_row(rows: Any) -> Any | None:
    live = [row for row in rows if row[4] != "superseded"]
    if len(live) > 1:
        raise RepairCutoverRefusal("partition topology has more than one live revision")
    return live[0] if live else None


def _single_row(rows: Any, what: str) -> Any:
    if len(rows) != 1:
        raise RepairCutoverRefusal(f"{what} must resolve to exactly one row")
    return rows[0]


def _result(
    status: RepairOutcome,
    intent: RepairIntent,
    dataset_id: str,
    *,
    candidate_identity: str | None,
    live: Any,
    detail: str,
    assessment_signature: str | None = None,
    assessment_status: str | None = None,
    eligibility_state: str | None = None,
    repair_code_ref: str | None = None,
    promoted_manifest_sha256: str | None = None,
    provenance_artifact_id: str | None = None,
) -> RepairCutoverResult:
    return RepairCutoverResult(
        status=status,
        intent_identity=intent.intent_identity,
        dataset_id=dataset_id,
        natural_partition_key=intent.partition_key,
        required_support=intent.required_support,
        predecessor=intent.predecessor,
        candidate_identity=candidate_identity,
        live_partition_id=None if live is None else str(live[0]),
        live_revision=None if live is None else int(live[3]),
        live_state=None if live is None else live[4],
        assessment_signature=assessment_signature,
        assessment_status=assessment_status,
        eligibility_state=eligibility_state,
        repair_code_ref=repair_code_ref,
        promoted_manifest_sha256=promoted_manifest_sha256,
        provenance_artifact_id=provenance_artifact_id,
        detail=detail,
    )


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RepairError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise RepairError(f"{field_name} must not contain control characters")
    return text


def _sha256_hex(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
        raise RepairError(f"{field_name} must be 64 lowercase hex characters")
    return value


def _text(value: Any) -> str | None:
    return None if value is None else str(value).strip()


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


__all__ = [
    "CANDIDATE_ATTEMPT_IDENTITY_DOMAIN",
    "REPAIR_CONVERGENCE_ARTIFACT_KIND",
    "REPAIR_INTENT_IDENTITY_DOMAIN",
    "REPAIR_SEMANTICS_VERSION",
    "CandidateAttempt",
    "CandidateProof",
    "CoverageGapTrigger",
    "InvalidLiveRevisionTrigger",
    "PredecessorReference",
    "RepairCutoverCatalog",
    "RepairCutoverRefusal",
    "RepairCutoverResult",
    "RepairError",
    "RepairIneligible",
    "RepairIntent",
    "RepairOutcome",
    "RepairTrigger",
    "manifest_metadata_fingerprint",
    "provenance_rel_path_for",
]
