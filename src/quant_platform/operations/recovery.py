"""K08 backup/restore v1: RecoverySetV1 identity, a pure identity/validation seam.

Mirrors ``quant_platform.operations.protection``'s discipline: this module
proves nothing about a filesystem, a catalog or a network topology.  It binds
caller-supplied, already-authoritative evidence (a finalized publication's
natural identity, durable manifest digests, declared coverage, physical
content identity and -- as an explicit, mandatory, attributed choice -- either
the full K06 protection assessment required to reconstruct it, or an
attributed assertion that no such evidence is applicable) into one
deterministic ``RecoverySetV1`` identity, and fails closed when that evidence
is not a self-consistent finalized generation.  It never crawls storage,
copies bytes, opens a catalog connection or decides whether a backup
destination is independent of a primary storage boundary -- independence is
an observed deployment property, never inferred here.

K08 v1's recovery contract is restricted to the S13 ``closed`` seal state:
restore always re-admits through the unmodified
``CertificationCatalogWriter`` contract, which always returns a ``closed``
row, so a recovery set never carries an unverifiable/unreproduced lifecycle
label -- what is captured is exactly what is reproduced.

``recovery_set_from_canonical_payload`` is the reverse direction: it
reconstructs a ``RecoverySetV1`` from its own durably persisted
``canonical_payload`` (see ``quant_platform.application.backup_restore``'s
recovery manifest), so an export survives process loss without needing a
surviving in-memory Python object -- every field is re-derived and
re-validated by the same ``__post_init__`` a fresh capture uses, never
trusted as an opaque blob.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Any

from ..data.models import CoverageInterval, DatasetIdentity, Instant, NaturalPartitionIdentity
from .protection import PROTECTION_UNIT_IDENTITY_DOMAIN


RECOVERY_SET_IDENTITY_DOMAIN = "recovery-set-v1"
K06_NOT_APPLICABLE_IDENTITY_DOMAIN = "recovery-k06-not-applicable-v1"

_SHA256_HEX_RE_LEN = 64
_PROTECTION_IDENTITY_RE = re.compile(
    rf"^{re.escape(PROTECTION_UNIT_IDENTITY_DOMAIN)}:sha256:[0-9a-f]{{64}}$"
)


class RecoveryError(ValueError):
    """A K08 recovery value or request violates the frozen v1 contract."""


@dataclass(frozen=True, slots=True)
class K06NotApplicableAssertion:
    """Attributed evidence that no K06-protected source evidence is required.

    Mirrors K06's own ``SafetyRelevanceAssertion``/``ProtectionObligationEvidence``
    discipline: a claim this module cannot independently verify must be
    attributed to an explicit accountable authority and instant, never an
    anonymous boolean or a silently omitted argument.  Binding this into a
    recovery set makes K06 applicability a deterministic, always-present
    decision -- a recovery set either carries protected source evidence or an
    attributed reason it does not, never neither.
    """

    asserting_authority_id: str
    asserted_at: Instant
    rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "asserting_authority_id",
            _non_empty_text(self.asserting_authority_id, "asserting_authority_id"),
        )
        if not isinstance(self.asserted_at, Instant):
            raise RecoveryError("asserted_at must be a canonical Instant")
        object.__setattr__(self, "rationale", _non_empty_text(self.rationale, "rationale"))

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": K06_NOT_APPLICABLE_IDENTITY_DOMAIN,
            "asserting_authority_id": self.asserting_authority_id,
            "asserted_at": self.asserted_at.isoformat(),
            "rationale": self.rationale,
        }

    @property
    def fingerprint(self) -> str:
        return _canonical_fingerprint(self.canonical_payload())


@dataclass(frozen=True, slots=True)
class RecoverySetV1:
    """The identity-bound recovery unit for one finalized canonical publication.

    Composite identity (``recovery_identity``) is derived only from the
    dataset/partition natural identity, durable manifest digests, declared
    coverage, physical content identity and the K06 applicability decision
    (the full bound assessment digest, or the not-applicable assertion's own
    fingerprint) -- never from a catalog row UUID, storage root path or
    discovery order, exactly as ``ProtectionUnitIdentity`` excludes
    filesystem/host accidents from K06 identity.
    ``catalog_dataset_id``/``catalog_partition_id`` are carried as
    informational locators only and never participate in
    :meth:`canonical_payload`.

    Exactly one of (``k06_protection_identity`` and ``k06_assessment_sha256``,
    both present) or ``k06_not_applicable_fingerprint`` must be set: K06
    applicability is a mandatory, attributed decision, never a silent
    omission.  ``k06_assessment_sha256`` binds the *complete* persisted K06
    assessment document (state, per-artifact outcomes, verifier, instant --
    not merely the protection unit's own identity), so a backed-up assessment
    cannot be silently altered as long as only its unit identity is
    preserved.
    """

    natural_identity: NaturalPartitionIdentity
    dataset_manifest_sha256: str
    partition_manifest_sha256: str
    coverage_manifest_sha256: tuple[str, ...]
    physical_content_sha256: str
    physical_size_bytes: int
    declared_coverage: CoverageInterval
    catalog_dataset_id: str
    catalog_partition_id: str
    k06_protection_identity: str | None = None
    k06_assessment_sha256: str | None = None
    k06_not_applicable_fingerprint: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.natural_identity, NaturalPartitionIdentity):
            raise RecoveryError("natural_identity must be NaturalPartitionIdentity")
        object.__setattr__(
            self, "dataset_manifest_sha256",
            _sha256_hex(self.dataset_manifest_sha256, "dataset_manifest_sha256"),
        )
        object.__setattr__(
            self, "partition_manifest_sha256",
            _sha256_hex(self.partition_manifest_sha256, "partition_manifest_sha256"),
        )
        coverage_hashes = tuple(self.coverage_manifest_sha256)
        if not coverage_hashes:
            raise RecoveryError(
                "a recovery set requires at least one declared-coverage manifest digest"
            )
        coverage_hashes = tuple(sorted(
            _sha256_hex(item, "coverage_manifest_sha256") for item in coverage_hashes
        ))
        object.__setattr__(self, "coverage_manifest_sha256", coverage_hashes)
        object.__setattr__(
            self, "physical_content_sha256",
            _sha256_hex(self.physical_content_sha256, "physical_content_sha256"),
        )
        object.__setattr__(
            self, "physical_size_bytes",
            _non_negative_int(self.physical_size_bytes, "physical_size_bytes"),
        )
        if not isinstance(self.declared_coverage, CoverageInterval):
            raise RecoveryError("declared_coverage must be CoverageInterval")
        if self.declared_coverage.start >= self.declared_coverage.end:
            raise RecoveryError("declared_coverage must be a non-degenerate half-open interval")
        object.__setattr__(
            self, "catalog_dataset_id", _non_empty_text(self.catalog_dataset_id, "catalog_dataset_id")
        )
        object.__setattr__(
            self, "catalog_partition_id",
            _non_empty_text(self.catalog_partition_id, "catalog_partition_id"),
        )

        has_protection = self.k06_protection_identity is not None or self.k06_assessment_sha256 is not None
        has_not_applicable = self.k06_not_applicable_fingerprint is not None
        if has_protection and (self.k06_protection_identity is None or self.k06_assessment_sha256 is None):
            raise RecoveryError(
                "k06_protection_identity and k06_assessment_sha256 must be supplied together"
            )
        if has_protection == has_not_applicable:
            raise RecoveryError(
                "a recovery set must bind exactly one of (k06_protection_identity + "
                "k06_assessment_sha256) or k06_not_applicable_fingerprint: K06 applicability "
                "must be an explicit, attributed decision, never both or neither"
            )
        if has_protection:
            object.__setattr__(
                self, "k06_protection_identity",
                _protection_identity(self.k06_protection_identity),
            )
            object.__setattr__(
                self, "k06_assessment_sha256",
                _sha256_hex(self.k06_assessment_sha256, "k06_assessment_sha256"),
            )
        else:
            object.__setattr__(
                self, "k06_not_applicable_fingerprint",
                _sha256_hex(self.k06_not_applicable_fingerprint, "k06_not_applicable_fingerprint"),
            )

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": RECOVERY_SET_IDENTITY_DOMAIN,
            "dataset_identity": self.natural_identity.dataset_identity.stable_dict(),
            "partition_key": self.natural_identity.partition_key,
            "revision": self.natural_identity.revision,
            "dataset_manifest_sha256": self.dataset_manifest_sha256,
            "partition_manifest_sha256": self.partition_manifest_sha256,
            "coverage_manifest_sha256": list(self.coverage_manifest_sha256),
            "physical_content_sha256": self.physical_content_sha256,
            "physical_size_bytes": self.physical_size_bytes,
            "declared_coverage": self.declared_coverage.stable_dict(),
            "k06_protection_identity": self.k06_protection_identity,
            "k06_assessment_sha256": self.k06_assessment_sha256,
            "k06_not_applicable_fingerprint": self.k06_not_applicable_fingerprint,
        }

    @property
    def recovery_identity(self) -> str:
        return f"{RECOVERY_SET_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(self.canonical_payload())}"

    @property
    def fingerprint_hex(self) -> str:
        """Filesystem/path-safe identity: the bare hex digest, no domain tag or colons."""

        return _canonical_fingerprint(self.canonical_payload())

    def stable_dict(self) -> dict[str, Any]:
        return {"recovery_identity": self.recovery_identity, "canonical_payload": self.canonical_payload()}


def recovery_set_from_canonical_payload(
    payload: Mapping[str, Any],
    *,
    catalog_dataset_id: str,
    catalog_partition_id: str,
) -> RecoverySetV1:
    """Reconstruct a ``RecoverySetV1`` from its own durably persisted ``canonical_payload``.

    Every field is re-derived and re-validated by the same
    ``RecoverySetV1.__post_init__`` a fresh capture uses; nothing here is
    trusted as an opaque blob.  ``catalog_dataset_id``/``catalog_partition_id``
    are supplied by the caller because they are informational locators, not
    part of the canonical (identity-bearing) payload -- see
    :meth:`RecoverySetV1.canonical_payload`.
    """

    if not isinstance(payload, Mapping):
        raise RecoveryError("recovery set canonical payload must be a mapping")
    if payload.get("identity_domain") != RECOVERY_SET_IDENTITY_DOMAIN:
        raise RecoveryError("recovery set canonical payload has an unsupported identity_domain")
    try:
        dataset_identity = DatasetIdentity(**payload["dataset_identity"])
        natural_identity = NaturalPartitionIdentity(
            dataset_identity, payload["partition_key"], payload["revision"],
        )
        coverage_document = payload["declared_coverage"]
        declared_coverage = CoverageInterval(
            Instant.parse(coverage_document["start"]), Instant.parse(coverage_document["end"]),
        )
        return RecoverySetV1(
            natural_identity=natural_identity,
            dataset_manifest_sha256=payload["dataset_manifest_sha256"],
            partition_manifest_sha256=payload["partition_manifest_sha256"],
            coverage_manifest_sha256=tuple(payload["coverage_manifest_sha256"]),
            physical_content_sha256=payload["physical_content_sha256"],
            physical_size_bytes=payload["physical_size_bytes"],
            declared_coverage=declared_coverage,
            catalog_dataset_id=catalog_dataset_id,
            catalog_partition_id=catalog_partition_id,
            k06_protection_identity=payload.get("k06_protection_identity"),
            k06_assessment_sha256=payload.get("k06_assessment_sha256"),
            k06_not_applicable_fingerprint=payload.get("k06_not_applicable_fingerprint"),
        )
    except RecoveryError:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise RecoveryError("recovery set canonical payload is malformed") from exc


def _protection_identity(value: Any) -> str:
    if not isinstance(value, str) or not _PROTECTION_IDENTITY_RE.fullmatch(value):
        raise RecoveryError("k06_protection_identity must be a well-formed K06 protection_identity")
    return value


def _sha256_hex(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or len(value) != _SHA256_HEX_RE_LEN:
        raise RecoveryError(f"{field_name} must be 64 lowercase hex characters")
    try:
        int(value, 16)
    except ValueError as exc:
        raise RecoveryError(f"{field_name} must be 64 lowercase hex characters") from exc
    if value != value.lower():
        raise RecoveryError(f"{field_name} must be 64 lowercase hex characters")
    return value


def _non_negative_int(value: Any, field_name: str) -> int:
    if type(value) is not int:
        raise RecoveryError(f"{field_name} must be an integer")
    if value < 0:
        raise RecoveryError(f"{field_name} must be non-negative")
    return value


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RecoveryError(f"{field_name} must be a non-empty string")
    return value.strip()


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
    "K06_NOT_APPLICABLE_IDENTITY_DOMAIN",
    "RECOVERY_SET_IDENTITY_DOMAIN",
    "K06NotApplicableAssertion",
    "RecoveryError",
    "RecoverySetV1",
    "recovery_set_from_canonical_payload",
]
