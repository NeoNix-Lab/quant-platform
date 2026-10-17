"""Application-owned composition for the current market-data capability."""

from __future__ import annotations

from dataclasses import dataclass

from ..access.catalog import Catalog
from ..access.gateway import DataGateway
from ..access.l2_gateway import L2DataGateway
from ..source_adapters.bybit import BYBIT_ORDERING_PROVIDER
from ..source_adapters.bybit_l2 import BYBIT_L2_ORDERING_PROVIDER
from .l2_market_data import (
    DEFAULT_L2_MAX_RESULT_BYTES,
    L2_BOOK_EVENTS_KIND,
    execute_l2_book_events_query,
)
from .market_data import (
    DEFAULT_MAX_RESULT_ROWS,
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
    max_result_rows: int = DEFAULT_MAX_RESULT_ROWS

    def __post_init__(self) -> None:
        if not isinstance(self.catalog_dsn, str):
            raise TypeError("catalog_dsn must be a resolved string")
        if (
            not isinstance(self.batch_size, int)
            or isinstance(self.batch_size, bool)
            or self.batch_size < 1
        ):
            raise ValueError("batch_size must be a positive integer")
        if (
            not isinstance(self.max_result_rows, int)
            or isinstance(self.max_result_rows, bool)
            or self.max_result_rows < 1
        ):
            raise ValueError("max_result_rows must be a positive integer")


@dataclass(frozen=True, slots=True)
class MarketDataApplication:
    """Concrete in-process market-data application assembled from C05 config."""

    config: MarketDataApplicationConfig

    def execute(self, query: ConsumerMarketDataQuery) -> ConsumerMarketDataResult:
        if getattr(getattr(query, "representation", None), "kind", None) == L2_BOOK_EVENTS_KIND:
            return execute_l2_book_events_query(
                query,
                gateway=L2DataGateway(
                    Catalog(dsn=self.config.catalog_dsn),
                    ordering_providers=(BYBIT_L2_ORDERING_PROVIDER,),
                ),
                batch_size=self.config.batch_size,
                # The L2 byte bound is the documented default (it is tied to J02's
                # fixed 16 MiB ceiling); the trades config stays capability-specific.
                max_result_bytes=DEFAULT_L2_MAX_RESULT_BYTES,
            )
        gateway = DataGateway(
            Catalog(dsn=self.config.catalog_dsn),
            ordering_providers=(BYBIT_ORDERING_PROVIDER,),
        )
        return execute_market_data_query(
            query,
            gateway=gateway,
            batch_size=self.config.batch_size,
            max_result_rows=self.config.max_result_rows,
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
    "DEFAULT_MAX_RESULT_ROWS",
    "MarketDataApplication",
    "MarketDataApplicationConfig",
    "compose_market_data_application",
]
