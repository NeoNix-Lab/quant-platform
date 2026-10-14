#!/usr/bin/env python3
"""Deck handoff client v1: K12 admitted input in, K13 result bundle out (issue #343, P06).

ADR-0069 section 8. This client speaks exactly two J02 message families over
WSS/mTLS, with the deck's own credential:

- ``j02-admitted-input-v1`` (scope ``j02.admitted_input.read``): ``admit``,
  ``manifest``, ``deliver``, ``acknowledge``, and the J14 ``resume`` of each
  delivered member;
- ``j02-result-import-v1`` (scope ``j02.result.submit``): ``upload_output`` and
  ``submit``.

It holds no other scope and has no other operation. It never imports
``quant_platform`` (the deck is a separate trust and deployment surface), never
falls back to an in-process call, a local file or another transport, and never
retries across a different wire family (ADR-0067 section 1 rule, applied per
family).

What it verifies before it hands a byte to the deck workload (:meth:`fetch_inputs`):

- the manifest the server returned hashes to the ``manifest_digest`` and the
  ``admission_id`` it carries;
- every J14 frame (chunk digest, ``transfer_id`` derivation, consistent
  metadata, every index exactly once, whole-payload digest) and the identity of
  the transfer (the member's logical result identity and the descriptor);
- the member document (admission, manifest digest, index, ``byte_length``) and,
  last, that the member bytes hash to the manifest's own content hash for that
  index.

Any failure is reported to the server with ``acknowledge`` (``failed``, with the
reason and the member index) and raised as :class:`DeliveryIntegrityFailure`;
**no** bytes are returned. Only after every member verified, and the server
accepted the ``delivered`` acknowledgement, are the bytes handed over.

Typed failures (ADR-0067 sections 3-4, never reinterpreted as one another):

- :class:`WireIncompatible`: an unexpected family, version or request id;
- :class:`RemoteSecurityFailure`: TLS, mTLS or scope denial (policy close
  ``1008``), before any application result existed;
- :class:`RemoteConsumerApiError`: a well-formed ``status: "error"`` reply,
  reconstructed losslessly.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
import hashlib
import json
import ssl
from typing import Any
import uuid

import websockets
from websockets.exceptions import ConnectionClosed, InvalidMessage

ADMITTED_INPUT_FAMILY = "j02-admitted-input-v1"
ADMITTED_INPUT_REQUEST_VERSION = ADMITTED_INPUT_FAMILY + "-request-v1"
ADMITTED_INPUT_RESPONSE_VERSION = ADMITTED_INPUT_FAMILY + "-response-v1"
ADMITTED_INPUT_SCOPE = "j02.admitted_input.read"
RESULT_IMPORT_FAMILY = "j02-result-import-v1"
RESULT_IMPORT_REQUEST_VERSION = RESULT_IMPORT_FAMILY + "-request-v1"
RESULT_IMPORT_RESPONSE_VERSION = RESULT_IMPORT_FAMILY + "-response-v1"
RESULT_IMPORT_SCOPE = "j02.result.submit"
DECK_SCOPES = (ADMITTED_INPUT_SCOPE, RESULT_IMPORT_SCOPE)

J14_FAMILY = "j14-framed-result-v1"
MEMBER_SCHEMA = "admitted-input-member-v1"
OUTPUT_IDENTITY_PREFIX = "result-output-v1:sha256:"
CHECKSUM_PREFIX = "sha256:"
BUNDLE_WIRE_VERSION = "governed-result-bundle-wire-v1"
DEFAULT_FRAME_PAYLOAD_BYTES = 64 * 1024

# Must match quant_platform.application.api_transport_server.J02_MAX_WIRE_MESSAGE_BYTES.
# This client cannot import quant_platform (ADR-0069 section 8), so the literal
# is duplicated by design; tests/test_deck_handoff_client_v1.py keeps it honest.
J02_MAX_WIRE_MESSAGE_BYTES = 16 * 1024 * 1024
_POLICY_DENIAL_CLOSE_CODE = 1008

Connector = Callable[..., Any]


class WireIncompatible(Exception):
    """An unexpected family, schema version or request id: never retried, never downgraded."""


class RemoteSecurityFailure(Exception):
    """TLS, mTLS or scope authorization failed; there is no application result."""


class DeliveryIntegrityFailure(Exception):
    """A delivered member did not verify; ``reason`` is the acknowledged failure reason."""

    def __init__(self, reason: str, member_index: int | None, detail: str):
        super().__init__(f"{reason} (member {member_index}): {detail}")
        self.reason = reason
        self.member_index = member_index


@dataclass(frozen=True, slots=True)
class RemoteConsumerApiError(Exception):
    """Lossless local mirror of a server-issued error payload."""

    code: str
    message: str
    context: Mapping[str, Any] = field(default_factory=dict)
    request_identity: str | None = None

    def __str__(self) -> str:
        reason = self.context.get("reason") if isinstance(self.context, Mapping) else None
        return f"{self.code}: {self.message}" + (f" ({reason})" if reason else "")


@dataclass(frozen=True, slots=True)
class Admission:
    admission_id: str
    manifest_digest: str
    manifest: Mapping[str, Any]
    state: str


@dataclass(frozen=True, slots=True)
class AdmittedInput:
    """Verified input, handed over only after every member verified and was acknowledged."""

    admission_id: str
    manifest_digest: str
    manifest: Mapping[str, Any]
    members: tuple[bytes, ...]
    member_identities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StagedOutput:
    checksum: str
    byte_length: int
    transfer_id: str


@dataclass(frozen=True, slots=True)
class Registration:
    bundle_digest: str
    run: Mapping[str, Any]
    outputs: tuple[Mapping[str, Any], ...]


def build_client_ssl_context(
    *, client_certificate_path: str, client_private_key_path: str, server_trust_anchor_path: str,
) -> ssl.SSLContext:
    """The strict TLS 1.3 mutual-TLS client context ADR-0063 requires."""

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.load_verify_locations(cafile=server_trust_anchor_path)
    context.load_cert_chain(client_certificate_path, client_private_key_path)
    return context


# -- identities the client recomputes (independent of the server) ----------------


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_digest(manifest: Mapping[str, Any]) -> str:
    """SHA-256 of the manifest's canonical payload (ADR-0064 section 2, sorted-compact-ascii-v1)."""

    return _digest(
        json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
        ).encode("utf-8")
    )


