"""Catalog-backed canonical market-data access."""

from .catalog import Catalog
from .gateway import DataGateway
from .models import (
    CatalogDataset,
    CatalogPartition,
    CatalogConflict,
    CorruptContent,
    CoverageInterval,
    DataGatewayError,
    DataIntegrityError,
    DataRequest,
    DataSlice,
    DatasetIdentity,
    DatasetNotFound,
    InvalidRequest,
    InvalidPartitionState,
    Instant,
    LifecyclePolicy,
    NaturalPartitionIdentity,
    NoCoverage,
    SchemaMismatch,
    StorageResolutionError,
    TradeRecord,
    UnsupportedDatasetKind,
    UnsupportedSchema,
)

__all__ = [
    "CatalogConflict",
    "Catalog",
    "CatalogDataset",
    "CatalogPartition",
    "CorruptContent",
    "CoverageInterval",
    "DataGateway",
    "DataGatewayError",
    "DataIntegrityError",
    "DataRequest",
    "DataSlice",
    "DatasetIdentity",
    "DatasetNotFound",
    "InvalidRequest",
    "InvalidPartitionState",
    "Instant",
    "LifecyclePolicy",
    "NaturalPartitionIdentity",
    "NoCoverage",
    "SchemaMismatch",
    "StorageResolutionError",
    "TradeRecord",
    "UnsupportedDatasetKind",
    "UnsupportedSchema",
    "read_trade_v1",
    "resolve_partition_path",
]

from .parquet import read_trade_v1, resolve_partition_path
