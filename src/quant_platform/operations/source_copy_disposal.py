"""Pure decision seam for disposing of one physical copy of a raw source archive.

ADR-0049 Amendment 1 (issue #423) lets one physical copy of a provider-native
source archive be removed only while another independent, freshly re-hashed copy
remains.  This module evaluates that rule from caller-supplied evidence.  It does
not open storage, query a catalog, persist audit rows or delete bytes, and it
never changes :func:`quant_platform.operations.retention.evaluate_retention_deletion`,
which keeps refusing ``UNIQUE_SOURCE`` candidates.

The decision uses the K09 audit shape and the K09 decision-identity form so it
fits ``catalog.deletion_audit_records`` unchanged; the ``decision_kind`` and the
policy identity keep its identities apart from a K09 decision's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import re
from typing import Any

from ..data.models import Instant
from quant_platform.canonical import canonical_bytes
from .protection import (
    ArtifactReadOutcome,
    ProtectionAssessment,
    ProtectionState,
)
from .relocation import RelocationPhase
from .retention import (
    RETENTION_DELETION_DECISION_IDENTITY_DOMAIN,
    DeletionCandidateV1,
    PreservationClass,
    RelocationEvidenceRef,
    RetentionDeletionDecision,
)


SOURCE_COPY_DISPOSAL_POLICY_VERSION = "1"
SOURCE_COPY_DISPOSAL_POLICY_IDENTITY_DOMAIN = "source-copy-disposal-policy-v1"
SOURCE_COPY_DISPOSAL_DECISION_KIND = "source_copy_disposal"
DEFAULT_MAX_EVIDENCE_AGE_SECONDS = 3600

_IN_FLIGHT_RELOCATION = frozenset(
    {
        RelocationPhase.PLANNED,
        RelocationPhase.STAGED,
        RelocationPhase.VERIFIED,
        RelocationPhase.SWITCHED,
    }
)
_SECOND_NS = 1_000_000_000


class SourceCopyDisposalError(ValueError):
    """A source-copy-disposal value is malformed."""


class SourceCopyDisposalRefusalReason(StrEnum):
    CANDIDATE_NOT_SOURCE_COPY = "candidate_not_source_copy"
    MISSING_CANDIDATE_OBSERVATION = "missing_candidate_observation"
    CANDIDATE_IDENTITY_MISMATCH = "candidate_identity_mismatch"
    MISSING_SURVIVING_COPY = "missing_surviving_copy"
    SURVIVING_COPY_IS_CANDIDATE = "surviving_copy_is_candidate"
    SURVIVING_COPY_SAME_ROOT = "surviving_copy_same_root"
    SURVIVING_COPY_IDENTITY_MISMATCH = "surviving_copy_identity_mismatch"
    EVIDENCE_STALE = "evidence_stale"
    MISSING_CANONICAL_VERIFICATION = "missing_canonical_verification"
    CANONICAL_PARTITION_NOT_VALID = "canonical_partition_not_valid"
    CANONICAL_BYTES_MISMATCH = "canonical_bytes_mismatch"
    CANONICAL_NOT_BOUND_TO_ARCHIVE = "canonical_not_bound_to_archive"
    MISSING_PROTECTION_ASSESSMENT = "missing_protection_assessment"
    PROTECTION_NOT_PROTECTED = "protection_not_protected"
    PROTECTION_UNIT_MISMATCH = "protection_unit_mismatch"
    PROTECTION_INCLUDES_CANDIDATE_COPY = "protection_includes_candidate_copy"
    PROTECTION_SURVIVING_COPY_NOT_READ = "protection_surviving_copy_not_read"
    RELOCATION_IN_FLIGHT = "relocation_in_flight"


@dataclass(frozen=True, slots=True)
class SourceCopyDisposalPolicyV1:
    """Identity-bearing parameters of the copy-disposal rule."""

    max_evidence_age_seconds: int = DEFAULT_MAX_EVIDENCE_AGE_SECONDS
    semantic_version: str = SOURCE_COPY_DISPOSAL_POLICY_VERSION

    def __post_init__(self) -> None:
        if self.semantic_version != SOURCE_COPY_DISPOSAL_POLICY_VERSION:
            raise SourceCopyDisposalError("SourceCopyDisposalPolicyV1 requires semantic_version == '1'")
        if type(self.max_evidence_age_seconds) is not int or self.max_evidence_age_seconds < 1:
            raise SourceCopyDisposalError("max_evidence_age_seconds must be a positive integer")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": SOURCE_COPY_DISPOSAL_POLICY_IDENTITY_DOMAIN,
            "decision_kind": SOURCE_COPY_DISPOSAL_DECISION_KIND,
            "semantic_version": self.semantic_version,
            "max_evidence_age_seconds": self.max_evidence_age_seconds,
            "requires": [
                "independent_surviving_copy_in_other_root",
                "canonical_installed_bytes_rehashed_equal_catalog",
                "k06_assessment_over_surviving_copy_only",
            ],
        }

    @property
    def definition_identity(self) -> str:
        return f"{SOURCE_COPY_DISPOSAL_POLICY_IDENTITY_DOMAIN}:sha256:{_digest_of(self.canonical_payload())}"


@dataclass(frozen=True, slots=True)
class CandidateObservationV1:
    """Fresh observation of the candidate copy's own bytes."""

    content_sha256: str
    byte_size: int
    verified_at: Instant

    def __post_init__(self) -> None:
        object.__setattr__(self, "content_sha256", _sha256_hex(self.content_sha256, "content_sha256"))
        object.__setattr__(self, "byte_size", _non_negative_int(self.byte_size, "byte_size"))
        object.__setattr__(self, "verified_at", Instant.parse(self.verified_at))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "content_sha256": self.content_sha256,
            "byte_size": self.byte_size,
            "verified_at": self.verified_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SurvivingCopyEvidenceV1:
    """Fresh re-hash of the other physical copy, in another storage root."""

    storage_root_id: str
    rel_path: str
    content_sha256: str
    byte_size: int
    verified_at: Instant
    is_same_file_as_candidate: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "storage_root_id", _text(self.storage_root_id, "storage_root_id"))
        object.__setattr__(self, "rel_path", _text(self.rel_path, "rel_path"))
        object.__setattr__(self, "content_sha256", _sha256_hex(self.content_sha256, "content_sha256"))
        object.__setattr__(self, "byte_size", _non_negative_int(self.byte_size, "byte_size"))
        object.__setattr__(self, "verified_at", Instant.parse(self.verified_at))
        if type(self.is_same_file_as_candidate) is not bool:
            raise SourceCopyDisposalError("is_same_file_as_candidate must be boolean")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "storage_root_id": self.storage_root_id,
            "rel_path": self.rel_path,
            "content_sha256": self.content_sha256,
            "byte_size": self.byte_size,
            "verified_at": self.verified_at.isoformat(),
            "is_same_file_as_candidate": self.is_same_file_as_candidate,
        }


