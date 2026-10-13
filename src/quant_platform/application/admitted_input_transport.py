"""Opt-in ``j02-admitted-input-v1``: K12 delivery to the deck over J02 (issue #341).

ADR-0069 section 5 over ADR-0064 section 2 (unchanged: the manifest fields, the
digest, the ``admission_id`` rule and the delivery states are K12's). Four
operations, all under ``j02.admitted_input.read`` and all initiated by the deck:

- ``admit`` takes a *semantic* request (a dataset selector in the Consumer API's
  own vocabulary, or accepted artifact identities, plus the operation and
  profile identities), has the server's authority resolve it, and seals the
  resulting :class:`AdmittedInputManifestV1`. Nothing locator-shaped is accepted
  from the deck: an unknown field, a path, a drive letter, a URL or a catalog
  UUID anywhere in the request is ``invalid_request`` before the resolver runs.
- ``manifest`` returns the sealed manifest of an ``admission_id``.
- ``deliver`` starts a delivery attempt for a ``SEALED`` admission
  (``DELIVERY_PENDING``). Each manifest member is read by the server's authority,
  verified against the manifest's content hash, and retained as its own J14
  transfer of ``admitted-input-member-v1`` (base64 bytes) with logical identity
  ``admitted-input-member-v1:<admission_id>:<member index>``. The response lists
  the transfers; the frames are fetched with J14 ``resume`` messages, which the
  connection handler answers only for a peer holding this family's scope.
- ``acknowledge`` records the deck's verified delivery (``DELIVERED``) or its
  reported failure (``DELIVERY_FAILED``). A later ``deliver`` is a new attempt
  on the same sealed admission; the manifest never changes.

Members. A manifest has no member list of its own, so this transport enumerates
them from the two fields the DataGateway keeps aligned: member *i* is
``natural_partition_identities[i]`` and its bytes must hash (SHA-256, lowercase
hex, as the catalog's ``content_sha256``) to ``content_hashes[i]``. A manifest
whose partitions and content hashes are not aligned and non-empty is refused
before sealing; nothing is delivered that cannot be verified.

Two server-owned seams are composed, not implemented here (each handler issue
supplies its own, like the ``result_bodies`` resolver of ``j02-job-v1``):

- ``resolve(request) -> AdmittedInputResolution``: the semantic request resolved
  against the server's authority, as the sealed manifest plus the byte length of
  each member (so oversized requests are refused before sealing);
- ``read_member(manifest, index) -> bytes``: the member's exact bytes.

The deck supplies neither a locator nor a length, and neither seam is reachable
by name from the wire. Composition is explicit and opt-in
(:func:`admitted_input_family_handlers`): no host wires it implicitly, and the
same ``transfers`` store must be given to the connection handler
(``framed_transfers=``, ``framed_resume_scopes`` containing
:data:`J02_ADMITTED_INPUT_SCOPE`).
"""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
import hashlib
import re
from typing import Any

from .admitted_input import (
    AdmittedInputConflict,
    AdmittedInputExpired,
    AdmittedInputManifestV1,
    AdmittedInputNotFound,
    AdmittedInputRecord,
    AdmittedInputState,
    AdmittedInputStore,
)
from .api_transport_server import J02FamilyHandler
from .framed_result_transport import FramedResultTransferStore, FramedResultTransportError
from .market_data import (
    ConsumerApiError,
    ConsumerErrorCode,
    ConsumerMarketDataQuery,
    RepresentationRef,
)

J02_ADMITTED_INPUT_FAMILY = "j02-admitted-input-v1"
J02_ADMITTED_INPUT_REQUEST_VERSION = J02_ADMITTED_INPUT_FAMILY + "-request-v1"
J02_ADMITTED_INPUT_RESPONSE_VERSION = J02_ADMITTED_INPUT_FAMILY + "-response-v1"
J02_ADMITTED_INPUT_SCOPE = "j02.admitted_input.read"
J02_ADMITTED_INPUT_OPERATIONS = ("admit", "manifest", "deliver", "acknowledge")
ADMITTED_INPUT_MEMBER_SCHEMA = "admitted-input-member-v1"

