"""Application-service composition seam.

This package owns composition of use cases that coordinate existing
capabilities without owning their domain semantics.  It is an in-process
seam: it is not an API, a transport, a runtime host, a job runtime or a
client.

ASS-01 established ownership and enforcement.  C02 added semantic selector
resolution for the frozen ``trades@1`` reference representation.  C03 added
execution over an injected access capability and translation of its outcome
into a stable consumer result or one of the six frozen Consumer API errors.
C05 adds typed immutable configuration and concrete composition for the current
market-data application service.  Transport and job runtime remain J02 and J03.
"""

from .composition import (
    DEFAULT_MARKET_DATA_BATCH_SIZE,
    MarketDataApplication,
    MarketDataApplicationConfig,
    compose_market_data_application,
)
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
    "DEFAULT_MARKET_DATA_BATCH_SIZE",
    "ApplicationRequestError",
    "ConsumerApiError",
    "ConsumerCoverage",
    "ConsumerErrorCode",
    "ConsumerMarketDataQuery",
    "ConsumerMarketDataResult",
    "ConsumerProvenance",
    "MarketDataApplication",
    "MarketDataApplicationConfig",
    "NormalizedMarketDataQuery",
    "RepresentationRef",
    "UnsupportedOption",
    "UnsupportedRepresentation",
    "UnsupportedVenue",
    "compose_market_data_application",
    "execute_market_data_query",
    "resolve_market_data_request",
]
