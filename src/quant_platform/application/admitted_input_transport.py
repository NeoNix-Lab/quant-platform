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

Members. A manifest has no member list of its own (and its logical identities
are stored sorted, so no positional pairing of identities to hashes can be
trusted), so the *server's authority* declares them: ``members(manifest)`` names,
for each content hash the manifest holds, the member identity and byte length.
The transport accepts a declaration only when it is exactly the manifest's own
``content_hashes``, in the manifest's order, and every identity is one the
manifest already names (a natural partition or a logical input identity); it
never invents a member or a pairing. A member's bytes must hash (SHA-256,
lowercase hex, as the catalog's ``content_sha256``) to its content hash, and
:func:`aligned_partition_members` is the DataGateway's own partition/hash
alignment for the common dataset case. A manifest the authority cannot declare
completely is refused before sealing; nothing is delivered that cannot be
verified.

Three server-owned seams are composed, not implemented here (each handler issue
supplies its own, like the ``result_bodies`` resolver of ``j02-job-v1``):

- ``resolve(request) -> AdmittedInputManifestV1``: the semantic request resolved
  against the server's authority;
- ``members(manifest) -> tuple[AdmittedInputMember, ...]``: the declared members
  of a sealed manifest, with their byte lengths (so oversized requests are
  refused before sealing);
- ``read_member(manifest, index) -> bytes``: the member's exact bytes.

Known limits, from the independent audit of this family (issue #357 closes
them; **do not compose this family in a host before it does**):

- K12 keeps one state per admission and no attempt identity. An acknowledgement
  that is late or duplicated for an *older* attempt is applied to the current
  one, and a delivery that is still running can publish after the admission
  was ended by another operation. The server never ends an attempt by itself.
- J14 ``resume`` of a member does not consult the admission: delivered members
  stay resumable until the J14 retention, also after ``EXPIRED``.
- A ``ConsumerApiError`` raised by an authority seam reaches the wire as the
  authority wrote it; the seams are trusted server code and must raise only
  public, stable messages until the projection of #357 exists.
- ``ok`` on ``deliver`` means "published at the time of the response"; a
  ``transfer_expired`` at ``resume`` is a normal outcome (failure acknowledgement,
  then a new ``deliver``).

The deck supplies neither a locator nor a length, and no seam is reachable by
name from the wire. Composition is explicit and opt-in
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
# A field *named* like a locator is a locator request, whatever its value. Names
# are compared as whole words (``storage_root``, ``filePath``), so ``profile``
# or ``direction`` are not mistaken for ``file`` or ``dir``.
_LOCATOR_WORDS = frozenset({
    "path", "paths", "uuid", "guid", "root", "locator", "url", "uri", "dir",
    "directory", "folder", "file", "filename", "filepath", "mount", "handle",
})
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
# A value that ends in a data-file extension is a file name, not an identity.
_DATA_FILE_SUFFIXES = frozenset({
    "parquet", "csv", "tsv", "json", "jsonl", "ndjson", "gz", "zst", "zip", "tar",
    "feather", "arrow", "db", "sqlite", "bin", "pkl", "npy", "h5", "hdf5",
})


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
class AdmittedInputMember:
    """One deliverable member of a sealed manifest, as the server's authority declares it."""

    identity: str
    content_hash: str
    byte_length: int

    def __post_init__(self) -> None:
        if type(self.identity) is not str or not self.identity:
            raise ValueError("identity must be a non-empty string")
        if type(self.content_hash) is not str or not _DIGEST.fullmatch(self.content_hash):
            raise ValueError("content_hash must be lowercase SHA-256 hexadecimal")
        if type(self.byte_length) is not int or self.byte_length < 0:
            raise ValueError("byte_length must be a non-negative integer")


def aligned_partition_members(
    manifest: AdmittedInputManifestV1, byte_lengths: tuple[int, ...],
) -> tuple[AdmittedInputMember, ...]:
    """Members of a dataset manifest: natural partition *i* with content hash *i*.

    The DataGateway keeps ``natural_partitions`` and ``content_hashes`` aligned
    (H01 refuses them otherwise); this is that alignment, offered to an
    authority that holds each partition's length. Misaligned input is refused.
    """

    identities, hashes = manifest.natural_partition_identities, manifest.content_hashes
    if len(identities) != len(hashes) or len(hashes) != len(byte_lengths):
        raise ValueError("partitions, content hashes and byte lengths must align")
    return tuple(
        AdmittedInputMember(identity, content_hash, length)
        for identity, content_hash, length in zip(identities, hashes, byte_lengths)
    )


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


AdmittedInputResolver = Callable[[AdmittedInputRequest], AdmittedInputManifestV1]
AdmittedInputMembers = Callable[[AdmittedInputManifestV1], tuple[AdmittedInputMember, ...]]
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


