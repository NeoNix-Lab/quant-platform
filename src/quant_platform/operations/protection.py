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

Every derived/computed value below is a ``field(init=False)`` recomputed by
its owning ``__post_init__`` from its own already-validated inputs.  A
caller can supply wrong or incomplete evidence and get a correctly-derived
negative result, but cannot construct a self-inconsistent positive one: a
frozen dataclass keeps a value from being *mutated*, not from being wrong at
construction, so validity is enforced by recomputation, never trusted from a
caller-supplied ``state``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import json
import re
from typing import Any

from ..data.models import Instant
from .pressure import (
    PressureDecision,
    PressureDecisionUnavailable,
    PressureRestrictions,
    PressureState,
)


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
    """What a caller actually observed when inspecting one required artifact
    at one local instance/location."""

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
class AcceptedReconstructionContract:
    """Operations-owned caller evidence that one triple is an accepted, complete claim.

    K06 does not maintain a source-semantics registry.  This is the explicit
    proof an owning caller (a source adapter, a future orchestration seam)
    must supply, binding one ``(source_semantics_id, mapping_id,
    reconstruction_contract_id)`` triple to the complete set of artifact
    roles that triple requires.  Without it, an unsupported or incomplete
    triple could otherwise reach ``PROTECTED`` merely because whatever bytes
    happened to be supplied hash-matched themselves.
    """

    source_semantics_id: str
    mapping_id: str
    reconstruction_contract_id: str
    required_roles: tuple[str, ...]

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
        roles = tuple(self.required_roles)
        if not roles:
            raise ProtectionError(
                "an accepted reconstruction contract requires at least one required role"
            )
        normalized = tuple(_role(role, "required_roles") for role in roles)
        if len(set(normalized)) != len(normalized):
            raise ProtectionError("accepted reconstruction contract required_roles must be unique")
        object.__setattr__(self, "required_roles", tuple(sorted(normalized)))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "source_semantics_id": self.source_semantics_id,
            "mapping_id": self.mapping_id,
            "reconstruction_contract_id": self.reconstruction_contract_id,
            "required_roles": list(self.required_roles),
        }


