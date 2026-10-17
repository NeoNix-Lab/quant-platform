"""Consumer API read of ``l2_book_events@1`` (ADR-0070 §6, A14e).

The ``trades@1`` functions of :mod:`.market_data` are not modified.  This module
resolves an L2 semantic query to the canonical L2 dataset, reads it through an
already-constructed :class:`~quant_platform.access.l2_gateway.L2DataGateway`, and
translates the result and every failure into the frozen Consumer API vocabulary.

Three choices differ from ``trades@1`` and are stated here, not hidden:

* **Coverage policy is ``allow-partial``.**  Amendment 1 puts the structural
  ``known_gap`` *between* partitions, so a read across one returns the events of
  both sides with the gap in ``coverage.gaps`` and ``complete = false``, never
  filled.  A read with no eligible coverage at all is still ``no_coverage``.
* **The result bound is in bytes.**  A row count cannot bound the size of an L2
  result (a depth-200 delta is about 1 KB, a snapshot about 9 KB).  The encoded
  size is accumulated while the scan streams and the scan is closed as soon as
  the bound is exceeded (``result_too_large``).  Framing is never used to avoid it.
* **``anchored`` is a derived property**, not a wire field: J02 v1 carries no
  field beyond the frozen envelope, and any client can read the first event's
  ``event_type``.  :func:`l2_events_anchored` is the pure helper.
"""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from typing import Any

from quant_platform.canonical import canonical_bytes

from ..access.models import CoveragePolicy, DataRequest, LifecyclePolicy
from ..data.models import (
    CoverageInterval,
    DataGatewayError,
    DatasetIdentity,
    L2BookEvent,
)
from ..source_adapters.bybit_l2 import BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY
from .market_data import (
    ApplicationRequestError,
    ConsumerApiError,
    ConsumerCoverage,
    ConsumerErrorCode,
    ConsumerMarketDataQuery,
    ConsumerMarketDataResult,
    ConsumerProvenance,
    NormalizedMarketDataQuery,
    RepresentationRef,
    UnsupportedOption,
    UnsupportedRepresentation,
    UnsupportedVenue,
    _MESSAGES,
    _REQUEST_PHASE_CODES,
    _gateway_phase_error,
    _require_mapping_shape,
)

L2_BOOK_EVENTS_KIND = "l2_book_events"
L2_BOOK_EVENTS_VERSION = 1
L2_BOOK_EVENTS_REPRESENTATION = f"{L2_BOOK_EVENTS_KIND}@{L2_BOOK_EVENTS_VERSION}"

# ~11.4 MiB of encoded events: under J02's 16 MiB wire ceiling with headroom for
# the envelope, the same reasoning ADR-0050 Amendment 1 applies to ``trades@1``.
DEFAULT_L2_MAX_RESULT_BYTES = 12_000_000

_LAYER = "canonical"
_DATASET_KIND = "l2"
_RECORD_SCHEMA_ID = "l2-book-event-v1"
_LIFECYCLE_POLICY = LifecyclePolicy.VALID_ONLY
_COVERAGE_POLICY = CoveragePolicy.ALLOW_PARTIAL

# Closed, explicit (venue, instrument) -> ordering policy mapping for the first
# L2 vertical (ADR-0070).  Anything else is refused; this is not a registry.
_L2_ORDERING_POLICY = {("bybit", "BTCUSDT"): BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY}


