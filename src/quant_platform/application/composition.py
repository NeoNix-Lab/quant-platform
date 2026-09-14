"""Application-owned composition for the current market-data capability."""

from __future__ import annotations

from dataclasses import dataclass

from ..access.catalog import Catalog
from ..access.gateway import DataGateway
from ..source_adapters.bybit import BYBIT_ORDERING_PROVIDER
from .market_data import (
    ConsumerMarketDataQuery,
    ConsumerMarketDataResult,
    execute_market_data_query,
)


DEFAULT_MARKET_DATA_BATCH_SIZE = 65_536


@dataclass(frozen=True, slots=True)
class MarketDataApplicationConfig:
    """Resolved configuration for the current ``trades@1`` application service."""

    catalog_dsn: str = ""
    batch_size: int = DEFAULT_MARKET_DATA_BATCH_SIZE

    def __post_init__(self) -> None:
        if not isinstance(self.catalog_dsn, str):
            raise TypeError("catalog_dsn must be a resolved string")
        if (
            not isinstance(self.batch_size, int)
            or isinstance(self.batch_size, bool)
            or self.batch_size < 1
        ):
            raise ValueError("batch_size must be a positive integer")


@dataclass(frozen=True, slots=True)
class MarketDataApplication:
    """Concrete in-process market-data application assembled from C05 config."""

    config: MarketDataApplicationConfig

    def execute(self, query: ConsumerMarketDataQuery) -> ConsumerMarketDataResult:
        gateway = DataGateway(
            Catalog(dsn=self.config.catalog_dsn),
            ordering_providers=(BYBIT_ORDERING_PROVIDER,),
        )
        return execute_market_data_query(
            query,
            gateway=gateway,
            batch_size=self.config.batch_size,
        )


def compose_market_data_application(
    config: MarketDataApplicationConfig,
) -> MarketDataApplication:
    """Compose the one current Application service from resolved values."""

    if not isinstance(config, MarketDataApplicationConfig):
        raise TypeError("config must be a MarketDataApplicationConfig")
    return MarketDataApplication(config)


__all__ = [
    "DEFAULT_MARKET_DATA_BATCH_SIZE",
    "MarketDataApplication",
    "MarketDataApplicationConfig",
    "compose_market_data_application",
]
