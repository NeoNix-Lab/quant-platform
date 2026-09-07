"""Semantic market-data selector resolution for the ``trades@1`` reference slice.

This module owns the C02 proposition only: a consumer expresses *what it
means* -- venue, instrument, a UTC half-open interval and a versioned
representation -- and this seam resolves that into the canonical
:class:`~quant_platform.access.models.DataRequest`.

The caller never supplies, and never needs to know, a ``DatasetIdentity``,
layer, dataset kind, record schema, read/lifecycle/coverage/ordering policy,
catalog identity, partition identity, storage root or path.  Those are
resolved here from the requested capability.

C03 adds execution over an *injected* access capability and translation of its
outcome into either a stable :class:`ConsumerMarketDataResult` or one of the six
frozen Consumer API error codes.

Deliberately out of scope: building a gateway, catalog, connection or any
configuration (C05); transport, wire format and serialization (J02); job
identity, scheduling and cancellation (J03); pagination, cursors, streaming and
caching; multi-venue or multi-representation resolution (C06).  A lazy batch
consumer surface is deferred, not foreclosed: this module consumes the existing
finite batch seam directly, so PRODUCER_CONSUMER_CONFORMITY.md §16 CA1 remains
structurally satisfiable one layer up.
"""

from __future__ import annotations

from collections.abc import Mapping as MappingABC
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import hashlib
import json
from typing import Any, Mapping

from ..access.models import DataRequest, LifecyclePolicy
from ..data.models import (
    CoverageInterval,
    DataGatewayError,
    DatasetIdentity,
    DatasetNotFound,
    Instant,
    InvalidRequest,
    NaturalPartitionIdentity,
    NoCoverage,
    RecordTimeBounds,
    SchemaMismatch,
    TradeRecord,
)
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

    # Both shapes are validated before either is interpreted, so a semantic
    # refusal on one field can never leave the other unchecked for anything
    # downstream -- including the error-context builder -- to iterate.
    _require_supported_representation(query.representation)
    definition = _require_mapping_shape(query.representation.definition, "representation definition")
    options = _require_mapping_shape(query.options, "request options")
    _require_no_unsupported_options(definition, "representation definition")
    _require_no_unsupported_options(options, "request options")

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
    # The version is an exact integer, not merely something equal to one:
    # ``True == 1`` and ``1.0 == 1``, so equality alone would admit a malformed
    # semantic version as trades@1.
    if (
        representation.kind != TRADES_V1_KIND
        or type(representation.version) is not int
        or representation.version != TRADES_V1_VERSION
    ):
        raise UnsupportedRepresentation(
            "unsupported representation",
            context={
                "requested": {"kind": representation.kind, "version": representation.version},
                "supported": {"kind": TRADES_V1_KIND, "version": TRADES_V1_VERSION},
            },
        )


def _require_mapping_shape(value: Mapping[str, Any] | None, what: str) -> Mapping[str, Any]:
    """Admit only an absent or mapping-shaped value, and never coerce one.

    A list, tuple, string or number is refused rather than converted, so its
    elements can never later be reported as option names.
    """

    if value is None:
        return {}
    if not isinstance(value, MappingABC):
        raise InvalidRequest(f"{what} must be a mapping")
    return value


def _require_no_unsupported_options(options: Mapping[str, Any], what: str) -> None:
    """Refuse a non-empty mapping.  The shape is already validated."""

    if not options:
        return
    raise UnsupportedOption(
        f"trades@1 defines no {what}",
        context={"unsupported": sorted(str(name) for name in options)},
    )


# ---------------------------------------------------------------------------
# C03 -- result and error translation
# ---------------------------------------------------------------------------

# Domain tag: the consumer request identity is a different identity domain from
# the access-layer DataRequest.request_identity and must never collide with it.
CONSUMER_QUERY_IDENTITY_DOMAIN = "consumer-market-data-query-v1"


class ConsumerErrorCode(str, Enum):
    """The frozen Consumer API error vocabulary.  This set is closed."""

    INVALID_REQUEST = "invalid_request"
    UNSUPPORTED_REPRESENTATION = "unsupported_representation"
    SOURCE_NOT_FOUND = "source_not_found"
    NO_COVERAGE = "no_coverage"
    SCHEMA_INCOMPATIBLE = "schema_incompatible"
    INTEGRITY_FAILURE = "integrity_failure"