def _is_locator_name(key: Any) -> bool:
    return any(word.lower() in _LOCATOR_WORDS for word in _WORD.findall(str(key)))


def _text(value: Any, *, symbol: bool = False) -> str:
    """Bounded identity text. ``symbol`` admits one inner ``/`` (``BTC/USD``).

    An instrument is a catalog key that only a lookup interprets, and the
    Consumer API's own vocabulary has separated symbols; a path never gets
    that far, because anything locator-shaped is refused here first.
    """

    if (
        type(value) is not str or not value or value != value.strip()
        or len(value) > MAX_TEXT_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("bounded non-empty text required")
    if symbol:
        parts = value.split("/")
        slashes_ok = len(parts) <= 2 and all(part and part not in (".", "..") for part in parts)
    else:
        slashes_ok = "/" not in value
    suffix = value.rsplit(".", 1)[-1].lower() if "." in value else ""
    if (
        not slashes_ok or "\\" in value or ".." in value or value.startswith("~")
        or _DRIVE.match(value) or _UUID.search(value) or "://" in value
        or suffix in _DATA_FILE_SUFFIXES
    ):
        raise _LocatorRejected("a locator is not an identity")
    return value


def _only(body: Any, required: set[str], optional: set[str] = frozenset()) -> Mapping[str, Any]:
    if type(body) is not dict:
        raise ValueError("an object is required")
    keys = set(body)
    unexpected = keys - required - optional
    if unexpected:
        if any(_is_locator_name(key) for key in unexpected):
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
        if _is_locator_name(key):
            raise _LocatorRejected("a locator field is not accepted")
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
        instrument=_text(item["instrument"], symbol=True),
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
        members: AdmittedInputMembers,
        read_member: AdmittedInputMemberReader,
        *,
        transfers: FramedResultTransferStore,
        bounds: AdmittedInputBounds = AdmittedInputBounds(),
    ):
        if not callable(store_provider):
            raise TypeError("store_provider must be callable")
        if not callable(resolve) or not callable(members) or not callable(read_member):
            raise TypeError("resolve, members and read_member must be callable")
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
        self._members = members
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
            manifest = self._resolve(request)
        except ConsumerApiError:
            raise  # the frozen Consumer API vocabulary passes through unchanged
        except Exception as error:  # The authority's own text is never forwarded.
            raise _integrity("admission_unavailable") from error
        if not isinstance(manifest, AdmittedInputManifestV1):
            raise _integrity("admission_unavailable")
        members = self._declared_members(manifest)
        # Refused before sealing: nothing oversized becomes an admission.
        self._check_bounds(tuple(member.byte_length for member in members))
        try:
            record = store.admit(manifest)
            if record.state is AdmittedInputState.ADMITTED:
                record = store.seal(manifest.admission_id)
        except AdmittedInputExpired as error:
            raise _refusal("admission_expired") from error
        except AdmittedInputConflict as error:
            raise _integrity("admission_conflict") from error
        return _record_document(record)

    def _declared_members(self, manifest: AdmittedInputManifestV1) -> tuple[AdmittedInputMember, ...]:
        """The authority's members, accepted only if they are exactly the manifest's own.

        Complete (every content hash the manifest holds), in the manifest's
        order, and naming only identities the manifest already carries: the
        transport adds no member and no pairing of its own.
        """

        try:
            declared = self._members(manifest)
        except ConsumerApiError:
            raise
        except Exception as error:  # The authority's own text is never forwarded.
            raise _integrity("members_unavailable") from error
        if type(declared) not in (tuple, list) or not all(
            isinstance(member, AdmittedInputMember) for member in declared
        ):
            raise _integrity("members_unavailable")
        declared = tuple(declared)
        if not declared:
            raise _integrity("manifest_not_deliverable")
        if [member.content_hash for member in declared] != list(manifest.content_hashes):
            raise _integrity("members_not_in_manifest")
        if any(len(member.identity) > MAX_TEXT_LENGTH for member in declared):
            raise _integrity("member_identity_not_in_manifest")
        partitions = manifest.natural_partition_identities
        if partitions:
            # The manifest's own pairs: natural partition *i* is content hash
            # *i* (the DataGateway keeps them aligned, in this order, in the
            # canonical payload). An identity that is merely *known* to the
            # manifest, attached to another partition's bytes, is a swap.
            if len(partitions) != len(manifest.content_hashes):
                raise _integrity("manifest_not_deliverable")
            if [member.identity for member in declared] != list(partitions):
                raise _integrity("member_identity_mismatch")
        else:
            # No partition list: logical identities are stored sorted, so the
            # manifest carries no pairing to check a member against. A single
            # input identity is the only attribution that is then verifiable.
            only = manifest.logical_input_identities
            if len(only) != 1 or any(member.identity != only[0] for member in declared):
                raise _integrity("member_attribution_unverifiable")
        return declared

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
        members = self._declared_members(manifest)
        if len(members) > self._bounds.max_members:
            raise _too_large("member_count_exceeded")
        # Everything is read and verified before anything is retained or the
        # state moves: a corrupted member leaves the admission SEALED and no
        # partial input exists.
        documents = []
        total = 0
        for index, member in enumerate(members):
            data = self._member_bytes(manifest, index)
            # The declared lengths are the authority's own statement; the bytes
            # actually read are held to it, and to the same bounds, member by member.
            total += len(data)
            if len(data) > self._bounds.max_member_bytes:
                raise _too_large("member_bytes_exceeded")
            if total > self._bounds.max_total_bytes:
                raise _too_large("total_bytes_exceeded")
            if len(data) != member.byte_length:
                raise _integrity("member_length_mismatch")
            if hashlib.sha256(data).hexdigest() != member.content_hash:
                raise _integrity("member_content_mismatch")
            documents.append((
                f"{ADMITTED_INPUT_MEMBER_SCHEMA}:{record.admission_id}:{index}",
                {
                    "schema_version": ADMITTED_INPUT_MEMBER_SCHEMA,
                    "admission_id": record.admission_id,
                    "manifest_digest": manifest.manifest_digest,
                    "member_index": index,
                    "member_identity": member.identity,
                    "content_hash": member.content_hash,
                    "byte_length": len(data),
                    "encoding": "base64",
                    "bytes": base64.b64encode(data).decode("ascii"),
                },
            ))
        try:
            # Held, not published: capacity is taken, or the whole batch is
            # refused and nothing is held. Nothing is resumable yet.
            reservation = self._transfers.reserve_many(
                documents, owner=J02_ADMITTED_INPUT_SCOPE,
            )
        except FramedResultTransportError as error:
            # ADR-0066 section 3: refuse admission rather than retain without bound.
            raise _too_large("transfer_refused") from error
        try:
            record = store.begin_delivery(admission_id)
        except AdmittedInputExpired as error:
            reservation.release()
            raise _refusal("admission_expired") from error
        except AdmittedInputConflict as error:
            reservation.release()
            raise _refusal("delivery_not_available") from error
        except BaseException:
            reservation.release()
            raise
        # The attempt is recorded: only now are the members published. What the
        # reservation did not create (an earlier or a concurrent delivery's
        # transfers) was never ours to withdraw, and release left it alone.
        try:
            reservation.commit()
        except FramedResultTransportError as error:
            # The reservation expired (or a copy was replaced) while the
            # transition waited: nothing is published and no descriptor is
            # returned. The attempt is deliberately *not* ended here. K12 keeps
            # one state per admission and no attempt identity, so a write made
            # now, after a wait of unknown length, could land on a later attempt
            # that has since begun and succeeded. Like any lost response, the
            # pending attempt is ended by the deck's own acknowledgement of a
            # failure, after which the sealed admission is delivered again.
            reservation.release()
            raise _integrity("transfer_expired") from error
        except BaseException:
            reservation.release()
            raise
        descriptors = [
            {
                "member_index": index,
                "member_identity": member.identity,
                "content_hash": member.content_hash,
                "byte_length": member.byte_length,
                "logical_result_identity": frames[0].logical_result_identity,
                "transfer_id": frames[0].transfer_id,
                "payload_sha256": frames[0].payload_sha256,
                "chunk_count": frames[0].chunk_count,
            }
            for index, (member, frames) in enumerate(zip(members, reservation.frames))
        ]
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
    members: AdmittedInputMembers,
    read_member: AdmittedInputMemberReader,
    *,
    transfers: FramedResultTransferStore,
    bounds: AdmittedInputBounds = AdmittedInputBounds(),
) -> dict[tuple[str, str, str], J02FamilyHandler]:
    """Explicit server composition only; no host and no other family is added.

    ``store_provider`` yields a store per request (its own connection, closed on
    exit). ``resolve``, ``members`` and ``read_member`` are the server-owned
    seams described in the module docstring. ``transfers`` retains each delivered member as a J14
    transfer owned by :data:`J02_ADMITTED_INPUT_SCOPE`; the *same* store must be
    given to the connection handler (``framed_transfers=``) with
    ``framed_resume_scopes`` containing that scope, or nothing can be fetched.
    """

    transport = AdmittedInputTransport(
        store_provider, resolve, members, read_member, transfers=transfers, bounds=bounds,
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
    "AdmittedInputMember",
    "AdmittedInputRequest",
    "AdmittedInputTransport",
    "FAILURE_REASONS",
    "J02_ADMITTED_INPUT_FAMILY",
    "J02_ADMITTED_INPUT_OPERATIONS",
    "J02_ADMITTED_INPUT_REQUEST_VERSION",
    "J02_ADMITTED_INPUT_RESPONSE_VERSION",
    "J02_ADMITTED_INPUT_SCOPE",
    "admitted_input_family_handlers",
    "aligned_partition_members",
]
