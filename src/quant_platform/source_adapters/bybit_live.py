"""Bybit public live trade acquisition semantics for A11 v1.

The module owns only source-facing meaning for the first live vertical:
Bybit public linear ``publicTrade.BTCUSDT`` messages, canonical
``TradeRecord`` mapping, duplicate/cutover rules, session evidence and
bounded reconnect reconciliation.  It does not own a live consumer cursor,
checkpoint persistence, scheduling, or a generic stream framework.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import re
from typing import Any

from ..data.models import DataIntegrityError, DatasetIdentity, Instant, TradeRecord
from .bybit import (
    bybit_trade_v1_applies_to,
    bybit_trade_v1_ordering_key,
    validate_bybit_trade_v1_eligibility,
)


BYBIT_LIVE_SOURCE_SEMANTICS_V1 = "bybit-public-trades-websocket-v1"
BYBIT_LIVE_MAPPING_V1 = "bybit-public-trade-live-v1"
BYBIT_RECENT_PUBLIC_TRADES_SEMANTICS_V1 = "bybit-recent-public-trades-v5-v1"
BYBIT_RECENT_PUBLIC_TRADES_MAPPING_V1 = "bybit-recent-public-trades-v5-to-trade-v1"
# Must be one of data/manifests.py's frozen coverage-manifest-v1
# `_EVIDENCE_KINDS`; that vocabulary is shared/frozen and not extended here.
# "connection_continuity" is the closest existing fit for "the session
# stayed connected/subscribed/acquiring across the asserted interval".
BYBIT_LIVE_COVERAGE_EVIDENCE_KIND = "connection_continuity"
# Distinct from bybit.py's BYBIT_TRADE_V1_CHECK_SUITE/_CERTIFICATION_PROFILE
# (#107: "do not weaken or reinterpret the historical profile" -- live
# evidence is certified under its own profile_id/check_suite so publication
# evidence records which profile actually certified a given partition).
BYBIT_LIVE_TRADE_V1_CHECK_SUITE = "producer-consumer-conformity-v1/bybit-trade-v1-live"
BYBIT_LIVE_TRADE_V1_CERTIFICATION_PROFILE = "producer-consumer-conformity-v1/bybit-trade-v1-live-v1"
SUPPORTED_CATEGORY = "linear"
SUPPORTED_SYMBOL = "BTCUSDT"
SUPPORTED_TOPIC = "publicTrade.BTCUSDT"
SUPPORTED_VENUE = "bybit"
RECORD_SCHEMA_ID = "trade-v1"

_POSITIVE_DECIMAL_RE = re.compile(r"^(?:0\.[0-9]*[1-9][0-9]*|[1-9][0-9]*(?:\.[0-9]+)?)$")
# schemas/trade-v1.json's `digit_string`: non-negative integer text, no
# leading zeros ("7" and "007" are distinct strings for the same value).
# Matches the `_DIGITS` convention used elsewhere for this same schema type
# (data/manifests.py, data/parquet.py).
_DIGIT_STRING_RE = re.compile(r"^(0|[1-9][0-9]*)$")
_FINGERPRINT_DOMAIN = b"quant-platform/bybit-live-acquisition-v1\x00"


class BybitLiveSourceError(ValueError):
    """A source validation or live acquisition semantic failure."""

    def __init__(self, message: str, *, field: str | None = None, trade_id: str | None = None):
        self.field = field
        self.trade_id = trade_id
        details = []
        if field is not None:
            details.append(f"field={field!r}")
        if trade_id is not None:
            details.append(f"trade_id={trade_id!r}")
        suffix = f"  [{', '.join(details)}]" if details else ""
        super().__init__(message + suffix)


class BybitLiveIntegrityError(DataIntegrityError):
    """Raised when same-key observations carry conflicting payload."""


class SessionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTED = "CONNECTED"
    SUBSCRIBED = "SUBSCRIBED"
    ACQUIRING = "ACQUIRING"


class ReconnectStatus(str, Enum):
    CONTINUITY_RESTORED = "CONTINUITY_RESTORED"
    UNRESOLVED_GAP = "UNRESOLVED_GAP"


@dataclass(frozen=True, slots=True)
class TradeKeyV1:
    venue: str
    instrument: str
    exchange_ts: Instant
    trade_id: str

    @property
    def order_key(self) -> tuple[Instant, str]:
        return self.exchange_ts, self.trade_id

    def stable_dict(self) -> dict[str, str]:
        return {
            "venue": self.venue,
            "instrument": self.instrument,
            "exchange_ts": self.exchange_ts.isoformat(),
            "trade_id": self.trade_id,
        }


@dataclass(frozen=True, slots=True)
class BybitLiveMessageEvidence:
    source_semantics_id: str
    mapping_id: str
    topic: str
    provider_message_ts_ms: int
    trade_count: int
    message_fingerprint_sha256: str


@dataclass(frozen=True, slots=True)
class SessionEvent:
    state: SessionState
    event: str
    detail: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class BybitLiveSessionEvidence:
    source_semantics_id: str
    mapping_id: str
    final_state: SessionState
    events: tuple[SessionEvent, ...]
    interruptions: tuple[str, ...]
    integrity_conflicts: tuple[str, ...]
    validated_messages: int
    accepted_records: int

    @property
    def can_assert_complete_interval(self) -> bool:
        states = tuple(event.state for event in self.events)
        return (
            SessionState.CONNECTED in states
            and SessionState.SUBSCRIBED in states
            and SessionState.ACQUIRING in states
            and not self.interruptions
            and not self.integrity_conflicts
        )


@dataclass(frozen=True, slots=True)
class LiveTradeBatch:
    records: tuple[TradeRecord, ...]
    message_evidence: BybitLiveMessageEvidence


@dataclass(frozen=True, slots=True)
class ReconnectResult:
    status: ReconnectStatus
    accepted_records: tuple[TradeRecord, ...]
    evidence: Mapping[str, Any]


class LiveSessionTracker:
    """Small explicit state machine for A11 session evidence."""

    def __init__(self) -> None:
        self._state = SessionState.DISCONNECTED
        self._events: list[SessionEvent] = [
            SessionEvent(self._state, "initial", {})
        ]
        self._interruptions: list[str] = []
        self._conflicts: list[str] = []
        self._validated_messages = 0
        self._accepted_records = 0

    @property
    def state(self) -> SessionState:
        return self._state

    def connected(self, *, conn_id: str | None = None) -> None:
        self._transition(SessionState.CONNECTED, "connected", {"conn_id": conn_id})

    def subscribed(self, *, topic: str = SUPPORTED_TOPIC, conn_id: str | None = None) -> None:
        if topic != SUPPORTED_TOPIC:
            raise BybitLiveSourceError("subscription acknowledgement is for an unsupported topic", field="topic")
        if self._state in {SessionState.SUBSCRIBED, SessionState.ACQUIRING}:
            # Bybit does not guarantee the subscribe ack is delivered before
            # the first topic message on the wire; observed_message() may
            # already have inferred SUBSCRIBED from a matching trade message
            # (see below). A same-topic ack arriving after that is
            # confirmation of the same subscription, not a new transition or
            # a protocol violation -- record it as evidence without moving
            # the state machine backward or raising.
            self._events.append(SessionEvent(self._state, "subscribed_ack_confirmed_late", {"topic": topic, "conn_id": conn_id}))
            return
        if self._state != SessionState.CONNECTED:
            raise BybitLiveSourceError("subscription acknowledgement requires CONNECTED state")
        self._transition(SessionState.SUBSCRIBED, "subscribed", {"topic": topic, "conn_id": conn_id})

    def heartbeat(self, *, conn_id: str | None = None) -> None:
        self._events.append(SessionEvent(self._state, "heartbeat_pong", {"conn_id": conn_id}))

    def observed_message(self, batch: LiveTradeBatch) -> None:
        if self._state == SessionState.CONNECTED:
            # A real, matching topic message is itself sufficient evidence
            # the subscription succeeded (Bybit may deliver the first topic
            # push before -- or without a client having yet processed -- the
            # subscribe ack; this is an ordinary wire-ordering race, not a
            # protocol violation). Treat it as an implicit ack rather than a
            # fatal error: a late/duplicate real ack is still accepted by
            # subscribed() above once this has already happened.
            self._transition(
                SessionState.SUBSCRIBED, "subscribed_implicitly_by_message",
                {"topic": batch.message_evidence.topic},
            )
        elif self._state not in {SessionState.SUBSCRIBED, SessionState.ACQUIRING}:
            raise BybitLiveSourceError("trade message observed before subscription was established")
        if self._state == SessionState.SUBSCRIBED:
            self._transition(SessionState.ACQUIRING, "acquiring", {"topic": batch.message_evidence.topic})
        self._validated_messages += 1
        self._accepted_records += len(batch.records)

    def disconnected(self, reason: str) -> None:
        if not isinstance(reason, str) or not reason.strip():
            raise BybitLiveSourceError("disconnect reason must be explicit")
        self._interruptions.append(reason.strip())
        self._transition(SessionState.DISCONNECTED, "disconnected", {"reason": reason.strip()})

    def integrity_conflict(self, reason: str) -> None:
        self._conflicts.append(str(reason))

    def evidence(self) -> BybitLiveSessionEvidence:
        return BybitLiveSessionEvidence(
            source_semantics_id=BYBIT_LIVE_SOURCE_SEMANTICS_V1,
            mapping_id=BYBIT_LIVE_MAPPING_V1,
            final_state=self._state,
            events=tuple(self._events),
            interruptions=tuple(self._interruptions),
            integrity_conflicts=tuple(self._conflicts),
            validated_messages=self._validated_messages,
            accepted_records=self._accepted_records,
        )

    def _transition(self, state: SessionState, event: str, detail: Mapping[str, Any]) -> None:
        self._state = state
        self._events.append(SessionEvent(state, event, dict(detail)))


def bybit_live_dataset_identity() -> DatasetIdentity:
    return DatasetIdentity("canonical", "trades", SUPPORTED_VENUE, SUPPORTED_SYMBOL, RECORD_SCHEMA_ID)


def trade_key_v1(record: TradeRecord) -> TradeKeyV1:
    if record.trade_id is None or record.trade_id == "":
        raise BybitLiveSourceError("live trade key requires non-empty trade_id", field="trade_id")
    return TradeKeyV1(record.venue, record.instrument, Instant.parse(record.exchange_ts), record.trade_id)


def canonicalize_bybit_live_message(message: Mapping[str, Any]) -> LiveTradeBatch:
    if not isinstance(message, Mapping):
        raise BybitLiveSourceError("Bybit live message must be an object")
    topic = _require_str(message, "topic")
    if topic != SUPPORTED_TOPIC:
        raise BybitLiveSourceError(f"unsupported live topic {topic!r}", field="topic")
    if message.get("type") != "snapshot":
        raise BybitLiveSourceError("Bybit public trade message type must be snapshot", field="type")
    provider_ts = _require_int(message, "ts")
    rows = message.get("data")
    if not isinstance(rows, list):
        raise BybitLiveSourceError("Bybit public trade message data must be an array", field="data")
    records = tuple(canonicalize_bybit_live_trade(item) for item in rows)
    return LiveTradeBatch(
        records=records,
        message_evidence=BybitLiveMessageEvidence(
            source_semantics_id=BYBIT_LIVE_SOURCE_SEMANTICS_V1,
            mapping_id=BYBIT_LIVE_MAPPING_V1,
            topic=topic,
            provider_message_ts_ms=provider_ts,
            trade_count=len(records),
            message_fingerprint_sha256=_stable_fingerprint(message),
        ),
    )


def canonicalize_bybit_live_trade(row: Mapping[str, Any]) -> TradeRecord:
    if not isinstance(row, Mapping):
        raise BybitLiveSourceError("Bybit live trade row must be an object")
    trade_id = _require_str(row, "i")
    symbol = _require_str(row, "s", trade_id=trade_id)
    if symbol != SUPPORTED_SYMBOL:
        raise BybitLiveSourceError(f"unsupported symbol {symbol!r}", field="s", trade_id=trade_id)
    trade_time_ms = _require_int(row, "T", trade_id=trade_id)
    side = _side(row.get("S"), field="S", trade_id=trade_id)
    price = _positive_decimal(row.get("p"), field="p", trade_id=trade_id)
    size = _positive_decimal(row.get("v"), field="v", trade_id=trade_id)
    sequence = _sequence_int(row.get("seq"), field="seq", trade_id=trade_id)
    return TradeRecord(
        venue=SUPPORTED_VENUE,
        instrument=symbol,
        exchange_ts=Instant(trade_time_ms * 1_000_000),
        receive_ts=None,
        price=price,
        size=size,
        aggressor_side=side,
        trade_id=trade_id,
        sequence=sequence,
    )


def canonicalize_bybit_recent_public_trade(row: Mapping[str, Any]) -> TradeRecord:
    if not isinstance(row, Mapping):
        raise BybitLiveSourceError("Bybit recent trade row must be an object")
    trade_id = _require_str(row, "execId")
    symbol = _require_str(row, "symbol", trade_id=trade_id)
    if symbol != SUPPORTED_SYMBOL:
        raise BybitLiveSourceError(f"unsupported symbol {symbol!r}", field="symbol", trade_id=trade_id)
    time_ms = _int_text(row.get("time"), field="time", trade_id=trade_id)
    side = _side(row.get("side"), field="side", trade_id=trade_id)
    price = _positive_decimal(row.get("price"), field="price", trade_id=trade_id)
    size = _positive_decimal(row.get("size"), field="size", trade_id=trade_id)
    sequence = _sequence_text(row.get("seq"), field="seq", trade_id=trade_id)
    return TradeRecord(
        venue=SUPPORTED_VENUE,
        instrument=symbol,
        exchange_ts=Instant(time_ms * 1_000_000),
        receive_ts=None,
        price=price,
        size=size,
        aggressor_side=side,
        trade_id=trade_id,
        sequence=sequence,
    )


def deduplicate_live_records(records: Iterable[TradeRecord]) -> tuple[TradeRecord, ...]:
    return _deduplicate(records).values_tuple


def converge_historical_live_records(
    historical_records: Iterable[TradeRecord],
    live_records: Iterable[TradeRecord],
    *,
    cutover_key: TradeKeyV1,
) -> tuple[TradeRecord, ...]:
    cutover = cutover_key.order_key
    accepted: dict[tuple[Instant, str], TradeRecord] = {}
    for record in sorted(historical_records, key=bybit_trade_v1_ordering_key):
        key = bybit_trade_v1_ordering_key(record)
        if key <= cutover:
            _insert_equivalent_or_fail(accepted, key, record, context="historical")
    for record in sorted(live_records, key=bybit_trade_v1_ordering_key):
        key = bybit_trade_v1_ordering_key(record)
        if key <= cutover:
            if key in accepted:
                # `accepted[key]` here is always the historical record inserted
                # above (historical is processed first, live records at/below
                # cutover only ever hit an existing accepted entry). Bybit's
                # historical archive never carries execution sequence
                # (bybit_historical.py always emits sequence=None), while a
                # live record for the same TradeKeyV1 always does -- so
                # `sequence` is expected to differ in this specific
                # comparison and must not be treated as a conflict. Every
                # other canonical/economic field is still compared.
                _require_equivalent(
                    accepted[key], record,
                    context="historical/live overlap",
                    ignore_sequence=True,
                )
            continue
        _insert_equivalent_or_fail(accepted, key, record, context="live")
    return tuple(record for _, record in sorted(accepted.items(), key=lambda item: item[0]))


def reconcile_after_disconnect(
    *,
    last_durable_key: TradeKeyV1,
    recent_rest_records: Iterable[TradeRecord],
    buffered_ws_records: Iterable[TradeRecord],
) -> ReconnectResult:
    rest = tuple(sorted(recent_rest_records, key=bybit_trade_v1_ordering_key))
    buffered = tuple(sorted(buffered_ws_records, key=bybit_trade_v1_ordering_key))
    durable = last_durable_key.order_key
    if not any(bybit_trade_v1_ordering_key(record) == durable for record in rest):
        return ReconnectResult(
            status=ReconnectStatus.UNRESOLVED_GAP,
            accepted_records=(),
            evidence={
                "reason": "last durable TradeKeyV1 absent from bounded recent-public-trades window",
                "last_durable_key": last_durable_key.stable_dict(),
                "recent_window_records": len(rest),
                "buffered_ws_records": len(buffered),
                "coverage_status": "non_complete",
                "evidence_kind": "transport_interruption",
            },
        )
    if buffered:
        # The anchor check above only proves REST's own window is
        # continuous from `last_durable_key`. When a WS buffer is also
        # being merged in, REST's tail and the buffer's head must be
        # proven to meet -- otherwise a real gap between "REST stopped
        # covering" and "WS buffering resumed" would be silently accepted
        # as CONTINUITY_RESTORED. The only evidence available from opaque
        # provider trade IDs is a real TradeKeyV1 present in both windows;
        # absent that, the boundary is unproven and must fail closed.
        rest_keys = {bybit_trade_v1_ordering_key(record) for record in rest}
        buffered_keys = {bybit_trade_v1_ordering_key(record) for record in buffered}
        if rest_keys.isdisjoint(buffered_keys):
            return ReconnectResult(
                status=ReconnectStatus.UNRESOLVED_GAP,
                accepted_records=(),
                evidence={
                    "reason": (
                        "no TradeKeyV1 overlap between the bounded recent-public-trades "
                        "window and the buffered WebSocket records; the REST-to-WS "
                        "boundary is unproven"
                    ),
                    "last_durable_key": last_durable_key.stable_dict(),
                    "recent_window_records": len(rest),
                    "buffered_ws_records": len(buffered),
                    "coverage_status": "non_complete",
                    "evidence_kind": "transport_interruption",
                },
            )
    later = [
        record for record in (*rest, *buffered)
        if bybit_trade_v1_ordering_key(record) > durable
    ]
    accepted = deduplicate_live_records(later)
    return ReconnectResult(
        status=ReconnectStatus.CONTINUITY_RESTORED,
        accepted_records=accepted,
        evidence={
            "reason": "last durable TradeKeyV1 found in bounded recent-public-trades window",
            "last_durable_key": last_durable_key.stable_dict(),
            "accepted_records": len(accepted),
            "coverage_status": "continuity_restored",
        },
    )


def build_bybit_live_coverage_document(
    *,
    dataset_identity: DatasetIdentity,
    coverage_id: str,
    intent_start: str,
    intent_end: str,
    assertion_id: str,
    assertion_start: str,
    assertion_end: str,
    partition_key: str,
    revision: int,
    session_evidence: BybitLiveSessionEvidence,
    created_at: str,
    producer: str,
    code_ref: str,
    supersedes: str | None = None,
) -> dict[str, Any]:
    if not bybit_trade_v1_applies_to(dataset_identity):
        raise BybitLiveSourceError("Bybit live coverage requires canonical Bybit trade-v1 identity")
    if session_evidence.integrity_conflicts:
        # ADR-0040: Bybit `seq` is not a gap-free +1 cursor and multiple
        # messages may legitimately share one seq, so an unresolved
        # integrity conflict (a real conflicting-payload duplicate,
        # already fail-closed at acquisition time -- see
        # BybitLiveIntegrityError) cannot be honestly expressed through
        # data/manifests.py's frozen evidence-kind vocabulary:
        # "sequence_discontinuity" would falsely imply a gap-detecting
        # sequence cursor Bybit's `seq` does not provide. Refuse to build
        # a coverage document at all rather than misdescribe it.
        raise BybitLiveSourceError(
            "cannot build live coverage while unresolved integrity conflicts remain: "
            + "; ".join(session_evidence.integrity_conflicts)
        )
    status = "complete" if session_evidence.can_assert_complete_interval else "known_gap"
    # data/manifests.py's frozen evidence-item shape is exactly {kind, detail}
    # (no additional fields); session detail is folded into `detail` text.
    evidence: list[dict[str, Any]] = [
        {
            "kind": BYBIT_LIVE_COVERAGE_EVIDENCE_KIND,
            "detail": (
                f"final_state={session_evidence.final_state.value}; "
                f"states={[event.state.value for event in session_evidence.events]}; "
                f"validated_messages={session_evidence.validated_messages}; "
                f"accepted_records={session_evidence.accepted_records}"
            ),
        }
    ]
    if session_evidence.interruptions:
        evidence.append({
            "kind": "transport_interruption",
            "detail": "; ".join(session_evidence.interruptions),
        })
    return {
        "source_dataset_identity": dataset_identity,
        "coverage_id": coverage_id,
        "supersedes": supersedes,
        "created_at": created_at,
        "acquisition": {
            # One of data/manifests.py's frozen `_COVERAGE_BASES`; a bounded
            # connected session to the source, as opposed to e.g. an
            # open-ended "live_stream" segment.
            "basis": "source_session",
            "intent_start": intent_start,
            "intent_end": intent_end,
            "source_semantics": BYBIT_LIVE_SOURCE_SEMANTICS_V1,
            "mapping": BYBIT_LIVE_MAPPING_V1,
        },
        "assertions": [
            {
                "assertion_id": assertion_id,
                "start": assertion_start,
                "end": assertion_end,
                "status": status,
                "partitions": [{"partition_key": partition_key, "revision": revision}],
                "evidence": evidence,
            }
        ],
        "producer": producer,
        "code_ref": code_ref,
    }


@dataclass(frozen=True, slots=True)
class BybitLiveTradeV1CertificationProfile:
    """Live-session counterpart to bybit.py's BybitTradeV1CertificationProfile.

    #107's "Important existing compatibility fact" is explicit: the
    historical profile must not be weakened or reinterpreted, and live
    evidence gets its own minimal source-owned profile instead. Canonical
    identity/order (AC4) is shared with the historical profile via
    bybit_trade_v1_applies_to/bybit_trade_v1_ordering_key -- both profiles
    certify the same `trade-v1`/`TradeKeyV1` domain -- but the source-
    specific evidence rules are the opposite of the historical profile's:
    live coverage carries its own source_semantics/mapping, and `sequence`
    is required (ADR-0040 requires it be preserved as canonical evidence
    for the live path) rather than forbidden.
    """

    code_ref: str
    profile_id: str = BYBIT_LIVE_TRADE_V1_CERTIFICATION_PROFILE
    check_suite: str = BYBIT_LIVE_TRADE_V1_CHECK_SUITE

    def applies_to(self, identity: DatasetIdentity) -> bool:
        return bybit_trade_v1_applies_to(identity)

    def ordering_key(self, record: TradeRecord) -> tuple[Instant, str]:
        return bybit_trade_v1_ordering_key(record)

    def validate_source(
        self,
        identity: DatasetIdentity,
        coverage_documents: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        if not self.applies_to(identity):
            raise BybitLiveSourceError("Bybit live certification profile does not apply")
        if not coverage_documents:
            raise BybitLiveSourceError("certification requires durable live session coverage evidence")
        for document in coverage_documents:
            acquisition = document.get("acquisition") or {}
            if acquisition.get("source_semantics") != BYBIT_LIVE_SOURCE_SEMANTICS_V1:
                raise BybitLiveSourceError("coverage source_semantics is not the frozen Bybit live-session profile")
            if acquisition.get("mapping") != BYBIT_LIVE_MAPPING_V1:
                raise BybitLiveSourceError("coverage mapping is not the frozen Bybit live-session mapping")
            for assertion in document.get("assertions") or ():
                if assertion.get("status") != "complete":
                    continue
                if not any(
                    item.get("kind") == BYBIT_LIVE_COVERAGE_EVIDENCE_KIND
                    for item in assertion.get("evidence") or ()
                ):
                    raise BybitLiveSourceError("complete Bybit live coverage lacks session evidence")
        return {
            "source_semantics": BYBIT_LIVE_SOURCE_SEMANTICS_V1,
            "mapping": BYBIT_LIVE_MAPPING_V1,
            "documents": len(coverage_documents),
        }

    def validate_records(
        self,
        identity: DatasetIdentity,
        records: Sequence[TradeRecord],
    ) -> Mapping[str, Any]:
        validate_bybit_trade_v1_eligibility(identity, tuple(records))
        if any(record.receive_ts is not None for record in records):
            raise BybitLiveSourceError("Bybit live records must not fabricate receive_ts")
        if any(record.sequence is None for record in records):
            raise BybitLiveSourceError("Bybit live records must carry provider sequence evidence")
        return {"records": len(records), "trade_id_policy": "non-null-unique-(exchange_ts,trade_id)"}


@dataclass(frozen=True, slots=True)
class _Deduplicated:
    by_key: dict[tuple[Instant, str], TradeRecord]

    @property
    def values_tuple(self) -> tuple[TradeRecord, ...]:
        return tuple(record for _, record in sorted(self.by_key.items(), key=lambda item: item[0]))


def _deduplicate(records: Iterable[TradeRecord]) -> _Deduplicated:
    accepted: dict[tuple[Instant, str], TradeRecord] = {}
    for record in sorted(records, key=bybit_trade_v1_ordering_key):
        _insert_equivalent_or_fail(accepted, bybit_trade_v1_ordering_key(record), record, context="live")
    return _Deduplicated(accepted)


def _insert_equivalent_or_fail(
    accepted: dict[tuple[Instant, str], TradeRecord],
    key: tuple[Instant, str],
    record: TradeRecord,
    *,
    context: str,
) -> None:
    previous = accepted.get(key)
    if previous is None:
        accepted[key] = record
        return
    _require_equivalent(previous, record, context=context)


def _require_equivalent(
    left: TradeRecord, right: TradeRecord, *, context: str, ignore_sequence: bool = False,
) -> None:
    if _payload(left, ignore_sequence=ignore_sequence) != _payload(right, ignore_sequence=ignore_sequence):
        raise BybitLiveIntegrityError(f"conflicting duplicate TradeKeyV1 in {context}")


def _payload(record: TradeRecord, *, ignore_sequence: bool = False) -> tuple[Any, ...]:
    return (
        record.venue, record.instrument, Instant.parse(record.exchange_ts).epoch_ns,
        record.price, record.size, record.aggressor_side, record.trade_id,
        None if ignore_sequence else record.sequence, record.receive_ts,
    )


def _require_str(row: Mapping[str, Any], field: str, *, trade_id: str | None = None) -> str:
    value = row.get(field)
    if not isinstance(value, str) or value == "":
        raise BybitLiveSourceError(f"{field} must be a non-empty string", field=field, trade_id=trade_id)
    return value


def _require_int(row: Mapping[str, Any], field: str, *, trade_id: str | None = None) -> int:
    value = row.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise BybitLiveSourceError(f"{field} must be an integer", field=field, trade_id=trade_id)
    return value


def _int_text(value: Any, *, field: str, trade_id: str | None = None) -> int:
    if not isinstance(value, str) or not value.isdigit():
        raise BybitLiveSourceError(f"{field} must be an integer text value", field=field, trade_id=trade_id)
    return int(value)


def _sequence_int(value: Any, *, field: str, trade_id: str | None = None) -> str:
    if not isinstance(value, int) or isinstance(value, bool):
        raise BybitLiveSourceError(f"{field} must be an integer", field=field, trade_id=trade_id)
    if value < 0:
        raise BybitLiveSourceError(f"{field} must be non-negative", field=field, trade_id=trade_id)
    return str(value)


def _sequence_text(value: Any, *, field: str, trade_id: str | None = None) -> str:
    # `str.isdigit()` alone accepts zero-padded text ("007") and non-ASCII
    # digit characters; schemas/trade-v1.json's `digit_string` rejects both.
    if not isinstance(value, str) or _DIGIT_STRING_RE.fullmatch(value) is None:
        raise BybitLiveSourceError(f"{field} must be non-negative integer text", field=field, trade_id=trade_id)
    return value


def _side(value: Any, *, field: str, trade_id: str | None = None) -> str:
    try:
        return {"Buy": "buy", "Sell": "sell"}[value]
    except KeyError:
        raise BybitLiveSourceError(f"{field} must be Buy or Sell", field=field, trade_id=trade_id) from None


def _positive_decimal(value: Any, *, field: str, trade_id: str | None = None) -> str:
    if not isinstance(value, str) or _POSITIVE_DECIMAL_RE.fullmatch(value) is None:
        raise BybitLiveSourceError(f"{field} must be a canonical positive decimal string", field=field, trade_id=trade_id)
    try:
        decimal = Decimal(value)
    except InvalidOperation as exc:
        raise BybitLiveSourceError(f"{field} must be decimal", field=field, trade_id=trade_id) from exc
    if not decimal.is_finite() or decimal <= 0:
        raise BybitLiveSourceError(f"{field} must be > 0", field=field, trade_id=trade_id)
    return value


def _stable_fingerprint(document: Mapping[str, Any]) -> str:
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    digest = hashlib.sha256()
    digest.update(_FINGERPRINT_DOMAIN)
    digest.update(len(payload).to_bytes(8, "big"))
    digest.update(payload)
    return digest.hexdigest()


__all__ = [
    "BYBIT_LIVE_COVERAGE_EVIDENCE_KIND",
    "BYBIT_LIVE_MAPPING_V1",
    "BYBIT_LIVE_SOURCE_SEMANTICS_V1",
    "BYBIT_LIVE_TRADE_V1_CERTIFICATION_PROFILE",
    "BYBIT_LIVE_TRADE_V1_CHECK_SUITE",
    "BYBIT_RECENT_PUBLIC_TRADES_MAPPING_V1",
    "BYBIT_RECENT_PUBLIC_TRADES_SEMANTICS_V1",
    "BybitLiveIntegrityError",
    "BybitLiveMessageEvidence",
    "BybitLiveSessionEvidence",
    "BybitLiveSourceError",
    "BybitLiveTradeV1CertificationProfile",
    "LiveSessionTracker",
    "LiveTradeBatch",
    "RECORD_SCHEMA_ID",
    "ReconnectResult",
    "ReconnectStatus",
    "SUPPORTED_CATEGORY",
    "SUPPORTED_SYMBOL",
    "SUPPORTED_TOPIC",
    "SUPPORTED_VENUE",
    "SessionEvent",
    "SessionState",
    "TradeKeyV1",
    "build_bybit_live_coverage_document",
    "bybit_live_dataset_identity",
    "canonicalize_bybit_live_message",
    "canonicalize_bybit_live_trade",
    "canonicalize_bybit_recent_public_trade",
    "converge_historical_live_records",
    "deduplicate_live_records",
    "reconcile_after_disconnect",
    "trade_key_v1",
]