class ConsumerApiError(Exception):
    """A stable consumer failure.

    The message and context are owned by this module.  No lower-layer message,
    argument, context, path, locator or stack trace is ever forwarded; the
    originating exception is retained through ``__cause__`` for diagnosis only.
    """

    def __init__(
        self,
        code: ConsumerErrorCode,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        request_identity: str | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.context = context or {}
        self.request_identity = request_identity


@dataclass(frozen=True, slots=True)
class NormalizedMarketDataQuery:
    """The accepted semantic request, after normalisation.

    Carries consumer meaning only.  Its fingerprint is the consumer request
    identity and excludes dataset, schema, policy, catalog, storage and runtime
    identity by construction.
    """

    venue: str
    instrument: str
    start: Instant
    end: Instant
    representation_kind: str
    representation_version: int

    def stable_dict(self) -> dict[str, Any]:
        return {
            "identity_domain": CONSUMER_QUERY_IDENTITY_DOMAIN,
            "venue": self.venue,
            "instrument": self.instrument,
            "interval": {"start": self.start.isoformat(), "end": self.end.isoformat()},
            "representation": {
                "kind": self.representation_kind,
                "version": self.representation_version,
            },
        }

    @property
    def request_identity(self) -> str:
        encoded = json.dumps(
            self.stable_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode()
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ConsumerCoverage:
    """Representation-level coverage over the requested interval."""

    covered_intervals: tuple[CoverageInterval, ...]
    gaps: tuple[CoverageInterval, ...]
    complete: bool


@dataclass(frozen=True, slots=True)
class ConsumerProvenance:
    """Allowlisted semantic and reproducibility evidence.

    Every field here is semantic or content-addressed.  Catalog dataset and
    partition ids, storage root ids, relative paths and absolute paths are
    deliberately absent and must never be added.
    """

    dataset_identity: DatasetIdentity
    record_schema_id: str
    schema_version: int
    schema_hash: str
    natural_partitions: tuple[NaturalPartitionIdentity, ...]
    manifest_hashes: tuple[str, ...]
    content_hashes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConsumerMarketDataResult:
    """The stable consumer result envelope for a finite historical read."""

    request: NormalizedMarketDataQuery
    request_identity: str
    representation: RepresentationRef
    data: tuple[TradeRecord, ...]
    requested_interval: CoverageInterval
    returned_temporal_bounds: RecordTimeBounds | None
    coverage: ConsumerCoverage
    provenance: ConsumerProvenance
    row_count: int


# Exact-type translation tables.  Lookup is by exact type with a fail-closed
# default, so an unrecognised or newly introduced failure becomes an integrity
# failure rather than being silently classified by inheritance.
_REQUEST_PHASE_CODES = {
    UnsupportedRepresentation: ConsumerErrorCode.UNSUPPORTED_REPRESENTATION,
    UnsupportedOption: ConsumerErrorCode.INVALID_REQUEST,
    UnsupportedVenue: ConsumerErrorCode.SOURCE_NOT_FOUND,
    InvalidRequest: ConsumerErrorCode.INVALID_REQUEST,
}

# After C02 has already resolved canonical trades@1, an unsupported schema,
# unsupported dataset kind or missing ordering provider is an internal
# composition contradiction, not a consumer schema request.  Everything absent
# from this table therefore fails closed to integrity_failure.
_GATEWAY_PHASE_CODES = {
    DatasetNotFound: ConsumerErrorCode.SOURCE_NOT_FOUND,
    NoCoverage: ConsumerErrorCode.NO_COVERAGE,
    SchemaMismatch: ConsumerErrorCode.SCHEMA_INCOMPATIBLE,
}

_MESSAGES = {
    ConsumerErrorCode.INVALID_REQUEST: "the request is not valid",
    ConsumerErrorCode.UNSUPPORTED_REPRESENTATION: "the requested representation is not available",
    ConsumerErrorCode.SOURCE_NOT_FOUND: "no approved source can satisfy the request",
    ConsumerErrorCode.NO_COVERAGE: "the requested interval is not fully covered",
    ConsumerErrorCode.SCHEMA_INCOMPATIBLE: "the available schema cannot satisfy the request",
    ConsumerErrorCode.INTEGRITY_FAILURE: "the platform cannot produce a trustworthy result",
}


def execute_market_data_query(
    query: ConsumerMarketDataQuery,
    *,
    gateway: Any,
) -> ConsumerMarketDataResult:
    """Resolve, read and translate one semantic market-data query.

    ``gateway`` is an already-constructed access capability.  This function
    never builds a gateway, catalog, connection or configuration.
    """

    try:
        data_request = resolve_market_data_request(query)
    except (ApplicationRequestError, DataGatewayError) as exc:
        raise _request_phase_error(query, exc) from exc

    # The resolved request supplies normalisation that C02 already established;
    # it is never itself the consumer identity.
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
        scan = gateway.scan(data_request)
        records: list[TradeRecord] = []
        # Consume the finite batch seam directly rather than requiring a
        # pre-materialised DataSlice, so the bounded read property is preserved
        # below this layer and a future batch surface stays available.
        for batch in scan:
            records.extend(batch)
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
        representation=RepresentationRef(TRADES_V1_KIND, TRADES_V1_VERSION),
        data=tuple(records),
        requested_interval=metadata.requested_interval,
        returned_temporal_bounds=metadata.returned_record_bounds,
        # trades@1 is served directly from canonical records, so representation
        # coverage is source coverage.  This equivalence is specific to this
        # representation and is not a general rule: a derived representation
        # with warmup, late-event or partial-result semantics owns its own
        # coverage and must not inherit this mapping.
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


def _request_phase_error(query: ConsumerMarketDataQuery, exc: Exception) -> ConsumerApiError:
    """Translate a resolution failure.  No consumer identity exists yet."""

    code = _REQUEST_PHASE_CODES.get(type(exc), ConsumerErrorCode.INTEGRITY_FAILURE)
    return ConsumerApiError(code, _MESSAGES[code], context=_request_phase_context(query, code, exc))


def _gateway_phase_error(
    normalized: NormalizedMarketDataQuery,
    request_identity: str,
    exc: DataGatewayError,
) -> ConsumerApiError:
    code = _GATEWAY_PHASE_CODES.get(type(exc), ConsumerErrorCode.INTEGRITY_FAILURE)
    return ConsumerApiError(
        code,
        _MESSAGES[code],
        context=_gateway_phase_context(normalized, code),
        request_identity=request_identity,
    )


def _request_phase_context(
    query: ConsumerMarketDataQuery,
    code: ConsumerErrorCode,
    exc: Exception,
) -> dict[str, Any]:
    """Build safe context from values the caller supplied, never from ``exc``."""

    if code is ConsumerErrorCode.UNSUPPORTED_REPRESENTATION:
        representation = query.representation
        if isinstance(representation, RepresentationRef):
            return {
                "requested_kind": str(representation.kind),
                "requested_version": str(representation.version),
                "supported": f"{TRADES_V1_KIND}@{TRADES_V1_VERSION}",
            }
        return {"supported": f"{TRADES_V1_KIND}@{TRADES_V1_VERSION}"}
    if code is ConsumerErrorCode.SOURCE_NOT_FOUND:
        return {
            "venue": str(query.venue),
            "instrument": str(query.instrument),
            "representation": f"{TRADES_V1_KIND}@{TRADES_V1_VERSION}",
        }
    if code is ConsumerErrorCode.INVALID_REQUEST and isinstance(exc, UnsupportedOption):
        # UnsupportedOption is raised only after both shapes are validated, so
        # these are mappings.  The guard keeps this builder non-raising by
        # construction rather than relying on that ordering; it is not the
        # primary validator and does not affect precedence.
        unsupported = _option_names(query.options)
        unsupported |= _option_names(getattr(query.representation, "definition", None))
        return {"unsupported_options": sorted(str(name) for name in unsupported)}
    return {}


def _option_names(value: Any) -> set[Any]:
    return set(value) if isinstance(value, MappingABC) else set()


def _gateway_phase_context(
    normalized: NormalizedMarketDataQuery,
    code: ConsumerErrorCode,
) -> dict[str, Any]:
    if code is ConsumerErrorCode.SOURCE_NOT_FOUND:
        return {
            "venue": normalized.venue,
            "instrument": normalized.instrument,
            "representation": f"{normalized.representation_kind}@{normalized.representation_version}",
        }
    if code is ConsumerErrorCode.NO_COVERAGE:
        return {
            "requested_interval": CoverageInterval(normalized.start, normalized.end).stable_dict()
        }
    if code is ConsumerErrorCode.SCHEMA_INCOMPATIBLE:
        return {
            "representation": f"{normalized.representation_kind}@{normalized.representation_version}"
        }
    # integrity_failure carries no context: it is the class of failure whose
    # lower-layer detail contains paths, catalog ids and connection state.
    return {}
