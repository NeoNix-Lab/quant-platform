"""K08 backup/restore v1: RecoverySetV1 identity, a pure identity/validation seam.

Mirrors ``quant_platform.operations.protection``'s discipline: this module
proves nothing about a filesystem, a catalog or a network topology.  It binds
caller-supplied, already-authoritative evidence (a finalized publication's
natural identity, durable manifest digests, declared coverage and physical
content identity) into one deterministic ``RecoverySetV1`` identity, and
fails closed when that evidence is not a self-consistent finalized
generation.  It never crawls storage, copies bytes, opens a catalog
connection or decides whether a backup destination is independent of a
primary storage boundary -- independence is an observed deployment property,
never inferred here.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from ..data.models import CoverageInterval, NaturalPartitionIdentity


RECOVERY_SET_IDENTITY_DOMAIN = "recovery-set-v1"

# A recovery set protects a *finalized* publication generation only.  These
# are the same lifecycle states the historical DataGateway can read
# (LifecyclePolicy.VALID_CLOSED_AND_DEGRADED, access-owned); operations does
# not import access, so the eligible vocabulary is restated here as the
# frozen set of terminal-but-readable partition states, never as an
# open-ended or inferred set.
FINALIZED_PARTITION_STATES = frozenset({"closed", "valid", "degraded"})

_SHA256_HEX_RE_LEN = 64


class RecoveryError(ValueError):
    """A K08 recovery value or request violates the frozen v1 contract."""


@dataclass(frozen=True, slots=True)
class RecoverySetV1:
    """The identity-bound recovery unit for one finalized canonical publication.

    Composite identity (``recovery_identity``) is derived only from the
    dataset/partition natural identity, durable manifest digests, declared
    coverage and physical content identity -- never from a catalog row UUID,
    storage root path or discovery order, exactly as
    ``ProtectionUnitIdentity`` excludes filesystem/host accidents from K06
    identity.  ``catalog_dataset_id``/``catalog_partition_id`` are carried as
    informational locators only and never participate in
    :meth:`canonical_payload`.
    """

    natural_identity: NaturalPartitionIdentity
    dataset_manifest_sha256: str
    partition_manifest_sha256: str
    coverage_manifest_sha256: tuple[str, ...]
    physical_content_sha256: str
    physical_size_bytes: int
    declared_coverage: CoverageInterval
    partition_state: str
    catalog_dataset_id: str
    catalog_partition_id: str

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
        if self.partition_state not in FINALIZED_PARTITION_STATES:
            raise RecoveryError(
                "a recovery set requires a finalized publication generation; got "
                f"{self.partition_state!r}"
            )
        object.__setattr__(
            self, "catalog_dataset_id", _non_empty_text(self.catalog_dataset_id, "catalog_dataset_id")
        )
        object.__setattr__(
            self, "catalog_partition_id",
            _non_empty_text(self.catalog_partition_id, "catalog_partition_id"),
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
            "partition_state": self.partition_state,
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
    "FINALIZED_PARTITION_STATES",
    "RECOVERY_SET_IDENTITY_DOMAIN",
    "RecoveryError",
    "RecoverySetV1",
]