@dataclass(frozen=True, slots=True)
class ProtectionUnitIdentity:
    """The bounded, declared reconstruction claim for one protected support.

    Composite identity is derived only from source semantics, mapping,
    protected support, reconstruction contract and canonically role-sorted
    artifact descriptors.  Filesystem path, mount, storage root, host, inode,
    mtime, ctime and artifact discovery order never participate.

    Construction requires ``accepted_contract`` to bind the same
    source/mapping/reconstruction triple and to declare exactly the set of
    roles ``artifacts`` supplies -- neither missing nor extra -- so an
    unsupported combination or an incomplete artifact set can never become a
    constructible protection claim in the first place.
    """

    source_semantics_id: str
    mapping_id: str
    reconstruction_contract_id: str
    protected_support: str
    artifacts: tuple[ArtifactProtectionIdentity, ...]
    accepted_contract: AcceptedReconstructionContract
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
        artifacts = tuple(sorted(artifacts, key=lambda artifact: artifact.role))
        object.__setattr__(self, "artifacts", artifacts)

        if not isinstance(self.accepted_contract, AcceptedReconstructionContract):
            raise ProtectionError("accepted_contract must be AcceptedReconstructionContract")
        if (
            self.accepted_contract.source_semantics_id != self.source_semantics_id
            or self.accepted_contract.mapping_id != self.mapping_id
            or self.accepted_contract.reconstruction_contract_id != self.reconstruction_contract_id
        ):
            raise ProtectionError(
                "protection unit is not bound to an accepted reconstruction contract "
                "for the same source/mapping/reconstruction triple"
            )
        declared_roles = frozenset(roles)
        required_roles = frozenset(self.accepted_contract.required_roles)
        if declared_roles != required_roles:
            raise ProtectionError(
                "protection unit artifacts do not exactly satisfy the accepted contract's "
                "required roles; missing=" + ",".join(sorted(required_roles - declared_roles))
                + " unexpected=" + ",".join(sorted(declared_roles - required_roles))
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
        payload["accepted_contract"] = self.accepted_contract.stable_dict()
        return {
            "protection_identity": self.protection_identity,
            "canonical_payload": payload,
        }


@dataclass(frozen=True, slots=True)
class ArtifactVerificationEvidence:
    """One caller-supplied observation of one required artifact at one local instance.

    ``instance_scope`` names which K06-local instance/location this
    observation covers (for example ``"hot"`` or ``"mirror-1"``).  A single
    ``ABSENT`` observation only proves absence at that one instance; it
    never by itself proves the artifact is lost -- see
    ``local_instances_exhaustively_checked`` on :func:`assess_protection`.

    ``READ`` is the only outcome that may carry observed content identity;
    ``UNREADABLE``/``ABSENT`` never fabricate a hash or size.
    """

    role: str
    instance_scope: str
    outcome: ArtifactReadOutcome
    observed_content_hash_sha256: str | None = None
    observed_size_bytes: int | None = None
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", _role(self.role, "role"))
        object.__setattr__(
            self, "instance_scope", _non_empty_text(self.instance_scope, "instance_scope")
        )
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
            "instance_scope": self.instance_scope,
            "outcome": self.outcome.value,
            "observed_content_hash_sha256": self.observed_content_hash_sha256,
            "observed_size_bytes": self.observed_size_bytes,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ArtifactAssessmentResult:
    """One required artifact's resolved verification outcome across all supplied instances.

    ``state`` is a ``field(init=False)`` recomputed here from ``declared``,
    ``evidence`` and ``local_instances_exhaustively_checked``: it can never
    be supplied inconsistently by a caller.  It is restricted to
    ``PROTECTED`` / ``UNAVAILABLE`` / ``CORRUPT`` / ``LOST``: ``AT_RISK`` is
    a unit-level obligation concept only.

    ``LOST`` is only reachable when every supplied instance is ``ABSENT``
    *and* the caller has explicitly attested that every known K06-local
    instance was checked; otherwise all-absent evidence resolves to
    ``UNAVAILABLE`` -- proven neither present nor exhaustively absent.
    """

    declared: ArtifactProtectionIdentity
    evidence: tuple[ArtifactVerificationEvidence, ...]
    local_instances_exhaustively_checked: bool
    state: ProtectionState = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.declared, ArtifactProtectionIdentity):
            raise ProtectionError("declared must be ArtifactProtectionIdentity")
        evidence = tuple(self.evidence)
        if not evidence:
            raise ProtectionError(
                "an artifact assessment result requires at least one verification evidence entry"
            )
        for index, item in enumerate(evidence):
            if not isinstance(item, ArtifactVerificationEvidence):
                raise ProtectionError(f"evidence[{index}] must be ArtifactVerificationEvidence")
            if item.role != self.declared.role:
                raise ProtectionError("evidence role must match the declared artifact role")
        scopes = [item.instance_scope for item in evidence]
        duplicates = sorted({scope for scope in scopes if scopes.count(scope) > 1})
        if duplicates:
            raise ProtectionError(
                "ambiguous verification evidence for instance_scope(s): " + ",".join(duplicates)
            )
        evidence = tuple(sorted(evidence, key=lambda item: item.instance_scope))
        object.__setattr__(self, "evidence", evidence)
        if type(self.local_instances_exhaustively_checked) is not bool:
            raise ProtectionError("local_instances_exhaustively_checked must be a boolean")
        object.__setattr__(
            self,
            "state",
            _classify_role(
                self.declared,
                evidence,
                absence_confirmed_exhaustive=self.local_instances_exhaustively_checked,
            ),
        )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "declared": self.declared.stable_dict(),
            "evidence": [item.stable_dict() for item in self.evidence],
            "local_instances_exhaustively_checked": self.local_instances_exhaustively_checked,
            "state": self.state.value,
        }


