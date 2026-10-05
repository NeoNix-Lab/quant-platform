"""Server-owned K13 registration of a deck result against sealed K12 input.

The service accepts no operational input locator as authority.  It first
compares the deck's complete declared input manifest with the server's sealed
admission, then verifies each result checksum, and only then calls the I02
Experiment owner.  Its SQLite evidence ledger retains the accepted immutable
bundle and prevents a later, conflicting result from sharing a Run/output role.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any

from quant_platform.experiments.accounting import TrialAttemptResult, record_trial_attempt
from quant_platform.experiments.identities import RunIdentity
from quant_platform.experiments.persistence import ArtifactRegistration, ExperimentRepository, RunRecord, RunState

from .admitted_input import (
    AdmittedInputError,
    AdmittedInputManifestV1,
    AdmittedInputState,
    AdmittedInputStore,
)


class GovernedResultImportError(RuntimeError):
    """Base class for K13 result-import failures."""


class GovernedResultRefused(GovernedResultImportError):
    """The submitted deck evidence is not admissible for registration."""


@dataclass(frozen=True, slots=True)
class ResultOutputEvidenceV1:
    """One deck output, with its claimed checksum and non-authoritative locator."""

    registration: ArtifactRegistration
    content_checksum: str

    def __post_init__(self) -> None:
        if not isinstance(self.registration, ArtifactRegistration):
            raise TypeError("registration must be an ArtifactRegistration")
        object.__setattr__(self, "content_checksum", _non_empty_text(self.content_checksum, "content_checksum"))
        if self.content_checksum != self.registration.identity.artifact_content_identity.content_identity:
            raise ValueError("content_checksum must match the artifact content identity")

    def stable_dict(self) -> dict[str, Any]:
        artifact = self.registration.identity
        content = artifact.artifact_content_identity
        return {
            "artifact_role": artifact.artifact_role,
            "artifact_kind": content.artifact_kind,
            "artifact_schema_version": content.artifact_schema_version,
            "content_checksum": self.content_checksum,
            "locator": _json_value(self.registration.locator, "locator"),
        }


@dataclass(frozen=True, slots=True)
class GovernedResultBundleV1:
    """The immutable evidence deck submits for one K13 result registration."""

    admission_id: str
    manifest_digest: str
    consumed_manifest: AdmittedInputManifestV1
    deck_code_identity: str
    runtime_environment: Mapping[str, Any]
    configuration: Mapping[str, Any]
    seeds: Mapping[str, Any] | None
    run_identity: RunIdentity
    outputs: Sequence[ResultOutputEvidenceV1]
    metrics_evidence: Mapping[str, Any] | None = None
    failure_evidence: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "admission_id", _non_empty_text(self.admission_id, "admission_id"))
        object.__setattr__(self, "manifest_digest", _non_empty_text(self.manifest_digest, "manifest_digest"))
        if not isinstance(self.consumed_manifest, AdmittedInputManifestV1):
            raise TypeError("consumed_manifest must be an AdmittedInputManifestV1")
        object.__setattr__(self, "deck_code_identity", _non_empty_text(self.deck_code_identity, "deck_code_identity"))
        if not isinstance(self.run_identity, RunIdentity):
            raise TypeError("run_identity must be a RunIdentity")
        object.__setattr__(self, "runtime_environment", _json_mapping(self.runtime_environment, "runtime_environment"))
        object.__setattr__(self, "configuration", _json_mapping(self.configuration, "configuration"))
        if self.seeds is not None:
            object.__setattr__(self, "seeds", _json_mapping(self.seeds, "seeds"))
        if self.metrics_evidence is not None:
            object.__setattr__(self, "metrics_evidence", _json_mapping(self.metrics_evidence, "metrics_evidence"))
        if self.failure_evidence is not None:
            object.__setattr__(self, "failure_evidence", _json_mapping(self.failure_evidence, "failure_evidence"))

        outputs = tuple(self.outputs)
        if not outputs:
            raise ValueError("outputs must not be empty")
        roles: set[str] = set()
        for index, output in enumerate(outputs):
            if not isinstance(output, ResultOutputEvidenceV1):
                raise TypeError(f"outputs[{index}] must be a ResultOutputEvidenceV1")
            if output.registration.identity.run_identity.stable_dict() != self.run_identity.stable_dict():
                raise ValueError("output artifact run identity does not match run_identity")
            role = output.registration.identity.artifact_role
            if role in roles:
                raise ValueError("outputs must not contain duplicate artifact roles")
            roles.add(role)
        object.__setattr__(self, "outputs", tuple(sorted(outputs, key=lambda output: output.registration.identity.artifact_role)))

    def stable_dict(self) -> dict[str, Any]:
        return {
            "version": "governed-result-bundle-v1",
            "admission_id": self.admission_id,
            "manifest_digest": self.manifest_digest,
            "consumed_manifest": json.loads(self.consumed_manifest.canonical_payload_v1),
            "deck_code_identity": self.deck_code_identity,
            "runtime_environment": dict(self.runtime_environment),
            "configuration": dict(self.configuration),
            "seeds": None if self.seeds is None else dict(self.seeds),
            "run_identity": self.run_identity.stable_dict(),
            "outputs": [output.stable_dict() for output in self.outputs],
            "metrics_evidence": None if self.metrics_evidence is None else dict(self.metrics_evidence),
            "failure_evidence": None if self.failure_evidence is None else dict(self.failure_evidence),
        }

    @property
    def canonical_payload_v1(self) -> str:
        return _canonical_json(self.stable_dict())

    @property
    def bundle_digest(self) -> str:
        return hashlib.sha256(self.canonical_payload_v1.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GovernedResultRegistration:
    """The attributable K13 registration result returned to the caller."""

    bundle_digest: str
    run_record: RunRecord


class GovernedResultEvidenceStore:
    """SQLite evidence ledger for accepted result bundles and output roles."""

    def __init__(self, connection: sqlite3.Connection):
        if not isinstance(connection, sqlite3.Connection):
            raise TypeError("connection must be a sqlite3.Connection")
        self.connection = connection
        with self.connection:
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS governed_result_bundles ("
                "bundle_digest TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                "run_spec_fingerprint TEXT NOT NULL, execution_id TEXT NOT NULL)"
            )
            self.connection.execute(
                "CREATE TABLE IF NOT EXISTS governed_result_outputs ("
                "run_spec_fingerprint TEXT NOT NULL, execution_id TEXT NOT NULL, "
                "artifact_role TEXT NOT NULL, content_checksum TEXT NOT NULL, bundle_digest TEXT NOT NULL, "
                "PRIMARY KEY (run_spec_fingerprint, execution_id, artifact_role), "
                "FOREIGN KEY (bundle_digest) REFERENCES governed_result_bundles(bundle_digest))"
            )

    def assert_admissible(self, bundle: GovernedResultBundleV1) -> None:
        """Refuse a conflicting accepted bundle before I02 receives a mutation."""

        row = self.connection.execute(
            "SELECT payload FROM governed_result_bundles WHERE bundle_digest = ?", (bundle.bundle_digest,)
        ).fetchone()
        if row is not None:
            if str(row[0]) != bundle.canonical_payload_v1:
                raise GovernedResultRefused("bundle digest payload conflicts")
            return
        run_spec, execution_id = _run_key(bundle.run_identity)
        for output in bundle.outputs:
            role = output.registration.identity.artifact_role
            existing = self.connection.execute(
                "SELECT content_checksum FROM governed_result_outputs "
                "WHERE run_spec_fingerprint = ? AND execution_id = ? AND artifact_role = ?",
                (run_spec, execution_id, role),
            ).fetchone()
            if existing is not None:
                raise GovernedResultRefused("conflicting accepted bundle for the same Run/output role")

    def record(self, bundle: GovernedResultBundleV1) -> None:
        """Persist one accepted immutable bundle after the I02 owner succeeds."""

        self.assert_admissible(bundle)
        existing = self.connection.execute(
            "SELECT 1 FROM governed_result_bundles WHERE bundle_digest = ?", (bundle.bundle_digest,)
        ).fetchone()
        if existing is not None:
            return
        run_spec, execution_id = _run_key(bundle.run_identity)
        with self.connection:
            self.connection.execute(
                "INSERT INTO governed_result_bundles "
                "(bundle_digest, payload, run_spec_fingerprint, execution_id) VALUES (?, ?, ?, ?)",
                (bundle.bundle_digest, bundle.canonical_payload_v1, run_spec, execution_id),
            )
            self.connection.executemany(
                "INSERT INTO governed_result_outputs "
                "(run_spec_fingerprint, execution_id, artifact_role, content_checksum, bundle_digest) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (run_spec, execution_id, output.registration.identity.artifact_role, output.content_checksum, bundle.bundle_digest)
                    for output in bundle.outputs
                ],
            )


class GovernedResultImportService:
    """Validate a deck bundle completely before delegating registration to I02."""

    def __init__(
        self,
        admitted_inputs: AdmittedInputStore,
        experiments: ExperimentRepository,
        evidence_store: GovernedResultEvidenceStore,
        verify_output_checksum: Callable[[ResultOutputEvidenceV1], str],
    ) -> None:
        if not isinstance(admitted_inputs, AdmittedInputStore):
            raise TypeError("admitted_inputs must be an AdmittedInputStore")
        if not isinstance(experiments, ExperimentRepository):
            raise TypeError("experiments must be an ExperimentRepository")
        if not isinstance(evidence_store, GovernedResultEvidenceStore):
            raise TypeError("evidence_store must be a GovernedResultEvidenceStore")
        if not callable(verify_output_checksum):
            raise TypeError("verify_output_checksum must be callable")
        self.admitted_inputs = admitted_inputs
        self.experiments = experiments
        self.evidence_store = evidence_store
        self.verify_output_checksum = verify_output_checksum

    def import_result(self, bundle: GovernedResultBundleV1) -> GovernedResultRegistration:
        if not isinstance(bundle, GovernedResultBundleV1):
            raise TypeError("bundle must be a GovernedResultBundleV1")
        self._validate_admission(bundle)
        self._validate_outputs(bundle)
        self.evidence_store.assert_admissible(bundle)
        try:
            run_record = record_trial_attempt(
                self.experiments,
                TrialAttemptResult(
                    bundle.run_identity,
                    RunState.SUCCEEDED,
                    artifact_identities=tuple(output.registration.identity for output in bundle.outputs),
                ),
                artifact_registrations=tuple(output.registration for output in bundle.outputs),
            )
        except Exception as error:
            raise GovernedResultImportError("Experiment registration failed") from error
        self.evidence_store.record(bundle)
        return GovernedResultRegistration(bundle.bundle_digest, run_record)

    def _validate_admission(self, bundle: GovernedResultBundleV1) -> None:
        try:
            record = self.admitted_inputs.get(bundle.admission_id)
        except AdmittedInputError as error:
            raise GovernedResultRefused("admitted input is missing or malformed") from error
        if record.state is not AdmittedInputState.DELIVERED:
            raise GovernedResultRefused("admitted input is not a complete delivered sealed evidence")
        if bundle.manifest_digest != record.manifest.manifest_digest:
            raise GovernedResultRefused("manifest digest does not match admitted input")
        if bundle.consumed_manifest.canonical_payload_v1 != record.manifest.canonical_payload_v1:
            raise GovernedResultRefused("declared input evidence does not match admitted input")
        if bundle.deck_code_identity != record.manifest.git_identity:
            raise GovernedResultRefused("deck code identity does not match admitted input")

    def _validate_outputs(self, bundle: GovernedResultBundleV1) -> None:
        for output in bundle.outputs:
            try:
                verified = self.verify_output_checksum(output)
            except Exception as error:
                raise GovernedResultRefused("output checksum verification failed") from error
            if not isinstance(verified, str) or verified != output.content_checksum:
                raise GovernedResultRefused("output checksum does not match declared output")


def _run_key(run: RunIdentity) -> tuple[str, str]:
    return run.run_spec_identity.fingerprint, run.execution_id


def _non_empty_text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _json_mapping(value: Mapping[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return _json_value(dict(value), field)


def _json_value(value: Any, field: str) -> Any:
    try:
        encoded = _canonical_json(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be JSON-compatible") from error
    return json.loads(encoded)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


__all__ = [
    "GovernedResultBundleV1",
    "GovernedResultEvidenceStore",
    "GovernedResultImportError",
    "GovernedResultImportService",
    "GovernedResultRefused",
    "GovernedResultRegistration",
    "ResultOutputEvidenceV1",
]