# Implementation bounds (not ADR values; ADR-0069 section 5 leaves them to the
# server's configuration). The defaults fit the default J14 retention.
MAX_TEXT_LENGTH = 512
MAX_ARTIFACT_IDENTITIES = 256
MAX_DEFINITION_ENTRIES = 16
DEFAULT_MAX_MEMBERS = 16
DEFAULT_MAX_MEMBER_BYTES = 3 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 4 * 1024 * 1024
# Upper bound on what one member document adds to its base64 payload (keys,
# identities, hashes, JSON escaping). The transfer store remains the authority.
_MEMBER_DOCUMENT_OVERHEAD = 8192

FAILURE_REASONS = (
    "content_hash_mismatch",
    "transfer_integrity_failure",
    "transfer_expired",
    "delivery_interrupted",
)

AdmittedInputStoreProvider = Callable[[], AbstractContextManager[AdmittedInputStore]]

_INVALID = "the request is not valid"
_ADMISSION_ID = re.compile(r"admitted-input-v1:[0-9a-f]{64}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_UUID = re.compile(
    r"(?<![0-9a-fA-F])(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}|[0-9a-fA-F]{32})(?![0-9a-fA-F])"
)
_DRIVE = re.compile(r"^[A-Za-z]:")
# A field *named* like a locator is a locator request, whatever its value.
_LOCATOR_KEY_PARTS = (
    "path", "uuid", "root", "locator", "url", "uri", "dir", "file", "mount", "handle",
)


@dataclass(frozen=True, slots=True)
class AdmittedInputRequest:
    """What the deck may ask for: semantic meaning, never a locator.

    Exactly one of ``dataset`` (the Consumer API's own selector: venue,
    instrument, interval and representation) and ``artifact_identities``.
    """

    dataset: ConsumerMarketDataQuery | None
    artifact_identities: tuple[str, ...]
    operation_identity: str
    profile_identity: str | None


@dataclass(frozen=True, slots=True)
class AdmittedInputResolution:
    """The server authority's answer to one request."""

    manifest: AdmittedInputManifestV1
    member_byte_lengths: tuple[int, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.manifest, AdmittedInputManifestV1):
            raise TypeError("manifest must be an AdmittedInputManifestV1")
        lengths = tuple(self.member_byte_lengths)
        if any(type(value) is not int or value < 0 for value in lengths):
            raise ValueError("member_byte_lengths must be non-negative integers")
        object.__setattr__(self, "member_byte_lengths", lengths)


