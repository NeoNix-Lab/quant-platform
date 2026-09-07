"""Semantic market-data selector resolution for the ``trades@1`` reference slice.

This module owns the C02 proposition only: a consumer expresses *what it
means* -- venue, instrument, a UTC half-open interval and a versioned
representation -- and this seam resolves that into the canonical
:class:`~quant_platform.access.models.DataRequest`.

The caller never supplies, and never needs to know, a ``DatasetIdentity``,
layer, dataset kind, record schema, read/lifecycle/coverage/ordering policy,
catalog identity, partition identity, storage root or path.  Those are
resolved here from the requested capability.

Deliberately out of scope: executing the read, building a gateway or catalog,
assembling a result envelope, and translating these failures into the stable
Consumer API error vocabulary.  Those are C03 and later atoms.  The exceptions
below are in-process application failures, not wire error codes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from ..access.models import DataRequest, LifecyclePolicy
from ..data.models import DatasetIdentity, Instant
from ..source_adapters.bybit import BYBIT_TRADE_V1_ORDERING_POLICY


# Application-owned reference values for trades@1.  The consumer selects the
# representation; the platform selects everything below it.
TRADES_V1_KIND = "trades"
TRADES_V1_VERSION = 1
_LAYER = "canonical"
_DATASET_KIND = "trades"
_RECORD_SCHEMA_ID = "trade-v1"
_LIFECYCLE_POLICY = LifecyclePolicy.VALID_ONLY
_COVERAGE_POLICY = "strict"

# Closed, explicit venue -> canonical ordering policy mapping.  A venue absent
# here is refused.  This is deliberately not a registry, plugin point or
# generic capability resolver: generalising it is C06, and C06 stays deferred
# until real second-venue evidence exists.
_TRADES_V1_ORDERING_POLICY_BY_VENUE = {
    "bybit": BYBIT_TRADE_V1_ORDERING_POLICY,
}


class ApplicationRequestError(Exception):
    """A semantic request the application boundary refuses to resolve."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None):
        super().__init__(message)
        self.context = context or {}


class UnsupportedRepresentation(ApplicationRequestError):
    """The requested representation kind/version is not implemented."""


class UnsupportedOption(ApplicationRequestError):
    """A representation definition or request option has no defined meaning."""


class UnsupportedVenue(ApplicationRequestError):
    """No approved canonical capability exists for the requested venue."""


@dataclass(frozen=True, slots=True)
class RepresentationRef:
    """A versioned representation reference, e.g. ``trades@1``."""

    kind: str
    version: int
    definition: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConsumerMarketDataQuery:
    """A semantic market-data request.

    Every field is consumer meaning.  There is deliberately no field naming a
    dataset, schema, policy, catalog entry, partition or storage location.
    """

    venue: str
    instrument: str
    start: Instant | datetime | str
    end: Instant | datetime | str
    representation: RepresentationRef
    options: Mapping[str, Any] = field(default_factory=dict)


def resolve_market_data_request(query: ConsumerMarketDataQuery) -> DataRequest:
    """Resolve a semantic query into the canonical access request.

    Admission checks that no lower layer can express are performed here.
    Everything already enforced by ``DatasetIdentity`` and ``DataRequest`` --
    venue/instrument primitives, interval parsing, ``start > end``, the empty
    ``start == end`` interval, coverage and lifecycle policy validity -- is
    composed rather than reimplemented.
    """

    _require_supported_representation(query.representation)
    _require_no_unsupported_options(query.representation.definition, "representation definition")
    _require_no_unsupported_options(query.options, "request options")

    # DatasetIdentity normalises the venue (strip + lowercase) and validates
    # the instrument, so the ordering-policy lookup uses the normalised venue
    # and equivalent spellings resolve identically.
    dataset_selector = DatasetIdentity(
        layer=_LAYER,
        dataset_kind=_DATASET_KIND,
        venue=query.venue,
        instrument=query.instrument,
        record_schema_id=_RECORD_SCHEMA_ID,
    )
    ordering_policy = _TRADES_V1_ORDERING_POLICY_BY_VENUE.get(dataset_selector.venue)
    if ordering_policy is None:
        raise UnsupportedVenue(
            "no approved trades@1 capability for the requested venue",
            context={
                "venue": dataset_selector.venue,
                "supported_venues": sorted(_TRADES_V1_ORDERING_POLICY_BY_VENUE),
            },
        )

    return DataRequest(
        dataset_selector=dataset_selector,
        start=query.start,
        end=query.end,
        schema_requirement=_RECORD_SCHEMA_ID,
        lifecycle_policy=_LIFECYCLE_POLICY,
        coverage_policy=_COVERAGE_POLICY,
        ordering_policy=ordering_policy,
    )


def _require_supported_representation(representation: RepresentationRef) -> None:
    if not isinstance(representation, RepresentationRef):
        raise UnsupportedRepresentation("representation must be a RepresentationRef")
    if representation.kind != TRADES_V1_KIND or representation.version != TRADES_V1_VERSION:
        raise UnsupportedRepresentation(
            "unsupported representation",
            context={
                "requested": {"kind": representation.kind, "version": representation.version},
                "supported": {"kind": TRADES_V1_KIND, "version": TRADES_V1_VERSION},
            },
        )


def _require_no_unsupported_options(options: Mapping[str, Any], what: str) -> None:
    if not options:
        return
    raise UnsupportedOption(
        f"trades@1 defines no {what}",
        context={"unsupported": sorted(options)},
    )