def output_identity(payload_sha256: str) -> str:
    return OUTPUT_IDENTITY_PREFIX + payload_sha256


def transfer_id(logical_result_identity: str, payload_sha256: str) -> str:
    """The ADR-0066 section 2 ``transfer_id``: the digest of the named preimage object.

    All four members are ASCII text without escapes, for which RFC 8785 and
    sorted-compact JSON are the same bytes.
    """

    return _digest(
        json.dumps(
            {
                "logical_result_identity": logical_result_identity,
                "message_family": J14_FAMILY,
                "payload_sha256": payload_sha256,
                "schema_version": J14_FAMILY,
            },
            sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        ).encode("utf-8")
    )


def output_chunks(
    payload: bytes, *, frame_payload_bytes: int = DEFAULT_FRAME_PAYLOAD_BYTES,
) -> list[dict[str, Any]]:
    """The ``upload_output`` request bodies of ``payload`` (deterministic offsets)."""

    if type(payload) is not bytes or not payload:
        raise ValueError("an output payload must be non-empty bytes")
    if type(frame_payload_bytes) is not int or frame_payload_bytes < 1:
        raise ValueError("frame_payload_bytes must be a positive integer")
    digest = _digest(payload)
    pieces = [
        payload[offset : offset + frame_payload_bytes]
        for offset in range(0, len(payload), frame_payload_bytes)
    ]
    return [
        {
            "transfer_id": transfer_id(output_identity(digest), digest),
            "logical_result_identity": output_identity(digest),
            "payload_sha256": digest,
            "chunk_index": index,
            "chunk_count": len(pieces),
            "chunk_sha256": _digest(piece),
            "chunk_base64": base64.b64encode(piece).decode("ascii"),
        }
        for index, piece in enumerate(pieces)
    ]


# -- frame verification ---------------------------------------------------------


class _FrameFailure(Exception):
    def __init__(self, reason: str, detail: str):
        super().__init__(detail)
        self.reason = reason