@dataclass(frozen=True, slots=True)
class CanonicalPartitionVerificationV1:
    """One canonical partition fed by the archive, re-read at decision time.

    ``observed_*`` come from re-hashing the installed bytes; ``None`` means the
    bytes could not be read.  ``binds_source_archive`` states that the
    partition's own coverage manifest records this archive's SHA-256.
    """

    partition_key: str
    revision: int
    catalog_partition_id: str
    catalog_state: str
    catalog_content_sha256: str
    catalog_byte_size: int
    observed_content_sha256: str | None
    observed_byte_size: int | None
    binds_source_archive: bool
    verified_at: Instant

    def __post_init__(self) -> None:
        object.__setattr__(self, "partition_key", _text(self.partition_key, "partition_key"))
        if type(self.revision) is not int or self.revision < 1:
            raise SourceCopyDisposalError("revision must be a positive integer")
        object.__setattr__(self, "catalog_partition_id", _text(self.catalog_partition_id, "catalog_partition_id"))
        object.__setattr__(self, "catalog_state", _text(self.catalog_state, "catalog_state"))
        object.__setattr__(self, "catalog_content_sha256", _sha256_hex(self.catalog_content_sha256, "catalog_content_sha256"))
        object.__setattr__(self, "catalog_byte_size", _non_negative_int(self.catalog_byte_size, "catalog_byte_size"))
        if (self.observed_content_sha256 is None) != (self.observed_byte_size is None):
            raise SourceCopyDisposalError("observed hash and size must be both present or both absent")
        if self.observed_content_sha256 is not None:
            object.__setattr__(self, "observed_content_sha256", _sha256_hex(self.observed_content_sha256, "observed_content_sha256"))
            object.__setattr__(self, "observed_byte_size", _non_negative_int(self.observed_byte_size, "observed_byte_size"))
        if type(self.binds_source_archive) is not bool:
            raise SourceCopyDisposalError("binds_source_archive must be boolean")
        object.__setattr__(self, "verified_at", Instant.parse(self.verified_at))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "partition_key": self.partition_key,
            "revision": self.revision,
            "catalog_partition_id": self.catalog_partition_id,
            "catalog_state": self.catalog_state,
            "catalog_content_sha256": self.catalog_content_sha256,
            "catalog_byte_size": self.catalog_byte_size,
            "observed_content_sha256": self.observed_content_sha256,
            "observed_byte_size": self.observed_byte_size,
            "binds_source_archive": self.binds_source_archive,
            "verified_at": self.verified_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class SourceCopyDisposalDecisionV1:
    policy: SourceCopyDisposalPolicyV1
    candidate: DeletionCandidateV1
    decision_time: Instant
    decision_actor_or_authority: str
    candidate_observation: CandidateObservationV1 | None = None
    surviving_copy: SurvivingCopyEvidenceV1 | None = None
    canonical_verifications: tuple[CanonicalPartitionVerificationV1, ...] = ()
    protection_assessment: ProtectionAssessment | None = None
    relocation_evidence: RelocationEvidenceRef | None = None
    decision: RetentionDeletionDecision = field(init=False)
    refusal_reasons: tuple[SourceCopyDisposalRefusalReason, ...] = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.policy, SourceCopyDisposalPolicyV1):
            raise SourceCopyDisposalError("policy must be SourceCopyDisposalPolicyV1")
        if not isinstance(self.candidate, DeletionCandidateV1):
            raise SourceCopyDisposalError("candidate must be DeletionCandidateV1")
        object.__setattr__(self, "decision_time", Instant.parse(self.decision_time))
        object.__setattr__(self, "decision_actor_or_authority", _text(self.decision_actor_or_authority, "decision_actor_or_authority"))
        for name, expected in (
            ("candidate_observation", CandidateObservationV1),
            ("surviving_copy", SurvivingCopyEvidenceV1),
            ("protection_assessment", ProtectionAssessment),
            ("relocation_evidence", RelocationEvidenceRef),
        ):
            value = getattr(self, name)
            if value is not None and not isinstance(value, expected):
                raise SourceCopyDisposalError(f"{name} must be {expected.__name__}")
        verifications = tuple(self.canonical_verifications)
        for item in verifications:
            if not isinstance(item, CanonicalPartitionVerificationV1):
                raise SourceCopyDisposalError("canonical_verifications must be CanonicalPartitionVerificationV1")
        object.__setattr__(
            self,
            "canonical_verifications",
            tuple(sorted(verifications, key=lambda v: (v.partition_key, v.revision))),
        )
        reasons = tuple(_evaluate_refusals(self))
        object.__setattr__(self, "refusal_reasons", reasons)
        object.__setattr__(
            self,
            "decision",
            RetentionDeletionDecision.PERMITTED if not reasons else RetentionDeletionDecision.REFUSED,
        )

    @property
    def decision_identity(self) -> str:
        return f"{RETENTION_DELETION_DECISION_IDENTITY_DOMAIN}:sha256:{_digest_of(self.stable_dict(include_identity=False))}"

    @property
    def identity(self) -> str:
        return self.decision_identity

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        assessment = self.protection_assessment
        payload = {
            "identity_domain": RETENTION_DELETION_DECISION_IDENTITY_DOMAIN,
            "decision_kind": SOURCE_COPY_DISPOSAL_DECISION_KIND,
            "policy_definition_identity": self.policy.definition_identity,
            "policy": self.policy.canonical_payload(),
            "decision_time": self.decision_time.isoformat(),
            "decision_actor_or_authority": self.decision_actor_or_authority,
            "decision": self.decision.value,
            "refusal_reasons": [reason.value for reason in self.refusal_reasons],
            "candidate": self.candidate.stable_dict(),
            "preservation_class": self.candidate.preservation_class.value,
            "candidate_observation": (
                None if self.candidate_observation is None else self.candidate_observation.stable_dict()
            ),
            "surviving_copy": None if self.surviving_copy is None else self.surviving_copy.stable_dict(),
            "canonical_verifications": [item.stable_dict() for item in self.canonical_verifications],
            "protection_evidence": (
                None
                if assessment is None
                else {
                    "k06_assessment_identity": assessment.assessment_identity,
                    "protection_identity": assessment.protection_identity,
                    "protection_state": assessment.state.value,
                    "verified_at": assessment.verified_at.isoformat(),
                    "verifier_identity": assessment.verifier_identity,
                }
            ),
            "relocation_evidence": (
                None if self.relocation_evidence is None else self.relocation_evidence.stable_dict()
            ),
        }
        if include_identity:
            payload["deletion_decision_id"] = self.decision_identity
        return payload


