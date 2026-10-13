"""J14 finite-result framing with transfer-local retention and verification."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
import threading
import time
from typing import Any

from quant_platform.canonical import canonical_bytes


J14_MESSAGE_FAMILY = "j14-framed-result-v1"
J14_SCHEMA_VERSION = "j14-framed-result-v1"
J14_FRAME_MESSAGE_TYPE = "frame"
J14_RESUME_MESSAGE_TYPE = "resume"
DEFAULT_J14_FRAME_PAYLOAD_BYTES = 64 * 1024
DEFAULT_J14_RETENTION_SECONDS = 300
DEFAULT_J14_MAX_RETAINED_PAYLOAD_BYTES = 8 * 1024 * 1024
DEFAULT_J14_MAX_IN_FLIGHT_TRANSFERS = 32


class FramedResultTransportError(ValueError):
    """A typed J14 transport outcome, never a Consumer API outcome."""

    def __init__(self, outcome: str, *, transfer_id: str | None = None):
        self.outcome = outcome
        self.transfer_id = transfer_id
        super().__init__(outcome)


@dataclass(frozen=True, slots=True)
class FramedResultConfig:
    frame_payload_bytes: int = DEFAULT_J14_FRAME_PAYLOAD_BYTES
    retention_seconds: int = DEFAULT_J14_RETENTION_SECONDS
    max_retained_payload_bytes: int = DEFAULT_J14_MAX_RETAINED_PAYLOAD_BYTES
    max_in_flight_transfers: int = DEFAULT_J14_MAX_IN_FLIGHT_TRANSFERS

    def __post_init__(self) -> None:
        for name in (
            "frame_payload_bytes",
            "retention_seconds",
            "max_retained_payload_bytes",
            "max_in_flight_transfers",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class FramedResultFrame:
    transfer_id: str
    logical_result_identity: str
    payload_sha256: str
    chunk_index: int
    chunk_count: int
    chunk_sha256: str
    chunk_payload: bytes

    def __post_init__(self) -> None:
        for name in ("transfer_id", "payload_sha256", "chunk_sha256"):
            if not _is_digest(getattr(self, name)):
                raise ValueError(f"{name} must be lowercase SHA-256 hexadecimal")
        if not isinstance(self.logical_result_identity, str) or not self.logical_result_identity:
            raise ValueError("logical_result_identity must be a non-empty string")
        if not isinstance(self.chunk_index, int) or isinstance(self.chunk_index, bool) or self.chunk_index < 0:
            raise ValueError("chunk_index must be a non-negative integer")
        if not isinstance(self.chunk_count, int) or isinstance(self.chunk_count, bool) or self.chunk_count < 1:
            raise ValueError("chunk_count must be a positive integer")
        if self.chunk_index >= self.chunk_count:
            raise ValueError("chunk_index must be smaller than chunk_count")
        if not isinstance(self.chunk_payload, bytes) or not self.chunk_payload:
            raise ValueError("chunk_payload must be non-empty bytes")

    def message(self) -> dict[str, Any]:
        return {
            "message_family": J14_MESSAGE_FAMILY,
            "schema_version": J14_SCHEMA_VERSION,
            "message_type": J14_FRAME_MESSAGE_TYPE,
            "transfer_id": self.transfer_id,
            "logical_result_identity": self.logical_result_identity,
            "payload_sha256": self.payload_sha256,
            "chunk_index": self.chunk_index,
            "chunk_count": self.chunk_count,
            "chunk_sha256": self.chunk_sha256,
            "chunk_base64": base64.b64encode(self.chunk_payload).decode("ascii"),
        }

    @classmethod
    def from_message(cls, value: Mapping[str, Any]) -> "FramedResultFrame":
        try:
            if value.get("message_family") != J14_MESSAGE_FAMILY or value.get("schema_version") != J14_SCHEMA_VERSION:
                raise ValueError("unsupported J14 message family or schema version")
            if value.get("message_type") != J14_FRAME_MESSAGE_TYPE:
                raise ValueError("message is not a J14 frame")
            chunk_payload = base64.b64decode(value["chunk_base64"], validate=True)
            return cls(
                transfer_id=value["transfer_id"],
                logical_result_identity=value["logical_result_identity"],
                payload_sha256=value["payload_sha256"],
                chunk_index=value["chunk_index"],
                chunk_count=value["chunk_count"],
                chunk_sha256=value["chunk_sha256"],
                chunk_payload=chunk_payload,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise FramedResultTransportError("integrity_failure") from error


@dataclass(frozen=True, slots=True)
class FramedResultResumeRequest:
    transfer_id: str
    next_verified_chunk_index: int

    def __post_init__(self) -> None:
        if not _is_digest(self.transfer_id):
            raise ValueError("transfer_id must be lowercase SHA-256 hexadecimal")
        if (
            not isinstance(self.next_verified_chunk_index, int)
            or isinstance(self.next_verified_chunk_index, bool)
            or self.next_verified_chunk_index < 0
        ):
            raise ValueError("next_verified_chunk_index must be a non-negative integer")

    def message(self) -> dict[str, Any]:
        return {
            "message_family": J14_MESSAGE_FAMILY,
            "schema_version": J14_SCHEMA_VERSION,
            "message_type": J14_RESUME_MESSAGE_TYPE,
            "transfer_id": self.transfer_id,
            "next_verified_chunk_index": self.next_verified_chunk_index,
        }

    @classmethod
    def from_message(cls, value: Mapping[str, Any]) -> "FramedResultResumeRequest":
        try:
            if value.get("message_family") != J14_MESSAGE_FAMILY or value.get("schema_version") != J14_SCHEMA_VERSION:
                raise ValueError("unsupported J14 message family or schema version")
            if value.get("message_type") != J14_RESUME_MESSAGE_TYPE:
                raise ValueError("message is not a J14 resume request")
            return cls(
                transfer_id=value["transfer_id"],
                next_verified_chunk_index=value["next_verified_chunk_index"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise FramedResultTransportError("integrity_failure") from error


def frame_complete_result(
    *, logical_result_identity: str, result: Any, config: FramedResultConfig = FramedResultConfig()
) -> tuple[FramedResultFrame, ...]:
    """RFC 8785 serialize one complete result into deterministic J14 frames."""
    if not isinstance(logical_result_identity, str) or not logical_result_identity:
        raise ValueError("logical_result_identity must be a non-empty string")
    payload = canonical_bytes(result, profile="rfc8785-v1")
    payload_sha256 = _digest(payload)
    transfer_id = _transfer_id(logical_result_identity, payload_sha256)
    chunks = tuple(
        payload[offset : offset + config.frame_payload_bytes]
        for offset in range(0, len(payload), config.frame_payload_bytes)
    )
    return tuple(
        FramedResultFrame(
            transfer_id=transfer_id,
            logical_result_identity=logical_result_identity,
            payload_sha256=payload_sha256,
            chunk_index=index,
            chunk_count=len(chunks),
            chunk_sha256=_digest(chunk),
            chunk_payload=chunk,
        )
        for index, chunk in enumerate(chunks)
    )


class FramedResultTransferStore:
    """Bounded server-local retention for completed immutable J14 payloads."""

    def __init__(
        self,
        config: FramedResultConfig = FramedResultConfig(),
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._config = config
        self._clock = clock
        # transfer_id -> (expiry, frames, payload bytes, owner). ``owner`` is the
        # authorization class that may resume it (a J02 scope), or None. The
        # store is reached from worker threads, so every access holds the lock.
        self._transfers: dict[str, tuple[float, tuple[FramedResultFrame, ...], int, str | None]] = {}
        self._lock = threading.RLock()

    @property
    def config(self) -> FramedResultConfig:
        return self._config

    def admit(
        self, *, logical_result_identity: str, result: Any, owner: str | None = None,
    ) -> tuple[FramedResultFrame, ...]:
        return self.admit_many(((logical_result_identity, result),), owner=owner)[0]

    def admit_many(
        self, items: Sequence[tuple[str, Any]], *, owner: str | None = None,
    ) -> tuple[tuple[FramedResultFrame, ...], ...]:
        """Admit a batch of complete results as one unit: all of them, or none.

        A refusal (:class:`FramedResultTransportError` ``transfer_refused``)
        leaves the store exactly as it was, so a caller that needs several
        transfers together never leaves a partial, resumable set behind.
        Already-retained identical transfers are reused, not duplicated.
        """

        if owner is not None and (not isinstance(owner, str) or not owner):
            raise ValueError("owner must be a non-empty string when supplied")
        with self._lock:
            self._expire()
            reused: dict[str, tuple[FramedResultFrame, ...]] = {}
            created: dict[str, tuple[FramedResultFrame, ...]] = {}
            retained_bytes = self._retained_payload_bytes
            ordered: list[str] = []
            for logical_result_identity, result in items:
                frames = frame_complete_result(
                    logical_result_identity=logical_result_identity, result=result,
                    config=self._config,
                )
                transfer_id = frames[0].transfer_id
                ordered.append(transfer_id)
                retained = self._transfers.get(transfer_id)
                if retained is not None:
                    if retained[3] != owner:
                        # Same bytes under the same logical identity but another
                        # authorization class: never widen or reassign a transfer.
                        raise FramedResultTransportError("transfer_refused", transfer_id=transfer_id)
                    reused[transfer_id] = retained[1]
                    continue
                if transfer_id in created:
                    continue
                payload_bytes = sum(len(frame.chunk_payload) for frame in frames)
                if (
                    payload_bytes > self._config.max_retained_payload_bytes
                    or len(self._transfers) + len(created) >= self._config.max_in_flight_transfers
                    or retained_bytes + payload_bytes > self._config.max_retained_payload_bytes
                ):
                    raise FramedResultTransportError("transfer_refused", transfer_id=transfer_id)
                created[transfer_id] = frames
                retained_bytes += payload_bytes
            expiry = self._clock() + self._config.retention_seconds
            for transfer_id, frames in created.items():
                self._transfers[transfer_id] = (
                    expiry, frames, sum(len(frame.chunk_payload) for frame in frames), owner,
                )
            return tuple(
                created[transfer_id] if transfer_id in created else reused[transfer_id]
                for transfer_id in ordered
            )

    def retained_owner(self, transfer_id: str) -> tuple[str | None] | None:
        """``(owner,)`` of a retained transfer; ``None`` if unknown or expired."""

        with self._lock:
            self._expire()
            retained = self._transfers.get(transfer_id)
            return None if retained is None else (retained[3],)

    def resume(
        self, request: FramedResultResumeRequest, *, owner: str | None = None,
    ) -> tuple[FramedResultFrame, ...]:
        with self._lock:
            self._expire()
            retained = self._transfers.get(request.transfer_id)
            # An owned transfer is not resumable by another class, and says only
            # what an expired one says: it must not confirm that it exists.
            if retained is None or retained[3] != owner:
                raise FramedResultTransportError("transfer_expired", transfer_id=request.transfer_id)
            frames = retained[1]
            if request.next_verified_chunk_index > len(frames):
                raise FramedResultTransportError("integrity_failure", transfer_id=request.transfer_id)
            return frames[request.next_verified_chunk_index :]

    @property
    def _retained_payload_bytes(self) -> int:
        return sum(value[2] for value in self._transfers.values())

    def _expire(self) -> None:
        now = self._clock()
        for transfer_id, retained in tuple(self._transfers.items()):
            if retained[0] <= now:
                del self._transfers[transfer_id]


class FramedResultReceiver:
    """Reassembles one immutable transfer without exposing partial results."""

    def __init__(self) -> None:
        self._frames: dict[int, FramedResultFrame] = {}
        self._transfer_id: str | None = None
        self._logical_result_identity: str | None = None
        self._payload_sha256: str | None = None
        self._chunk_count: int | None = None

    def receive(self, frame: FramedResultFrame | Mapping[str, Any]) -> Any | None:
        received = FramedResultFrame.from_message(frame) if isinstance(frame, Mapping) else frame
        if not isinstance(received, FramedResultFrame):
            raise TypeError("frame must be a FramedResultFrame or mapping")
        self._validate_frame(received)
        previous = self._frames.get(received.chunk_index)
        if previous is not None and previous != received:
            raise FramedResultTransportError("integrity_failure", transfer_id=received.transfer_id)
        self._frames[received.chunk_index] = received
        if len(self._frames) != received.chunk_count:
            return None
        payload = b"".join(self._frames[index].chunk_payload for index in range(received.chunk_count))
        if _digest(payload) != received.payload_sha256:
            raise FramedResultTransportError("integrity_failure", transfer_id=received.transfer_id)
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FramedResultTransportError("integrity_failure", transfer_id=received.transfer_id) from error

    def _validate_frame(self, frame: FramedResultFrame) -> None:
        if _digest(frame.chunk_payload) != frame.chunk_sha256:
            raise FramedResultTransportError("integrity_failure", transfer_id=frame.transfer_id)
        if frame.transfer_id != _transfer_id(frame.logical_result_identity, frame.payload_sha256):
            raise FramedResultTransportError("integrity_failure", transfer_id=frame.transfer_id)
        metadata = (frame.transfer_id, frame.logical_result_identity, frame.payload_sha256, frame.chunk_count)
        existing = (self._transfer_id, self._logical_result_identity, self._payload_sha256, self._chunk_count)
        if self._transfer_id is None:
            self._transfer_id, self._logical_result_identity, self._payload_sha256, self._chunk_count = metadata
        elif metadata != existing:
            raise FramedResultTransportError("integrity_failure", transfer_id=frame.transfer_id)


def _transfer_id(logical_result_identity: str, payload_sha256: str) -> str:
    return _digest(
        canonical_bytes(
            {
                "logical_result_identity": logical_result_identity,
                "message_family": J14_MESSAGE_FAMILY,
                "payload_sha256": payload_sha256,
                "schema_version": J14_SCHEMA_VERSION,
            },
            profile="rfc8785-v1",
        )
    )


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_digest(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


__all__ = [
    "DEFAULT_J14_FRAME_PAYLOAD_BYTES",
    "DEFAULT_J14_MAX_IN_FLIGHT_TRANSFERS",
    "DEFAULT_J14_MAX_RETAINED_PAYLOAD_BYTES",
    "DEFAULT_J14_RETENTION_SECONDS",
    "FramedResultConfig",
    "FramedResultFrame",
    "FramedResultReceiver",
    "FramedResultResumeRequest",
    "FramedResultTransferStore",
    "FramedResultTransportError",
    "J14_MESSAGE_FAMILY",
    "J14_SCHEMA_VERSION",
    "frame_complete_result",
]
