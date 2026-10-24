"""Bybit source-owned policies for canonical ``trade-v1``."""

from __future__ import annotations

from collections.abc import Iterable
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..data.models import DataIntegrityError, DatasetIdentity, Instant, InvalidRequest, TradeRecord
from ..data.materializer import ParquetMaterialization, materialize_trade_v1
from ..ordering import OrderingProvider, TRADES_CANONICAL_TOTAL_ORDER_V1


BYBIT_TRADE_V1_ORDERING_POLICY = "bybit-trade-v1-exchange-ts-trade-id-v1"
BYBIT_TRADE_V1_CHECK_SUITE = "producer-consumer-conformity-v1/bybit-trade-v1-first-vertical"
BYBIT_TRADE_V1_CERTIFICATION_PROFILE = "producer-consumer-conformity-v1/bybit-trade-v1-first-vertical-v1"


class BybitTradeV1EligibilityError(ValueError):
    """Raised when schema-valid trade-v1 cannot enter the Bybit valid profile."""


# ADR-0074 section 1: the Bybit linear USDT-settled perpetuals the platform admits for canonical
# ``trades`` / ``trade-v1``. A closed constant, not a free string; a new member is an ADR amendment.
BYBIT_LINEAR_TRADE_INSTRUMENTS_V1 = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT")

# The historical source profiles ``(source_semantics, mapping)`` a Bybit ``trade-v1`` day may declare: the maintainer's SQLite
# (first vertical) and, by ADR-0074 Amendment 1, the Bybit public trade archive. Any other profile is refused by certification.
BYBIT_SQLITE_SOURCE_PROFILE_V1 = ("bybit-public-trades-sqlite-v1", "bybit-sqlite-day-extract-v1")
BYBIT_ARCHIVE_SOURCE_PROFILE_V1 = ("bybit-public-trade-archive-v1", "bybit-archive-day-extract-v1")
BYBIT_TRADE_V1_SOURCE_PROFILES = (BYBIT_SQLITE_SOURCE_PROFILE_V1, BYBIT_ARCHIVE_SOURCE_PROFILE_V1)


def bybit_trade_v1_applies_to(identity: DatasetIdentity) -> bool:
    return (
        identity.layer == "canonical"
        and identity.venue == "bybit"
        and identity.dataset_kind == "trades"
        and identity.instrument in BYBIT_LINEAR_TRADE_INSTRUMENTS_V1
        and identity.record_schema_id == "trade-v1"
    )


def bybit_trade_v1_ordering_key(record: TradeRecord) -> tuple[Instant, str]:
    """Build Bybit's opaque-ID tie-break key: ``(exchange_ts, trade_id)``."""

    trade_id = record.trade_id
    if trade_id is None or trade_id == "":
        raise DataIntegrityError("Bybit trade-v1 ordering requires a non-empty trade_id")
    if not isinstance(trade_id, str):
        raise DataIntegrityError("Bybit trade-v1 ordering requires trade_id as a string")
    try:
        exchange_ts = Instant.parse(record.exchange_ts)
    except (InvalidRequest, TypeError) as exc:
        raise DataIntegrityError("Bybit trade-v1 ordering requires a valid exchange_ts") from exc
    return exchange_ts, trade_id


BYBIT_ORDERING_PROVIDER = OrderingProvider(
    identity=BYBIT_TRADE_V1_ORDERING_POLICY,
    satisfies_requirements=frozenset({TRADES_CANONICAL_TOTAL_ORDER_V1}),
    key=bybit_trade_v1_ordering_key,
    applies_to=bybit_trade_v1_applies_to,
)


def validate_bybit_trade_v1_eligibility(
    identity: DatasetIdentity,
    records: tuple[TradeRecord, ...],
) -> None:
    """Enforce the first-vertical publication profile from conformity §10."""

    if not bybit_trade_v1_applies_to(identity):
        raise BybitTradeV1EligibilityError(
            "Bybit trade-v1 eligibility profile does not apply to the dataset identity"
        )
    seen: set[tuple[int, str]] = set()
    for index, record in enumerate(records):
        trade_id = record.trade_id
        if trade_id is None or trade_id == "":
            raise BybitTradeV1EligibilityError(
                f"record {index} makes the partition ineligible: trade_id must be non-null and non-empty"
            )
        key = (Instant.parse(record.exchange_ts).epoch_ns, trade_id)
        if key in seen:
            raise BybitTradeV1EligibilityError(
                "partition is ineligible: duplicate (exchange_ts, trade_id) ordering key"
            )
        seen.add(key)