def resolve_l2_book_events_request(query: ConsumerMarketDataQuery) -> DataRequest:
    """Resolve a semantic L2 query into the canonical access request."""

    _require_supported_representation(query.representation)
    definition = _require_mapping_shape(query.representation.definition, "representation definition")
    options = _require_mapping_shape(query.options, "request options")
    for what, mapping in (("representation definition", definition), ("request options", options)):
        if mapping:
            raise UnsupportedOption(
                f"{L2_BOOK_EVENTS_REPRESENTATION} defines no {what}",
                context={"unsupported": sorted(str(name) for name in mapping)},
            )
    selector = DatasetIdentity(
        layer=_LAYER,
        dataset_kind=_DATASET_KIND,
        venue=query.venue,
        instrument=query.instrument,
        record_schema_id=_RECORD_SCHEMA_ID,
    )
    ordering_policy = _L2_ORDERING_POLICY.get((selector.venue, selector.instrument))
    if ordering_policy is None:
        raise UnsupportedVenue(
            f"no approved {L2_BOOK_EVENTS_REPRESENTATION} capability for the requested source",
            context={"venue": selector.venue, "instrument": selector.instrument},
        )
    return DataRequest(
        dataset_selector=selector,
        start=query.start,
        end=query.end,
        schema_requirement=_RECORD_SCHEMA_ID,
        lifecycle_policy=_LIFECYCLE_POLICY,
        coverage_policy=_COVERAGE_POLICY,
        ordering_policy=ordering_policy,
    )


def _require_supported_representation(representation: Any) -> None:
    if not isinstance(representation, RepresentationRef):
        raise UnsupportedRepresentation("representation must be a RepresentationRef")
    if (
        representation.kind != L2_BOOK_EVENTS_KIND
        or type(representation.version) is not int
        or representation.version != L2_BOOK_EVENTS_VERSION
    ):
        raise UnsupportedRepresentation(
            "unsupported representation",
            context={
                "requested": {"kind": representation.kind, "version": representation.version},
                "supported": {"kind": L2_BOOK_EVENTS_KIND, "version": L2_BOOK_EVENTS_VERSION},
            },
        )


def _event_bytes(event: L2BookEvent) -> int:
    """Encoded size of one event on the wire: compact sorted ASCII JSON of its stable
    form (the canonical serializer, ``sorted-compact-ascii-v1``) plus a separator."""

    return len(canonical_bytes(event.stable_dict(), profile="sorted-compact-ascii-v1", allow_nan=False)) + 1


def execute_l2_book_events_query(
    query: ConsumerMarketDataQuery,
    *,
    gateway: Any,
    batch_size: int = 65_536,
    max_result_bytes: int = DEFAULT_L2_MAX_RESULT_BYTES,
) -> ConsumerMarketDataResult:
    """Resolve, read and translate one ``l2_book_events@1`` query.

    ``gateway`` is an already-constructed access capability; this function never
    builds a gateway, catalog, connection or configuration.
    """

    if (
        isinstance(max_result_bytes, bool)
        or not isinstance(max_result_bytes, int)
        or max_result_bytes < 1
    ):
        raise ValueError("max_result_bytes must be a positive integer")
    try:
        data_request = resolve_l2_book_events_request(query)
    except (ApplicationRequestError, DataGatewayError) as exc:
        raise _request_error(query, exc) from exc

    normalized = NormalizedMarketDataQuery(
        venue=data_request.dataset_selector.venue,
        instrument=data_request.dataset_selector.instrument,
        start=data_request.start,
        end=data_request.end,
        representation_kind=query.representation.kind,
        representation_version=query.representation.version,
    )
    request_identity = normalized.request_identity

    try:
        scan = gateway.scan(data_request, batch_size=batch_size)
        events: list[L2BookEvent] = []
        encoded = 0
        for batch in scan:
            for event in batch:
                encoded += _event_bytes(event)
                events.append(event)
            if encoded > max_result_bytes:
                # Stop reading as soon as the bound is exceeded: close() aborts
                # without manufacturing final provenance, so a pathological
                # interval costs at most one batch beyond the bound.
                scan.close()
                raise ConsumerApiError(
                    ConsumerErrorCode.RESULT_TOO_LARGE,
                    _MESSAGES[ConsumerErrorCode.RESULT_TOO_LARGE],
                    context={
                        "row_count": str(len(events)),
                        "max_result_bytes": str(max_result_bytes),
                        "requested_interval": CoverageInterval(
                            normalized.start, normalized.end
                        ).stable_dict(),
                    },
                    request_identity=request_identity,
                )
        metadata = scan.completed_metadata
    except DataGatewayError as exc:
        raise _gateway_phase_error(normalized, request_identity, exc) from exc

    if metadata is None:  # defensive: a drained scan must carry completion metadata
        raise ConsumerApiError(
            ConsumerErrorCode.INTEGRITY_FAILURE,
            _MESSAGES[ConsumerErrorCode.INTEGRITY_FAILURE],
            request_identity=request_identity,
        )

    return ConsumerMarketDataResult(
        request=normalized,
        request_identity=request_identity,
        representation=RepresentationRef(L2_BOOK_EVENTS_KIND, L2_BOOK_EVENTS_VERSION),
        data=tuple(events),  # type: ignore[arg-type]
        requested_interval=metadata.requested_interval,
        returned_temporal_bounds=metadata.returned_record_bounds,
        # Served directly from canonical events, so representation coverage is
        # source coverage; the gaps are the structural gaps between partitions.
        coverage=ConsumerCoverage(
            covered_intervals=metadata.eligible_coverage,
            gaps=metadata.coverage_gaps,
            complete=metadata.coverage_complete,
        ),
        provenance=ConsumerProvenance(
            dataset_identity=metadata.dataset_identity,
            record_schema_id=metadata.record_schema_id,
            schema_version=metadata.schema_version,
            schema_hash=metadata.schema_hash,
            natural_partitions=metadata.natural_partitions,
            manifest_hashes=metadata.manifest_hashes,
            content_hashes=metadata.content_hashes,
        ),
        row_count=metadata.row_count,
    )


