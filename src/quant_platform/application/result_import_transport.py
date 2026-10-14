"""Opt-in ``j02-result-import-v1``: K13 result submission from the deck over J02 (issue #342).

ADR-0069 section 6 over ADR-0064 section 3 (unchanged: ``GovernedResultBundleV1``'s
fields and digest rules, and every K13 refusal rule, are the K13 owner's). Two
operations, both under ``j02.result.submit`` and both initiated by the deck:

- ``upload_output`` sends one output's bytes, one chunk per request, following the
  J14 frame rules in the client-to-server direction: deterministic offsets
  (every chunk but the last is exactly the server's ``frame_payload_bytes``,
  which every progress document and a layout refusal carry), a per-chunk and
  a whole-payload SHA-256, a ``transfer_id`` derived from the logical identity
  and the payload digest, and transfer-local resume (a chunk may be sent again;
  the response says which chunk is next). The bytes are *staged* under their
  checksum (``sha256:<hex>``) as non-canonical evidence in a bounded, expiring
  store. Staged bytes are not an artifact, are not readable through any
  operation, and are never registered by an upload.
- ``submit`` carries one ``GovernedResultBundleV1`` as a lossless document
  (:func:`bundle_to_wire` / :func:`bundle_from_wire`). It is handed whole to the
  existing :class:`GovernedResultImportService`, whose output-checksum
  verification is the staged bytes (:meth:`StagedOutputStore.verify_output`): any
  output whose checksum is not that of a staged payload refuses the whole bundle
  before the Experiment owner or the evidence ledger is touched. An identical
  re-submission is the owner's idempotent same registration.

The logical identity of an uploaded output is derived from its bytes
(``result-output-v1:sha256:<payload sha256>``), so an upload is addressed by
content only: no role, run or path is supplied or trusted at upload time. The
role a payload plays is decided by the bundle, never by the uploader.

Replay outputs. A role whose contract is :class:`ReplayOutputContractV1` needs
``configuration.replay_feature_provider`` (ADR-0069 section 7); the identity is
recomputed from the payload and nothing is imported or resolved, which
``tests/test_result_import_transport_v1.py`` asserts. This is checked *before*
registration, in addition to the K13 rules and not instead of them.

Known limits (v1, recorded rather than hidden):

- Staging is in process memory. A server restart loses staged bytes; the deck
  holds the output (an output that was not registered "stays deck-local
  evidence", ADR-0069 section 6) and uploads it again. A registered bundle is
  durable in the K13 ledger regardless.
- An identical re-submission after the staged bytes expired is refused as
  ``output_not_staged`` until the deck uploads the bytes again; the bytes are
  never read back from the registration.
- Retention values are operational defaults, not ADR values
  (``[DA CONFERMARE]``): :class:`StagingBounds`.
- The request does not carry the uploader's identity to the staging store
  (``j02.result.submit`` is one scope); the store is content-addressed, so a
  payload staged by one principal is verifiable by a bundle of another. This is
  not a confidentiality boundary between holders of the scope.
- A zero-byte output cannot be framed (J14 frames carry at least one byte).

Composition is explicit and opt-in (:func:`result_import_family_handlers`); no
host wires it implicitly. The import service handed to the family must verify
output checksums with the *same* staging store given to the family; the family
refuses a service that does not.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
import hashlib
import re
import threading
import time
from typing import Any

from quant_platform.canonical import canonical_bytes
from quant_platform.experiments.identities import (
    ArtifactContentIdentity,
    ArtifactIdentity,
    ComparisonProtocolIdentity,
    IdentityReference,
    RunIdentity,
    RunSpecIdentity,
    StudyIdentity,
    TrialIdentity,
)
from quant_platform.experiments.persistence import ArtifactRegistration

from .admitted_input import AdmittedInputManifestV1
from .api_transport_server import J02FamilyHandler
from .feature_provider_identity import FeatureProviderIdentityError, ReplayOutputContractV1
from .framed_result_transport import DEFAULT_J14_FRAME_PAYLOAD_BYTES, _transfer_id
from .governed_result_import import (
    GovernedResultBundleV1,
    GovernedResultImportError,
    GovernedResultImportService,
    GovernedResultRefused,
    ResultOutputEvidenceV1,
)
from .market_data import ConsumerApiError, ConsumerErrorCode

J02_RESULT_IMPORT_FAMILY = "j02-result-import-v1"
J02_RESULT_IMPORT_REQUEST_VERSION = J02_RESULT_IMPORT_FAMILY + "-request-v1"
J02_RESULT_IMPORT_RESPONSE_VERSION = J02_RESULT_IMPORT_FAMILY + "-response-v1"
J02_RESULT_IMPORT_SCOPE = "j02.result.submit"
J02_RESULT_IMPORT_OPERATIONS = ("upload_output", "submit")
OUTPUT_IDENTITY_PREFIX = "result-output-v1:sha256:"
CHECKSUM_PREFIX = "sha256:"
BUNDLE_WIRE_VERSION = "governed-result-bundle-wire-v1"

# Implementation bounds (not ADR values; ADR-0069 section 6 leaves them to the
# server's configuration, and the issue defers the retention values).
DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_STAGED_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_IN_FLIGHT_UPLOADS = 8
DEFAULT_STAGING_RETENTION_SECONDS = 900
DEFAULT_REGISTERED_RETENTION_SECONDS = 3600
MAX_TEXT_LENGTH = 1024
MAX_ITEMS = 1024
MAX_OUTPUTS = 64
MAX_DOCUMENT_BYTES = 256 * 1024

_INVALID = "the request is not valid"
_DIGEST = re.compile(r"[0-9a-f]{64}")
_CHECKSUM = re.compile(r"sha256:[0-9a-f]{64}")

# The K13 owner's refusal texts, as stable wire reasons. The mapping is by the
# owner's own fixed message; an unknown one is ``bundle_refused`` (a message is
# never forwarded). ``tests/test_result_import_transport_v1.py`` reaches each.
_REFUSAL_REASONS = {
    "admitted input is missing or malformed": "admission_not_found",
    "admitted input is not a complete delivered sealed evidence": "admission_not_delivered",
    "manifest digest does not match admitted input": "manifest_digest_mismatch",
    "declared input evidence does not match admitted input": "consumed_manifest_mismatch",
    "deck code identity does not match admitted input": "code_identity_mismatch",
    "RunIdentity code identity does not match admitted input": "run_code_identity_mismatch",
    "RunIdentity data identities do not cover admitted input": "run_data_identity_mismatch",
    "RunIdentity definition identities do not cover admitted input": "run_definition_mismatch",
    "output role or schema is not admitted": "output_not_admitted",
    "output checksum verification failed": "output_not_staged",
    "output checksum does not match declared output": "output_checksum_mismatch",
    "bundle digest payload conflicts": "conflicting_bundle",
    "conflicting accepted bundle for the same Run/output role": "conflicting_bundle",
}


class _Refused(Exception):
    """A staging outcome: ``kind`` is ``integrity``, ``too_large`` or ``busy``."""

    def __init__(self, kind: str, reason: str):
        super().__init__(reason)
        self.kind = kind
        self.reason = reason


class StagedOutputMissing(LookupError):
    """No staged payload has the requested checksum."""


def _integrity(reason: str, **extra: Any) -> ConsumerApiError:
    return ConsumerApiError(
        ConsumerErrorCode.INTEGRITY_FAILURE,
        "the platform cannot produce a trustworthy result",
        context={"reason": reason, **extra},
    )


def _refusal(reason: str) -> ConsumerApiError:
    """A fixed message plus a stable reason: no store or owner text is forwarded."""
    return ConsumerApiError(ConsumerErrorCode.INVALID_REQUEST, _INVALID, context={"reason": reason})


def _too_large(reason: str) -> ConsumerApiError:
    return ConsumerApiError(
        ConsumerErrorCode.RESULT_TOO_LARGE,
        "the output exceeds the configured upload bounds",
        context={"reason": reason},
    )


@dataclass(frozen=True, slots=True)
class StagingBounds:
    """Operational bounds of the staging store (defaults are not ADR values)."""

    frame_payload_bytes: int = DEFAULT_J14_FRAME_PAYLOAD_BYTES
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_staged_bytes: int = DEFAULT_MAX_STAGED_BYTES
    max_in_flight_uploads: int = DEFAULT_MAX_IN_FLIGHT_UPLOADS
    retention_seconds: int = DEFAULT_STAGING_RETENTION_SECONDS
    registered_retention_seconds: int = DEFAULT_REGISTERED_RETENTION_SECONDS

    def __post_init__(self) -> None:
        for name in (
            "frame_payload_bytes", "max_output_bytes", "max_staged_bytes",
            "max_in_flight_uploads", "retention_seconds", "registered_retention_seconds",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_output_bytes > self.max_staged_bytes:
            raise ValueError("max_output_bytes must not exceed max_staged_bytes")


@dataclass(frozen=True, slots=True)
class UploadChunk:
    """One decoded, digest-verified chunk of an output upload."""

    transfer_id: str
    payload_sha256: str
    chunk_index: int
    chunk_count: int
    payload: bytes


@dataclass(slots=True)
class _Upload:
    payload_sha256: str
    chunk_count: int
    expiry: float
    chunks: dict[int, bytes]
    held: int = 0


@dataclass(slots=True)
class _Staged:
    data: bytes
    expiry: float
    registered: bool = False


def output_identity(payload_sha256: str) -> str:
    return OUTPUT_IDENTITY_PREFIX + payload_sha256


def upload_transfer_id(payload_sha256: str) -> str:
    """The J14 ``transfer_id`` of an output: the ADR-0066 section 2 preimage.

    The J14 module's own derivation, not a second copy of it: the J14 transport
    is the only place that declares the RFC 8785 profile.
    """

    return _transfer_id(output_identity(payload_sha256), payload_sha256)


def upload_chunks(
    payload: bytes, *, frame_payload_bytes: int = DEFAULT_J14_FRAME_PAYLOAD_BYTES,
) -> list[dict[str, Any]]:
    """The ``upload_output`` request bodies of ``payload``, in order.

    The reference chunker of the family: what a client must send, and what the
    tests send. ``payload`` must not be empty.
    """

    if type(payload) is not bytes or not payload:
        raise ValueError("an output payload must be non-empty bytes")
    digest = hashlib.sha256(payload).hexdigest()
    pieces = [
        payload[offset : offset + frame_payload_bytes]
        for offset in range(0, len(payload), frame_payload_bytes)
    ]
    return [
        {
            "transfer_id": upload_transfer_id(digest),
            "logical_result_identity": output_identity(digest),
            "payload_sha256": digest,
            "chunk_index": index,
            "chunk_count": len(pieces),
            "chunk_sha256": hashlib.sha256(piece).hexdigest(),
            "chunk_base64": base64.b64encode(piece).decode("ascii"),
        }
        for index, piece in enumerate(pieces)
    ]


class StagedOutputStore:
    """Bounded, expiring, content-addressed staging of uploaded output bytes.

    Not canonical: nothing here is an artifact until a successful ``submit``
    names it, and nothing reads staged bytes back to a caller. Unreferenced
    bytes expire after ``retention_seconds``; bytes that a registration
    referenced are kept ``registered_retention_seconds`` (so an identical
    re-submission verifies again). Every access holds one lock: the store is
    reached from worker threads.
    """

    def __init__(
        self, bounds: StagingBounds = StagingBounds(), *, clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not isinstance(bounds, StagingBounds):
            raise TypeError("bounds must be StagingBounds")
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._bounds = bounds
        self._clock = clock
        self._uploads: dict[str, _Upload] = {}
        self._staged: dict[str, _Staged] = {}
        self._lock = threading.RLock()

    @property
    def bounds(self) -> StagingBounds:
        return self._bounds

    # -- upload ----------------------------------------------------------------

    def receive(self, chunk: UploadChunk) -> dict[str, Any]:
        """Accept one chunk; the progress document says what the client does next."""

        bounds = self._bounds
        size, count, index = len(chunk.payload), chunk.chunk_count, chunk.chunk_index
        if size > bounds.frame_payload_bytes or (index < count - 1 and size != bounds.frame_payload_bytes):
            raise _Refused("integrity", "chunk_layout_mismatch")
        if (count - 1) * bounds.frame_payload_bytes + 1 > bounds.max_output_bytes:
            raise _Refused("too_large", "output_too_large")
        checksum = CHECKSUM_PREFIX + chunk.payload_sha256
        with self._lock:
            now = self._clock()
            self._expire(now)
            staged = self._staged.get(checksum)
            if staged is not None:
                return self._repeat(staged, chunk, now)
            upload = self._uploads.get(chunk.transfer_id)
            if upload is None:
                if len(self._uploads) >= bounds.max_in_flight_uploads:
                    raise _Refused("too_large", "too_many_uploads")
                fresh = _Upload(
                    chunk.payload_sha256, count, now + bounds.retention_seconds, {},
                )
            elif upload.chunk_count != count or upload.payload_sha256 != chunk.payload_sha256:
                del self._uploads[chunk.transfer_id]
                raise _Refused("integrity", "transfer_metadata_mismatch")
            else:
                fresh = None
            current = upload if upload is not None else fresh
            known = current.chunks.get(index)
            if known is not None:
                if known != chunk.payload:
                    self._uploads.pop(chunk.transfer_id, None)
                    raise _Refused("integrity", "chunk_conflict")
            else:
                if self._held() + size > bounds.max_staged_bytes:
                    raise _Refused("too_large", "staging_full")
                current.chunks[index] = chunk.payload
                current.held += size
            if fresh is not None:
                self._uploads[chunk.transfer_id] = fresh
            if len(current.chunks) < count:
                return self._progress(chunk, current.chunks, complete=False)
            return self._complete(chunk, current, now)

    def _complete(self, chunk: UploadChunk, upload: _Upload, now: float) -> dict[str, Any]:
        del self._uploads[chunk.transfer_id]
        data = b"".join(upload.chunks[position] for position in range(upload.chunk_count))
        if len(data) > self._bounds.max_output_bytes:
            raise _Refused("too_large", "output_too_large")
        if hashlib.sha256(data).hexdigest() != upload.payload_sha256:
            raise _Refused("integrity", "payload_digest_mismatch")
        self._staged[CHECKSUM_PREFIX + upload.payload_sha256] = _Staged(
            data, now + self._bounds.retention_seconds,
        )
        return self._progress(chunk, upload.chunks, complete=True, byte_length=len(data))

    def _repeat(self, staged: _Staged, chunk: UploadChunk, now: float) -> dict[str, Any]:
        """A chunk of a payload that is already staged: idempotent, or a conflict."""

        size = self._bounds.frame_payload_bytes
        offset = chunk.chunk_index * size
        if (
            -(-len(staged.data) // size) != chunk.chunk_count
            or staged.data[offset : offset + size] != chunk.payload
        ):
            raise _Refused("integrity", "chunk_conflict")
        if not staged.registered:
            staged.expiry = max(staged.expiry, now + self._bounds.retention_seconds)
        return self._progress(chunk, {}, complete=True, byte_length=len(staged.data))

    def _progress(
        self, chunk: UploadChunk, received: Mapping[int, bytes], *, complete: bool,
        byte_length: int | None = None,
    ) -> dict[str, Any]:
        missing = next(
            (position for position in range(chunk.chunk_count) if position not in received), None,
        )
        return {
            "transfer_id": chunk.transfer_id,
            "logical_result_identity": output_identity(chunk.payload_sha256),
            "payload_sha256": chunk.payload_sha256,
            "chunk_count": chunk.chunk_count,
            "frame_payload_bytes": self._bounds.frame_payload_bytes,
            "received_chunks": chunk.chunk_count if complete else len(received),
            "next_chunk_index": None if complete else missing,
            "complete": complete,
            "checksum": CHECKSUM_PREFIX + chunk.payload_sha256 if complete else None,
            "byte_length": byte_length if complete else None,
        }

    # -- verification and retention -------------------------------------------

    def verify_output(self, evidence: ResultOutputEvidenceV1) -> str:
        """The checksum of the staged bytes the output names, recomputed.

        This is the ``verify_output_checksum`` seam of
        :class:`GovernedResultImportService`: a checksum with no staged payload
        raises (the owner refuses the bundle); a staged payload is hashed again,
        not trusted by its key.
        """

        with self._lock:
            self._expire(self._clock())
            staged = self._staged.get(evidence.content_checksum)
            data = None if staged is None else staged.data
        if data is None:
            raise StagedOutputMissing("the output is not staged")
        return CHECKSUM_PREFIX + hashlib.sha256(data).hexdigest()

    def mark_registered(self, checksums: Sequence[str]) -> None:
        """Keep the referenced payloads for the registered retention."""

        with self._lock:
            now = self._clock()
            for checksum in checksums:
                staged = self._staged.get(checksum)
                if staged is not None and staged.expiry > now:
                    staged.registered = True
                    staged.expiry = max(staged.expiry, now + self._bounds.registered_retention_seconds)

    def staged_checksums(self) -> tuple[str, ...]:
        with self._lock:
            self._expire(self._clock())
            return tuple(sorted(self._staged))

    def in_flight(self) -> int:
        with self._lock:
            self._expire(self._clock())
            return len(self._uploads)

    def _held(self) -> int:
        return sum(upload.held for upload in self._uploads.values()) + sum(
            len(staged.data) for staged in self._staged.values()
        )

    def _expire(self, now: float) -> None:
        for key in [key for key, upload in self._uploads.items() if upload.expiry <= now]:
            del self._uploads[key]
        for key in [key for key, staged in self._staged.items() if staged.expiry <= now]:
            del self._staged[key]


# -- the bundle on the wire ---------------------------------------------------


def _canonical(value: Any) -> bytes:
    return canonical_bytes(value, profile="sorted-compact-ascii-v1", allow_nan=False)


def _object(value: Any, required: set[str], optional: frozenset[str] = frozenset()) -> dict[str, Any]:
    if type(value) is not dict or not required <= set(value) or set(value) - required - optional:
        raise ValueError("the object fields do not match")
    return value


def _string(value: Any, *, limit: int = MAX_TEXT_LENGTH) -> str:
    if (
        type(value) is not str or not value or value != value.strip() or len(value) > limit
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("bounded non-empty text required")
    return value


def _optional_string(value: Any) -> str | None:
    return None if value is None else _string(value)


def _string_list(value: Any) -> list[str]:
    if type(value) is not list or len(value) > MAX_ITEMS:
        raise ValueError("a bounded array of text is required")
    return [_string(item) for item in value]


def _document(value: Any, *, nullable: bool = False) -> dict[str, Any] | None:
    """A JSON object of bounded canonical size (the K13 owner canonicalizes it again)."""

    if value is None and nullable:
        return None
    if type(value) is not dict:
        raise ValueError("a JSON object is required")
    if len(_canonical(value)) > MAX_DOCUMENT_BYTES:
        raise ValueError("the document is too large")
    return value


def _reference(value: Any) -> IdentityReference:
    item = _object(value, {"identity_kind", "identity"})
    return IdentityReference(_string(item["identity_kind"]), _string(item["identity"]))


def _optional_reference(value: Any) -> IdentityReference | None:
    return None if value is None else _reference(value)


def _reference_list(value: Any) -> list[IdentityReference]:
    if type(value) is not list or len(value) > MAX_ITEMS:
        raise ValueError("a bounded array of identity references is required")
    return [_reference(item) for item in value]


def _manifest_from_wire(value: Any) -> AdmittedInputManifestV1:
    item = _object(value, {
        "content_hashes", "declared_coverage", "definition_identities", "git_identity",
        "implementation_identity", "logical_input_identities", "manifest_hashes",
        "natural_partition_identities", "operation_identity", "profile_identity",
        "request_identity", "result_identity", "schema_hash", "schema_identity",
        "schema_version", "state", "version",
    })
    if item["version"] != "admitted-input-manifest-v1" or item["state"] != "SEALED":
        raise ValueError("not a sealed admitted-input manifest")
    return AdmittedInputManifestV1(
        logical_input_identities=tuple(_string_list(item["logical_input_identities"])),
        natural_partition_identities=tuple(_string_list(item["natural_partition_identities"])),
        schema_identity=_optional_string(item["schema_identity"]),
        schema_version=_optional_string(item["schema_version"]),
        schema_hash=_optional_string(item["schema_hash"]),
        manifest_hashes=tuple(_string_list(item["manifest_hashes"])),
        content_hashes=tuple(_string_list(item["content_hashes"])),
        declared_coverage=_document(item["declared_coverage"], nullable=True),
        request_identity=_optional_string(item["request_identity"]),
        result_identity=_optional_string(item["result_identity"]),
        definition_identities=tuple(_string_list(item["definition_identities"])),
        implementation_identity=_optional_string(item["implementation_identity"]),
        git_identity=_optional_string(item["git_identity"]),
        operation_identity=_string(item["operation_identity"]),
        profile_identity=_optional_string(item["profile_identity"]),
    )


def run_identity_to_wire(run: RunIdentity) -> dict[str, Any]:
    """A :class:`RunIdentity` in full, with its own fingerprint to cross-check."""

    spec = run.run_spec_identity
    trial = spec.trial_identity
    study = trial.study_identity
    protocol = study.comparison_protocol_identity
    return {
        "run_spec_fingerprint": spec.fingerprint,
        "execution_id": run.execution_id,
        "run_spec": {
            "study": {
                "study_definition_version": study.study_definition_version,
                "research_identity": study.research_identity.stable_dict(),
                "evaluation_objective_identity": study.evaluation_objective_identity.stable_dict(),
                "comparison_protocol_identity": {
                    "identity": protocol.identity.stable_dict(),
                    "trial_dimensions": list(protocol.trial_dimensions),
                },
            },
            "trial_assignments": trial.stable_assignments(),
            "code_identity": spec.code_identity.stable_dict(),
            "data_identities": [value.stable_dict() for value in spec.data_identities],
            "feature_identities": [value.stable_dict() for value in spec.feature_identities],
            "research_label_identities": [
                value.stable_dict() for value in spec.research_label_identities
            ],
            "validation_identity": (
                None if spec.validation_identity is None else spec.validation_identity.stable_dict()
            ),
            "strategy_policy_execution_identities": [
                value.stable_dict() for value in spec.strategy_policy_execution_identities
            ],
            "environment_identity": (
                None if spec.environment_identity is None else spec.environment_identity.stable_dict()
            ),
            "run_configuration": spec.fingerprint_payload()["explicit_run_configuration"],
        },
    }


class _FingerprintMismatch(ValueError):
    """The deck's fingerprint is not the one the server's identity code computes."""


def run_identity_from_wire(value: Any) -> RunIdentity:
    item = _object(value, {"run_spec_fingerprint", "execution_id", "run_spec"})
    body = _object(item["run_spec"], {
        "study", "trial_assignments", "code_identity", "data_identities", "feature_identities",
        "research_label_identities", "validation_identity",
        "strategy_policy_execution_identities", "environment_identity", "run_configuration",
    })
    study = _object(body["study"], {
        "study_definition_version", "research_identity", "evaluation_objective_identity",
        "comparison_protocol_identity",
    })
    protocol = _object(study["comparison_protocol_identity"], {"identity", "trial_dimensions"})
    study_identity = StudyIdentity(
        _string(study["study_definition_version"]),
        _reference(study["research_identity"]),
        _reference(study["evaluation_objective_identity"]),
        ComparisonProtocolIdentity(
            _reference(protocol["identity"]), tuple(_string_list(protocol["trial_dimensions"])),
        ),
    )
    spec = RunSpecIdentity(
        TrialIdentity(study_identity, _document(body["trial_assignments"])),
        _reference(body["code_identity"]),
        data_identities=_reference_list(body["data_identities"]),
        feature_identities=_reference_list(body["feature_identities"]),
        research_label_identities=_reference_list(body["research_label_identities"]),
        validation_identity=_optional_reference(body["validation_identity"]),
        strategy_policy_execution_identities=_reference_list(
            body["strategy_policy_execution_identities"]
        ),
        environment_identity=_optional_reference(body["environment_identity"]),
        run_configuration=_document(body["run_configuration"]),
    )
    if item["run_spec_fingerprint"] != spec.fingerprint:
        raise _FingerprintMismatch("the run spec fingerprint is not the recomputed one")
    return RunIdentity(spec, _string(item["execution_id"]))


def bundle_to_wire(bundle: GovernedResultBundleV1) -> dict[str, Any]:
    """The lossless ``submit`` request body of a bundle (the deck's encoder, mirrored)."""

    return {
        "version": BUNDLE_WIRE_VERSION,
        "admission_id": bundle.admission_id,
        "manifest_digest": bundle.manifest_digest,
        "consumed_manifest": bundle.consumed_manifest.canonical_fields(),
        "deck_code_identity": bundle.deck_code_identity,
        "runtime_environment": dict(bundle.runtime_environment),
        "configuration": dict(bundle.configuration),
        "seeds": None if bundle.seeds is None else dict(bundle.seeds),
        "run_identity": run_identity_to_wire(bundle.run_identity),
        "outputs": [
            {
                "artifact_role": output.registration.identity.artifact_role,
                "artifact_kind": output.registration.identity.artifact_content_identity.artifact_kind,
                "artifact_schema_version": (
                    output.registration.identity.artifact_content_identity.artifact_schema_version
                ),
                "content_checksum": output.content_checksum,
                "locator": (
                    None if output.registration.locator is None else dict(output.registration.locator)
                ),
            }
            for output in bundle.outputs
        ],
        "metrics_evidence": None if bundle.metrics_evidence is None else dict(bundle.metrics_evidence),
        "failure_evidence": None if bundle.failure_evidence is None else dict(bundle.failure_evidence),
    }


def bundle_from_wire(body: Any) -> GovernedResultBundleV1:
    """Decode a ``submit`` body into the owner's bundle; any malformation raises ``ValueError``/``TypeError``."""

    item = _object(body, {
        "version", "admission_id", "manifest_digest", "consumed_manifest", "deck_code_identity",
        "runtime_environment", "configuration", "seeds", "run_identity", "outputs",
        "metrics_evidence", "failure_evidence",
    })
    if item["version"] != BUNDLE_WIRE_VERSION:
        raise ValueError("unexpected bundle wire version")
    run = run_identity_from_wire(item["run_identity"])
    outputs_wire = item["outputs"]
    if type(outputs_wire) is not list or not outputs_wire or len(outputs_wire) > MAX_OUTPUTS:
        raise ValueError("a bounded, non-empty array of outputs is required")
    outputs = []
    for entry in outputs_wire:
        output = _object(entry, {
            "artifact_role", "artifact_kind", "artifact_schema_version", "content_checksum", "locator",
        })
        checksum = _string(output["content_checksum"])
        if not _CHECKSUM.fullmatch(checksum):
            raise ValueError("an output checksum is sha256:<64 hex>")
        outputs.append(
            ResultOutputEvidenceV1(
                ArtifactRegistration(
                    ArtifactIdentity(
                        run,
                        _string(output["artifact_role"]),
                        ArtifactContentIdentity(
                            _string(output["artifact_kind"]),
                            _string(output["artifact_schema_version"]),
                            checksum,
                        ),
                    ),
                    _document(output["locator"], nullable=True),
                ),
                checksum,
            )
        )
    return GovernedResultBundleV1(
        admission_id=_string(item["admission_id"]),
        manifest_digest=_string(item["manifest_digest"]),
        consumed_manifest=_manifest_from_wire(item["consumed_manifest"]),
        deck_code_identity=_string(item["deck_code_identity"]),
        runtime_environment=_document(item["runtime_environment"]),
        configuration=_document(item["configuration"]),
        seeds=_document(item["seeds"], nullable=True),
        run_identity=run,
        outputs=tuple(outputs),
        metrics_evidence=_document(item["metrics_evidence"], nullable=True),
        failure_evidence=_document(item["failure_evidence"], nullable=True),
    )


def _upload_chunk(body: Any) -> UploadChunk:
    try:
        item = _object(body, {
            "transfer_id", "logical_result_identity", "payload_sha256", "chunk_index",
            "chunk_count", "chunk_sha256", "chunk_base64",
        })
        index, count = item["chunk_index"], item["chunk_count"]
        if (
            type(index) is not int or type(count) is not int or count < 1 or count > MAX_ITEMS * 1024
            or index < 0 or index >= count
        ):
            raise ValueError("chunk_index and chunk_count are not a position")
        for name in ("transfer_id", "payload_sha256", "chunk_sha256"):
            if type(item[name]) is not str or not _DIGEST.fullmatch(item[name]):
                raise ValueError(f"{name} must be lowercase SHA-256 hexadecimal")
        if type(item["logical_result_identity"]) is not str or type(item["chunk_base64"]) is not str:
            raise ValueError("identity and chunk are text")
    except (ValueError, TypeError, KeyError) as error:
        raise _refusal("invalid_request_shape") from error
    # From here the body is shaped: what is wrong is the *bytes*, which J14
    # treats as an integrity failure of the transfer.
    try:
        payload = base64.b64decode(item["chunk_base64"], validate=True)
    except (binascii.Error, ValueError) as error:
        raise _integrity("chunk_not_base64") from error
    if not payload:
        raise _integrity("chunk_empty")
    if hashlib.sha256(payload).hexdigest() != item["chunk_sha256"]:
        raise _integrity("chunk_digest_mismatch")
    digest = item["payload_sha256"]
    if (
        item["logical_result_identity"] != output_identity(digest)
        or item["transfer_id"] != upload_transfer_id(digest)
    ):
        raise _integrity("transfer_identity_mismatch")
    return UploadChunk(item["transfer_id"], digest, index, count, payload)


ImportServiceProvider = Callable[[], AbstractContextManager[GovernedResultImportService]]


class ResultImportTransport:
    """Decode and execute the two operations against a staging store and the K13 owner."""

    def __init__(self, service_provider: ImportServiceProvider, staging: StagedOutputStore) -> None:
        if not callable(service_provider):
            raise TypeError("service_provider must be callable")
        if not isinstance(staging, StagedOutputStore):
            raise TypeError("staging must be a StagedOutputStore")
        self._provider = service_provider
        self._staging = staging

    # -- decode: pure, never touches the store or the owner --------------------

    def decode(self, operation: str, body: Mapping[str, Any]) -> Any:
        if operation == "upload_output":
            return _upload_chunk(body)
        if operation == "submit":
            try:
                return bundle_from_wire(body)
            except _FingerprintMismatch as error:
                raise _refusal("run_identity_mismatch") from error
            except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as error:
                raise _refusal("invalid_request_shape") from error
        raise _refusal("unsupported_operation")  # pragma: no cover - routes are fixed

    # -- execute ---------------------------------------------------------------

    def execute(self, operation: str, decoded: Any) -> dict[str, Any]:
        if operation == "upload_output":
            return self._upload(decoded)
        return self._submit(decoded)

    def _upload(self, chunk: UploadChunk) -> dict[str, Any]:
        try:
            return self._staging.receive(chunk)
        except _Refused as refusal:
            if refusal.kind == "integrity":
                # The chunk size is the server's bound (every chunk but the last
                # is exactly it); a client that sent another size learns it here.
                extra = (
                    {"frame_payload_bytes": self._staging.bounds.frame_payload_bytes}
                    if refusal.reason == "chunk_layout_mismatch" else {}
                )
                raise _integrity(refusal.reason, **extra) from refusal
            raise _too_large(refusal.reason) from refusal

    def _submit(self, bundle: GovernedResultBundleV1) -> dict[str, Any]:
        with self._provider() as service:
            if (
                not isinstance(service, GovernedResultImportService)
                or service.verify_output_checksum != self._staging.verify_output
            ):
                # Registering against anything but the staged bytes would make
                # a staged upload meaningless; fail closed at the call.
                raise _integrity("import_service_not_staged")
            self._check_replay_contracts(service, bundle)
            try:
                registration = service.import_result(bundle)
            except GovernedResultRefused as error:
                raise _refusal(_REFUSAL_REASONS.get(str(error), "bundle_refused")) from error
            except GovernedResultImportError as error:
                raise _integrity("registration_failed") from error
            except Exception as error:  # A store's own text is never forwarded.
                raise _integrity("registration_unavailable") from error
        self._staging.mark_registered([output.content_checksum for output in bundle.outputs])
        run = registration.run_record
        return {
            "bundle_digest": registration.bundle_digest,
            "run": {
                "run_spec_fingerprint": run.run_spec_fingerprint,
                "execution_id": run.execution_id,
                "state": run.state.value,
                "failure_details": (
                    None if run.failure_details is None else dict(run.failure_details)
                ),
            },
            "outputs": [
                {
                    "artifact_role": output.registration.identity.artifact_role,
                    "content_checksum": output.content_checksum,
                }
                for output in bundle.outputs
            ],
        }

    @staticmethod
    def _check_replay_contracts(
        service: GovernedResultImportService, bundle: GovernedResultBundleV1,
    ) -> None:
        for output in bundle.outputs:
            contract = service.output_contracts.get(output.registration.identity.artifact_role)
            if isinstance(contract, ReplayOutputContractV1):
                try:
                    contract.check(bundle.configuration)
                except FeatureProviderIdentityError as error:
                    raise _refusal(f"replay_{error.reason}") from error


def result_import_family_handlers(
    service_provider: ImportServiceProvider, *, staging: StagedOutputStore,
) -> dict[tuple[str, str, str], J02FamilyHandler]:
    """Explicit server composition only; no host and no other family is added.

    ``service_provider`` yields a :class:`GovernedResultImportService` per
    request (its own connection, closed on exit) whose
    ``verify_output_checksum`` is ``staging.verify_output``.
    """

    transport = ResultImportTransport(service_provider, staging)
    return {
        (J02_RESULT_IMPORT_FAMILY, J02_RESULT_IMPORT_REQUEST_VERSION, operation): J02FamilyHandler(
            J02_RESULT_IMPORT_SCOPE, J02_RESULT_IMPORT_RESPONSE_VERSION,
            lambda body, operation=operation: transport.decode(operation, body),
            lambda decoded, operation=operation: transport.execute(operation, decoded),
        )
        for operation in J02_RESULT_IMPORT_OPERATIONS
    }


__all__ = [
    "BUNDLE_WIRE_VERSION",
    "CHECKSUM_PREFIX",
    "J02_RESULT_IMPORT_FAMILY",
    "J02_RESULT_IMPORT_OPERATIONS",
    "J02_RESULT_IMPORT_REQUEST_VERSION",
    "J02_RESULT_IMPORT_RESPONSE_VERSION",
    "J02_RESULT_IMPORT_SCOPE",
    "OUTPUT_IDENTITY_PREFIX",
    "ResultImportTransport",
    "StagedOutputMissing",
    "StagedOutputStore",
    "StagingBounds",
    "UploadChunk",
    "bundle_from_wire",
    "bundle_to_wire",
    "output_identity",
    "result_import_family_handlers",
    "run_identity_from_wire",
    "run_identity_to_wire",
    "upload_chunks",
    "upload_transfer_id",
]