def _is_digest(value: Any) -> bool:
    return (
        isinstance(value, str) and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def reassemble(frames: Sequence[Mapping[str, Any]], *, descriptor: Mapping[str, Any]) -> bytes:
    """The verified payload bytes of one J14 transfer, or a ``_FrameFailure``.

    ``descriptor`` is what ``deliver`` announced for the member (logical
    identity, ``transfer_id``, ``payload_sha256``, ``chunk_count``): the frames
    must agree with it, not only with themselves.
    """

    expected_count = descriptor["chunk_count"]
    if len(frames) != expected_count:
        raise _FrameFailure("transfer_integrity_failure", "frame count differs from the descriptor")
    seen: dict[int, bytes] = {}
    for frame in frames:
        try:
            if (
                frame["message_family"] != J14_FAMILY or frame["schema_version"] != J14_FAMILY
                or frame["message_type"] != "frame"
            ):
                raise _FrameFailure("transfer_integrity_failure", "not a J14 frame")
            chunk = base64.b64decode(frame["chunk_base64"], validate=True)
            index, count = frame["chunk_index"], frame["chunk_count"]
            if (
                type(index) is not int or type(count) is not int or not 0 <= index < count
                or not chunk or _digest(chunk) != frame["chunk_sha256"]
            ):
                raise _FrameFailure("transfer_integrity_failure", "chunk does not verify")
            if (
                frame["logical_result_identity"] != descriptor["logical_result_identity"]
                or frame["payload_sha256"] != descriptor["payload_sha256"]
                or frame["transfer_id"] != descriptor["transfer_id"]
                or count != expected_count
                or transfer_id(frame["logical_result_identity"], frame["payload_sha256"])
                != frame["transfer_id"]
            ):
                raise _FrameFailure("transfer_integrity_failure", "frame metadata is inconsistent")
        except (KeyError, TypeError, binascii.Error, ValueError) as error:
            raise _FrameFailure("transfer_integrity_failure", f"malformed frame: {error}") from error
        if index in seen:
            raise _FrameFailure("transfer_integrity_failure", "duplicate chunk index")
        seen[index] = chunk
    payload = b"".join(seen[position] for position in range(expected_count))
    if _digest(payload) != descriptor["payload_sha256"]:
        raise _FrameFailure("transfer_integrity_failure", "payload digest mismatch")
    return payload


def verify_member_document(
    document: Any, *, admission: Admission, index: int, content_hash: str,
) -> tuple[bytes, str]:
    """The member's bytes and identity from a reassembled ``admitted-input-member-v1`` document.

    The bytes are checked against the *manifest's* content hash for this index
    (the admission's own, not the document's), last, before anything uses them.
    """

    if (
        not isinstance(document, dict) or document.get("schema_version") != MEMBER_SCHEMA
        or document.get("admission_id") != admission.admission_id
        or document.get("manifest_digest") != admission.manifest_digest
        or document.get("member_index") != index
        or document.get("encoding") != "base64"
        or not isinstance(document.get("member_identity"), str)
    ):
        raise _FrameFailure("transfer_integrity_failure", "member document does not match its admission")
    try:
        data = base64.b64decode(document["bytes"], validate=True)
    except (KeyError, TypeError, binascii.Error, ValueError) as error:
        raise _FrameFailure("transfer_integrity_failure", "member bytes are not base64") from error
    if document.get("byte_length") != len(data):
        raise _FrameFailure("transfer_integrity_failure", "byte_length does not match the bytes")
    if _digest(data) != content_hash or document.get("content_hash") != content_hash:
        raise _FrameFailure("content_hash_mismatch", "member bytes do not hash to the manifest's content hash")
    return data, document["member_identity"]


# -- the session -------------------------------------------------------------------


class DeckHandoffClient:
    """One authenticated connection carrying a handoff: use as ``async with``."""

    def __init__(
        self, url: str, *, ssl_context: ssl.SSLContext | None, connect: Connector = websockets.connect,
    ) -> None:
        self._url = url
        self._ssl = ssl_context
        self._connect = connect
        self._context: Any = None
        self._socket: Any = None

    async def __aenter__(self) -> "DeckHandoffClient":
        try:
            self._context = self._connect(
                self._url, ssl=self._ssl, max_size=J02_MAX_WIRE_MESSAGE_BYTES,
            )
            self._socket = await self._context.__aenter__()
        except ssl.SSLError as error:
            raise RemoteSecurityFailure(f"TLS/mTLS handshake failed: {error}") from error
        except InvalidMessage as error:
            # An aborted TLS handshake (untrusted chain, expired or revoked peer
            # certificate, hostname mismatch) surfaces as a failed opening
            # handshake; this client only speaks wss to a J09 listener.
            raise RemoteSecurityFailure(
                f"TLS/mTLS handshake failed: {error.__cause__ or error}"
            ) from error
        return self

    async def __aexit__(self, *exc: object) -> bool:
        context, self._context, self._socket = self._context, None, None
        if context is not None:
            return bool(await context.__aexit__(*exc))
        return False

    # -- message exchange ------------------------------------------------------

    async def _send(self, message: Mapping[str, Any]) -> None:
        try:
            await self._socket.send(json.dumps(dict(message), sort_keys=True, separators=(",", ":")))
        except ConnectionClosed as error:
            self._closed(error)

    async def _receive(self) -> Mapping[str, Any]:
        try:
            raw = await self._socket.recv()
        except ConnectionClosed as error:
            self._closed(error)
        try:
            message = json.loads(raw)
        except (TypeError, ValueError) as error:
            raise WireIncompatible(f"response was not valid JSON: {error}") from error
        if not isinstance(message, Mapping):
            raise WireIncompatible("response was not a JSON object")
        return message

    @staticmethod
    def _closed(error: ConnectionClosed) -> None:
        close = error.rcvd or error.sent
        if close is not None and close.code == _POLICY_DENIAL_CLOSE_CODE:
            raise RemoteSecurityFailure(
                f"remote policy denied the connection: {close.reason or 'policy denied'}"
            ) from error
        raise error

    async def _call(self, family: str, operation: str, request: Mapping[str, Any]) -> Mapping[str, Any]:
        """One family request; only the two declared families are ever spoken."""

        if family == ADMITTED_INPUT_FAMILY:
            version, response_version = ADMITTED_INPUT_REQUEST_VERSION, ADMITTED_INPUT_RESPONSE_VERSION
        elif family == RESULT_IMPORT_FAMILY:
            version, response_version = RESULT_IMPORT_REQUEST_VERSION, RESULT_IMPORT_RESPONSE_VERSION
        else:  # pragma: no cover - the public methods are the only callers
            raise ValueError("this client speaks only the two deck families")
        request_id = f"deck-{uuid.uuid4()}"
        await self._send({
            "message_family": family, "schema_version": version, "operation": operation,
            "request_id": request_id, "request": dict(request),
        })
        envelope = await self._receive()
        if (
            envelope.get("message_family") != family
            or envelope.get("schema_version") != response_version
            or envelope.get("request_id") != request_id
        ):
            raise WireIncompatible(
                "unexpected response family/version/request id: "
                f"{envelope.get('message_family')!r} {envelope.get('schema_version')!r}"
            )
        status = envelope.get("status")
        if status == "error":
            error = envelope.get("error")
            if not isinstance(error, Mapping):
                raise WireIncompatible("error envelope without a mapping 'error'")
            raise RemoteConsumerApiError(
                code=error.get("code"), message=error.get("message"),
                context=dict(error.get("context") or {}),
                request_identity=error.get("request_identity"),
            )
        result = envelope.get("result")
        if status != "ok" or not isinstance(result, Mapping):
            raise WireIncompatible(f"unexpected response status {status!r}")
        return result

    # -- K12: admitted input -----------------------------------------------------

    async def admit(
        self, *, operation_identity: str, profile_identity: str | None = None,
        dataset: Mapping[str, Any] | None = None, artifact_identities: Sequence[str] = (),
    ) -> Admission:
        """Ask for a semantic input (a dataset selector or accepted artifact identities)."""

        result = await self._call(ADMITTED_INPUT_FAMILY, "admit", {
            "dataset": None if dataset is None else dict(dataset),
            "artifact_identities": list(artifact_identities),
            "operation_identity": operation_identity,
            "profile_identity": profile_identity,
        })
        return self._admission(result)

    async def manifest(self, admission_id: str) -> Admission:
        result = await self._call(ADMITTED_INPUT_FAMILY, "manifest", {"admission_id": admission_id})
        return self._admission(result)

    @staticmethod
    def _admission(result: Mapping[str, Any]) -> Admission:
        try:
            manifest = result["manifest"]
            digest = result["manifest_digest"]
            admission_id = result["admission_id"]
            state = result["state"]
        except KeyError as error:
            raise WireIncompatible(f"admission document is missing {error}") from error
        if (
            not isinstance(manifest, dict) or manifest_digest(manifest) != digest
            or admission_id != f"admitted-input-v1:{digest}"
        ):
            # The server's own words about the manifest do not hash to its identity.
            raise DeliveryIntegrityFailure(
                "transfer_integrity_failure", None, "manifest does not hash to its identity",
            )
        return Admission(admission_id, digest, manifest, state)

    async def fetch_inputs(self, admission: Admission) -> AdmittedInput:
        """Deliver, verify every member, acknowledge, and only then hand the bytes over."""

        manifest = admission.manifest
        content_hashes = manifest.get("content_hashes")
        if not isinstance(content_hashes, list) or not content_hashes:
            raise DeliveryIntegrityFailure("transfer_integrity_failure", None, "manifest has no members")
        delivery = await self._call(
            ADMITTED_INPUT_FAMILY, "deliver", {"admission_id": admission.admission_id},
        )
        members = delivery.get("members")
        if (
            delivery.get("admission_id") != admission.admission_id
            or delivery.get("manifest_digest") != admission.manifest_digest
            or not isinstance(members, list) or len(members) != len(content_hashes)
        ):
            await self._acknowledge_failure(admission, "transfer_integrity_failure", None)
            raise DeliveryIntegrityFailure(
                "transfer_integrity_failure", None, "the delivery does not describe the admission",
            )
        verified: list[tuple[bytes, str]] = []
        for index, descriptor in enumerate(members):
            try:
                verified.append(await self._member(admission, index, descriptor, content_hashes[index]))
            except _FrameFailure as failure:
                await self._acknowledge_failure(admission, failure.reason, index)
                raise DeliveryIntegrityFailure(failure.reason, index, str(failure)) from failure
        await self._call(ADMITTED_INPUT_FAMILY, "acknowledge", {
            "admission_id": admission.admission_id, "outcome": "delivered",
            "manifest_digest": admission.manifest_digest,
            "failure_reason": None, "failed_member_index": None,
        })
        return AdmittedInput(
            admission.admission_id, admission.manifest_digest, manifest,
            tuple(data for data, _ in verified), tuple(identity for _, identity in verified),
        )

    async def _member(
        self, admission: Admission, index: int, descriptor: Any, content_hash: str,
    ) -> tuple[bytes, str]:
        expected = f"{MEMBER_SCHEMA}:{admission.admission_id}:{index}"
        if (
            not isinstance(descriptor, Mapping)
            or descriptor.get("logical_result_identity") != expected
            or descriptor.get("member_index") != index
            or descriptor.get("content_hash") != content_hash
            or not _is_digest(descriptor.get("transfer_id"))
            or not _is_digest(descriptor.get("payload_sha256"))
            or type(descriptor.get("chunk_count")) is not int or descriptor["chunk_count"] < 1
        ):
            raise _FrameFailure("transfer_integrity_failure", "descriptor does not match the manifest")
        await self._send({
            "message_family": J14_FAMILY, "schema_version": J14_FAMILY, "message_type": "resume",
            "transfer_id": descriptor["transfer_id"], "next_verified_chunk_index": 0,
        })
        frames: list[Mapping[str, Any]] = []
        while len(frames) < descriptor["chunk_count"]:
            message = await self._receive()
            if message.get("message_family") != J14_FAMILY or message.get("schema_version") != J14_FAMILY:
                raise WireIncompatible("a J14 resume was answered by another family")
            if message.get("message_type") == "outcome":
                outcome = message.get("outcome")
                reason = "transfer_expired" if outcome == "transfer_expired" else "transfer_integrity_failure"
                raise _FrameFailure(reason, f"the transfer answered {outcome!r}")
            frames.append(message)
        try:
            document = json.loads(self._json_text(reassemble(frames, descriptor=descriptor)))
        except ValueError as error:
            raise _FrameFailure("transfer_integrity_failure", "payload is not JSON") from error
        return verify_member_document(
            document, admission=admission, index=index, content_hash=content_hash,
        )

    @staticmethod
    def _json_text(payload: bytes) -> str:
        try:
            return payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise _FrameFailure("transfer_integrity_failure", "payload is not UTF-8") from error

    async def _acknowledge_failure(
        self, admission: Admission, reason: str, member_index: int | None,
    ) -> None:
        await self._call(ADMITTED_INPUT_FAMILY, "acknowledge", {
            "admission_id": admission.admission_id, "outcome": "failed", "manifest_digest": None,
            "failure_reason": reason, "failed_member_index": member_index,
        })

    # -- K13: result import -------------------------------------------------------

    async def upload_output(
        self, payload: bytes, *, frame_payload_bytes: int = DEFAULT_FRAME_PAYLOAD_BYTES,
    ) -> StagedOutput:
        """Stage one output's bytes (J14 rules, client to server); returns its checksum."""

        digest = _digest(payload) if type(payload) is bytes and payload else None
        if digest is None:
            raise ValueError("an output payload must be non-empty bytes")
        size = frame_payload_bytes
        for attempt in range(2):
            progress: Mapping[str, Any] | None = None
            chunks = output_chunks(payload, frame_payload_bytes=size)
            position = 0
            try:
                while position is not None:
                    progress = await self._call(RESULT_IMPORT_FAMILY, "upload_output", chunks[position])
                    self._check_progress(progress, chunks[0])
                    position = None if progress["complete"] else progress["next_chunk_index"]
                    if position is not None and not 0 <= position < len(chunks):
                        raise WireIncompatible("the server asked for a chunk that does not exist")
            except RemoteConsumerApiError as error:
                learned = error.context.get("frame_payload_bytes") if isinstance(error.context, Mapping) else None
                if (
                    attempt == 0 and error.context.get("reason") == "chunk_layout_mismatch"
                    and type(learned) is int and learned > 0 and learned != size
                ):
                    size = learned  # the server's chunk size: start the transfer again with it
                    continue
                raise
            assert progress is not None
            if progress["checksum"] != CHECKSUM_PREFIX + digest or progress["byte_length"] != len(payload):
                raise WireIncompatible("the server staged something other than what was sent")
            return StagedOutput(progress["checksum"], len(payload), chunks[0]["transfer_id"])
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _check_progress(progress: Mapping[str, Any], first: Mapping[str, Any]) -> None:
        if (
            progress.get("transfer_id") != first["transfer_id"]
            or progress.get("payload_sha256") != first["payload_sha256"]
            or progress.get("chunk_count") != first["chunk_count"]
            or not isinstance(progress.get("complete"), bool)
        ):
            raise WireIncompatible("the upload progress is not about the transfer that was sent")

    async def submit(self, bundle: Mapping[str, Any]) -> Registration:
        """Submit one result bundle (the lossless wire document the workload produced)."""

        if not isinstance(bundle, Mapping) or bundle.get("version") != BUNDLE_WIRE_VERSION:
            raise ValueError(f"a bundle is a {BUNDLE_WIRE_VERSION} document")
        result = await self._call(RESULT_IMPORT_FAMILY, "submit", bundle)
        try:
            return Registration(
                result["bundle_digest"], dict(result["run"]),
                tuple(dict(item) for item in result["outputs"]),
            )
        except (KeyError, TypeError) as error:
            raise WireIncompatible(f"registration document is malformed: {error}") from error


__all__ = [
    "ADMITTED_INPUT_FAMILY",
    "ADMITTED_INPUT_SCOPE",
    "Admission",
    "AdmittedInput",
    "BUNDLE_WIRE_VERSION",
    "DECK_SCOPES",
    "DeckHandoffClient",
    "DeliveryIntegrityFailure",
    "J02_MAX_WIRE_MESSAGE_BYTES",
    "RESULT_IMPORT_FAMILY",
    "RESULT_IMPORT_SCOPE",
    "Registration",
    "RemoteConsumerApiError",
    "RemoteSecurityFailure",
    "StagedOutput",
    "WireIncompatible",
    "build_client_ssl_context",
    "manifest_digest",
    "output_chunks",
    "reassemble",
    "transfer_id",
    "verify_member_document",
]