def l2_events_anchored(events: Any) -> bool:
    """True when a returned event sequence can be replayed from its first event.

    A read that starts mid-run begins with a ``delta`` and is *not* anchored
    until its first ``snapshot``; the read never fabricates a snapshot and never
    replays a book.  An empty result is trivially anchored.
    """

    for event in events:
        return event.event_type == "snapshot"
    return True


def _request_error(query: ConsumerMarketDataQuery, exc: Exception) -> ConsumerApiError:
    """Translate a resolution failure.  No consumer identity exists yet."""

    code = _REQUEST_PHASE_CODES.get(type(exc), ConsumerErrorCode.INTEGRITY_FAILURE)
    return ConsumerApiError(code, _MESSAGES[code], context=_request_context(query, code, exc))


def _request_context(
    query: ConsumerMarketDataQuery, code: ConsumerErrorCode, exc: Exception
) -> dict[str, Any]:
    """Safe context from caller-supplied values only, never from ``exc``."""

    if code is ConsumerErrorCode.UNSUPPORTED_REPRESENTATION:
        representation = query.representation
        if isinstance(representation, RepresentationRef):
            return {
                "requested_kind": str(representation.kind),
                "requested_version": str(representation.version),
                "supported": L2_BOOK_EVENTS_REPRESENTATION,
            }
        return {"supported": L2_BOOK_EVENTS_REPRESENTATION}
    if code is ConsumerErrorCode.SOURCE_NOT_FOUND:
        return {
            "venue": str(query.venue),
            "instrument": str(query.instrument),
            "representation": L2_BOOK_EVENTS_REPRESENTATION,
        }
    if code is ConsumerErrorCode.INVALID_REQUEST and isinstance(exc, UnsupportedOption):
        names: set[Any] = set()
        for value in (query.options, getattr(query.representation, "definition", None)):
            if isinstance(value, MappingABC):
                names |= set(value)
        return {"unsupported_options": sorted(str(name) for name in names)}
    return {}


__all__ = [
    "DEFAULT_L2_MAX_RESULT_BYTES",
    "L2_BOOK_EVENTS_KIND",
    "L2_BOOK_EVENTS_REPRESENTATION",
    "L2_BOOK_EVENTS_VERSION",
    "execute_l2_book_events_query",
    "l2_events_anchored",
    "resolve_l2_book_events_request",
]