@dataclass(frozen=True, slots=True)
class AdmittedInputBounds:
    max_members: int = DEFAULT_MAX_MEMBERS
    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES

    def __post_init__(self) -> None:
        for name in ("max_members", "max_member_bytes", "max_total_bytes"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_member_bytes > self.max_total_bytes:
            raise ValueError("max_member_bytes must not exceed max_total_bytes")


AdmittedInputResolver = Callable[[AdmittedInputRequest], AdmittedInputResolution]
AdmittedInputMemberReader = Callable[[AdmittedInputManifestV1, int], bytes]


class _LocatorRejected(ValueError):
    """The request names, or is, a locator."""


def _integrity(reason: str) -> ConsumerApiError:
    return ConsumerApiError(
        ConsumerErrorCode.INTEGRITY_FAILURE,
        "the platform cannot produce a trustworthy result",
        context={"reason": reason},
    )


def _refusal(reason: str) -> ConsumerApiError:
    """A fixed message plus a stable reason: no store or resolver text is forwarded."""
    return ConsumerApiError(
        ConsumerErrorCode.INVALID_REQUEST, _INVALID, context={"reason": reason},
    )


def _too_large(reason: str) -> ConsumerApiError:
    return ConsumerApiError(
        ConsumerErrorCode.RESULT_TOO_LARGE,
        "the admitted input exceeds the configured delivery bounds",
        context={"reason": reason},
    )


def _text(value: Any) -> str:
    if (
        type(value) is not str or not value or value != value.strip()
        or len(value) > MAX_TEXT_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("bounded non-empty text required")
    if (
        "/" in value or "\\" in value or ".." in value or value.startswith("~")
        or _DRIVE.match(value) or _UUID.search(value)
    ):
        raise _LocatorRejected("a locator is not an identity")
    return value


def _only(body: Any, required: set[str], optional: set[str] = frozenset()) -> Mapping[str, Any]:
    if type(body) is not dict:
        raise ValueError("an object is required")
    keys = set(body)
    unexpected = keys - required - optional
    if unexpected:
        if any(part in str(key).lower() for key in unexpected for part in _LOCATOR_KEY_PARTS):
            raise _LocatorRejected("a locator field is not accepted")
        raise ValueError("the request object fields do not match")
    if not required <= keys:
        raise ValueError("the request object fields do not match")
    return body


def _definition(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if type(value) is not dict or len(value) > MAX_DEFINITION_ENTRIES:
        raise ValueError("representation definition must be a small object")
    document: dict[str, Any] = {}
    for key, item in value.items():
        name = _text(key)
        if type(item) is str:
            item = _text(item)
        elif item is not None and type(item) not in (bool, int):
            raise ValueError("representation definition must be flat lossless JSON")
        document[name] = item
    return document


def _query(value: Any) -> ConsumerMarketDataQuery:
    item = _only(value, {"venue", "instrument", "start", "end", "representation"})
    representation = _only(item["representation"], {"kind", "version"}, {"definition"})
    if type(representation["version"]) is not int:
        raise ValueError("representation version must be an integer")
    return ConsumerMarketDataQuery(
        venue=_text(item["venue"]),
        instrument=_text(item["instrument"]),
        start=_text(item["start"]),
        end=_text(item["end"]),
        representation=RepresentationRef(
            kind=_text(representation["kind"]),
            version=representation["version"],
            definition=_definition(representation.get("definition")),
        ),
    )


def _admit_request(body: Any) -> AdmittedInputRequest:
    item = _only(body, {"dataset", "artifact_identities", "operation_identity", "profile_identity"})
    artifacts = item["artifact_identities"]
    if type(artifacts) is not list or len(artifacts) > MAX_ARTIFACT_IDENTITIES:
        raise ValueError("artifact_identities must be a bounded array")
    identities = tuple(_text(entry) for entry in artifacts)
    if len(set(identities)) != len(identities):
        raise ValueError("artifact_identities must not repeat")
    dataset = item["dataset"]
    if (dataset is None) == (not identities):
        raise ValueError("exactly one of dataset and artifact_identities is required")
    profile = item["profile_identity"]
    return AdmittedInputRequest(
        dataset=None if dataset is None else _query(dataset),
        artifact_identities=identities,
        operation_identity=_text(item["operation_identity"]),
        profile_identity=None if profile is None else _text(profile),
    )


def _admission_id(body: Any) -> str:
    value = _only(body, {"admission_id"})["admission_id"]
    if type(value) is not str or not _ADMISSION_ID.fullmatch(value):
        raise ValueError("admission_id must be an admitted-input-v1 identity")
    return value


@dataclass(frozen=True, slots=True)
class _Acknowledgement:
    admission_id: str
    delivered: bool
    manifest_digest: str | None
    failure_reason: str | None
    failed_member_index: int | None


def _acknowledgement(body: Any) -> _Acknowledgement:
    item = _only(
        body,
        {"admission_id", "outcome", "manifest_digest", "failure_reason", "failed_member_index"},
    )
    admission_id = _admission_id({"admission_id": item["admission_id"]})
    outcome = item["outcome"]
    digest, reason, index = item["manifest_digest"], item["failure_reason"], item["failed_member_index"]
    if outcome == "delivered":
        if type(digest) is not str or not _DIGEST.fullmatch(digest) or reason is not None or index is not None:
            raise ValueError("a delivered acknowledgement carries only the manifest digest")
        return _Acknowledgement(admission_id, True, digest, None, None)
    if outcome == "failed":
        if (
            digest is not None or reason not in FAILURE_REASONS
            or (index is not None and (type(index) is not int or index < 0))
        ):
            raise ValueError("a failed acknowledgement names a reason and optionally a member")
        return _Acknowledgement(admission_id, False, None, reason, index)
    raise ValueError("outcome must be delivered or failed")


def _members(manifest: AdmittedInputManifestV1) -> tuple[tuple[str, str], ...]:
    """``(identity, content hash)`` per member, or a refusal that nothing is deliverable."""

    identities, hashes = manifest.natural_partition_identities, manifest.content_hashes
    if (
        not identities or len(identities) != len(hashes)
        or any(len(identity) > MAX_TEXT_LENGTH for identity in identities)
    ):
        raise _integrity("manifest_not_deliverable")
    return tuple(zip(identities, hashes))


def _record_document(record: AdmittedInputRecord) -> dict[str, Any]:
    return {
        "admission_id": record.admission_id,
        "manifest_digest": record.manifest.manifest_digest,
        "manifest": record.manifest.canonical_fields(),
        "state": record.state.value,
        "delivery_failure_reason": record.delivery_failure_reason,
    }


class AdmittedInputTransport:
    """Decode and execute the four operations against a store and two seams."""

    def __init__(
        self,
        store_provider: AdmittedInputStoreProvider,
        resolve: AdmittedInputResolver,
        read_member: AdmittedInputMemberReader,
        *,
        transfers: FramedResultTransferStore,
        bounds: AdmittedInputBounds = AdmittedInputBounds(),
    ):
        if not callable(store_provider):
            raise TypeError("store_provider must be callable")
        if not callable(resolve) or not callable(read_member):
            raise TypeError("resolve and read_member must be callable")
        if not isinstance(transfers, FramedResultTransferStore):
            raise TypeError("transfers must be a FramedResultTransferStore")
        if not isinstance(bounds, AdmittedInputBounds):
            raise TypeError("bounds must be AdmittedInputBounds")
        config = transfers.config
        projected = (
            4 * ((bounds.max_total_bytes + 2) // 3)
            + bounds.max_members * _MEMBER_DOCUMENT_OVERHEAD
        )
        if (
            bounds.max_members > config.max_in_flight_transfers
            or projected > config.max_retained_payload_bytes
        ):
            # Admit must never promise a delivery the transfer store cannot hold.
            raise ValueError("delivery bounds exceed what the transfer store retains")
        self._provider = store_provider
        self._resolve = resolve
        self._read_member = read_member
        self._transfers = transfers
        self._bounds = bounds

    # -- decode: pure, never touches the store or a seam -----------------------

    def decode(self, operation: str, body: Mapping[str, Any]) -> Any:
        try:
            if operation == "admit":
                return _admit_request(body)
            if operation in ("manifest", "deliver"):
                return _admission_id(body)
            if operation == "acknowledge":
                return _acknowledgement(body)
        except _LocatorRejected as error:
            raise _refusal("locator_not_accepted") from error
        except (ValueError, TypeError, KeyError, ArithmeticError, RecursionError) as error:
            raise _refusal("invalid_request_shape") from error
        raise _refusal("unsupported_operation")  # pragma: no cover - routes are fixed

    # -- execute ---------------------------------------------------------------

    def execute(self, operation: str, decoded: Any) -> dict[str, Any]:
        with self._provider() as store:
            if operation == "admit":
                return self._admit(store, decoded)
            if operation == "manifest":
                return _record_document(self._get(store, decoded))
            if operation == "deliver":
                return self._deliver(store, decoded)
            return self._acknowledge(store, decoded)

    def _admit(self, store: AdmittedInputStore, request: AdmittedInputRequest) -> dict[str, Any]:
        try:
            resolution = self._resolve(request)
        except ConsumerApiError:
            raise  # the frozen Consumer API vocabulary passes through unchanged
        except Exception as error:  # The authority's own text is never forwarded.
            raise _integrity("admission_unavailable") from error
        if not isinstance(resolution, AdmittedInputResolution):
            raise _integrity("admission_unavailable")
        manifest = resolution.manifest
        members = _members(manifest)
        lengths = resolution.member_byte_lengths
        if len(lengths) != len(members):
            raise _integrity("resolution_inconsistent")
        # Refused before sealing: nothing oversized becomes an admission.
        self._check_bounds(lengths)
        try:
            record = store.admit(manifest)
            if record.state is AdmittedInputState.ADMITTED:
                record = store.seal(manifest.admission_id)
        except AdmittedInputExpired as error:
            raise _refusal("admission_expired") from error
        except AdmittedInputConflict as error:
            raise _integrity("admission_conflict") from error
        return _record_document(record)

    def _check_bounds(self, lengths: tuple[int, ...]) -> None:
        bounds = self._bounds
        if len(lengths) > bounds.max_members:
            raise _too_large("member_count_exceeded")
        if any(length > bounds.max_member_bytes for length in lengths):
            raise _too_large("member_bytes_exceeded")
        if sum(lengths) > bounds.max_total_bytes:
            raise _too_large("total_bytes_exceeded")

    def _deliver(self, store: AdmittedInputStore, admission_id: str) -> dict[str, Any]:
        record = self._get(store, admission_id)
        if record.state is AdmittedInputState.EXPIRED:
            raise _refusal("admission_expired")
        # A pending attempt is ended by its acknowledgement, a failed one is
        # retried; delivered and unsealed evidence is not delivered again.
        if record.state not in (AdmittedInputState.SEALED, AdmittedInputState.DELIVERY_FAILED):
            raise _refusal("delivery_not_available")
        manifest = record.manifest
        members = _members(manifest)
        # Everything is read and verified before anything is retained or the
        # state moves: a corrupted member leaves the admission SEALED and no
        # partial input exists.
        if len(members) > self._bounds.max_members:
            raise _too_large("member_count_exceeded")
        documents = []
        total = 0
        for index, (identity, content_hash) in enumerate(members):
            data = self._member_bytes(manifest, index)
            # The admitted lengths are the server's own estimate; the bytes
            # actually read are held to the same bounds, member by member.
            total += len(data)
            if len(data) > self._bounds.max_member_bytes:
                raise _too_large("member_bytes_exceeded")
            if total > self._bounds.max_total_bytes:
                raise _too_large("total_bytes_exceeded")
            if hashlib.sha256(data).hexdigest() != content_hash:
                raise _integrity("member_content_mismatch")
            documents.append((index, identity, content_hash, data))
        descriptors = [
            self._retain(record, index, identity, content_hash, data)
            for index, identity, content_hash, data in documents
        ]
        try:
            record = store.begin_delivery(admission_id)
        except AdmittedInputExpired as error:
            raise _refusal("admission_expired") from error
        except AdmittedInputConflict as error:
            raise _refusal("delivery_not_available") from error
        return {
            "admission_id": record.admission_id,
            "manifest_digest": manifest.manifest_digest,
            "state": record.state.value,
            "members": descriptors,
        }

    def _member_bytes(self, manifest: AdmittedInputManifestV1, index: int) -> bytes:
        try:
            data = self._read_member(manifest, index)
        except ConsumerApiError:
            raise
        except Exception as error:  # The authority's own text is never forwarded.
            raise _integrity("member_unavailable") from error
        if type(data) is not bytes:
            raise _integrity("member_unavailable")
        return data

    def _retain(
        self, record: AdmittedInputRecord, index: int, identity: str, content_hash: str,
        data: bytes,
    ) -> dict[str, Any]:
        logical_identity = f"{ADMITTED_INPUT_MEMBER_SCHEMA}:{record.admission_id}:{index}"
        document = {
            "schema_version": ADMITTED_INPUT_MEMBER_SCHEMA,
            "admission_id": record.admission_id,
            "manifest_digest": record.manifest.manifest_digest,
            "member_index": index,
            "member_identity": identity,
            "content_hash": content_hash,
            "byte_length": len(data),
            "encoding": "base64",
            "bytes": base64.b64encode(data).decode("ascii"),
        }
        try:
            first = self._transfers.admit(
                logical_result_identity=logical_identity, result=document,
                owner=J02_ADMITTED_INPUT_SCOPE,
            )[0]
        except FramedResultTransportError as error:
            # ADR-0066 section 3: refuse admission rather than retain without bound.
            raise _too_large("transfer_refused") from error
        return {
            "member_index": index,
            "member_identity": identity,
            "content_hash": content_hash,
            "byte_length": len(data),
            "logical_result_identity": first.logical_result_identity,
            "transfer_id": first.transfer_id,
            "payload_sha256": first.payload_sha256,
            "chunk_count": first.chunk_count,
        }

    def _acknowledge(self, store: AdmittedInputStore, request: _Acknowledgement) -> dict[str, Any]:
        record = self._get(store, request.admission_id)
        if record.state is AdmittedInputState.EXPIRED:
            raise _refusal("admission_expired")
        if record.state is not AdmittedInputState.DELIVERY_PENDING:
            raise _refusal("acknowledgement_not_expected")
        try:
            if request.delivered:
                record = store.record_delivery(request.admission_id, request.manifest_digest)
            else:
                index = request.failed_member_index
                if index is not None and index >= len(record.manifest.content_hashes):
                    raise _refusal("member_index_out_of_range")
                reason = (
                    request.failure_reason if index is None
                    else f"{request.failure_reason}:member:{index}"
                )
                record = store.record_delivery_failure(request.admission_id, reason)
        except AdmittedInputConflict as error:
            raise _refusal("acknowledgement_not_expected") from error
        return {
            "admission_id": record.admission_id,
            "manifest_digest": record.manifest.manifest_digest,
            "state": record.state.value,
            "delivery_failure_reason": record.delivery_failure_reason,
        }

    @staticmethod
    def _get(store: AdmittedInputStore, admission_id: str) -> AdmittedInputRecord:
        try:
            return store.get(admission_id)
        except AdmittedInputNotFound as error:
            raise _refusal("admission_not_found") from error
        except AdmittedInputConflict as error:
            # A stored payload that no longer matches its identity is corruption.
            raise _integrity("admission_corrupt") from error


def admitted_input_family_handlers(
    store_provider: AdmittedInputStoreProvider,
    resolve: AdmittedInputResolver,
    read_member: AdmittedInputMemberReader,
    *,
    transfers: FramedResultTransferStore,
    bounds: AdmittedInputBounds = AdmittedInputBounds(),
) -> dict[tuple[str, str, str], J02FamilyHandler]:
    """Explicit server composition only; no host and no other family is added.

    ``store_provider`` yields a store per request (its own connection, closed on
    exit). ``resolve`` and ``read_member`` are the server-owned seams described
    in the module docstring. ``transfers`` retains each delivered member as a J14
    transfer owned by :data:`J02_ADMITTED_INPUT_SCOPE`; the *same* store must be
    given to the connection handler (``framed_transfers=``) with
    ``framed_resume_scopes`` containing that scope, or nothing can be fetched.
    """

    transport = AdmittedInputTransport(
        store_provider, resolve, read_member, transfers=transfers, bounds=bounds,
    )
    return {
        (J02_ADMITTED_INPUT_FAMILY, J02_ADMITTED_INPUT_REQUEST_VERSION, operation): J02FamilyHandler(
            J02_ADMITTED_INPUT_SCOPE, J02_ADMITTED_INPUT_RESPONSE_VERSION,
            lambda body, operation=operation: transport.decode(operation, body),
            lambda decoded, operation=operation: transport.execute(operation, decoded),
        )
        for operation in J02_ADMITTED_INPUT_OPERATIONS
    }


__all__ = [
    "ADMITTED_INPUT_MEMBER_SCHEMA",
    "AdmittedInputBounds",
    "AdmittedInputRequest",
    "AdmittedInputResolution",
    "AdmittedInputTransport",
    "FAILURE_REASONS",
    "J02_ADMITTED_INPUT_FAMILY",
    "J02_ADMITTED_INPUT_OPERATIONS",
    "J02_ADMITTED_INPUT_REQUEST_VERSION",
    "J02_ADMITTED_INPUT_RESPONSE_VERSION",
    "J02_ADMITTED_INPUT_SCOPE",
    "admitted_input_family_handlers",
]
