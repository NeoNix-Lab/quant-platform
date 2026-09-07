"""Application-service composition seam.

This package owns composition of use cases that coordinate existing
capabilities without owning their domain semantics.  It is an in-process
seam: it is not an API, a transport, a runtime host, a job runtime or a
client.

ASS-01 established ownership and enforcement.  C02 adds semantic selector
resolution for the frozen ``trades@1`` reference representation.  Result
envelopes, stable error translation, read execution and configuration
resolution remain C03 and ASS-03.
"""

from .market_data import (
    ApplicationRequestError,
    ConsumerMarketDataQuery,
    RepresentationRef,
    UnsupportedOption,
    UnsupportedRepresentation,
    UnsupportedVenue,
    resolve_market_data_request,
)

__all__ = [
    "ApplicationRequestError",
    "ConsumerMarketDataQuery",
    "RepresentationRef",
    "UnsupportedOption",
    "UnsupportedRepresentation",
    "UnsupportedVenue",
    "resolve_market_data_request",
]
