"""Server-owned K12 admitted-input manifests and delivery evidence.

This in-process persistence seam deliberately has no transport or filesystem
locator API.  It records the immutable input selected by the server, then
allows a deck delivery attempt to be evidenced against that sealed manifest.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
import sqlite3
from typing import Any

from quant_platform.canonical import canonical_bytes


class AdmittedInputError(RuntimeError):
    """Base class for admitted-input refusals."""


class AdmittedInputConflict(AdmittedInputError):
    """An immutable admission or delivery transition conflicts."""


class AdmittedInputExpired(AdmittedInputError):
    """An expired admission cannot be revived."""


class AdmittedInputNotFound(AdmittedInputError):
    """The requested server admission is absent."""


class AdmittedInputState(StrEnum):
    ADMITTED = "ADMITTED"
    SEALED = "SEALED"
    DELIVERY_PENDING = "DELIVERY_PENDING"
    DELIVERED = "DELIVERED"
    DELIVERY_FAILED = "DELIVERY_FAILED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class AdmittedInputManifestV1:
    """The fixed v1 identity payload for a server-owned admitted input.

    Operational locators intentionally have no field here.  A delivery may use
    one, but it cannot affect the payload, digest, or admission identity.
    """

    logical_input_identities: tuple[str, ...]
    natural_partition_identities: tuple[str, ...]
    schema_identity: str | None
    schema_version: str | None
    schema_hash: str | None
    manifest_hashes: tuple[str, ...]
    content_hashes: tuple[str, ...]
    declared_coverage: Mapping[str, Any] | None
    request_identity: str | None
    result_identity: str | None
    definition_identities: tuple[str, ...]
    implementation_identity: str | None
    git_identity: str | None
    operation_identity: str
    profile_identity: str | None

    def __post_init__(self) -> None:
        for name in (
            "logical_input_identities",
            "natural_partition_identities",
            "manifest_hashes",
            "content_hashes",
            "definition_identities",
        ):
            values = tuple(getattr(self, name))
            if any(not isinstance(value, str) or not value.strip() for value in values):
                raise ValueError(f"{name} must contain non-empty strings")
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")
            object.__setattr__(self, name, values)
        if not self.logical_input_identities:
            raise ValueError("logical_input_identities must not be empty")
        for name in (
            "schema_identity",
            "schema_version",
            "schema_hash",
            "request_identity",
            "result_identity",
            "implementation_identity",
            "git_identity",
            "profile_identity",
        ):
            _optional_identity(getattr(self, name), name)
        if not isinstance(self.operation_identity, str) or not self.operation_identity.strip():
            raise ValueError("operation_identity must be a non-empty string")
        if self.declared_coverage is not None and not isinstance(self.declared_coverage, Mapping):
            raise TypeError("declared_coverage must be a mapping or None")
        _canonical_json(self.canonical_fields())

    def canonical_fields(self) -> dict[str, Any]:
        """Return exactly the immutable fields frozen by ADR-0064 section 2."""
        return {
            "content_hashes": list(self.content_hashes),
            "declared_coverage": dict(self.declared_coverage) if self.declared_coverage is not None else None,
            "definition_identities": sorted(self.definition_identities),
            "git_identity": self.git_identity,
            "implementation_identity": self.implementation_identity,
            "logical_input_identities": sorted(self.logical_input_identities),
            "manifest_hashes": list(self.manifest_hashes),
            "natural_partition_identities": list(self.natural_partition_identities),
            "operation_identity": self.operation_identity,
            "profile_identity": self.profile_identity,
            "request_identity": self.request_identity,
            "result_identity": self.result_identity,
            "schema_hash": self.schema_hash,
            "schema_identity": self.schema_identity,
            "schema_version": self.schema_version,
            "state": AdmittedInputState.SEALED.value,
            "version": "admitted-input-manifest-v1",
        }

    @property
    def canonical_payload_v1(self) -> str:
        return _canonical_json(self.canonical_fields())

    @property
    def manifest_digest(self) -> str:
        return hashlib.sha256(self.canonical_payload_v1.encode("utf-8")).hexdigest()

    @property
    def admission_id(self) -> str:
        return f"admitted-input-v1:{self.manifest_digest}"


@dataclass(frozen=True, slots=True)
class AdmittedInputRecord:
    admission_id: str
    manifest: AdmittedInputManifestV1
    state: AdmittedInputState
    delivery_failure_reason: str | None


class AdmittedInputStore:
    """SQLite-backed server evidence store for K12 admission and delivery."""

    def __init__(self, connection: sqlite3.Connection):
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be a sqlite3.Connection")
        self.connection = connection
        with self.connection:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS admitted_inputs ("
                "admission_id TEXT PRIMARY KEY, manifest_payload TEXT NOT NULL, state TEXT NOT NULL, "
                "delivery_failure_reason TEXT)"
            )

    def admit(self, manifest: AdmittedInputManifestV1) -> AdmittedInputRecord:
        if not isinstance(manifest, AdmittedInputManifestV1):
            raise TypeError("manifest must be an AdmittedInputManifestV1")
        payload = manifest.canonical_payload_v1
        with self.connection:
            row = self.connection.execute(
                "SELECT manifest_payload, state FROM admitted_inputs WHERE admission_id = ?",
                (manifest.admission_id,),
            ).fetchone()
            if row is None:
                self.connection.execute(
                    "INSERT INTO admitted_inputs (admission_id, manifest_payload, state) VALUES (?, ?, ?)",
                    (manifest.admission_id, payload, AdmittedInputState.ADMITTED.value),
                )
            elif str(row[0]) != payload:
                raise AdmittedInputConflict("admission identity payload conflicts")
            elif AdmittedInputState(str(row[1])) == AdmittedInputState.EXPIRED:
                raise AdmittedInputExpired("expired admission requires a new server admission")
        return self.get(manifest.admission_id)

    def get(self, admission_id: str) -> AdmittedInputRecord:
        row = self.connection.execute(
            "SELECT admission_id, manifest_payload, state, delivery_failure_reason "
            "FROM admitted_inputs WHERE admission_id = ?",
            (admission_id,),
        ).fetchone()
        if row is None:
            raise AdmittedInputNotFound("admitted input is absent")
        manifest = _manifest_from_payload(str(row[1]))
        if manifest.admission_id != str(row[0]):
            raise AdmittedInputConflict("stored manifest digest does not match admission identity")
        return AdmittedInputRecord(
            admission_id=str(row[0]),
            manifest=manifest,
            state=AdmittedInputState(str(row[2])),
            delivery_failure_reason=row[3],
        )

    def seal(self, admission_id: str) -> AdmittedInputRecord:
        return self._transition(admission_id, AdmittedInputState.ADMITTED, AdmittedInputState.SEALED)

    def begin_delivery(self, admission_id: str) -> AdmittedInputRecord:
        current = self.get(admission_id)
        if current.state == AdmittedInputState.EXPIRED:
            raise AdmittedInputExpired("expired admission requires a new server admission")
        if current.state not in {AdmittedInputState.SEALED, AdmittedInputState.DELIVERY_FAILED}:
            raise AdmittedInputConflict("delivery requires SEALED evidence")
        with self.connection:
            self.connection.execute(
                "UPDATE admitted_inputs SET state = ?, delivery_failure_reason = NULL WHERE admission_id = ?",
                (AdmittedInputState.DELIVERY_PENDING.value, admission_id),
            )
        return self.get(admission_id)

    def record_delivery(self, admission_id: str, delivered_manifest_digest: str) -> AdmittedInputRecord:
        current = self.get(admission_id)
        if current.state != AdmittedInputState.DELIVERY_PENDING:
            raise AdmittedInputConflict("delivery result requires DELIVERY_PENDING")
        if not isinstance(delivered_manifest_digest, str) or not delivered_manifest_digest.strip():
            raise ValueError("delivered_manifest_digest must be a non-empty string")
        matched = delivered_manifest_digest == current.manifest.manifest_digest
        with self.connection:
            self.connection.execute(
                "UPDATE admitted_inputs SET state = ?, delivery_failure_reason = ? WHERE admission_id = ?",
                (
                    AdmittedInputState.DELIVERED.value if matched else AdmittedInputState.DELIVERY_FAILED.value,
                    None if matched else "manifest_digest_mismatch",
                    admission_id,
                ),
            )
        return self.get(admission_id)

    def expire(self, admission_id: str) -> AdmittedInputRecord:
        current = self.get(admission_id)
        if current.state != AdmittedInputState.DELIVERY_PENDING:
            raise AdmittedInputConflict("expiry requires DELIVERY_PENDING")
        with self.connection:
            self.connection.execute(
                "UPDATE admitted_inputs SET state = ? WHERE admission_id = ?",
                (AdmittedInputState.EXPIRED.value, admission_id),
            )
        return self.get(admission_id)

    def _transition(
        self, admission_id: str, expected: AdmittedInputState, target: AdmittedInputState
    ) -> AdmittedInputRecord:
        current = self.get(admission_id)
        if current.state == target:
            return current
        if current.state == AdmittedInputState.EXPIRED:
            raise AdmittedInputExpired("expired admission requires a new server admission")
        if current.state != expected:
            raise AdmittedInputConflict(f"illegal admission transition {current.state.value} -> {target.value}")
        with self.connection:
            self.connection.execute(
                "UPDATE admitted_inputs SET state = ? WHERE admission_id = ?", (target.value, admission_id)
            )
        return self.get(admission_id)


def _manifest_from_payload(payload: str) -> AdmittedInputManifestV1:
    document = json.loads(payload)
    if document.get("version") != "admitted-input-manifest-v1" or document.get("state") != "SEALED":
        raise AdmittedInputConflict("stored manifest payload is not admitted-input-manifest-v1 SEALED evidence")
    return AdmittedInputManifestV1(
        logical_input_identities=tuple(document["logical_input_identities"]),
        natural_partition_identities=tuple(document["natural_partition_identities"]),
        schema_identity=document["schema_identity"],
        schema_version=document["schema_version"],
        schema_hash=document["schema_hash"],
        manifest_hashes=tuple(document["manifest_hashes"]),
        content_hashes=tuple(document["content_hashes"]),
        declared_coverage=document["declared_coverage"],
        request_identity=document["request_identity"],
        result_identity=document["result_identity"],
        definition_identities=tuple(document["definition_identities"]),
        implementation_identity=document["implementation_identity"],
        git_identity=document["git_identity"],
        operation_identity=document["operation_identity"],
        profile_identity=document["profile_identity"],
    )


def _optional_identity(value: str | None, field: str) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{field} must be a non-empty string when supplied")


def _canonical_json(value: Any) -> str:
    return canonical_bytes(value, profile="sorted-compact-ascii-v1", allow_nan=False).decode("utf-8")


__all__ = [
    "AdmittedInputConflict",
    "AdmittedInputError",
    "AdmittedInputExpired",
    "AdmittedInputManifestV1",
    "AdmittedInputNotFound",
    "AdmittedInputRecord",
    "AdmittedInputState",
    "AdmittedInputStore",
]
