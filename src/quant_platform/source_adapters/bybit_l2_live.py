"""Bybit public live L2 order book acquisition semantics (A14f v2).

ADR-0070 §8 (Live supervision, v2) and Amendment 1 §9 (Live day roll);
ADR-0040 (Bybit live conventions), ADR-0041 (live supervisor process boundary),
ADR-0042 (checkpoint recovery), ADR-0043 (durable checkpoint state).

This module owns source-facing semantics for live L2 WebSocket ingest:
1. Normalization of public linear ``orderbook.200.BTCUSDT`` streaming messages
   into canonical :class:`~quant_platform.data.models.L2BookEvent` instances.
2. Mandatory matching-engine timestamp ``cts`` enforcement
   (refusing messages with ``missing_engine_time``).
3. Fail-closed in-band resync state machine:
   - Disconnect, heartbeat timeout, or update discontinuity ($u_{t+1} \\ne u_t + 1$)
     opens a ``known_gap`` with ``sequence_discontinuity`` / ``connection_loss``.
   - Unanchored deltas during disconnection/resync are dropped.
   - Resync requires an authentic in-band venue ``snapshot``; never synthesizes
     snapshots (ADR-0044 NO_AUTHORITATIVE_REPAIR_PATH_PROVEN).
4. Historical/live cutover convergence and idempotent duplicate resolution
   (ADR-0040 §5).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
import hashlib
from typing import Any

from ..canonical import canonical_bytes
from ..data.models import (
    DataIntegrityError,
    DatasetIdentity,
    Instant,
    L2BookEvent,
    l2_book_event_identity_v1,
)
from .bybit_l2 import (
    BybitL2SequenceConflict,
    L2CertificationParameters,
    L2CertificationReport,
    bybit_l2_book_event_v1_applies_to,
    bybit_l2_book_event_v1_ordering_key,
    bybit_l2_continuity_evidence_detail,
    certify_bybit_l2_book_events,
)
from .l2 import (
    BYBIT_L2_SOURCE_SEMANTICS_V2,
    L2SourceError,
    normalize_bybit_l2_message,
)

BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1 = "bybit-orderbook-l2-live-v1"
BYBIT_L2_LIVE_MAPPING_V1 = "bybit-orderbook-l2-live-to-canonical-v1"
SUPPORTED_CHANNEL = "orderbook.200.BTCUSDT"
SUPPORTED_VENUE = "bybit"
SUPPORTED_SYMBOL = "BTCUSDT"
SUPPORTED_DEPTH = 200
BYBIT_PUBLIC_LINEAR_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_L2_LIVE_COVERAGE_EVIDENCE_KIND = "connection_continuity"
BYBIT_L2_LIVE_DISCONTINUITY_EVIDENCE_KIND = "sequence_discontinuity"


class BybitL2LiveSourceError(L2SourceError):
    """A source validation or live L2 acquisition semantic failure."""


class BybitL2LiveIntegrityError(DataIntegrityError):
    """A data integrity violation during live L2 acquisition or convergence."""


def canonicalize_bybit_l2_live_message(
    message: Mapping[str, Any],
    *,
    acquisition_mode: str = "live_websocket",
    market_type: str = "linear_perp",
    instrument: str | None = None,
) -> L2BookEvent:
    """Normalize a Bybit live L2 WebSocket message.

    Enforces:
    - Valid topic (``orderbook.200.BTCUSDT`` by default).
    - Matching-engine timestamp ``cts`` is required; messages missing ``cts``
      are refused with reason ``missing_engine_time``.
    - Normalization via :func:`normalize_bybit_l2_message` with ``acquisition_mode="live_websocket"``.
    """
    if not isinstance(message, Mapping):
        raise BybitL2LiveSourceError("Bybit live L2 message must be a mapping", field="message")

    cts = message.get("cts")
    if cts is None:
        raise BybitL2LiveSourceError(
            "Bybit live L2 message missing engine time (cts)",
            field="cts",
            reason="missing_engine_time",
        )

    topic = message.get("topic")
    if topic is not None and not str(topic).startswith("orderbook."):
        raise BybitL2LiveSourceError(
            f"Unsupported topic {topic!r} for live L2 order book",
            field="topic",
            reason="unsupported_topic",
        )

    try:
        return normalize_bybit_l2_message(
            message,
            acquisition_mode=acquisition_mode,
            market_type=market_type,
            instrument=instrument,
        )
    except L2SourceError as exc:
        raise BybitL2LiveSourceError(str(exc), field=exc.field, reason=exc.reason) from exc


class L2ResyncState(str, Enum):
    AWAITING_INITIAL_SNAPSHOT = "awaiting_initial_snapshot"
    STREAMING = "streaming"
    AWAITING_RESYNC_SNAPSHOT = "awaiting_resync_snapshot"
    DISCONNECTED = "disconnected"


class L2GapReason(str, Enum):
    SEQUENCE_DISCONTINUITY = "sequence_discontinuity"
    CONNECTION_LOSS = "connection_loss"
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"
    OUT_OF_ORDER_SEQUENCE = "out_of_order_sequence"
    UNANCHORED_HEAD = "unanchored_head"


@dataclass(frozen=True, slots=True)
class L2LiveGapEvent:
    """Explicit gap opened by disconnect, timeout, or sequence break."""

    reason: str
    gap_start: Instant
    gap_end: Instant | None = None
    last_good_sequence: int | None = None
    last_good_update_id: int | None = None
    resync_sequence: int | None = None
    resync_update_id: int | None = None
    dropped_deltas_count: int = 0

    def close(
        self,
        gap_end: Instant,
        resync_sequence: int,
        resync_update_id: int,
    ) -> L2LiveGapEvent:
        return L2LiveGapEvent(
            reason=self.reason,
            gap_start=self.gap_start,
            gap_end=gap_end,
            last_good_sequence=self.last_good_sequence,
            last_good_update_id=self.last_good_update_id,
            resync_sequence=resync_sequence,
            resync_update_id=resync_update_id,
            dropped_deltas_count=self.dropped_deltas_count,
        )

    def with_dropped_delta(self) -> L2LiveGapEvent:
        return L2LiveGapEvent(
            reason=self.reason,
            gap_start=self.gap_start,
            gap_end=self.gap_end,
            last_good_sequence=self.last_good_sequence,
            last_good_update_id=self.last_good_update_id,
            resync_sequence=self.resync_sequence,
            resync_update_id=self.resync_update_id,
            dropped_deltas_count=self.dropped_deltas_count + 1,
        )


class BybitL2LiveResyncStateMachine:
    """In-band resync state machine for Bybit L2 live streaming.

    Rules:
    - Never fabricates synthetic snapshots across disconnects or discontinuities.
    - Requires an authentic venue snapshot to initialize or resume a stream.
    - An update discontinuity ($u_{t+1} \\ne u_t + 1$) transitions to
      ``AWAITING_RESYNC_SNAPSHOT`` and signals that a re-subscription is required.
    - Deltas arriving while in an unanchored state are dropped and counted in the gap.
    """

    def __init__(
        self,
        initial_state: L2ResyncState = L2ResyncState.AWAITING_INITIAL_SNAPSHOT,
        anchor_sequence: int | None = None,
        anchor_update_id: int | None = None,
        anchor_exchange_ts: Instant | None = None,
    ) -> None:
        self._state = initial_state
        self._last_sequence = anchor_sequence
        self._last_update_id = anchor_update_id
        self._last_exchange_ts = anchor_exchange_ts
        self._active_gap: L2LiveGapEvent | None = None
        self._closed_gaps: list[L2LiveGapEvent] = []

    @property
    def state(self) -> L2ResyncState:
        return self._state

    @property
    def last_sequence(self) -> int | None:
        return self._last_sequence

    @property
    def last_update_id(self) -> int | None:
        return self._last_update_id

    @property
    def last_exchange_ts(self) -> Instant | None:
        return self._last_exchange_ts

    @property
    def active_gap(self) -> L2LiveGapEvent | None:
        return self._active_gap

    @property
    def closed_gaps(self) -> tuple[L2LiveGapEvent, ...]:
        return tuple(self._closed_gaps)

    def on_connected(self) -> None:
        """Called when transport connection is established."""
        if self._state == L2ResyncState.DISCONNECTED:
            if self._last_update_id is not None:
                self._state = L2ResyncState.AWAITING_RESYNC_SNAPSHOT
            else:
                self._state = L2ResyncState.AWAITING_INITIAL_SNAPSHOT

    def on_disconnected(
        self,
        reason: str = L2GapReason.CONNECTION_LOSS.value,
        at: Instant | None = None,
    ) -> L2LiveGapEvent | None:
        """Called on connection loss."""
        start_ts = at or self._last_exchange_ts or Instant(0)
        if self._active_gap is None and self._state == L2ResyncState.STREAMING:
            self._active_gap = L2LiveGapEvent(
                reason=reason,
                gap_start=start_ts,
                last_good_sequence=self._last_sequence,
                last_good_update_id=self._last_update_id,
            )
        self._state = L2ResyncState.DISCONNECTED
        return self._active_gap

    def on_heartbeat_timeout(
        self,
        at: Instant | None = None,
    ) -> L2LiveGapEvent:
        """Called when heartbeat timeout is detected."""
        start_ts = at or self._last_exchange_ts or Instant(0)
        if self._active_gap is None:
            self._active_gap = L2LiveGapEvent(
                reason=L2GapReason.HEARTBEAT_TIMEOUT.value,
                gap_start=start_ts,
                last_good_sequence=self._last_sequence,
                last_good_update_id=self._last_update_id,
            )
        self._state = L2ResyncState.AWAITING_RESYNC_SNAPSHOT
        return self._active_gap

    def process_message(
        self,
        message: Mapping[str, Any] | L2BookEvent,
    ) -> tuple[L2BookEvent | None, L2LiveGapEvent | None, bool]:
        """Process an incoming message or L2BookEvent through the resync machine.

        Returns:
            (accepted_event, closed_gap, resubscribe_required)
            - accepted_event: The L2BookEvent if accepted and anchored, else None.
            - closed_gap: L2LiveGapEvent if a gap was cleanly closed by an authentic
              snapshot, else None.
            - resubscribe_required: True if an unrecoverable discontinuity requires
              triggering re-subscription.
        """
        event = (
            message
            if isinstance(message, L2BookEvent)
            else canonicalize_bybit_l2_live_message(message)
        )

        seq = int(event.native_sequence) if event.native_sequence else None
        u = int(event.native_update_id) if event.native_update_id else None

        if seq is None or u is None:
            raise BybitL2LiveIntegrityError(
                "L2 live event missing numeric native_sequence or native_update_id"
            )

        # State 1: Awaiting initial snapshot (session startup)
        if self._state == L2ResyncState.AWAITING_INITIAL_SNAPSHOT:
            if event.event_type == "delta":
                # Dropped unanchored delta before first snapshot
                return None, None, False

            # Authentic snapshot arrives! Anchor initial stream
            self._last_sequence = seq
            self._last_update_id = u
            self._last_exchange_ts = event.exchange_ts
            self._state = L2ResyncState.STREAMING
            return event, None, False

        # State 2: Awaiting resync snapshot (recovering from gap/disconnect)
        if self._state in (L2ResyncState.AWAITING_RESYNC_SNAPSHOT, L2ResyncState.DISCONNECTED):
            if event.event_type == "delta":
                # Delta dropped while book state is unknown
                if self._active_gap is not None:
                    self._active_gap = self._active_gap.with_dropped_delta()
                return None, None, False

            # In-band venue snapshot arrives! Closes the gap
            closed_gap = None
            if self._active_gap is not None:
                closed_gap = self._active_gap.close(
                    gap_end=event.exchange_ts,
                    resync_sequence=seq,
                    resync_update_id=u,
                )
                self._closed_gaps.append(closed_gap)
                self._active_gap = None

            self._last_sequence = seq
            self._last_update_id = u
            self._last_exchange_ts = event.exchange_ts
            self._state = L2ResyncState.STREAMING
            return event, closed_gap, False

        # State 3: Streaming (steady state)
        if self._state == L2ResyncState.STREAMING:
            if event.event_type == "snapshot":
                # In-band re-snapshot from venue cleanly starts a new segment
                self._last_sequence = seq
                self._last_update_id = u
                self._last_exchange_ts = event.exchange_ts
                return event, None, False

            # Event is delta: verify sequence order and update id continuity
            prev_seq = self._last_sequence
            prev_u = self._last_update_id

            # Idempotent duplicate check
            if prev_seq is not None and seq <= prev_seq:
                if seq == prev_seq and prev_u == u:
                    # Idempotent duplicate: ignore cleanly
                    return None, None, False

                # Sequence regression or conflict: open gap and request resync
                gap = L2LiveGapEvent(
                    reason=L2GapReason.OUT_OF_ORDER_SEQUENCE.value,
                    gap_start=self._last_exchange_ts or event.exchange_ts,
                    last_good_sequence=prev_seq,
                    last_good_update_id=prev_u,
                    dropped_deltas_count=1,
                )
                self._active_gap = gap
                self._state = L2ResyncState.AWAITING_RESYNC_SNAPSHOT
                return None, None, True

            # Continuity check: u_{t+1} must equal u_t + 1
            if prev_u is not None and u != prev_u + 1:
                # Sequence discontinuity!
                gap = L2LiveGapEvent(
                    reason=L2GapReason.SEQUENCE_DISCONTINUITY.value,
                    gap_start=self._last_exchange_ts or event.exchange_ts,
                    last_good_sequence=prev_seq,
                    last_good_update_id=prev_u,
                    dropped_deltas_count=1,
                )
                self._active_gap = gap
                self._state = L2ResyncState.AWAITING_RESYNC_SNAPSHOT
                return None, None, True

            # Clean delta advance
            self._last_sequence = seq
            self._last_update_id = u
            self._last_exchange_ts = event.exchange_ts
            return event, None, False

        return None, None, False


def converge_historical_live_l2(
    historical_events: Iterable[L2BookEvent],
    live_events: Iterable[L2BookEvent],
    *,
    cut_sequence: int | None = None,
) -> tuple[L2BookEvent, ...]:
    """Converge historical archive and live stream L2 events across cutover.

    Rules (ADR-0070 §8 / ADR-0040 §5):
    - ``seq <= cut_sequence`` is historical authority.
    - ``seq > cut_sequence`` is live stream authority.
    - On overlap without explicit cutover: idempotent duplicates are resolved;
      differing payloads for the same ``native_sequence`` fail closed with
      :class:`~quant_platform.source_adapters.bybit_l2.BybitL2SequenceConflict`.
    """
    hist_list = list(historical_events)
    live_list = list(live_events)

    if cut_sequence is not None:
        hist_filtered = [
            e for e in hist_list
            if e.native_sequence and int(e.native_sequence) <= cut_sequence
        ]
        live_filtered = [
            e for e in live_list
            if e.native_sequence and int(e.native_sequence) > cut_sequence
        ]
    else:
        hist_filtered = hist_list
        live_filtered = live_list

    # Map by sequence to verify idempotent duplicates
    events_by_seq: dict[int, L2BookEvent] = {}
    identities_by_seq: dict[int, str] = {}

    for event in hist_filtered:
        seq = int(event.native_sequence)  # type: ignore[arg-type]
        identity = l2_book_event_identity_v1(event)
        events_by_seq[seq] = event
        identities_by_seq[seq] = identity

    for event in live_filtered:
        seq = int(event.native_sequence)  # type: ignore[arg-type]
        identity = l2_book_event_identity_v1(event)
        if seq in identities_by_seq:
            if identities_by_seq[seq] != identity:
                raise BybitL2SequenceConflict(
                    f"Conflicting L2 payload for native_sequence {seq} during historical/live convergence"
                )
            # Idempotent match: keep existing
            continue
        events_by_seq[seq] = event
        identities_by_seq[seq] = identity

    sorted_events = sorted(
        events_by_seq.values(),
        key=bybit_l2_book_event_v1_ordering_key,
    )
    return tuple(sorted_events)


def build_l2_live_coverage_document(
    *,
    dataset_identity: DatasetIdentity,
    partition_key: str,
    start: Instant,
    end: Instant,
    status: str = "complete",
    gaps: Sequence[Mapping[str, Any]] = (),
    events_count: int = 0,
    run_id: str = "run-001",
) -> dict[str, Any]:
    """Emit a coverage manifest document for a live L2 partition.

    Follows ADR-0070 §4-§5 and ADR-0022 coverage manifest format.
    """
    dataset_id = (
        f"{dataset_identity.layer}/{dataset_identity.dataset_kind}/"
        f"{dataset_identity.venue}/{dataset_identity.instrument}/"
        f"{dataset_identity.record_schema_id}"
    )
    coverage_id = (
        f"{dataset_id}#{partition_key}#live#{run_id}"
    )
    document: dict[str, Any] = {
        "schema_version": "coverage-manifest-v1",
        "coverage_id": coverage_id,
        "dataset_identity": dataset_identity.stable_dict(),
        "partition_key": partition_key,
        "interval": {
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        "coverage_status": status,
        "evidence": {
            "kind": BYBIT_L2_LIVE_COVERAGE_EVIDENCE_KIND,
            "basis": "live_stream",
            "source_semantics": BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
            "events_count": events_count,
        },
        "assertions": [],
    }

    if gaps:
        for idx, gap in enumerate(gaps, 1):
            document["assertions"].append({
                "assertion_id": f"gap-{idx:03d}",
                "interval": {
                    "start": gap["start"],
                    "end": gap["end"],
                },
                "status": "known_gap",
                "evidence": {
                    "kind": gap.get("reason", BYBIT_L2_LIVE_DISCONTINUITY_EVIDENCE_KIND),
                    "detail": gap.get("detail", "live stream sequence discontinuity or disconnect"),
                },
            })

    return document


BYBIT_L2_LIVE_BOOK_EVENT_V1_CERTIFICATION_PROFILE = "bybit-l2-live-certification-profile-v1"
BYBIT_L2_LIVE_BOOK_EVENT_V1_CHECK_SUITE = "bybit-l2-live-book-event-v1"


@dataclass(frozen=True, slots=True)
class BybitL2LiveBookEventV1CertificationProfile:
    """Source-owned profile for live Bybit L2 certification (A14f)."""

    code_ref: str
    profile_id: str = BYBIT_L2_LIVE_BOOK_EVENT_V1_CERTIFICATION_PROFILE
    check_suite: str = BYBIT_L2_LIVE_BOOK_EVENT_V1_CHECK_SUITE
    parameters: L2CertificationParameters = field(default_factory=L2CertificationParameters)

    def applies_to(self, identity: DatasetIdentity) -> bool:
        return bybit_l2_book_event_v1_applies_to(identity)

    def ordering_key(self, event: L2BookEvent) -> tuple[int]:
        return bybit_l2_book_event_v1_ordering_key(event)

    def certify(
        self, identity: DatasetIdentity, events: Iterable[L2BookEvent]
    ) -> L2CertificationReport:
        return certify_bybit_l2_book_events(
            identity,
            events,
            parameters=self.parameters,
            code_ref=self.code_ref,
            profile_id=self.profile_id,
            check_suite=self.check_suite,
        )

    def validate_events(
        self, identity: DatasetIdentity, events: Iterable[L2BookEvent]
    ) -> L2CertificationReport:
        return self.certify(identity, events)

    def validate_source(
        self,
        identity: DatasetIdentity,
        coverage_documents: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        if not self.applies_to(identity):
            raise BybitL2LiveSourceError("Bybit live L2 certification profile does not apply")
        if not coverage_documents:
            raise BybitL2LiveSourceError("certification requires durable live coverage evidence")
        for document in coverage_documents:
            acquisition = document.get("acquisition") or {}
            if acquisition.get("basis") != "live_stream":
                raise BybitL2LiveSourceError("coverage basis is not live_stream")
            if acquisition.get("source_semantics") != BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1:
                raise BybitL2LiveSourceError("coverage source_semantics is not the live profile")
            if acquisition.get("mapping") != BYBIT_L2_LIVE_MAPPING_V1:
                raise BybitL2LiveSourceError("coverage mapping is not the live mapping")
            for assertion in document.get("assertions") or ():
                if assertion.get("status") != "complete":
                    continue
                kinds = {item.get("kind") for item in assertion.get("evidence") or ()}
                for required in (BYBIT_L2_LIVE_COVERAGE_EVIDENCE_KIND, "sequence_continuity"):
                    if required not in kinds:
                        raise BybitL2LiveSourceError(
                            f"complete Bybit L2 live coverage lacks {required} evidence"
                        )
        return {
            "source_semantics": BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1,
            "mapping": BYBIT_L2_LIVE_MAPPING_V1,
            "documents": len(coverage_documents),
        }

    def validate_source_evidence(
        self,
        coverage_documents: Sequence[Mapping[str, Any]],
        report: L2CertificationReport,
    ) -> Mapping[str, Any]:
        expected = bybit_l2_continuity_evidence_detail(report)
        checked = 0
        for document in coverage_documents:
            for assertion in document.get("assertions") or ():
                if assertion.get("status") != "complete":
                    continue
                details = [
                    item.get("detail")
                    for item in assertion.get("evidence") or ()
                    if item.get("kind") == "sequence_continuity"
                ]
                if not details or any(detail != expected for detail in details):
                    raise BybitL2LiveSourceError(
                        "sequence_continuity evidence does not describe the stored events "
                        f"(assertion {assertion.get('assertion_id')!r})"
                    )
                checked += 1
        return {"sequence_continuity_detail": expected, "bound_assertions": checked}


__all__ = [
    "BYBIT_L2_LIVE_BOOK_EVENT_V1_CERTIFICATION_PROFILE",
    "BYBIT_L2_LIVE_BOOK_EVENT_V1_CHECK_SUITE",
    "BYBIT_L2_LIVE_COVERAGE_EVIDENCE_KIND",
    "BYBIT_L2_LIVE_DISCONTINUITY_EVIDENCE_KIND",
    "BYBIT_L2_LIVE_MAPPING_V1",
    "BYBIT_L2_LIVE_SOURCE_SEMANTICS_V1",
    "BYBIT_PUBLIC_LINEAR_WS_URL",
    "SUPPORTED_CHANNEL",
    "SUPPORTED_DEPTH",
    "SUPPORTED_SYMBOL",
    "SUPPORTED_VENUE",
    "BybitL2LiveBookEventV1CertificationProfile",
    "BybitL2LiveIntegrityError",
    "BybitL2LiveResyncStateMachine",
    "BybitL2LiveSourceError",
    "L2GapReason",
    "L2LiveGapEvent",
    "L2ResyncState",
    "build_l2_live_coverage_document",
    "canonicalize_bybit_l2_live_message",
    "converge_historical_live_l2",
]
