"""K07 storage-tier relocation v1 pure domain rules.

This module owns only deterministic relocation admission, identity,
verification and phase-transition semantics.  It does not inspect filesystems,
talk to a catalog, schedule work or authorize deletion.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
import hashlib
import re
from typing import Any

from ..data.models import DatasetIdentity, Instant
from .pressure import PressureDecision, PressureDecisionUnavailable, PressureState
from .protection import ProtectionAssessment, ProtectionState
from quant_platform.canonical import canonical_bytes


RELOCATION_RECORD_V1_SCHEMA_VERSION = "relocation-record-v1"
RELOCATION_IDENTITY_DOMAIN = "relocation-v1"
_ADMISSIBLE_PARTITION_STATES = frozenset({"valid", "closed", "degraded"})
_ADMISSIBLE_PRESSURE_STATES = frozenset({PressureState.NORMAL, PressureState.PRESSURE})
_ADMISSIBLE_PROTECTION_STATES = frozenset({ProtectionState.PROTECTED, ProtectionState.AT_RISK})


class RelocationPhase(StrEnum):
    PLANNED = "PLANNED"
    STAGED = "STAGED"
    VERIFIED = "VERIFIED"
    SWITCHED = "SWITCHED"
    CLEANED_UP = "CLEANED_UP"
    REFUSED = "REFUSED"


class RelocationError(ValueError):
    """Base class for K07 relocation semantic errors."""


class RelocationDomainMismatch(RelocationError):
    """A transition attempted to cross relocation identity domains."""


class RelocationPhaseError(RelocationError):
    """A relocation transition violates ADR-0048 phase ordering."""


class RelocationVerificationFailed(RelocationError):
    """Target content identity did not match the source catalog identity."""


class RelocationRefused(RelocationError):
    """Relocation is inadmissible under K05/K06/lifecycle gates."""


@dataclass(frozen=True, slots=True)
class RelocationPlan:
    dataset_identity: DatasetIdentity
    catalog_partition_id: str
    partition_key: str
    revision: int
    source_storage_root_id: str
    target_storage_root_id: str
    dataset_rel_root: str
    rel_path: str
    expected_content_sha256: str
    expected_size_bytes: int
    partition_state: str
    pressure_decision_identity: str
    protection_assessment_identity: str

    def __post_init__(self) -> None:
        if not isinstance(self.dataset_identity, DatasetIdentity):
            raise RelocationError("dataset_identity must be DatasetIdentity")
        object.__setattr__(self, "catalog_partition_id", _non_empty_text(self.catalog_partition_id, "catalog_partition_id"))
        object.__setattr__(self, "partition_key", _partition_key(self.partition_key))
        object.__setattr__(self, "revision", _positive_int(self.revision, "revision"))
        source = _root_id(self.source_storage_root_id, "source_storage_root_id")
        target = _root_id(self.target_storage_root_id, "target_storage_root_id")
        if source == target:
            raise RelocationRefused("source and target storage roots must differ")
        object.__setattr__(self, "source_storage_root_id", source)
        object.__setattr__(self, "target_storage_root_id", target)
        object.__setattr__(self, "dataset_rel_root", _rel_path(self.dataset_rel_root, "dataset_rel_root"))
        object.__setattr__(self, "rel_path", _rel_path(self.rel_path, "rel_path"))
        object.__setattr__(
            self,
            "expected_content_sha256",
            _sha256_hex(self.expected_content_sha256, "expected_content_sha256"),
        )
        object.__setattr__(self, "expected_size_bytes", _non_negative_int(self.expected_size_bytes, "expected_size_bytes"))
        state = _non_empty_text(self.partition_state, "partition_state")
        if state not in _ADMISSIBLE_PARTITION_STATES:
            raise RelocationRefused(f"partition state {state!r} is not relocation eligible")
        object.__setattr__(self, "partition_state", state)
        object.__setattr__(
            self,
            "pressure_decision_identity",
            _identity(self.pressure_decision_identity, "pressure_decision_identity"),
        )
        object.__setattr__(
            self,
            "protection_assessment_identity",
            _identity(self.protection_assessment_identity, "protection_assessment_identity"),
        )

    @property
    def relocation_id(self) -> str:
        payload = {
            "identity_domain": RELOCATION_IDENTITY_DOMAIN,
            "dataset_identity": self.dataset_identity.stable_dict(),
            "partition_key": self.partition_key,
            "revision": self.revision,
            "source_storage_root_id": self.source_storage_root_id,
            "target_storage_root_id": self.target_storage_root_id,
        }
        return f"{RELOCATION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}"

    def planned_record(self, *, updated_at: Instant | datetime | str) -> "RelocationRecordV1":
        return RelocationRecordV1.from_plan(self, phase=RelocationPhase.PLANNED, updated_at=updated_at)


@dataclass(frozen=True, slots=True)
class RelocationRecordV1:
    dataset_identity: DatasetIdentity
    catalog_partition_id: str
    partition_key: str
    revision: int
    source_storage_root_id: str
    target_storage_root_id: str
    dataset_rel_root: str
    rel_path: str
    expected_content_sha256: str
    expected_size_bytes: int
    phase: RelocationPhase
    updated_at: Instant
    target_content_sha256: str | None = None
    target_size_bytes: int | None = None
    refusal_reason: str | None = None

    @classmethod
    def from_plan(
        cls,
        plan: RelocationPlan,
        *,
        phase: RelocationPhase,
        updated_at: Instant | datetime | str,
        target_content_sha256: str | None = None,
        target_size_bytes: int | None = None,
        refusal_reason: str | None = None,
    ) -> "RelocationRecordV1":
        return cls(
            dataset_identity=plan.dataset_identity,
            catalog_partition_id=plan.catalog_partition_id,
            partition_key=plan.partition_key,
            revision=plan.revision,
            source_storage_root_id=plan.source_storage_root_id,
            target_storage_root_id=plan.target_storage_root_id,
            dataset_rel_root=plan.dataset_rel_root,
            rel_path=plan.rel_path,
            expected_content_sha256=plan.expected_content_sha256,
            expected_size_bytes=plan.expected_size_bytes,
            phase=phase,
            updated_at=Instant.parse(updated_at),
            target_content_sha256=target_content_sha256,
            target_size_bytes=target_size_bytes,
            refusal_reason=refusal_reason,
        )

    def __post_init__(self) -> None:
        plan = RelocationPlan(
            dataset_identity=self.dataset_identity,
            catalog_partition_id=self.catalog_partition_id,
            partition_key=self.partition_key,
            revision=self.revision,
            source_storage_root_id=self.source_storage_root_id,
            target_storage_root_id=self.target_storage_root_id,
            dataset_rel_root=self.dataset_rel_root,
            rel_path=self.rel_path,
            expected_content_sha256=self.expected_content_sha256,
            expected_size_bytes=self.expected_size_bytes,
            partition_state="valid",
            pressure_decision_identity="pressure-decision-v1:sha256:" + "0" * 64,
            protection_assessment_identity="protection-assessment-v1:sha256:" + "0" * 64,
        )
        object.__setattr__(self, "dataset_identity", plan.dataset_identity)
        object.__setattr__(self, "catalog_partition_id", plan.catalog_partition_id)
        object.__setattr__(self, "partition_key", plan.partition_key)
        object.__setattr__(self, "revision", plan.revision)
        object.__setattr__(self, "source_storage_root_id", plan.source_storage_root_id)
        object.__setattr__(self, "target_storage_root_id", plan.target_storage_root_id)
        object.__setattr__(self, "dataset_rel_root", plan.dataset_rel_root)
        object.__setattr__(self, "rel_path", plan.rel_path)
        object.__setattr__(self, "expected_content_sha256", plan.expected_content_sha256)
        object.__setattr__(self, "expected_size_bytes", plan.expected_size_bytes)
        object.__setattr__(self, "phase", _phase(self.phase))
        object.__setattr__(self, "updated_at", Instant.parse(self.updated_at))
        if self.target_content_sha256 is not None:
            object.__setattr__(
                self,
                "target_content_sha256",
                _sha256_hex(self.target_content_sha256, "target_content_sha256"),
            )
        if self.target_size_bytes is not None:
            object.__setattr__(self, "target_size_bytes", _non_negative_int(self.target_size_bytes, "target_size_bytes"))
        if self.refusal_reason is not None:
            object.__setattr__(self, "refusal_reason", _non_empty_text(self.refusal_reason, "refusal_reason"))

        if self.phase in {RelocationPhase.VERIFIED, RelocationPhase.SWITCHED, RelocationPhase.CLEANED_UP}:
            if self.target_content_sha256 is None or self.target_size_bytes is None:
                raise RelocationPhaseError("verified or later relocation records require target identity")
            if not verify_target_identity(
                self.expected_content_sha256,
                self.expected_size_bytes,
                self.target_content_sha256,
                self.target_size_bytes,
            ):
                raise RelocationVerificationFailed("target identity does not match expected partition identity")
        if self.phase == RelocationPhase.REFUSED and self.refusal_reason is None:
            raise RelocationPhaseError("REFUSED relocation records require refusal_reason")

    @property
    def relocation_id(self) -> str:
        return f"{RELOCATION_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self._identity_payload())}"

    def _identity_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": RELOCATION_IDENTITY_DOMAIN,
            "dataset_identity": self.dataset_identity.stable_dict(),
            "partition_key": self.partition_key,
            "revision": self.revision,
            "source_storage_root_id": self.source_storage_root_id,
            "target_storage_root_id": self.target_storage_root_id,
        }

    def with_phase(
        self,
        phase: RelocationPhase,
        *,
        updated_at: Instant | datetime | str,
        target_content_sha256: str | None = None,
        target_size_bytes: int | None = None,
        refusal_reason: str | None = None,
    ) -> "RelocationRecordV1":
        return replace(
            self,
            phase=phase,
            updated_at=Instant.parse(updated_at),
            target_content_sha256=self.target_content_sha256 if target_content_sha256 is None else target_content_sha256,
            target_size_bytes=self.target_size_bytes if target_size_bytes is None else target_size_bytes,
            refusal_reason=self.refusal_reason if refusal_reason is None else refusal_reason,
        )

    def stable_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RELOCATION_RECORD_V1_SCHEMA_VERSION,
            "relocation_id": self.relocation_id,
            "dataset_identity": self.dataset_identity.stable_dict(),
            "catalog_partition_id": self.catalog_partition_id,
            "partition_key": self.partition_key,
            "revision": self.revision,
            "source_storage_root_id": self.source_storage_root_id,
            "target_storage_root_id": self.target_storage_root_id,
            "dataset_rel_root": self.dataset_rel_root,
            "rel_path": self.rel_path,
            "expected_content_sha256": self.expected_content_sha256,
            "expected_size_bytes": self.expected_size_bytes,
            "phase": self.phase.value,
            "target_content_sha256": self.target_content_sha256,
            "target_size_bytes": self.target_size_bytes,
            "refusal_reason": self.refusal_reason,
            "updated_at": self.updated_at.isoformat(),
        }


def plan_relocation(
    *,
    dataset_identity: DatasetIdentity,
    catalog_partition_id: str,
    partition_key: str,
    revision: int,
    source_storage_root_id: str,
    target_storage_root_id: str,
    dataset_rel_root: str,
    rel_path: str,
    content_sha256: str,
    byte_size: int,
    partition_state: str,
    target_pressure: PressureDecision | PressureDecisionUnavailable,
    protection: ProtectionAssessment,
) -> RelocationPlan:
    """Admit one partition relocation under ADR-0048 gates."""

    if isinstance(target_pressure, PressureDecisionUnavailable):
        raise RelocationRefused("target pressure state is unavailable")
    if not isinstance(target_pressure, PressureDecision):
        raise RelocationError("target_pressure must be PressureDecision or PressureDecisionUnavailable")
    if target_pressure.storage_root_id != _root_id(target_storage_root_id, "target_storage_root_id"):
        raise RelocationDomainMismatch("pressure decision is not bound to target_storage_root_id")
    if target_pressure.state not in _ADMISSIBLE_PRESSURE_STATES:
        raise RelocationRefused(f"target pressure state {target_pressure.state.value!r} refuses relocation")

    if not isinstance(protection, ProtectionAssessment):
        raise RelocationError("protection must be ProtectionAssessment")
    if protection.state not in _ADMISSIBLE_PROTECTION_STATES:
        raise RelocationRefused(f"protection state {protection.state.value!r} refuses relocation")

    return RelocationPlan(
        dataset_identity=dataset_identity,
        catalog_partition_id=catalog_partition_id,
        partition_key=partition_key,
        revision=revision,
        source_storage_root_id=source_storage_root_id,
        target_storage_root_id=target_storage_root_id,
        dataset_rel_root=dataset_rel_root,
        rel_path=rel_path,
        expected_content_sha256=content_sha256,
        expected_size_bytes=byte_size,
        partition_state=partition_state,
        pressure_decision_identity=target_pressure.decision_identity,
        protection_assessment_identity=protection.assessment_identity,
    )


def verify_target_identity(
    expected_sha256: str,
    expected_size: int,
    actual_sha256: str | None,
    actual_size: int | None,
) -> bool:
    """Return whether a staged target byte stream equals the catalog identity."""

    try:
        expected_hash = _sha256_hex(expected_sha256, "expected_sha256")
        actual_hash = _sha256_hex(actual_sha256 or "", "actual_sha256")
        expected_len = _non_negative_int(expected_size, "expected_size")
        actual_len = _non_negative_int(actual_size, "actual_size")
    except RelocationError:
        return False
    return expected_hash == actual_hash and expected_len == actual_len


def advance_relocation(
    previous: RelocationRecordV1 | None,
    candidate: RelocationRecordV1,
) -> RelocationRecordV1:
    """Validate one durable K07 phase transition."""

    if not isinstance(candidate, RelocationRecordV1):
        raise RelocationError("candidate must be RelocationRecordV1")
    if previous is None:
        if candidate.phase != RelocationPhase.PLANNED:
            raise RelocationPhaseError("a relocation must start at PLANNED")
        return candidate
    if not isinstance(previous, RelocationRecordV1):
        raise RelocationError("previous must be RelocationRecordV1 or None")
    if previous.relocation_id != candidate.relocation_id:
        raise RelocationDomainMismatch("relocation_id mismatch")
    if previous.stable_dict() == candidate.stable_dict():
        return previous
    allowed = {
        RelocationPhase.PLANNED: {RelocationPhase.STAGED, RelocationPhase.REFUSED},
        RelocationPhase.STAGED: {RelocationPhase.VERIFIED, RelocationPhase.REFUSED},
        RelocationPhase.VERIFIED: {RelocationPhase.STAGED, RelocationPhase.SWITCHED},
        RelocationPhase.SWITCHED: {RelocationPhase.CLEANED_UP},
        RelocationPhase.CLEANED_UP: set(),
        RelocationPhase.REFUSED: set(),
    }
    if candidate.phase not in allowed[previous.phase]:
        raise RelocationPhaseError(f"invalid relocation transition {previous.phase.value} -> {candidate.phase.value}")
    if previous.phase == RelocationPhase.VERIFIED and candidate.phase == RelocationPhase.STAGED:
        if candidate.target_content_sha256 != previous.target_content_sha256 or candidate.target_size_bytes != previous.target_size_bytes:
            raise RelocationPhaseError("VERIFIED -> STAGED preserves the last observed target identity")
    return candidate


def _canonical_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: dict[str, Any]) -> str:
    return canonical_bytes(payload, profile="sorted-compact-ascii-v1", allow_nan=True).decode("utf-8")


def _phase(value: RelocationPhase | str) -> RelocationPhase:
    if isinstance(value, RelocationPhase):
        return value
    try:
        return RelocationPhase(value)
    except ValueError as exc:
        raise RelocationPhaseError("unknown relocation phase") from exc


def _identity(value: str, field: str) -> str:
    text = _non_empty_text(value, field)
    if ":sha256:" not in text:
        raise RelocationError(f"{field} must be a content identity")
    suffix = text.rsplit(":sha256:", 1)[1]
    _sha256_hex(suffix, field)
    return text


def _sha256_hex(value: str, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise RelocationError(f"{field} must be 64 lowercase hex characters")
    return value


def _non_empty_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RelocationError(f"{field} must be a non-empty string")
    if any(ord(char) < 32 for char in value):
        raise RelocationError(f"{field} must not contain control characters")
    return value.strip()


def _root_id(value: str, field: str) -> str:
    text = _non_empty_text(value, field)
    if re.fullmatch(r"[a-z][a-z0-9._-]*", text) is None:
        raise RelocationError(f"{field} must be a canonical storage root id")
    return text


def _partition_key(value: str) -> str:
    text = _non_empty_text(value, "partition_key")
    if re.fullmatch(r"[a-z_]+=[A-Za-z0-9._-]+(/[a-z_]+=[A-Za-z0-9._-]+)*", text) is None:
        raise RelocationError("partition_key is not canonical")
    return text


def _rel_path(value: str, field: str) -> str:
    text = _non_empty_text(value, field).replace("\\", "/")
    if text.startswith("/") or text.startswith("../") or "/../" in text or text.endswith("/.."):
        raise RelocationError(f"{field} must be a relative path without traversal")
    if text == "." or text.startswith("./") or "/./" in text:
        raise RelocationError(f"{field} must not contain current-directory segments")
    return text


def _positive_int(value: int, field: str) -> int:
    if type(value) is not int or value <= 0:
        raise RelocationError(f"{field} must be a positive integer")
    return value


def _non_negative_int(value: int, field: str) -> int:
    if type(value) is not int or value < 0:
        raise RelocationError(f"{field} must be a non-negative integer")
    return value


def utc_now_instant() -> Instant:
    """Return a microsecond-precision UTC instant for application composition."""

    return Instant.parse(datetime.now(timezone.utc))


__all__ = [
    "RELOCATION_IDENTITY_DOMAIN",
    "RELOCATION_RECORD_V1_SCHEMA_VERSION",
    "RelocationDomainMismatch",
    "RelocationError",
    "RelocationPhase",
    "RelocationPhaseError",
    "RelocationPlan",
    "RelocationRecordV1",
    "RelocationRefused",
    "RelocationVerificationFailed",
    "advance_relocation",
    "plan_relocation",
    "utc_now_instant",
    "verify_target_identity",
]
