"""K09 retention/deletion authority v1 pure decision seam.

This module evaluates deletion authority from caller-supplied evidence.  It
does not open storage, query a catalog, persist audit rows or delete bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

from ..data.models import DatasetIdentity, Instant
from .relocation import RelocationPhase


RETENTION_POLICY_DEFINITION_V1_VERSION = "1"
RETENTION_POLICY_DEFINITION_IDENTITY_DOMAIN = "retention-policy-definition-v1"
RETENTION_DELETION_DECISION_IDENTITY_DOMAIN = "retention-deletion-decision-v1"
VERIFIED_RESTORE_PROOF_REF_IDENTITY_DOMAIN = "verified-restore-proof-ref-v1"
PROTECTION_EVIDENCE_REF_IDENTITY_DOMAIN = "deletion-protection-evidence-ref-v1"
RELOCATION_EVIDENCE_REF_IDENTITY_DOMAIN = "deletion-relocation-evidence-ref-v1"

_PERMANENT_CLASSES = frozenset({"UNIQUE_SOURCE", "PROTECTED_EVIDENCE"})
_RESTORABLE_CLASSES = frozenset({"CANONICAL_RESTORABLE", "DERIVED_RESTORABLE"})
_FINALIZED_STATES = frozenset({"closed", "valid", "degraded", "superseded"})
_IN_FLIGHT_RELOCATION = frozenset(
    {
        RelocationPhase.PLANNED,
        RelocationPhase.STAGED,
        RelocationPhase.VERIFIED,
        RelocationPhase.SWITCHED,
    }
)
_DAY_NS = 86_400 * 1_000_000_000


class RetentionDeletionError(ValueError):
    """K09 retention/deletion semantic validation failed."""


class PreservationClass(StrEnum):
    UNIQUE_SOURCE = "UNIQUE_SOURCE"
    PROTECTED_EVIDENCE = "PROTECTED_EVIDENCE"
    CANONICAL_RESTORABLE = "CANONICAL_RESTORABLE"
    DERIVED_RESTORABLE = "DERIVED_RESTORABLE"


class RetentionDeletionDecision(StrEnum):
    PERMITTED = "PERMITTED"
    REFUSED = "REFUSED"


class RetentionRefusalReason(StrEnum):
    PERMANENT_PRESERVATION_CLASS = "permanent_preservation_class"
    INELIGIBLE_PRESERVATION_CLASS = "ineligible_preservation_class"
    NOT_FINALIZED = "not_finalized"
    RETENTION_PERIOD_NOT_ELAPSED = "retention_period_not_elapsed"
    MISSING_VERIFIED_RESTORE = "missing_verified_restore"
    RESTORE_PROOF_NOT_INDEPENDENT = "restore_proof_not_independent"
    RESTORE_PROOF_DEPENDS_ON_CANDIDATE = "restore_proof_depends_on_candidate"
    RESTORE_PROOF_IDENTITY_MISMATCH = "restore_proof_identity_mismatch"
    K06_PROTECTED_EVIDENCE = "k06_protected_evidence"
    SOLE_RECOVERABLE_EVIDENCE = "sole_recoverable_evidence"
    RELOCATION_IN_FLIGHT = "relocation_in_flight"


@dataclass(frozen=True, slots=True)
class RetentionPolicyDefinitionV1:
    canonical_min_retention_days: int = 90
    derived_min_retention_days: int = 30
    semantic_version: str = RETENTION_POLICY_DEFINITION_V1_VERSION

    def __post_init__(self) -> None:
        if self.semantic_version != RETENTION_POLICY_DEFINITION_V1_VERSION:
            raise RetentionDeletionError("RetentionPolicyDefinitionV1 requires semantic_version == '1'")
        canonical_days = _min_int(self.canonical_min_retention_days, "canonical_min_retention_days", minimum=90)
        derived_days = _min_int(self.derived_min_retention_days, "derived_min_retention_days", minimum=30)
        object.__setattr__(self, "canonical_min_retention_days", canonical_days)
        object.__setattr__(self, "derived_min_retention_days", derived_days)

    @property
    def definition_identity(self) -> str:
        return f"{RETENTION_POLICY_DEFINITION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"

    @property
    def identity(self) -> str:
        return self.definition_identity

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": RETENTION_POLICY_DEFINITION_IDENTITY_DOMAIN,
            "semantic_version": self.semantic_version,
            "canonical_min_retention_days": self.canonical_min_retention_days,
            "derived_min_retention_days": self.derived_min_retention_days,
            "permanent_classes": sorted(_PERMANENT_CLASSES),
        }

    def min_retention_days_for(self, preservation_class: PreservationClass) -> int | None:
        if preservation_class == PreservationClass.CANONICAL_RESTORABLE:
            return self.canonical_min_retention_days
        if preservation_class == PreservationClass.DERIVED_RESTORABLE:
            return self.derived_min_retention_days
        return None


@dataclass(frozen=True, slots=True)
class DeletionCandidateV1:
    dataset_identity: DatasetIdentity
    storage_root_id: str
    rel_path: str
    content_sha256: str
    byte_size: int
    lifecycle_state: str
    producer: str
    code_ref: str
    finalized_at: Instant
    preservation_class: PreservationClass
    catalog_partition_id: str | None = None
    partition_key: str | None = None
    revision: int | None = None
    superseded_at: Instant | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_identity, DatasetIdentity):
            raise RetentionDeletionError("dataset_identity must be DatasetIdentity")
        object.__setattr__(self, "storage_root_id", _root_id(self.storage_root_id, "storage_root_id"))
        object.__setattr__(self, "rel_path", _rel_path(self.rel_path, "rel_path"))
        object.__setattr__(self, "content_sha256", _sha256_hex(self.content_sha256, "content_sha256"))
        object.__setattr__(self, "byte_size", _non_negative_int(self.byte_size, "byte_size"))
        object.__setattr__(self, "lifecycle_state", _non_empty_text(self.lifecycle_state, "lifecycle_state"))
        object.__setattr__(self, "producer", _non_empty_text(self.producer, "producer"))
        object.__setattr__(self, "code_ref", _non_empty_text(self.code_ref, "code_ref"))
        object.__setattr__(self, "finalized_at", Instant.parse(self.finalized_at))
        object.__setattr__(self, "preservation_class", _preservation_class(self.preservation_class))
        if self.catalog_partition_id is not None:
            object.__setattr__(self, "catalog_partition_id", _non_empty_text(self.catalog_partition_id, "catalog_partition_id"))
        if self.partition_key is not None:
            object.__setattr__(self, "partition_key", _partition_key(self.partition_key))
        if self.revision is not None:
            object.__setattr__(self, "revision", _positive_int(self.revision, "revision"))
        if self.superseded_at is not None:
            object.__setattr__(self, "superseded_at", Instant.parse(self.superseded_at))

    @property
    def content_identity(self) -> dict[str, Any]:
        return {
            "dataset_identity": self.dataset_identity.stable_dict(),
            "catalog_partition_id": self.catalog_partition_id,
            "partition_key": self.partition_key,
            "revision": self.revision,
            "storage_root_id": self.storage_root_id,
            "rel_path": self.rel_path,
            "content_sha256": self.content_sha256,
            "byte_size": self.byte_size,
        }

    def stable_dict(self) -> dict[str, Any]:
        return {
            **self.content_identity,
            "lifecycle_state": self.lifecycle_state,
            "producer": self.producer,
            "code_ref": self.code_ref,
            "finalized_at": self.finalized_at.isoformat(),
            "superseded_at": None if self.superseded_at is None else self.superseded_at.isoformat(),
            "preservation_class": self.preservation_class.value,
        }


@dataclass(frozen=True, slots=True)
class VerifiedRestoreProofRef:
    recovery_set_identity: str
    isolated_restore_proof_identity: str
    dataset_identity: DatasetIdentity
    restored_content_sha256: str
    restored_size_bytes: int
    restored_coverage_or_support: str
    storage_boundary_independent: bool
    partition_key: str | None = None
    revision: int | None = None
    depends_on_candidate: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "recovery_set_identity", _identity(self.recovery_set_identity, "recovery_set_identity"))
        object.__setattr__(
            self,
            "isolated_restore_proof_identity",
            _identity(self.isolated_restore_proof_identity, "isolated_restore_proof_identity"),
        )
        if not isinstance(self.dataset_identity, DatasetIdentity):
            raise RetentionDeletionError("dataset_identity must be DatasetIdentity")
        object.__setattr__(self, "restored_content_sha256", _sha256_hex(self.restored_content_sha256, "restored_content_sha256"))
        object.__setattr__(self, "restored_size_bytes", _non_negative_int(self.restored_size_bytes, "restored_size_bytes"))
        object.__setattr__(self, "restored_coverage_or_support", _non_empty_text(self.restored_coverage_or_support, "restored_coverage_or_support"))
        if type(self.storage_boundary_independent) is not bool:
            raise RetentionDeletionError("storage_boundary_independent must be boolean")
        if self.partition_key is not None:
            object.__setattr__(self, "partition_key", _partition_key(self.partition_key))
        if self.revision is not None:
            object.__setattr__(self, "revision", _positive_int(self.revision, "revision"))
        if type(self.depends_on_candidate) is not bool:
            raise RetentionDeletionError("depends_on_candidate must be boolean")

    @property
    def proof_identity(self) -> str:
        return f"{VERIFIED_RESTORE_PROOF_REF_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict(include_identity=False))}"

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": VERIFIED_RESTORE_PROOF_REF_IDENTITY_DOMAIN,
            "recovery_set_identity": self.recovery_set_identity,
            "isolated_restore_proof_identity": self.isolated_restore_proof_identity,
            "dataset_identity": self.dataset_identity.stable_dict(),
            "partition_key": self.partition_key,
            "revision": self.revision,
            "restored_content_sha256": self.restored_content_sha256,
            "restored_size_bytes": self.restored_size_bytes,
            "restored_coverage_or_support": self.restored_coverage_or_support,
            "storage_boundary_independent": self.storage_boundary_independent,
            "depends_on_candidate": self.depends_on_candidate,
        }
        if include_identity:
            payload["proof_identity"] = self.proof_identity
        return payload


@dataclass(frozen=True, slots=True)
class ProtectionEvidenceRef:
    candidate_is_k06_protected_evidence: bool = False
    candidate_is_sole_recoverable_evidence: bool = False
    k06_assessment_identity: str | None = None
    protection_state: str | None = None
    evidence_identity: str | None = None

    def __post_init__(self) -> None:
        if type(self.candidate_is_k06_protected_evidence) is not bool:
            raise RetentionDeletionError("candidate_is_k06_protected_evidence must be boolean")
        if type(self.candidate_is_sole_recoverable_evidence) is not bool:
            raise RetentionDeletionError("candidate_is_sole_recoverable_evidence must be boolean")
        if self.k06_assessment_identity is not None:
            object.__setattr__(self, "k06_assessment_identity", _identity(self.k06_assessment_identity, "k06_assessment_identity"))
        if self.protection_state is not None:
            object.__setattr__(self, "protection_state", _non_empty_text(self.protection_state, "protection_state"))
        if self.evidence_identity is not None:
            object.__setattr__(self, "evidence_identity", _identity(self.evidence_identity, "evidence_identity"))

    @property
    def evidence_ref_identity(self) -> str:
        return f"{PROTECTION_EVIDENCE_REF_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict(include_identity=False))}"

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": PROTECTION_EVIDENCE_REF_IDENTITY_DOMAIN,
            "candidate_is_k06_protected_evidence": self.candidate_is_k06_protected_evidence,
            "candidate_is_sole_recoverable_evidence": self.candidate_is_sole_recoverable_evidence,
            "k06_assessment_identity": self.k06_assessment_identity,
            "protection_state": self.protection_state,
            "evidence_identity": self.evidence_identity,
        }
        if include_identity:
            payload["evidence_ref_identity"] = self.evidence_ref_identity
        return payload


@dataclass(frozen=True, slots=True)
class RelocationEvidenceRef:
    relocation_id: str | None = None
    relocation_phase: RelocationPhase | str | None = None

    def __post_init__(self) -> None:
        if self.relocation_id is not None:
            object.__setattr__(self, "relocation_id", _identity(self.relocation_id, "relocation_id"))
        if self.relocation_phase is not None:
            try:
                object.__setattr__(self, "relocation_phase", RelocationPhase(self.relocation_phase))
            except ValueError as exc:
                raise RetentionDeletionError("unknown relocation_phase") from exc

    @property
    def evidence_ref_identity(self) -> str:
        return f"{RELOCATION_EVIDENCE_REF_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict(include_identity=False))}"

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": RELOCATION_EVIDENCE_REF_IDENTITY_DOMAIN,
            "relocation_id": self.relocation_id,
            "relocation_phase": None if self.relocation_phase is None else self.relocation_phase.value,
        }
        if include_identity:
            payload["evidence_ref_identity"] = self.evidence_ref_identity
        return payload


@dataclass(frozen=True, slots=True)
class RetentionDeletionDecisionV1:
    policy: RetentionPolicyDefinitionV1
    candidate: DeletionCandidateV1
    decision_time: Instant
    decision_actor_or_authority: str
    restore_proof: VerifiedRestoreProofRef | None = None
    protection_evidence: ProtectionEvidenceRef | None = None
    relocation_evidence: RelocationEvidenceRef | None = None
    decision: RetentionDeletionDecision = field(init=False)
    refusal_reasons: tuple[RetentionRefusalReason, ...] = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.policy, RetentionPolicyDefinitionV1):
            raise RetentionDeletionError("policy must be RetentionPolicyDefinitionV1")
        if not isinstance(self.candidate, DeletionCandidateV1):
            raise RetentionDeletionError("candidate must be DeletionCandidateV1")
        object.__setattr__(self, "decision_time", Instant.parse(self.decision_time))
        object.__setattr__(self, "decision_actor_or_authority", _non_empty_text(self.decision_actor_or_authority, "decision_actor_or_authority"))
        if self.restore_proof is not None and not isinstance(self.restore_proof, VerifiedRestoreProofRef):
            raise RetentionDeletionError("restore_proof must be VerifiedRestoreProofRef")
        if self.protection_evidence is not None and not isinstance(self.protection_evidence, ProtectionEvidenceRef):
            raise RetentionDeletionError("protection_evidence must be ProtectionEvidenceRef")
        if self.relocation_evidence is not None and not isinstance(self.relocation_evidence, RelocationEvidenceRef):
            raise RetentionDeletionError("relocation_evidence must be RelocationEvidenceRef")

        reasons = tuple(_evaluate_refusals(self))
        object.__setattr__(self, "refusal_reasons", reasons)
        object.__setattr__(
            self,
            "decision",
            RetentionDeletionDecision.PERMITTED if not reasons else RetentionDeletionDecision.REFUSED,
        )

    @property
    def minimum_retention_days(self) -> int | None:
        return self.policy.min_retention_days_for(self.candidate.preservation_class)

    @property
    def eligible_at(self) -> Instant | None:
        days = self.minimum_retention_days
        if days is None:
            return None
        basis = self.candidate.finalized_at
        if self.candidate.superseded_at is not None and self.candidate.superseded_at > basis:
            basis = self.candidate.superseded_at
        return Instant(basis.epoch_ns + days * _DAY_NS)

    @property
    def decision_identity(self) -> str:
        return f"{RETENTION_DELETION_DECISION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.stable_dict(include_identity=False))}"

    @property
    def identity(self) -> str:
        return self.decision_identity

    def stable_dict(self, *, include_identity: bool = True) -> dict[str, Any]:
        payload = {
            "identity_domain": RETENTION_DELETION_DECISION_IDENTITY_DOMAIN,
            "policy_definition_identity": self.policy.definition_identity,
            "policy": self.policy.canonical_payload(),
            "decision_time": self.decision_time.isoformat(),
            "decision_actor_or_authority": self.decision_actor_or_authority,
            "decision": self.decision.value,
            "refusal_reasons": [reason.value for reason in self.refusal_reasons],
            "candidate": self.candidate.stable_dict(),
            "preservation_class": self.candidate.preservation_class.value,
            "retention": {
                "finalized_at": self.candidate.finalized_at.isoformat(),
                "superseded_at": None if self.candidate.superseded_at is None else self.candidate.superseded_at.isoformat(),
                "minimum_retention_days": self.minimum_retention_days,
                "eligible_at": None if self.eligible_at is None else self.eligible_at.isoformat(),
            },
            "restore_evidence": None if self.restore_proof is None else self.restore_proof.stable_dict(),
            "protection_evidence": None if self.protection_evidence is None else self.protection_evidence.stable_dict(),
            "relocation_evidence": None if self.relocation_evidence is None else self.relocation_evidence.stable_dict(),
        }
        if include_identity:
            payload["deletion_decision_id"] = self.decision_identity
        return payload


def evaluate_retention_deletion(
    *,
    policy: RetentionPolicyDefinitionV1,
    candidate: DeletionCandidateV1,
    decision_time: Instant | str,
    decision_actor_or_authority: str,
    restore_proof: VerifiedRestoreProofRef | None = None,
    protection_evidence: ProtectionEvidenceRef | None = None,
    relocation_evidence: RelocationEvidenceRef | None = None,
) -> RetentionDeletionDecisionV1:
    return RetentionDeletionDecisionV1(
        policy=policy,
        candidate=candidate,
        decision_time=Instant.parse(decision_time),
        decision_actor_or_authority=decision_actor_or_authority,
        restore_proof=restore_proof,
        protection_evidence=protection_evidence,
        relocation_evidence=relocation_evidence,
    )


def _evaluate_refusals(decision: RetentionDeletionDecisionV1) -> list[RetentionRefusalReason]:
    candidate = decision.candidate
    reasons: list[RetentionRefusalReason] = []
    if candidate.preservation_class.value in _PERMANENT_CLASSES:
        reasons.append(RetentionRefusalReason.PERMANENT_PRESERVATION_CLASS)
    if candidate.preservation_class.value not in _RESTORABLE_CLASSES:
        reasons.append(RetentionRefusalReason.INELIGIBLE_PRESERVATION_CLASS)
    if candidate.lifecycle_state not in _FINALIZED_STATES:
        reasons.append(RetentionRefusalReason.NOT_FINALIZED)
    eligible_at = decision.eligible_at
    if eligible_at is None or decision.decision_time < eligible_at:
        reasons.append(RetentionRefusalReason.RETENTION_PERIOD_NOT_ELAPSED)

    restore = decision.restore_proof
    if restore is None:
        reasons.append(RetentionRefusalReason.MISSING_VERIFIED_RESTORE)
    else:
        if not restore.storage_boundary_independent:
            reasons.append(RetentionRefusalReason.RESTORE_PROOF_NOT_INDEPENDENT)
        if restore.depends_on_candidate:
            reasons.append(RetentionRefusalReason.RESTORE_PROOF_DEPENDS_ON_CANDIDATE)
        if not _restore_matches_candidate(restore, candidate):
            reasons.append(RetentionRefusalReason.RESTORE_PROOF_IDENTITY_MISMATCH)

    protection = decision.protection_evidence
    if protection is not None:
        if protection.candidate_is_k06_protected_evidence:
            reasons.append(RetentionRefusalReason.K06_PROTECTED_EVIDENCE)
        if protection.candidate_is_sole_recoverable_evidence:
            reasons.append(RetentionRefusalReason.SOLE_RECOVERABLE_EVIDENCE)

    relocation = decision.relocation_evidence
    if relocation is not None and relocation.relocation_phase in _IN_FLIGHT_RELOCATION:
        reasons.append(RetentionRefusalReason.RELOCATION_IN_FLIGHT)

    return _deduplicate(reasons)


def _restore_matches_candidate(restore: VerifiedRestoreProofRef, candidate: DeletionCandidateV1) -> bool:
    if restore.dataset_identity != candidate.dataset_identity:
        return False
    if restore.restored_content_sha256 != candidate.content_sha256:
        return False
    if restore.restored_size_bytes != candidate.byte_size:
        return False
    if candidate.partition_key is not None and restore.partition_key != candidate.partition_key:
        return False
    if candidate.revision is not None and restore.revision != candidate.revision:
        return False
    return True


def _deduplicate(reasons: list[RetentionRefusalReason]) -> tuple[RetentionRefusalReason, ...]:
    seen: set[RetentionRefusalReason] = set()
    result: list[RetentionRefusalReason] = []
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            result.append(reason)
    return tuple(result)


def _canonical_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _preservation_class(value: PreservationClass | str) -> PreservationClass:
    if isinstance(value, PreservationClass):
        return value
    try:
        return PreservationClass(value)
    except ValueError as exc:
        raise RetentionDeletionError("unknown preservation_class") from exc


def _identity(value: str, field: str) -> str:
    text = _non_empty_text(value, field)
    if ":sha256:" not in text:
        raise RetentionDeletionError(f"{field} must be a content identity")
    _sha256_hex(text.rsplit(":sha256:", 1)[1], field)
    return text


def _sha256_hex(value: str, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise RetentionDeletionError(f"{field} must be 64 lowercase hex characters")
    return value


def _root_id(value: str, field: str) -> str:
    text = _non_empty_text(value, field)
    if re.fullmatch(r"[a-z][a-z0-9._-]*", text) is None:
        raise RetentionDeletionError(f"{field} must be a canonical storage root id")
    return text


def _partition_key(value: str) -> str:
    text = _non_empty_text(value, "partition_key")
    if re.fullmatch(r"[a-z_]+=[A-Za-z0-9._-]+(/[a-z_]+=[A-Za-z0-9._-]+)*", text) is None:
        raise RetentionDeletionError("partition_key is not canonical")
    return text


def _rel_path(value: str, field: str) -> str:
    text = _non_empty_text(value, field).replace("\\", "/")
    if text.startswith("/") or text.startswith("../") or "/../" in text or text.endswith("/.."):
        raise RetentionDeletionError(f"{field} must be a relative path without traversal")
    if text == "." or text.startswith("./") or "/./" in text:
        raise RetentionDeletionError(f"{field} must not contain current-directory segments")
    return text


def _non_empty_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RetentionDeletionError(f"{field} must be a non-empty string")
    if any(ord(char) < 32 for char in value):
        raise RetentionDeletionError(f"{field} must not contain control characters")
    return value.strip()


def _positive_int(value: int, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise RetentionDeletionError(f"{field} must be a positive integer")
    return value


def _non_negative_int(value: int, field: str) -> int:
    if type(value) is not int or value < 0:
        raise RetentionDeletionError(f"{field} must be a non-negative integer")
    return value


def _min_int(value: int, field: str, *, minimum: int) -> int:
    if type(value) is not int or value < minimum:
        raise RetentionDeletionError(f"{field} must be an integer >= {minimum}")
    return value


__all__ = [
    "PROTECTION_EVIDENCE_REF_IDENTITY_DOMAIN",
    "RELOCATION_EVIDENCE_REF_IDENTITY_DOMAIN",
    "RETENTION_DELETION_DECISION_IDENTITY_DOMAIN",
    "RETENTION_POLICY_DEFINITION_IDENTITY_DOMAIN",
    "RETENTION_POLICY_DEFINITION_V1_VERSION",
    "VERIFIED_RESTORE_PROOF_REF_IDENTITY_DOMAIN",
    "DeletionCandidateV1",
    "PreservationClass",
    "ProtectionEvidenceRef",
    "RelocationEvidenceRef",
    "RetentionDeletionDecision",
    "RetentionDeletionDecisionV1",
    "RetentionDeletionError",
    "RetentionPolicyDefinitionV1",
    "RetentionRefusalReason",
    "VerifiedRestoreProofRef",
    "evaluate_retention_deletion",
]
