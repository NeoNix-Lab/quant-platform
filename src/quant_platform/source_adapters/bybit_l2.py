"""Bybit source-owned ordering provider and certification profile for L2 events.

ADR-0070 §1, §2 and §5 (A14a).  Everything here is a pure function over an
iterable of :class:`~quant_platform.data.models.L2BookEvent`: no I/O, no
filesystem, no network, no catalog.  The Parquet layout, coverage fold and
physical-artifact checks (ADR-0070 §5 checks 7-8) belong to later atoms.

Rules, in one place:

* **Order** is the numeric ``native_sequence`` (Bybit ``seq``) of one stream.
  ``exchange_ts`` (the matching-engine ``cts``) is not part of the key; it is a
  certified non-decreasing property along that order.  ``native_update_id``
  (``u``) is not an ordering key because a snapshot resets it.
* **Continuity** is the ``u`` signal.  A *segment* starts at a ``snapshot`` and
  continues while each following event has ``u == previous u + 1``.  A
  ``delta`` that breaks the rule opens a discontinuity: the interval until the
  next in-band ``snapshot`` is ``book_state_unknown``.  Events inside it are
  preserved but flagged unanchored and excluded from replay.  No snapshot is
  ever fabricated.
* **Outcome**: ``eligible``; ``degraded`` (explicit discontinuity or an
  unanchored head, both publishable with non-complete coverage); or
  ``ineligible`` (any structural violation).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from ..data.models import (
    DataIntegrityError,
    DatasetIdentity,
    InvalidRequest,
    L2BookEvent,
    l2_book_event_identity_v1,
)
from ..ordering import L2_BOOK_EVENTS_CANONICAL_TOTAL_ORDER_V1, OrderingProvider

BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY = "bybit-l2-book-event-v1-seq-v1"
BYBIT_L2_BOOK_EVENT_V1_CERTIFICATION_PROFILE = (
    "producer-consumer-conformity-v1/bybit-l2-book-event-v1"
)
BYBIT_L2_BOOK_EVENT_V1_CHECK_SUITE = (
    "producer-consumer-conformity-v1/bybit-l2-book-event-v1-checks"
)
BYBIT_L2_SOURCE_CHANNEL_V1 = "orderbook.200.BTCUSDT"
BYBIT_L2_EFFECTIVE_DEPTH_V1 = 200

OUTCOME_ELIGIBLE = "eligible"
OUTCOME_DEGRADED = "degraded"
OUTCOME_INELIGIBLE = "ineligible"

# Stable violation codes (every one makes the partition ineligible).
V_RECORD_INVALID = "record_invalid"
V_RECORD_IDENTITY_MISMATCH = "record_identity_mismatch"
V_MISSING_SEQUENCE = "missing_sequence"
V_MISSING_UPDATE_ID = "missing_update_id"
V_SOURCE_CHANNEL_MISMATCH = "source_channel_mismatch"
V_SOURCE_DEPTH_MISMATCH = "source_depth_mismatch"
V_DUPLICATE_SEQUENCE = "duplicate_sequence"
V_SEQUENCE_REGRESSION = "sequence_regression"
V_EVENT_TIME_REGRESSION = "event_time_regression"
V_CROSSED_BOOK = "crossed_book"
V_DEPTH_EXCEEDED = "depth_exceeded"
V_DELETE_OF_ABSENT = "delete_of_absent_over_policy"

_MAX_SAMPLED_VIOLATIONS = 32


class BybitL2EligibilityError(ValueError):
    """The certification profile does not apply to the dataset identity."""


class BybitL2SequenceConflict(DataIntegrityError):
    """Two different payloads share one ``native_sequence`` (fail closed)."""


def bybit_l2_book_event_v1_applies_to(identity: DatasetIdentity) -> bool:
    return (
        identity.layer == "canonical"
        and identity.venue == "bybit"
        and identity.dataset_kind == "l2"
        and identity.instrument == "BTCUSDT"
        and identity.record_schema_id == "l2-book-event-v1"
    )


def _digits(value: str | None) -> int | None:
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        return None
    if len(value) > 1 and value[0] == "0":
        return None
    return int(value)


def bybit_l2_book_event_v1_ordering_key(event: L2BookEvent) -> tuple[int]:
    """Numeric ``native_sequence``; never lexicographic."""

    if not isinstance(event, L2BookEvent):
        raise DataIntegrityError("Bybit L2 ordering requires an L2BookEvent")
    sequence = _digits(event.native_sequence)
    if sequence is None:
        raise DataIntegrityError(
            "Bybit L2 ordering requires a canonical decimal native_sequence"
        )
    return (sequence,)


BYBIT_L2_ORDERING_PROVIDER = OrderingProvider(
    identity=BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
    satisfies_requirements=frozenset({L2_BOOK_EVENTS_CANONICAL_TOTAL_ORDER_V1}),
    key=bybit_l2_book_event_v1_ordering_key,
    applies_to=bybit_l2_book_event_v1_applies_to,
)


def resolve_duplicate_sequences(events: Iterable[L2BookEvent]) -> tuple[L2BookEvent, ...]:
    """Order events by ``native_sequence`` and collapse idempotent duplicates.

    Two observations with the same ``native_sequence`` are one event when their
    ``l2_book_event_identity_v1`` is equal (at-least-once transport, ADR-0040
    §4 rule).  A different payload for the same sequence raises
    :class:`BybitL2SequenceConflict`; last-writer-wins is forbidden.

    The whole input is materialized: this is for bounded batches.  The
    streaming certification below does not need it.
    """

    keyed = [(bybit_l2_book_event_v1_ordering_key(event), event) for event in events]
    keyed.sort(key=lambda item: item[0])
    result: list[L2BookEvent] = []
    previous_key: tuple[int] | None = None
    previous_identity: str | None = None
    for key, event in keyed:
        identity = l2_book_event_identity_v1(event)
        if key == previous_key:
            if identity != previous_identity:
                raise BybitL2SequenceConflict(
                    "conflicting L2 payloads share native_sequence "
                    f"{key[0]}; last-writer-wins is forbidden"
                )
            continue
        result.append(event)
        previous_key = key
        previous_identity = identity
    return tuple(result)


@dataclass(frozen=True, slots=True)
class L2CertificationParameters:
    """Profile parameters.  Defaults are the ADR-0070 values.

    ``source_depth_limit`` and ``effective_depth`` are separate so a later atom
    (A14g, a declared top-N projection) can certify depth-500 source events
    with an effective depth of 200 without changing the rules.
    ``max_deletes_of_absent`` is the one value ADR-0070 leaves to measurement
    (evidence item E3); the default is 0.
    """

    source_channel: str = BYBIT_L2_SOURCE_CHANNEL_V1
    source_depth_limit: int = BYBIT_L2_EFFECTIVE_DEPTH_V1
    effective_depth: int = BYBIT_L2_EFFECTIVE_DEPTH_V1
    max_deletes_of_absent: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.source_channel, str) or not self.source_channel.strip():
            raise InvalidRequest("source_channel must be a non-empty string")
        for name in ("source_depth_limit", "effective_depth"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise InvalidRequest(f"{name} must be a positive integer")
        value = self.max_deletes_of_absent
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise InvalidRequest("max_deletes_of_absent must be a non-negative integer")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "source_channel": self.source_channel,
            "source_depth_limit": self.source_depth_limit,
            "effective_depth": self.effective_depth,
            "max_deletes_of_absent": self.max_deletes_of_absent,
        }


@dataclass(frozen=True, slots=True)
class L2Violation:
    code: str
    index: int
    message: str

    def stable_dict(self) -> dict[str, Any]:
        return {"code": self.code, "index": self.index, "message": self.message}


@dataclass(frozen=True, slots=True)
class L2Segment:
    """A maximal run of events.  ``anchored`` runs start at a snapshot."""

    anchored: bool
    first_sequence: int
    last_sequence: int
    first_update_id: int
    last_update_id: int
    event_count: int

    def stable_dict(self) -> dict[str, Any]:
        return {
            "anchored": self.anchored,
            "first_sequence": str(self.first_sequence),
            "last_sequence": str(self.last_sequence),
            "first_update_id": str(self.first_update_id),
            "last_update_id": str(self.last_update_id),
            "event_count": self.event_count,
        }


@dataclass(frozen=True, slots=True)
class L2Discontinuity:
    """An interval whose book state is unknown (``known_gap`` evidence)."""

    after_sequence: int
    after_update_id: int
    after_exchange_ts: str
    expected_update_id: int
    observed_update_id: int
    observed_sequence: int
    resumes_at_sequence: int | None
    resumes_at_exchange_ts: str | None
    unanchored_event_count: int

    def stable_dict(self) -> dict[str, Any]:
        return {
            "after_sequence": str(self.after_sequence),
            "after_update_id": str(self.after_update_id),
            "after_exchange_ts": self.after_exchange_ts,
            "expected_update_id": str(self.expected_update_id),
            "observed_update_id": str(self.observed_update_id),
            "observed_sequence": str(self.observed_sequence),
            "resumes_at_sequence": None
            if self.resumes_at_sequence is None
            else str(self.resumes_at_sequence),
            "resumes_at_exchange_ts": self.resumes_at_exchange_ts,
            "unanchored_event_count": self.unanchored_event_count,
        }


@dataclass(frozen=True, slots=True)
class L2CertificationReport:
    outcome: str
    profile_id: str
    check_suite: str
    code_ref: str
    parameters: L2CertificationParameters
    event_count: int
    first_event: Mapping[str, str] | None
    last_event: Mapping[str, str] | None
    segments: tuple[L2Segment, ...]
    discontinuities: tuple[L2Discontinuity, ...]
    unanchored_head: bool
    unanchored_event_count: int
    deletes_of_absent: int
    crossed_states: int
    violation_counts: Mapping[str, int]
    violations: tuple[L2Violation, ...]

    def stable_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "profile_id": self.profile_id,
            "check_suite": self.check_suite,
            "code_ref": self.code_ref,
            "parameters": self.parameters.stable_dict(),
            "event_count": self.event_count,
            "first_event": None if self.first_event is None else dict(self.first_event),
            "last_event": None if self.last_event is None else dict(self.last_event),
            "segments": [segment.stable_dict() for segment in self.segments],
            "discontinuities": [item.stable_dict() for item in self.discontinuities],
            "unanchored_head": self.unanchored_head,
            "unanchored_event_count": self.unanchored_event_count,
            "deletes_of_absent": self.deletes_of_absent,
            "crossed_states": self.crossed_states,
            "violation_counts": {
                code: self.violation_counts[code] for code in sorted(self.violation_counts)
            },
            "violations": [violation.stable_dict() for violation in self.violations],
        }


def _event_summary(event: L2BookEvent, sequence: int, update_id: int) -> dict[str, str]:
    return {
        "native_sequence": str(sequence),
        "native_update_id": str(update_id),
        "event_type": event.event_type,
        "exchange_ts": event.exchange_ts.isoformat(),
    }


class _Run:
    """Accumulator for one maximal run of events."""

    __slots__ = ("anchored", "first_sequence", "first_update_id", "last_sequence", "last_update_id", "count")

    def __init__(self, anchored: bool, sequence: int, update_id: int) -> None:
        self.anchored = anchored
        self.first_sequence = self.last_sequence = sequence
        self.first_update_id = self.last_update_id = update_id
        self.count = 1

    def add(self, sequence: int, update_id: int) -> None:
        self.last_sequence = sequence
        self.last_update_id = update_id
        self.count += 1

    def freeze(self) -> L2Segment:
        return L2Segment(
            anchored=self.anchored,
            first_sequence=self.first_sequence,
            last_sequence=self.last_sequence,
            first_update_id=self.first_update_id,
            last_update_id=self.last_update_id,
            event_count=self.count,
        )


class _Book:
    """One replayed side pair with incremental best-price tracking."""

    __slots__ = ("bids", "asks", "best_bid", "best_ask")

    def __init__(self) -> None:
        self.bids: dict[Decimal, str] = {}
        self.asks: dict[Decimal, str] = {}
        self.best_bid: Decimal | None = None
        self.best_ask: Decimal | None = None

    def reset(self) -> None:
        self.bids.clear()
        self.asks.clear()
        self.best_bid = None
        self.best_ask = None

    def upsert(self, side: str, price: Decimal, size: str) -> None:
        if side == "bid":
            self.bids[price] = size
            if self.best_bid is None or price > self.best_bid:
                self.best_bid = price
        else:
            self.asks[price] = size
            if self.best_ask is None or price < self.best_ask:
                self.best_ask = price

    def delete(self, side: str, price: Decimal) -> bool:
        """Remove a level; return False when it was absent."""

        if side == "bid":
            if price not in self.bids:
                return False
            del self.bids[price]
            if price == self.best_bid:
                self.best_bid = max(self.bids) if self.bids else None
        else:
            if price not in self.asks:
                return False
            del self.asks[price]
            if price == self.best_ask:
                self.best_ask = min(self.asks) if self.asks else None
        return True

    def crossed(self) -> bool:
        return (
            self.best_bid is not None
            and self.best_ask is not None
            and self.best_bid >= self.best_ask
        )


def certify_bybit_l2_book_events(
    identity: DatasetIdentity,
    events: Iterable[L2BookEvent],
    *,
    parameters: L2CertificationParameters | None = None,
    code_ref: str = "unspecified",
    profile_id: str = BYBIT_L2_BOOK_EVENT_V1_CERTIFICATION_PROFILE,
    check_suite: str = BYBIT_L2_BOOK_EVENT_V1_CHECK_SUITE,
) -> L2CertificationReport:
    """Run ADR-0070 §5 checks 1-6 over events in physical order, once.

    Memory is bounded by the book depth and by the number of segments and
    sampled violations, not by the stream length.
    """

    if not bybit_l2_book_event_v1_applies_to(identity):
        raise BybitL2EligibilityError(
            "Bybit L2 book-event certification profile does not apply to the dataset identity"
        )
    params = parameters or L2CertificationParameters()

    counts: dict[str, int] = {}
    sampled: list[L2Violation] = []

    def violate(code: str, index: int, message: str) -> None:
        counts[code] = counts.get(code, 0) + 1
        if len(sampled) < _MAX_SAMPLED_VIOLATIONS:
            sampled.append(L2Violation(code, index, message))

    book = _Book()
    segments: list[L2Segment] = []
    discontinuities: list[L2Discontinuity] = []
    run: _Run | None = None
    anchored = False  # True while an applicable snapshot-rooted run is intact
    open_gap: dict[str, Any] | None = None

    prev_sequence: int | None = None
    prev_update_id: int | None = None
    prev_exchange_ns: int | None = None
    prev_exchange_iso: str | None = None
    first_event: dict[str, str] | None = None
    last_event: dict[str, str] | None = None
    event_count = 0
    unanchored_events = 0
    unanchored_head = False
    deletes_of_absent = 0
    crossed_states = 0

    def close_gap(resumes_sequence: int | None, resumes_ts: str | None) -> None:
        nonlocal open_gap
        if open_gap is None:
            return
        discontinuities.append(
            L2Discontinuity(
                after_sequence=open_gap["after_sequence"],
                after_update_id=open_gap["after_update_id"],
                after_exchange_ts=open_gap["after_exchange_ts"],
                expected_update_id=open_gap["expected_update_id"],
                observed_update_id=open_gap["observed_update_id"],
                observed_sequence=open_gap["observed_sequence"],
                resumes_at_sequence=resumes_sequence,
                resumes_at_exchange_ts=resumes_ts,
                unanchored_event_count=open_gap["unanchored"],
            )
        )
        open_gap = None

    def close_run() -> None:
        nonlocal run
        if run is not None:
            segments.append(run.freeze())
            run = None

    for index, event in enumerate(events):
        # Check 1: record validity and identity match.
        if not isinstance(event, L2BookEvent):
            violate(V_RECORD_INVALID, index, "record is not an L2BookEvent")
            continue
        if event.venue != identity.venue or event.instrument != identity.instrument:
            violate(
                V_RECORD_IDENTITY_MISMATCH,
                index,
                "record venue/instrument does not match the dataset identity",
            )
            continue
        sequence = _digits(event.native_sequence)
        update_id = _digits(event.native_update_id)
        if sequence is None:
            violate(V_MISSING_SEQUENCE, index, "native_sequence is missing or not canonical digits")
            continue
        if update_id is None:
            violate(V_MISSING_UPDATE_ID, index, "native_update_id is missing or not canonical digits")
            continue

        # Check 2: single stream and effective depth.
        if event.source_channel != params.source_channel:
            violate(
                V_SOURCE_CHANNEL_MISMATCH,
                index,
                f"source_channel {event.source_channel!r} != {params.source_channel!r}",
            )
        if event.source_depth_limit != params.source_depth_limit:
            violate(
                V_SOURCE_DEPTH_MISMATCH,
                index,
                f"source_depth_limit {event.source_depth_limit!r} != {params.source_depth_limit}",
            )

        # Check 3: strictly increasing native_sequence.  An out-of-order or
        # repeated event is not applied: replaying it would corrupt the book.
        if prev_sequence is not None and sequence <= prev_sequence:
            code = V_DUPLICATE_SEQUENCE if sequence == prev_sequence else V_SEQUENCE_REGRESSION
            violate(code, index, f"native_sequence {sequence} after {prev_sequence}")
            continue

        # Check 4: exchange_ts non-decreasing along native_sequence.
        exchange_ns = event.exchange_ts.epoch_ns
        if prev_exchange_ns is not None and exchange_ns < prev_exchange_ns:
            violate(
                V_EVENT_TIME_REGRESSION,
                index,
                f"exchange_ts {event.exchange_ts.isoformat()} before {prev_exchange_iso}",
            )

        exchange_iso = event.exchange_ts.isoformat()
        event_count += 1
        summary = _event_summary(event, sequence, update_id)
        if first_event is None:
            first_event = summary
        last_event = summary

        # Check 5: continuity segments.
        if event.event_type == "snapshot":
            close_run()
            close_gap(sequence, exchange_iso)
            run = _Run(True, sequence, update_id)
            anchored = True
            book.reset()
        else:
            if prev_sequence is None:
                # Unanchored head: no snapshot has been seen in this partition.
                unanchored_head = True
                anchored = False
                run = _Run(False, sequence, update_id)
                unanchored_events += 1
            elif anchored and update_id != (prev_update_id or 0) + 1:
                # Discontinuity: this delta opens a book_state_unknown interval.
                close_run()
                anchored = False
                run = _Run(False, sequence, update_id)
                open_gap = {
                    "after_sequence": prev_sequence,
                    "after_update_id": prev_update_id,
                    "after_exchange_ts": prev_exchange_iso,
                    "expected_update_id": (prev_update_id or 0) + 1,
                    "observed_update_id": update_id,
                    "observed_sequence": sequence,
                    "unanchored": 1,
                }
                unanchored_events += 1
            elif anchored:
                assert run is not None
                run.add(sequence, update_id)
            else:
                assert run is not None
                run.add(sequence, update_id)
                unanchored_events += 1
                if open_gap is not None:
                    open_gap["unanchored"] += 1

        if event.event_type == "snapshot" or anchored:
            # Check 6: replay sanity, only for anchored events.
            for change in event.bids + event.asks:
                price = Decimal(change.price)
                if change.action == "upsert":
                    book.upsert(change.side, price, change.size)
                elif not book.delete(change.side, price):
                    deletes_of_absent += 1
            if book.crossed():
                crossed_states += 1
                violate(
                    V_CROSSED_BOOK,
                    index,
                    f"best bid {book.best_bid} >= best ask {book.best_ask}",
                )
            if len(book.bids) > params.effective_depth or len(book.asks) > params.effective_depth:
                violate(
                    V_DEPTH_EXCEEDED,
                    index,
                    f"side depth {len(book.bids)}/{len(book.asks)} exceeds {params.effective_depth}",
                )

        prev_sequence = sequence
        prev_update_id = update_id
        prev_exchange_ns = exchange_ns
        prev_exchange_iso = exchange_iso

    close_run()
    close_gap(None, None)  # an interval still open at the end of the partition

    if deletes_of_absent > params.max_deletes_of_absent:
        violate(
            V_DELETE_OF_ABSENT,
            -1,
            f"{deletes_of_absent} deletes of absent levels exceed {params.max_deletes_of_absent}",
        )

    if counts:
        outcome = OUTCOME_INELIGIBLE
    elif discontinuities or unanchored_head:
        outcome = OUTCOME_DEGRADED
    else:
        outcome = OUTCOME_ELIGIBLE

    return L2CertificationReport(
        outcome=outcome,
        profile_id=profile_id,
        check_suite=check_suite,
        code_ref=code_ref,
        parameters=params,
        event_count=event_count,
        first_event=first_event,
        last_event=last_event,
        segments=tuple(segments),
        discontinuities=tuple(discontinuities),
        unanchored_head=unanchored_head,
        unanchored_event_count=unanchored_events,
        deletes_of_absent=deletes_of_absent,
        crossed_states=crossed_states,
        violation_counts=dict(counts),
        violations=tuple(sampled),
    )


@dataclass(frozen=True, slots=True)
class BybitL2BookEventV1CertificationProfile:
    """Source-owned profile bundling the ids, parameters and entry points."""

    code_ref: str
    profile_id: str = BYBIT_L2_BOOK_EVENT_V1_CERTIFICATION_PROFILE
    check_suite: str = BYBIT_L2_BOOK_EVENT_V1_CHECK_SUITE
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


__all__ = [
    "BYBIT_L2_BOOK_EVENT_V1_CERTIFICATION_PROFILE",
    "BYBIT_L2_BOOK_EVENT_V1_CHECK_SUITE",
    "BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY",
    "BYBIT_L2_EFFECTIVE_DEPTH_V1",
    "BYBIT_L2_ORDERING_PROVIDER",
    "BYBIT_L2_SOURCE_CHANNEL_V1",
    "BybitL2BookEventV1CertificationProfile",
    "BybitL2EligibilityError",
    "BybitL2SequenceConflict",
    "L2CertificationParameters",
    "L2CertificationReport",
    "L2Discontinuity",
    "L2Segment",
    "L2Violation",
    "OUTCOME_DEGRADED",
    "OUTCOME_ELIGIBLE",
    "OUTCOME_INELIGIBLE",
    "bybit_l2_book_event_v1_applies_to",
    "bybit_l2_book_event_v1_ordering_key",
    "certify_bybit_l2_book_events",
    "resolve_duplicate_sequences",
]