def evaluate_source_copy_disposal(
    *,
    policy: SourceCopyDisposalPolicyV1,
    candidate: DeletionCandidateV1,
    decision_time: Instant | str,
    decision_actor_or_authority: str,
    candidate_observation: CandidateObservationV1 | None = None,
    surviving_copy: SurvivingCopyEvidenceV1 | None = None,
    canonical_verifications: tuple[CanonicalPartitionVerificationV1, ...] = (),
    protection_assessment: ProtectionAssessment | None = None,
    relocation_evidence: RelocationEvidenceRef | None = None,
) -> SourceCopyDisposalDecisionV1:
    return SourceCopyDisposalDecisionV1(
        policy=policy,
        candidate=candidate,
        decision_time=Instant.parse(decision_time),
        decision_actor_or_authority=decision_actor_or_authority,
        candidate_observation=candidate_observation,
        surviving_copy=surviving_copy,
        canonical_verifications=tuple(canonical_verifications),
        protection_assessment=protection_assessment,
        relocation_evidence=relocation_evidence,
    )


def _evaluate_refusals(decision: SourceCopyDisposalDecisionV1) -> list[SourceCopyDisposalRefusalReason]:
    R = SourceCopyDisposalRefusalReason
    candidate = decision.candidate
    reasons: list[SourceCopyDisposalRefusalReason] = []

    if (
        candidate.preservation_class != PreservationClass.UNIQUE_SOURCE
        or candidate.catalog_partition_id is not None
        or candidate.partition_key is not None
        or candidate.revision is not None
    ):
        reasons.append(R.CANDIDATE_NOT_SOURCE_COPY)

    observation = decision.candidate_observation
    if observation is None:
        reasons.append(R.MISSING_CANDIDATE_OBSERVATION)
    else:
        if (observation.content_sha256, observation.byte_size) != (candidate.content_sha256, candidate.byte_size):
            reasons.append(R.CANDIDATE_IDENTITY_MISMATCH)
        if _is_stale(decision, observation.verified_at):
            reasons.append(R.EVIDENCE_STALE)

    surviving = decision.surviving_copy
    if surviving is None:
        reasons.append(R.MISSING_SURVIVING_COPY)
    else:
        if surviving.is_same_file_as_candidate or (
            surviving.storage_root_id == candidate.storage_root_id and surviving.rel_path == candidate.rel_path
        ):
            reasons.append(R.SURVIVING_COPY_IS_CANDIDATE)
        if surviving.storage_root_id == candidate.storage_root_id:
            reasons.append(R.SURVIVING_COPY_SAME_ROOT)
        if (surviving.content_sha256, surviving.byte_size) != (candidate.content_sha256, candidate.byte_size):
            reasons.append(R.SURVIVING_COPY_IDENTITY_MISMATCH)
        if _is_stale(decision, surviving.verified_at):
            reasons.append(R.EVIDENCE_STALE)

    if not decision.canonical_verifications:
        reasons.append(R.MISSING_CANONICAL_VERIFICATION)
    for item in decision.canonical_verifications:
        if item.catalog_state != "valid":
            reasons.append(R.CANONICAL_PARTITION_NOT_VALID)
        if (item.observed_content_sha256, item.observed_byte_size) != (
            item.catalog_content_sha256,
            item.catalog_byte_size,
        ):
            reasons.append(R.CANONICAL_BYTES_MISMATCH)
        if not item.binds_source_archive:
            reasons.append(R.CANONICAL_NOT_BOUND_TO_ARCHIVE)
        if _is_stale(decision, item.verified_at):
            reasons.append(R.EVIDENCE_STALE)

    assessment = decision.protection_assessment
    if assessment is None:
        reasons.append(R.MISSING_PROTECTION_ASSESSMENT)
    else:
        if assessment.state != ProtectionState.PROTECTED:
            reasons.append(R.PROTECTION_NOT_PROTECTED)
        matching = [
            result
            for result in assessment.artifact_results
            if (result.declared.content_hash_sha256, result.declared.size_bytes)
            == (candidate.content_sha256, candidate.byte_size)
        ]
        if not matching:
            reasons.append(R.PROTECTION_UNIT_MISMATCH)
        for result in assessment.artifact_results:
            if any(item.instance_scope == candidate.storage_root_id for item in result.evidence):
                reasons.append(R.PROTECTION_INCLUDES_CANDIDATE_COPY)
                break
        if surviving is not None and not any(
            item.instance_scope == surviving.storage_root_id
            and item.outcome == ArtifactReadOutcome.READ
            and item.observed_content_hash_sha256 == candidate.content_sha256
            and item.observed_size_bytes == candidate.byte_size
            for result in matching
            for item in result.evidence
        ):
            reasons.append(R.PROTECTION_SURVIVING_COPY_NOT_READ)
        if _is_stale(decision, assessment.verified_at):
            reasons.append(R.EVIDENCE_STALE)

    relocation = decision.relocation_evidence
    if relocation is not None and relocation.relocation_phase in _IN_FLIGHT_RELOCATION:
        reasons.append(R.RELOCATION_IN_FLIGHT)

    seen: set[SourceCopyDisposalRefusalReason] = set()
    unique: list[SourceCopyDisposalRefusalReason] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            unique.append(reason)
    return unique