@dataclass(frozen=True, slots=True)
class ProtectionAssessment:
    """One immutable K06 protection assessment.

    ``state`` and ``delete_authorized`` are both ``field(init=False)``:
    ``state`` is recomputed here from ``artifact_results`` and
    ``protection_obligation_unmet`` (never accepted as a caller-supplied
    value), and ``delete_authorized`` is fixed ``False`` because K06 never
    authorizes destructive removal of source evidence, regardless of state
    or pressure.
    """

    protection_identity: str
    source_semantics_id: str
    mapping_id: str
    reconstruction_contract_id: str
    protected_support: str
    verified_at: Instant
    verifier_identity: str
    artifact_results: tuple[ArtifactAssessmentResult, ...]
    protection_obligation_unmet: bool = False
    obligation_detail: str = ""
    state: ProtectionState = field(init=False)
    delete_authorized: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "protection_identity", _non_empty_text(self.protection_identity, "protection_identity")
        )
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
        if not isinstance(self.verified_at, Instant):
            raise ProtectionError("verified_at must be a canonical Instant")
        object.__setattr__(
            self, "verifier_identity", _non_empty_text(self.verifier_identity, "verifier_identity")
        )
        artifact_results = tuple(self.artifact_results)
        if not artifact_results:
            raise ProtectionError("a protection assessment requires at least one artifact result")
        for index, result in enumerate(artifact_results):
            if not isinstance(result, ArtifactAssessmentResult):
                raise ProtectionError(f"artifact_results[{index}] must be ArtifactAssessmentResult")
        object.__setattr__(self, "artifact_results", artifact_results)
        if type(self.protection_obligation_unmet) is not bool:
            raise ProtectionError("protection_obligation_unmet must be a boolean")
        if not isinstance(self.obligation_detail, str):
            raise ProtectionError("obligation_detail must be a string")
        if not self.protection_obligation_unmet and self.obligation_detail:
            raise ProtectionError("obligation_detail requires protection_obligation_unmet")

        base_severity = max(_STATE_SEVERITY[result.state] for result in artifact_results)
        if base_severity == _STATE_SEVERITY[ProtectionState.PROTECTED] and self.protection_obligation_unmet:
            final_severity = _STATE_SEVERITY[ProtectionState.AT_RISK]
        else:
            final_severity = base_severity
        object.__setattr__(self, "state", _SEVERITY_STATE[final_severity])

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
            "protection_obligation_unmet": self.protection_obligation_unmet,
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

    Bound to the specific ``protection_identity`` and ``write_size_bytes``
    it was decided for, plus the exact K05 ``pressure_decision_identity``
    and ``restrictions`` used to decide, so the decision is reproducible
    evidence rather than an unbound pair of assertions.
    ``fits_evidenced_capacity`` is computed here from real K05 capacity
    evidence, never trusted as a caller-supplied claim.

    ``delete_authorized`` is fixed ``False`` for the same reason as on
    :class:`ProtectionAssessment`.
    """

    protection_identity: str
    write_size_bytes: int
    pressure_decision_identity: str
    pressure_state: PressureState | None
    restrictions: PressureRestrictions | None
    is_safety_relevant: bool
    fits_evidenced_capacity: bool | None
    decision: ProtectionWriteDecision
    reason: str
    resulting_obligation_state: ProtectionState | None
    delete_authorized: bool = field(init=False, default=False)

    def stable_dict(self) -> dict[str, Any]:
        return {
            "protection_identity": self.protection_identity,
            "write_size_bytes": self.write_size_bytes,
            "pressure_decision_identity": self.pressure_decision_identity,
            "pressure_state": None if self.pressure_state is None else self.pressure_state.value,
            "restrictions": None if self.restrictions is None else self.restrictions.stable_dict(),
            "is_safety_relevant": self.is_safety_relevant,
            "fits_evidenced_capacity": self.fits_evidenced_capacity,
            "decision": self.decision.value,
            "reason": self.reason,
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
    local_instances_exhaustively_checked: bool = False,
    protection_obligation_unmet: bool = False,
    obligation_detail: str = "",
) -> ProtectionAssessment:
    """Deterministically classify one protection unit from caller-supplied evidence.

    ``verified_at`` is the only source of assessment time; this function
    never reads an implicit host clock.  Missing, duplicate or unexpected
    per-role evidence is an explicit fail-closed refusal, never a silent
    substitution.  ``local_instances_exhaustively_checked`` must be true for
    an all-``ABSENT`` role to resolve to ``LOST`` rather than fail closed to
    ``UNAVAILABLE``: a single absence observation never overclaims that no
    other K06-local verified instance exists.
    """

    if not isinstance(unit, ProtectionUnitIdentity):
        raise ProtectionError("unit must be a ProtectionUnitIdentity")
    if not isinstance(verified_at, Instant):
        raise ProtectionError("verified_at must be a canonical Instant")
    verifier_identity = _non_empty_text(verifier_identity, "verifier_identity")
    if type(local_instances_exhaustively_checked) is not bool:
        raise ProtectionError("local_instances_exhaustively_checked must be a boolean")
    if type(protection_obligation_unmet) is not bool:
        raise ProtectionError("protection_obligation_unmet must be a boolean")
    if not isinstance(obligation_detail, str):
        raise ProtectionError("obligation_detail must be a string")

    verification_list = tuple(verifications)
    for index, verification in enumerate(verification_list):
        if not isinstance(verification, ArtifactVerificationEvidence):
            raise ProtectionError(f"verifications[{index}] must be ArtifactVerificationEvidence")

    by_role: dict[str, list[ArtifactVerificationEvidence]] = {}
    for verification in verification_list:
        by_role.setdefault(verification.role, []).append(verification)

    required_roles = {artifact.role for artifact in unit.artifacts}
    supplied_roles = set(by_role)
    missing = sorted(required_roles - supplied_roles)
    unexpected = sorted(supplied_roles - required_roles)
    if missing:
        raise ProtectionError("missing verification evidence for role(s): " + ",".join(missing))
    if unexpected:
        raise ProtectionError("verification evidence for undeclared role(s): " + ",".join(unexpected))

    artifact_results = tuple(
        ArtifactAssessmentResult(
            declared=artifact,
            evidence=tuple(by_role[artifact.role]),
            local_instances_exhaustively_checked=local_instances_exhaustively_checked,
        )
        for artifact in unit.artifacts
    )

    return ProtectionAssessment(
        protection_identity=unit.protection_identity,
        source_semantics_id=unit.source_semantics_id,
        mapping_id=unit.mapping_id,
        reconstruction_contract_id=unit.reconstruction_contract_id,
        protected_support=unit.protected_support,
        verified_at=verified_at,
        verifier_identity=verifier_identity,
        artifact_results=artifact_results,
        protection_obligation_unmet=protection_obligation_unmet,
        obligation_detail=obligation_detail if protection_obligation_unmet else "",
    )


def _classify_role(
    declared: ArtifactProtectionIdentity,
    evidence: tuple[ArtifactVerificationEvidence, ...],
    *,
    absence_confirmed_exhaustive: bool,
) -> ProtectionState:
    """Resolve one role's state from every supplied per-instance observation.

    A matching instance anywhere proves the reconstruction claim regardless
    of other instances' problems.  Failing that, a readable-but-wrong
    instance is ``CORRUPT`` evidence in its own right.  Failing that, an
    unreadable instance means integrity cannot be established -- fail closed
    to ``UNAVAILABLE`` rather than assume absence.  Only when every supplied
    instance is genuinely ``ABSENT`` *and* the caller attests that every
    known local instance was checked does the role become ``LOST``.
    """

    if any(
        item.outcome == ArtifactReadOutcome.READ
        and item.observed_content_hash_sha256 == declared.content_hash_sha256
        and item.observed_size_bytes == declared.size_bytes
        for item in evidence
    ):
        return ProtectionState.PROTECTED
    if any(item.outcome == ArtifactReadOutcome.READ for item in evidence):
        return ProtectionState.CORRUPT
    if any(item.outcome == ArtifactReadOutcome.UNREADABLE for item in evidence):
        return ProtectionState.UNAVAILABLE
    # Every supplied instance is ABSENT.
    if absence_confirmed_exhaustive:
        return ProtectionState.LOST
    return ProtectionState.UNAVAILABLE


def authorize_protection_write(
    *,
    protection_identity: str,
    write_size_bytes: int,
    pressure: PressureDecision | PressureDecisionUnavailable,
    is_safety_relevant: bool,
) -> ProtectionWriteAuthorization:
    """Admit or refuse one bounded protection-producing write under K05.

    ``fits_evidenced_capacity`` is computed here from ``pressure``'s own
    capacity evidence against ``write_size_bytes``; it is never a trusted
    caller assertion.  K05 pressure is an upper-bound restriction only; it
    never grants K06 mutation authority.  Unavailable pressure evidence is
    never treated as ``NORMAL``.
    """

    if not isinstance(pressure, (PressureDecision, PressureDecisionUnavailable)):
        raise ProtectionError("pressure must be a PressureDecision or PressureDecisionUnavailable")
    protection_identity = _non_empty_text(protection_identity, "protection_identity")
    write_size_bytes = _non_negative_int(write_size_bytes, "write_size_bytes")
    if type(is_safety_relevant) is not bool:
        raise ProtectionError("is_safety_relevant must be a boolean")

    if isinstance(pressure, PressureDecisionUnavailable):
        return ProtectionWriteAuthorization(
            protection_identity=protection_identity,
            write_size_bytes=write_size_bytes,
            pressure_decision_identity=pressure.decision_identity,
            pressure_state=None,
            restrictions=None,
            is_safety_relevant=is_safety_relevant,
            fits_evidenced_capacity=None,
            decision=ProtectionWriteDecision.DEFERRED,
            reason="pressure evidence is unavailable and is never treated as NORMAL",
            resulting_obligation_state=ProtectionState.AT_RISK,
        )

    available_bytes = pressure.capacity_evidence.get("available_bytes")
    if not isinstance(available_bytes, int) or isinstance(available_bytes, bool):
        raise ProtectionError("pressure capacity evidence must expose an integer available_bytes")
    fits_evidenced_capacity = write_size_bytes <= available_bytes

    state = pressure.state
    restrictions = pressure.restrictions

    def authorization(
        decision: ProtectionWriteDecision,
        reason: str,
        resulting_obligation_state: ProtectionState | None = None,
    ) -> ProtectionWriteAuthorization:
        return ProtectionWriteAuthorization(
            protection_identity=protection_identity,
            write_size_bytes=write_size_bytes,
            pressure_decision_identity=pressure.decision_identity,
            pressure_state=state,
            restrictions=restrictions,
            is_safety_relevant=is_safety_relevant,
            fits_evidenced_capacity=fits_evidenced_capacity,
            decision=decision,
            reason=reason,
            resulting_obligation_state=resulting_obligation_state,
        )

    if state == PressureState.NORMAL:
        return authorization(
            ProtectionWriteDecision.PERMITTED,
            "NORMAL pressure adds no restriction to an otherwise-authorized write",
        )
    if state == PressureState.PRESSURE:
        return authorization(
            ProtectionWriteDecision.PERMITTED,
            "unique source protection is safety-relevant, never disposable/recomputable work",
        )
    if state == PressureState.CRITICAL:
        if is_safety_relevant and fits_evidenced_capacity:
            return authorization(
                ProtectionWriteDecision.PERMITTED,
                "independently proven safety-relevant write fits evidenced capacity",
            )
        return authorization(
            ProtectionWriteDecision.DEFERRED,
            "CRITICAL pressure requires independent safety/capacity proof, which was not established",
            ProtectionState.AT_RISK,
        )
    if state == PressureState.EXHAUSTED:
        return authorization(
            ProtectionWriteDecision.REFUSED,
            "EXHAUSTED pressure never permits a new data-producing protection copy to start",
            ProtectionState.AT_RISK,
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
    "AcceptedReconstructionContract",
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
