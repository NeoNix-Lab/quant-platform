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

from .backup_restore import (
    RecoveryBackupExport,
    RestoredRecoverySet,
    capture_recovery_set,
    export_recovery_set,
    restore_recovery_set,
)
from .composition import (
    DEFAULT_MARKET_DATA_BATCH_SIZE,
    MarketDataApplication,
    MarketDataApplicationConfig,
    compose_market_data_application,
)
from .bybit_import import (
    CANONICAL_FIELD_ORDER,
    ImportError_,
    ImportReport,
    RECORD_SCHEMA_ID,
    SUPPORTED_CATEGORY,
    SUPPORTED_INSTRUMENT,
    SUPPORTED_VENUE,
    TradeStats,
    WriteResult,
    encode_record,
    format_exchange_ts,
    run_import,
    write_jsonl_atomic,
)
from .conformity import (
    Check,
    HarnessConfig,
    HarnessFailure,
    HarnessRunFailure,
    PreflightResult,
    RunReport,
    Target,
    VerificationMismatch,
    collect_preflight,
    inspect_vertical,
    run_vertical,
    verify_vertical,
)
from .h01_composition import (
    BUCKET_OBSERVATION_IDENTITY_DOMAIN,
    H01CompositionError,
    H01Evaluation,
    bucket_observation_identity,
    evaluate_h01_imbalance,
    materialize_h01_feature_artifact,
)
from .golden_conformity import (
    GoldenExpectation,
    ScanObservation,
    format_observation,
    golden_field_mismatches,
    load_golden_expectation,
    observe_scan,
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
    "BUCKET_OBSERVATION_IDENTITY_DOMAIN",
    "CANONICAL_FIELD_ORDER",
    "DEFAULT_MARKET_DATA_BATCH_SIZE",
    "ApplicationRequestError",
    "Check",
    "ConsumerApiError",
    "ConsumerCoverage",
    "ConsumerErrorCode",
    "ConsumerMarketDataQuery",
    "ConsumerMarketDataResult",
    "ConsumerProvenance",
    "GoldenExpectation",
    "H01CompositionError",
    "H01Evaluation",
    "HarnessConfig",
    "HarnessFailure",
    "HarnessRunFailure",
    "ImportError_",
    "ImportReport",
    "MarketDataApplication",
    "MarketDataApplicationConfig",
    "NormalizedMarketDataQuery",
    "RepresentationRef",
    "PreflightResult",
    "RECORD_SCHEMA_ID",
    "RecoveryBackupExport",
    "RestoredRecoverySet",
    "RunReport",
    "SUPPORTED_CATEGORY",
    "SUPPORTED_INSTRUMENT",
    "SUPPORTED_VENUE",
    "ScanObservation",
    "Target",
    "TradeStats",
    "UnsupportedOption",
    "UnsupportedRepresentation",
    "UnsupportedVenue",
    "VerificationMismatch",
    "WriteResult",
    "bucket_observation_identity",
    "capture_recovery_set",
    "collect_preflight",
    "compose_market_data_application",
    "encode_record",
    "evaluate_h01_imbalance",
    "execute_market_data_query",
    "export_recovery_set",
    "format_exchange_ts",
    "format_observation",
    "golden_field_mismatches",
    "inspect_vertical",
    "load_golden_expectation",
    "materialize_h01_feature_artifact",
    "observe_scan",
    "resolve_market_data_request",
    "restore_recovery_set",
    "run_import",
    "run_vertical",
    "verify_vertical",
    "write_jsonl_atomic",
]