def _is_stale(decision: SourceCopyDisposalDecisionV1, verified_at: Instant) -> bool:
    # Evidence is fresh when it lies within the policy window of the decision instant,
    # on either side: the operator-declared decision time and the moment the bytes were
    # hashed are two clock readings of one short action.
    skew_ns = abs(decision.decision_time.epoch_ns - verified_at.epoch_ns)
    return skew_ns > decision.policy.max_evidence_age_seconds * _SECOND_NS


def _digest_of(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=True)
    ).hexdigest()


def _sha256_hex(value: str, field_name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SourceCopyDisposalError(f"{field_name} must be 64 lowercase hex characters")
    return value


def _non_negative_int(value: int, field_name: str) -> int:
    if type(value) is not int or value < 0:
        raise SourceCopyDisposalError(f"{field_name} must be a non-negative integer")
    return value


def _text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceCopyDisposalError(f"{field_name} must be a non-empty string")
    if any(ord(char) < 32 for char in value):
        raise SourceCopyDisposalError(f"{field_name} must not contain control characters")
    return value.strip()


__all__ = [
    "DEFAULT_MAX_EVIDENCE_AGE_SECONDS",
    "SOURCE_COPY_DISPOSAL_DECISION_KIND",
    "SOURCE_COPY_DISPOSAL_POLICY_IDENTITY_DOMAIN",
    "SOURCE_COPY_DISPOSAL_POLICY_VERSION",
    "CandidateObservationV1",
    "CanonicalPartitionVerificationV1",
    "SourceCopyDisposalDecisionV1",
    "SourceCopyDisposalError",
    "SourceCopyDisposalPolicyV1",
    "SourceCopyDisposalRefusalReason",
    "SurvivingCopyEvidenceV1",
    "evaluate_source_copy_disposal",
]