@dataclass(frozen=True, slots=True)
class BybitTradeV1CertificationProfile:
    """Source-owned semantics injected into the generic S13 certifier."""

    code_ref: str
    profile_id: str = BYBIT_TRADE_V1_CERTIFICATION_PROFILE
    check_suite: str = BYBIT_TRADE_V1_CHECK_SUITE

    def applies_to(self, identity: DatasetIdentity) -> bool:
        return bybit_trade_v1_applies_to(identity)

    def ordering_key(self, record: TradeRecord) -> tuple[Instant, str]:
        return bybit_trade_v1_ordering_key(record)

    def validate_source(
        self,
        identity: DatasetIdentity,
        coverage_documents: Sequence[Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        if not self.applies_to(identity):
            raise BybitTradeV1EligibilityError("Bybit certification profile does not apply")
        if not coverage_documents:
            raise BybitTradeV1EligibilityError("certification requires durable source coverage evidence")
        profiles = set()
        for document in coverage_documents:
            acquisition = document.get("acquisition") or {}
            semantics, mapping = acquisition.get("source_semantics"), acquisition.get("mapping")
            if semantics not in {profile[0] for profile in BYBIT_TRADE_V1_SOURCE_PROFILES}:
                raise BybitTradeV1EligibilityError("coverage source_semantics is not the frozen Bybit extract profile")
            if (semantics, mapping) not in BYBIT_TRADE_V1_SOURCE_PROFILES:
                raise BybitTradeV1EligibilityError("coverage mapping is not the frozen Bybit extract mapping")
            profiles.add((semantics, mapping))
            for assertion in document.get("assertions") or ():
                if assertion.get("status") != "complete":
                    continue
                if not any(item.get("kind") == "deterministic_source_extract" for item in assertion.get("evidence") or ()):
                    raise BybitTradeV1EligibilityError("complete Bybit coverage lacks deterministic source-extract evidence")
        if len(profiles) != 1:
            raise BybitTradeV1EligibilityError("coverage documents of one partition declare different source profiles")
        ((semantics, mapping),) = profiles
        return {
            "source_semantics": semantics,
            "mapping": mapping,
            "documents": len(coverage_documents),
        }

    def validate_records(
        self,
        identity: DatasetIdentity,
        records: Sequence[TradeRecord],
    ) -> Mapping[str, Any]:
        validate_bybit_trade_v1_eligibility(identity, tuple(records))
        if any(record.receive_ts is not None for record in records):
            raise BybitTradeV1EligibilityError("Bybit first-vertical records must not fabricate receive_ts")
        if any(record.sequence is not None for record in records):
            raise BybitTradeV1EligibilityError("Bybit first-vertical records must not fabricate sequence")
        return {"records": len(records), "trade_id_policy": "non-null-unique-(exchange_ts,trade_id)"}


def materialize_bybit_trade_v1(
    path: str | Path,
    records: Iterable[TradeRecord],
    *,
    dataset_identity: DatasetIdentity,
    compression: str | None = "zstd",
    row_group_size: int = 65_536,
) -> ParquetMaterialization:
    """Materialize a Bybit canonical trade-v1 partition under the v1 profile."""

    return materialize_trade_v1(
        path,
        records,
        dataset_identity=dataset_identity,
        ordering_provider=BYBIT_ORDERING_PROVIDER,
        eligibility_validator=validate_bybit_trade_v1_eligibility,
        compression=compression,
        row_group_size=row_group_size,
    )


def build_bybit_trade_v1_source_extract_coverage(
    *,
    dataset_identity: DatasetIdentity,
    coverage_id: str,
    intent_start: str,
    intent_end: str,
    assertion_id: str,
    assertion_start: str,
    assertion_end: str,
    partition_key: str,
    revision: int,
    source_extract_detail: str,
    created_at: str,
    producer: str,
    code_ref: str,
    supersedes: str | None = None,
    source_profile: tuple[str, str] = BYBIT_SQLITE_SOURCE_PROFILE_V1,
) -> dict[str, Any]:
    """Translate explicit source-extract evidence to generic coverage input.

    The complete status is tied to the caller-provided deterministic extract
    evidence, never to the presence or count of materialized trades. The
    source profile defaults to the first-vertical SQLite one; the archive
    import passes ``BYBIT_ARCHIVE_SOURCE_PROFILE_V1`` (ADR-0074 Amendment 1).
    """

    if tuple(source_profile) not in BYBIT_TRADE_V1_SOURCE_PROFILES:
        raise BybitTradeV1EligibilityError("source-extract coverage names an unknown Bybit source profile")

    if not bybit_trade_v1_applies_to(dataset_identity):
        raise BybitTradeV1EligibilityError(
            "Bybit source-extract coverage requires canonical/trades/trade-v1 identity"
        )
    if not isinstance(source_extract_detail, str) or not source_extract_detail.strip():
        raise BybitTradeV1EligibilityError(
            "source extract coverage requires explicit deterministic extract evidence"
        )
    return {
        "source_dataset_identity": dataset_identity,
        "coverage_id": coverage_id,
        "supersedes": supersedes,
        "created_at": created_at,
        "acquisition": {
            "basis": "source_extract",
            "intent_start": intent_start,
            "intent_end": intent_end,
            "source_semantics": source_profile[0],
            "mapping": source_profile[1],
        },
        "assertions": [
            {
                "assertion_id": assertion_id,
                "start": assertion_start,
                "end": assertion_end,
                "status": "complete",
                "partitions": [
                    {"partition_key": partition_key, "revision": revision}
                ],
                "evidence": [
                    {
                        "kind": "deterministic_source_extract",
                        "detail": source_extract_detail,
                    }
                ],
            }
        ],
        "producer": producer,
        "code_ref": code_ref,
    }


__all__ = [
    "BYBIT_ARCHIVE_SOURCE_PROFILE_V1",
    "BYBIT_LINEAR_TRADE_INSTRUMENTS_V1",
    "BYBIT_ORDERING_PROVIDER",
    "BYBIT_SQLITE_SOURCE_PROFILE_V1",
    "BYBIT_TRADE_V1_CERTIFICATION_PROFILE",
    "BYBIT_TRADE_V1_CHECK_SUITE",
    "BYBIT_TRADE_V1_ORDERING_POLICY",
    "BYBIT_TRADE_V1_SOURCE_PROFILES",
    "BybitTradeV1CertificationProfile",
    "BybitTradeV1EligibilityError",
    "build_bybit_trade_v1_source_extract_coverage",
    "bybit_trade_v1_applies_to",
    "bybit_trade_v1_ordering_key",
    "materialize_bybit_trade_v1",
    "validate_bybit_trade_v1_eligibility",
]
