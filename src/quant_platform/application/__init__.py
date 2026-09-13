"""Application-service composition seam.

This package owns composition of use cases that coordinate existing
capabilities without owning their domain semantics.  It is an in-process
seam: it is not an API, a transport, a runtime host, a job runtime or a
client.

ASS-01 established ownership and enforcement.  C02 added semantic selector
resolution for the frozen ``trades@1`` reference representation.  C03 adds
execution over an injected access capability and translation of its outcome
into a stable consumer result or one of the six frozen Consumer API errors.
Transport, job runtime and configuration resolution remain J02, J03 and
ASS-03.
"""

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
    execute_market_data_query,
    resolve_market_data_request,
)

__all__ = [
    "ApplicationRequestError",
    "ConsumerApiError",
    "ConsumerCoverage",
    "ConsumerErrorCode",
    "ConsumerMarketDataQuery",
    "ConsumerMarketDataResult",
    "ConsumerProvenance",
    "NormalizedMarketDataQuery",
    "RepresentationRef",
    "UnsupportedOption",
    "UnsupportedRepresentation",
    "UnsupportedVenue",
    "execute_market_data_query",
    "resolve_market_data_request",
]
