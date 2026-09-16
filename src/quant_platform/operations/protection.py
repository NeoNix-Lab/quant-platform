"""K06 RAW/source protection v1: a pure protection-identity and assessment seam.

Protection is a reconstruction claim, not a path claim.  This module proves
whether the bounded set of source evidence required to reproduce one
declared canonical source-acquired result is present, content-verified and
complete -- from caller-supplied evidence only.  It never crawls a
filesystem, watches for changes, copies bytes, schedules work or authorizes
deletion.

It deliberately does not import ``quant_platform.source_adapters``.  Callers
(source-adapter code, tools, future orchestration) supply canonical source
semantics/mapping identifiers and artifact verification evidence through the
Operations-owned values below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

from ..data.models import Instant
from .pressure import PressureDecision, PressureDecisionUnavailable, PressureState


PROTECTION_UNIT_IDENTITY_DOMAIN = "protection-unit-identity-v1"
PROTECTION_ASSESSMENT_IDENTITY_DOMAIN = "protection-assessment-v1"

_ROLE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class ProtectionError(ValueError):
    """A K06 protection value or request violates the frozen v1 contract."""


class ProtectionState(StrEnum):
    """Canonical fail-closed protection states.

    ``UNAVAILABLE``, ``CORRUPT`` and ``LOST`` never normalize to
    ``PROTECTED`` or ``AT_RISK``.
    """

    PROTECTED = "PROTECTED"
    AT_RISK = "AT_RISK"
    UNAVAILABLE = "UNAVAILABLE"
    CORRUPT = "CORRUPT"
    LOST = "LOST"


# Deterministic multi-failure precedence, most severe first. AT_RISK only
# ever applies when every artifact independently verified (severity 0).
_STATE_SEVERITY = {
    ProtectionState.PROTECTED: 0,
    ProtectionState.AT_RISK: 1,
    ProtectionState.UNAVAILABLE: 2,
    ProtectionState.CORRUPT: 3,
    ProtectionState.LOST: 4,
}
_SEVERITY_STATE = {value: key for key, value in _STATE_SEVERITY.items()}


class ArtifactReadOutcome(StrEnum):
    """What a caller actually observed when inspecting one required artifact."""

    READ = "READ"
    UNREADABLE = "UNREADABLE"
    ABSENT = "ABSENT"


class ProtectionWriteDecision(StrEnum):
    PERMITTED = "PERMITTED"
    DEFERRED = "DEFERRED"
    REFUSED = "REFUSED"


@dataclass(frozen=True, slots=True)
class ArtifactProtectionIdentity:
    """One required artifact's canonical, host-independent semantic identity.

    ``role`` is source-contract-owned and canonical/relative -- never an
    absolute path, mount, host or discovery-order value.
    """

    role: str
    content_hash_sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _role(self.role, "role"))
        object.__setattr__(
            self,
            "content_hash_sha256",
            _sha256_hex(self.content_hash_sha256, "content_hash_sha256"),
        )
        object.__setattr__(self, "size_bytes", _non_negative_int(self.size_bytes, "size_bytes"))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content_hash_sha256": self.content_hash_sha256,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class ProtectionUnitIdentity:
    """The bounded, declared reconstruction claim for one protected support.

    Composite identity is derived only from source semantics, mapping,
    protected support, reconstruction contract and canonically role-sorted
    artifact descriptors.  Filesystem path, mount, storage root, host, inode,
    mtime, ctime and artifact discovery order never participate.
    """

    source_semantics_id: str
    mapping_id: str
    reconstruction_contract_id: str
    protected_support: str
    artifacts: tuple[ArtifactProtectionIdentity, ...]
    extract_fingerprint_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_semantics_id", _non_empty_text(self.source_semantics_id, "source_semantics_id")
        )
        object.__setattr__(self, "mapping_id", _non_empty_text(self.mapping_id, "mapping_id"))
        object.__setattr__(
            self,
            "reconstruction_contract_id",
            _non_empty_text(self.reconstruction_contract_id, "reconstruction_contract_id"),
        )
        object.__setattr__(
            self, "protected_support", _non_empty_text(self.protected_support, "protected_support")
        )
        artifacts = tuple(self.artifacts)
        if not artifacts:
            raise ProtectionError("a protection unit requires at least one required artifact")
        for index, artifact in enumerate(artifacts):
            if not isinstance(artifact, ArtifactProtectionIdentity):
                raise ProtectionError(f"artifacts[{index}] must be ArtifactProtectionIdentity")
        roles = [artifact.role for artifact in artifacts]
        duplicates = sorted({role for role in roles if roles.count(role) > 1})
        if duplicates:
            raise ProtectionError("duplicate artifact roles: " + ",".join(duplicates))
        object.__setattr__(
            self, "artifacts", tuple(sorted(artifacts, key=lambda artifact: artifact.role))
        )
        if self.extract_fingerprint_sha256 is not None:
            object.__setattr__(
                self,
                "extract_fingerprint_sha256",
                _sha256_hex(self.extract_fingerprint_sha256, "extract_fingerprint_sha256"),
            )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": PROTECTION_UNIT_IDENTITY_DOMAIN,
            "source_semantics_id": self.source_semantics_id,
            "mapping_id": self.mapping_id,
            "reconstruction_contract_id": self.reconstruction_contract_id,
            "protected_support": self.protected_support,
            "artifacts": [artifact.stable_dict() for artifact in self.artifacts],
        }

    @property
    def protection_identity(self) -> str:
        return f"{PROTECTION_UNIT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"

    def stable_dict(self) -> dict[str, Any]:
        payload = self.canonical_payload()
        payload["extract_fingerprint_sha256"] = self.extract_fingerprint_sha256
        return {
            "protection_identity": self.protection_identity,
            "canonical_payload": payload,
        }


@dataclass(frozen=True, slots=True)
class ArtifactVerificationEvidence:
    """One caller-supplied observation of one required artifact's current state.

    ``READ`` is the only outcome that may carry observed content identity;
    ``UNREADABLE``/``ABSENT`` never fabricate a hash or size.
    """

    role: str
    outcome: ArtifactReadOutcome
    observed_content_hash_sha256: str | None = None
    observed_size_bytes: int | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _role(self.role, "role"))
        outcome = _enum(ArtifactReadOutcome, self.outcome, "outcome")
        object.__setattr__(self, "outcome", outcome)
        if outcome == ArtifactReadOutcome.READ:
            object.__setattr__(
                self,
                "observed_content_hash_sha256",
                _sha256_hex(self.observed_content_hash_sha256, "observed_content_hash_sha256"),
            )
            object.__setattr__(
                self,
                "observed_size_bytes",
                _non_negative_int(self.observed_size_bytes, "observed_size_bytes"),
            )
        elif self.observed_content_hash_sha256 is not None or self.observed_size_bytes is not None:
            raise ProtectionError(
                "UNREADABLE/ABSENT evidence must not carry observed content identity"
            )
        if not isinstance(self.detail, str):
            raise ProtectionError("detail must be a string")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "outcome": self.outcome.value,
            "observed_content_hash_sha256": self.observed_content_hash_sha256,
            "observed_size_bytes": self.observed_size_bytes,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ArtifactAssessmentResult:
    """One required artifact's resolved verification outcome.

    ``state`` is restricted to ``PROTECTED`` / ``UNAVAILABLE`` / ``CORRUPT`` /
    ``LOST``: ``AT_RISK`` is a unit-level obligation concept only.
    """

    declared: ArtifactProtectionIdentity
    evidence: ArtifactVerificationEvidence
    state: ProtectionState

    def stable_dict(self) -> dict[str, Any]:
        return {
            "declared": self.declared.stable_dict(),
            "evidence": self.evidence.stable_dict(),
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True)
class ProtectionAssessment:
    """One immutable K06 protection assessment.

    ``delete_authorized`` is fixed ``False``: K06 never authorizes
    destructive removal of source evidence, regardless of state or pressure.
    """

    protection_identity: str
    source_semantics_id: str
    mapping_id: str
    reconstruction_contract_id: str
    protected_support: str
    verified_at: Instant
    verifier_identity: str
    artifact_results: tuple[ArtifactAssessmentResult, ...]
    state: ProtectionState
    obligation_detail: str = ""
    delete_authorized: bool = field(init=False, default=False)

    @property
    def assessment_identity(self) -> str:
        return f"{PROTECTION_ASSESSMENT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self._identity_payload())}"

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": PROTECTION_ASSESSMENT_IDENTITY_DOMAIN,
            "protection_identity": self.protection_identity,
            "source_semantics_id": self.source_semantics_id,
            "mapping_id": self.mapping_id,
            "reconstruction_contract_id": self.reconstruction_contract_id,
            "protected_support": self.protected_support,
            "verified_at": self.verified_at.isoformat(),
            "verifier_identity": self.verifier_identity,
            "artifact_results": [result.stable_dict() for result in self.artifact_results],
            "state": self.state.value,
            "obligation_detail": self.obligation_detail,
            "delete_authorized": self.delete_authorized,
        }

    def stable_dict(self) -> dict[str, Any]:
        payload = self._identity_payload()
        payload["assessment_identity"] = self.assessment_identity
        return payload


@dataclass(frozen=True, slots=True)
class ProtectionWriteAuthorization:
    """One K06 admission decision for a bounded protection-producing write.

    ``delete_authorized`` is fixed ``False`` for the same reason as on
    :class:`ProtectionAssessment`.
    """

    decision: ProtectionWriteDecision
    reason: str
    pressure_state: PressureState | None
    resulting_obligation_state: ProtectionState | None
    delete_authorized: bool = field(init=False, default=False)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason": self.reason,
            "pressure_state": None if self.pressure_state is None else self.pressure_state.value,
            "resulting_obligation_state": (
                None if self.resulting_obligation_state is None else self.resulting_obligation_state.value
            ),
            "delete_authorized": self.delete_authorized,
        }


def assess_protection(
    unit: ProtectionUnitIdentity,
    verifications: tuple[ArtifactVerificationEvidence, ...],
    *,
    verified_at: Instant,
    verifier_identity: str,
    protection_obligation_unmet: bool = False,
    obligation_detail: str = "",
) -> ProtectionAssessment:
    """Deterministically classify one protection unit from caller-supplied evidence.

    ``verified_at`` is the only source of assessment time; this function
    never reads an implicit host clock.  Missing, duplicate or unexpected
    per-role evidence is an explicit fail-closed refusal, never a silent
    substitution.
    """

    if not isinstance(unit, ProtectionUnitIdentity):
        raise ProtectionError("unit must be a ProtectionUnitIdentity")
    if not isinstance(verified_at, Instant):
        raise ProtectionError("verified_at must be a canonical Instant")
    verifier_identity = _non_empty_text(verifier_identity, "verifier_identity")
    if type(protection_obligation_unmet) is not bool:
        raise ProtectionError("protection_obligation_unmet must be a boolean")
    if not isinstance(obligation_detail, str):
        raise ProtectionError("obligation_detail must be a string")

    verification_list = tuple(verifications)
    for index, verification in enumerate(verification_list):
        if not isinstance(verification, ArtifactVerificationEvidence):
            raise ProtectionError(f"verifications[{index}] must be ArtifactVerificationEvidence")

    by_role: dict[str, ArtifactVerificationEvidence] = {}
    for verification in verification_list:
        if verification.role in by_role:
            raise ProtectionError(f"ambiguous verification evidence for role: {verification.role}")
        by_role[verification.role] = verification

    required_roles = {artifact.role for artifact in unit.artifacts}
    missing = sorted(required_roles - set(by_role))
    unexpected = sorted(set(by_role) - required_roles)
    if missing:
        raise ProtectionError("missing verification evidence for role(s): " + ",".join(missing))
    if unexpected:
        raise ProtectionError("verification evidence for undeclared role(s): " + ",".join(unexpected))

    artifact_results = tuple(
        _assess_artifact(artifact, by_role[artifact.role]) for artifact in unit.artifacts
    )

    base_severity = max(_STATE_SEVERITY[result.state] for result in artifact_results)
    if base_severity == _STATE_SEVERITY[ProtectionState.PROTECTED] and protection_obligation_unmet:
        final_severity = _STATE_SEVERITY[ProtectionState.AT_RISK]
    else:
        final_severity = base_severity
    state = _SEVERITY_STATE[final_severity]

    return ProtectionAssessment(
        protection_identity=unit.protection_identity,
        source_semantics_id=unit.source_semantics_id,
        mapping_id=unit.mapping_id,
        reconstruction_contract_id=unit.reconstruction_contract_id,
        protected_support=unit.protected_support,
        verified_at=verified_at,
        verifier_identity=verifier_identity,
        artifact_results=artifact_results,
        state=state,
        obligation_detail=obligation_detail if protection_obligation_unmet else "",
    )


def _assess_artifact(
    declared: ArtifactProtectionIdentity, evidence: ArtifactVerificationEvidence
) -> ArtifactAssessmentResult:
    if evidence.outcome == ArtifactReadOutcome.ABSENT:
        state = ProtectionState.LOST
    elif evidence.outcome == ArtifactReadOutcome.UNREADABLE:
        state = ProtectionState.UNAVAILABLE
    elif (
        evidence.observed_content_hash_sha256 == declared.content_hash_sha256
        and evidence.observed_size_bytes == declared.size_bytes
    ):
        state = ProtectionState.PROTECTED
    else:
        state = ProtectionState.CORRUPT
    return ArtifactAssessmentResult(declared=declared, evidence=evidence, state=state)


def authorize_protection_write(
    *,
    pressure: PressureDecision | PressureDecisionUnavailable,
    is_safety_relevant: bool,
    fits_evidenced_capacity: bool,
) -> ProtectionWriteAuthorization:
    """Admit or refuse one bounded protection-producing write under K05.

    K05 pressure is an upper-bound restriction only; it never grants K06
    mutation authority.  Unavailable pressure evidence is never treated as
    ``NORMAL``.
    """

    if not isinstance(pressure, (PressureDecision, PressureDecisionUnavailable)):
        raise ProtectionError("pressure must be a PressureDecision or PressureDecisionUnavailable")
    if type(is_safety_relevant) is not bool:
        raise ProtectionError("is_safety_relevant must be a boolean")
    if type(fits_evidenced_capacity) is not bool:
        raise ProtectionError("fits_evidenced_capacity must be a boolean")

    if isinstance(pressure, PressureDecisionUnavailable):
        return ProtectionWriteAuthorization(
            decision=ProtectionWriteDecision.DEFERRED,
            reason="pressure evidence is unavailable and is never treated as NORMAL",
            pressure_state=None,
            resulting_obligation_state=ProtectionState.AT_RISK,
        )

    state = pressure.state
    if state == PressureState.NORMAL:
        return ProtectionWriteAuthorization(
            decision=ProtectionWriteDecision.PERMITTED,
            reason="NORMAL pressure adds no restriction to an otherwise-authorized write",
            pressure_state=state,
            resulting_obligation_state=None,
        )
    if state == PressureState.PRESSURE:
        return ProtectionWriteAuthorization(
            decision=ProtectionWriteDecision.PERMITTED,
            reason="unique source protection is safety-relevant, never disposable/recomputable work",
            pressure_state=state,
            resulting_obligation_state=None,
        )
    if state == PressureState.CRITICAL:
        if is_safety_relevant and fits_evidenced_capacity:
            return ProtectionWriteAuthorization(
                decision=ProtectionWriteDecision.PERMITTED,
                reason="independently proven safety-relevant write fits evidenced capacity",
                pressure_state=state,
                resulting_obligation_state=None,
            )
        return ProtectionWriteAuthorization(
            decision=ProtectionWriteDecision.DEFERRED,
            reason="CRITICAL pressure requires independent safety/capacity proof, which was not established",
            pressure_state=state,
            resulting_obligation_state=ProtectionState.AT_RISK,
        )
    if state == PressureState.EXHAUSTED:
        return ProtectionWriteAuthorization(
            decision=ProtectionWriteDecision.REFUSED,
            reason="EXHAUSTED pressure never permits a new data-producing protection copy to start",
            pressure_state=state,
            resulting_obligation_state=ProtectionState.AT_RISK,
        )
    raise AssertionError("unreachable pressure state")  # pragma: no cover


def _role(value: Any, field_name: str) -> str:
    text = _non_empty_text(value, field_name)
    if not _ROLE_RE.fullmatch(text):
        raise ProtectionError(f"{field_name} must be a canonical relative role spelling")
    return text


def _sha256_hex(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_HEX_RE.fullmatch(value):
        raise ProtectionError(f"{field_name} must be 64 lowercase hex characters")
    return value


def _non_negative_int(value: Any, field_name: str) -> int:
    if type(value) is not int:
        raise ProtectionError(f"{field_name} must be an integer")
    if value < 0:
        raise ProtectionError(f"{field_name} must be non-negative")
    return value


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtectionError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if any(ord(character) < 32 for character in text):
        raise ProtectionError(f"{field_name} must not contain control characters")
    return text


def _enum(enum_type: Any, value: Any, field_name: str) -> Any:
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ProtectionError(f"{field_name} is not a supported value") from exc


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _canonical_fingerprint(payload: Any) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


__all__ = [
    "PROTECTION_ASSESSMENT_IDENTITY_DOMAIN",
    "PROTECTION_UNIT_IDENTITY_DOMAIN",
    "ArtifactAssessmentResult",
    "ArtifactProtectionIdentity",
    "ArtifactReadOutcome",
    "ArtifactVerificationEvidence",
    "ProtectionAssessment",
    "ProtectionError",
    "ProtectionState",
    "ProtectionUnitIdentity",
    "ProtectionWriteAuthorization",
    "ProtectionWriteDecision",
    "assess_protection",
    "authorize_protection_write",
]
