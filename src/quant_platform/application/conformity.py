#!/usr/bin/env python3
"""Application-owned orchestration for the first human Bybit conformity vertical.

This module composes existing production seams from resolved configuration.
Executable entry points remain responsible for CLI/environment acquisition and
operator presentation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

from .golden_conformity import (
    GoldenExpectation,
    candle_field_mismatches,
    format_observation,
    golden_field_mismatches,
    load_golden_expectation,
    observe_scan,
    observe_scan_with_candles,
)
from quant_platform.access.catalog import Catalog
from quant_platform.data.publication_catalog import CatalogPublicationWriter
from quant_platform.access.gateway import DataGateway
from quant_platform.access.models import (
    DataRequest,
    LifecyclePolicy,
)
from quant_platform.data import (
    DatasetIdentity,
    DatasetNotFound,
    Instant,
)
from quant_platform.data.publication import (
    PublicationCertification,
    SealedPartitionEvidence,
)
from quant_platform.data.publication_eligibility import (
    PublicationEligibilityBridge,
    PublicationEligibilityEvidence,
)
from quant_platform.data.publication_eligibility_catalog import PublicationEligibilityCatalog
from quant_platform.data.manifests import (
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
)
from quant_platform.source_adapters.bybit import (
    BYBIT_ORDERING_PROVIDER,
    BYBIT_TRADE_V1_CHECK_SUITE,
    BYBIT_TRADE_V1_CERTIFICATION_PROFILE,
    BYBIT_TRADE_V1_ORDERING_POLICY,
    BybitTradeV1CertificationProfile,
    build_bybit_trade_v1_source_extract_coverage,
    materialize_bybit_trade_v1,
)
from quant_platform.source_adapters.bybit_historical import (
    BybitHistoricalExtractAccumulator,
    BybitHistoricalSourceError,
    SUPPORTED_CATEGORY,
    SUPPORTED_SYMBOL as SUPPORTED_INSTRUMENT,
    SUPPORTED_VENUE,
    bybit_historical_legacy_read,
    canonicalize_bybit_historical_trade_v1,
    iter_bybit_historical_trade_rows,
    open_bybit_historical_legacy_source,
    open_bybit_historical_source,
    utc_day_bounds_ms,
)


ROOT = Path(__file__).resolve().parents[3]
ALL_PARTITION_STATES = (
    "writing",
    "closed",
    "valid",
    "degraded",
    "invalid",
    "superseded",
)
DATASET_ORIGIN = "source_acquired"
DATASET_TRANSFORM = "canonicalize-trades-v1"
PRODUCER_ID = "human-e2e-operator-harness-v1"
CERTIFIER_CODE_REF = "human-e2e-operator-harness-v1"


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    sqlite_path: Path | None
    dsn: str | None
    storage_root: Path | None
    storage_root_id: str
    golden_path: Path
    batch_size: int = 65_536
    legacy_source: bool = False


@dataclass(frozen=True, slots=True)
class Target:
    golden: GoldenExpectation
    identity: DatasetIdentity
    day: str
    start: Instant
    end: Instant
    start_ms: int
    end_ms: int
    partition_key: str
    rel_path: str
    dataset_root: Path
    artifact_path: Path
    dataset_manifest_path: Path
    partition_manifest_path: Path
    coverage_manifest_path: Path


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class PreflightResult:
    target: Target | None
    checks: tuple[Check, ...]

    @property
    def passed(self) -> bool:
        return self.target is not None and all(check.passed for check in self.checks)


@dataclass(frozen=True, slots=True)
class RunReport:
    target: Target
    source_row_count: int
    source_fingerprint_sha256: str
    materialization: Any
    dataset_manifest_sha256: str
    partition_manifest_sha256: str
    coverage_manifest_sha256: str
    certification: Any
    eligibility: Any


class HarnessFailure(RuntimeError):
    """An operator-readable failure with a fail-closed command boundary."""


class HarnessRunFailure(HarnessFailure):
    def __init__(self, phase: str, cause: BaseException, target: Target | None = None):
        self.phase = phase
        self.cause = cause
        self.target = target
        super().__init__(str(cause))


class VerificationMismatch(HarnessFailure):
    def __init__(
        self,
        target: Target,
        observation: Any,
        mismatches: tuple[str, ...],
        candle: Any = None,
    ):
        self.target = target
        self.observation = observation
        self.mismatches = mismatches
        self.candle = candle
        super().__init__("Golden comparison did not match the frozen expectation")


def _target_from_golden(config: HarnessConfig) -> Target:
    golden = load_golden_expectation(config.golden_path)
    if golden.venue != SUPPORTED_VENUE or golden.instrument != SUPPORTED_INSTRUMENT:
        raise HarnessFailure(
            "Golden fixture is outside the frozen Bybit BTCUSDT first vertical"
        )
    day = golden.interval_start[:10]
    start_ms, end_ms = utc_day_bounds_ms(day)
    start = Instant.parse(golden.interval_start)
    end = Instant.parse(golden.interval_end)
    if start != Instant(start_ms * 1_000_000) or end != Instant(end_ms * 1_000_000):
        raise HarnessFailure("Golden fixture interval is not one complete UTC day")

    identity = DatasetIdentity(
        "canonical", "trades", SUPPORTED_VENUE, SUPPORTED_INSTRUMENT, "trade-v1"
    )
    if config.storage_root is None:
        dataset_root = Path(".")
    else:
        dataset_root = config.storage_root.joinpath(
            identity.layer,
            identity.dataset_kind,
            identity.venue,
            identity.instrument,
            identity.record_schema_id,
        )
    partition_key = f"dt={day}"
    rel_path = f"{partition_key}/part-001.parquet"
    return Target(
        golden=golden,
        identity=identity,
        day=day,
        start=start,
        end=end,
        start_ms=start_ms,
        end_ms=end_ms,
        partition_key=partition_key,
        rel_path=rel_path,
        dataset_root=dataset_root,
        artifact_path=dataset_root / rel_path,
        dataset_manifest_path=dataset_root / "dataset-manifest.json",
        partition_manifest_path=dataset_root / "partition-manifest.json",
        coverage_manifest_path=dataset_root / "coverage-manifest.json",
    )


def _connect_catalog(dsn: str | None):
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise HarnessFailure("psycopg is required for catalog operations") from exc
    try:
        return psycopg.connect(dsn) if dsn else psycopg.connect()
    except Exception as exc:
        raise HarnessFailure("could not connect to the PostgreSQL catalog") from exc


def _storage_check(root: Path | None) -> Check:
    if root is None:
        return Check("storage", False, "storage root is not configured")
    try:
        if not root.is_absolute():
            return Check("storage", False, f"storage root must be absolute: {root}")
        resolved = root.resolve()
        if resolved.exists():
            if not resolved.is_dir():
                return Check("storage", False, f"not a directory: {resolved}")
            if not os.access(resolved, os.W_OK):
                return Check("storage", False, f"not writable: {resolved}")
            return Check("storage", True, str(resolved))
        parent = resolved
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if not parent.is_dir() or not os.access(parent, os.W_OK):
            return Check("storage", False, f"storage root and writable parent are unavailable: {resolved}")
        return Check("storage", True, f"{resolved} (will be created under {parent})")
    except OSError as exc:
        return Check("storage", False, str(exc))


def _source_shape_check(config: HarnessConfig, target: Target | None) -> Check:
    if config.sqlite_path is None:
        return Check("source", False, "SQLite source is not configured")
    if not config.sqlite_path.is_file():
        return Check("source", False, f"SQLite source not found: {config.sqlite_path}")
    if target is None:
        return Check("source", False, "cannot check source without a valid Golden target")
    connection = None
    stream = None
    try:
        opener = (
            open_bybit_historical_legacy_source
            if config.legacy_source
            else open_bybit_historical_source
        )
        connection = opener(config.sqlite_path)
        stream = iter_bybit_historical_trade_rows(
            connection,
            target.start_ms,
            target.end_ms,
            category=SUPPORTED_CATEGORY,
            symbol=SUPPORTED_INSTRUMENT,
            batch_size=1,
        )
        next(stream, None)
        return Check("source", True, f"read-only SQLite source and trades shape accessible: {config.sqlite_path}")
    except Exception as exc:
        return Check("source", False, str(exc))
    finally:
        if stream is not None:
            stream.close()
        if connection is not None:
            connection.close()


def _catalog_checks(config: HarnessConfig, target: Target | None) -> tuple[Check, ...]:
    if target is None:
        return (
            Check("catalog", False, "cannot check catalog without a valid Golden target"),
        )
    connection = None
    try:
        connection = _connect_catalog(config.dsn)
        catalog = Catalog(connection=connection)
        try:
            catalog.resolve_dataset(target.identity)
        except DatasetNotFound:
            return (
                Check("catalog", True, "PostgreSQL catalog connected"),
                Check("lineage", True, "source-acquired canonical has no required catalog parent"),
                Check("rerun", True, "canonical target natural identity is absent"),
            )
        else:
            return (
                Check("catalog", True, "PostgreSQL catalog connected"),
                Check("lineage", True, "source-acquired canonical has no required catalog parent"),
                Check("rerun", False, "canonical target natural identity already exists; refusing ambiguous rerun"),
            )
    except Exception as exc:
        return (Check("catalog", False, str(exc)),)
    finally:
        if connection is not None:
            connection.close()


def _output_check(target: Target | None) -> Check:
    if target is None:
        return Check("output", False, "cannot check output targets without a valid Golden target")
    existing = tuple(
        path
        for path in (
            target.artifact_path,
            target.dataset_manifest_path,
            target.partition_manifest_path,
            target.coverage_manifest_path,
        )
        if path.exists()
    )
    if existing:
        return Check(
            "output",
            False,
            "run targets already exist; refusing overwrite: "
            + ", ".join(str(path) for path in existing),
        )
    return Check("output", True, "artifact and durable manifest targets are absent")


def collect_preflight(config: HarnessConfig, *, check_rerun: bool = True) -> PreflightResult:
    """Collect bounded, read-only run preconditions."""

    checks: list[Check] = []
    target: Target | None = None
    try:
        target = _target_from_golden(config)
        checks.append(Check("golden", True, str(config.golden_path)))
    except Exception as exc:
        checks.append(Check("golden", False, str(exc)))

    checks.append(_source_shape_check(config, target))
    checks.append(_storage_check(config.storage_root))
    checks.append(_output_check(target))
    if not isinstance(config.storage_root_id, str) or not config.storage_root_id.strip():
        checks.append(Check("runtime", False, "storage root id is empty"))
    else:
        checks.append(Check("runtime", True, f"storage_root_id={config.storage_root_id.strip()}"))
    catalog_checks = _catalog_checks(config, target)
    if not check_rerun:
        catalog_checks = tuple(check for check in catalog_checks if check.name != "rerun")
    checks.extend(catalog_checks)
    return PreflightResult(target, tuple(checks))


def _require_absent_run_targets(target: Target) -> None:
    existing = tuple(
        path
        for path in (
            target.artifact_path,
            target.dataset_manifest_path,
            target.partition_manifest_path,
            target.coverage_manifest_path,
        )
        if path.exists()
    )
    if existing:
        rendered = ", ".join(str(path) for path in existing)
        raise HarnessFailure(f"run targets already exist; refusing overwrite: {rendered}")


def _canonical_records(connection: Any, target: Target, accumulator: BybitHistoricalExtractAccumulator):
    for row in iter_bybit_historical_trade_rows(
        connection,
        target.start_ms,
        target.end_ms,
        category=SUPPORTED_CATEGORY,
        symbol=SUPPORTED_INSTRUMENT,
        batch_size=65_536,
        accumulator=accumulator,
    ):
        yield canonicalize_bybit_historical_trade_v1(row)


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def run_vertical(config: HarnessConfig) -> RunReport:
    """Execute the one mutating vertical through existing production owners."""

    preflight = collect_preflight(config)
    if not preflight.passed:
        detail = "; ".join(
            f"{check.name}: {check.detail}" for check in preflight.checks if not check.passed
        )
        raise HarnessRunFailure("PREFLIGHT", HarnessFailure(detail), preflight.target)
    assert preflight.target is not None
    target = preflight.target
    try:
        _require_absent_run_targets(target)
    except Exception as exc:
        raise HarnessRunFailure("PREFLIGHT", exc, target) from exc

    phase = "SOURCE"
    materialization = None
    source_evidence = None
    certification = None
    eligibility = None
    source_connection = None
    catalog_connection = None
    try:
        accumulator = BybitHistoricalExtractAccumulator()
        if config.legacy_source:
            with bybit_historical_legacy_read(config.sqlite_path) as source_connection:
                phase = "MATERIALIZE"
                materialization = materialize_bybit_trade_v1(
                    target.artifact_path,
                    _canonical_records(source_connection, target, accumulator),
                    dataset_identity=target.identity,
                )
                source_evidence = accumulator.evidence(
                    target.day, target.start_ms, target.end_ms
                )
                if source_evidence.source_row_count != materialization.row_count:
                    raise HarnessFailure(
                        "source evidence row count differs from materialization row count"
                    )
        else:
            source_connection = open_bybit_historical_source(config.sqlite_path)
            phase = "MATERIALIZE"
            materialization = materialize_bybit_trade_v1(
                target.artifact_path,
                _canonical_records(source_connection, target, accumulator),
                dataset_identity=target.identity,
            )
            source_evidence = accumulator.evidence(
                target.day, target.start_ms, target.end_ms
            )
            if source_evidence.source_row_count != materialization.row_count:
                raise HarnessFailure(
                    "source evidence row count differs from materialization row count"
                )
    except HarnessRunFailure:
        raise
    except BybitHistoricalSourceError as exc:
        raise HarnessRunFailure("SOURCE", exc, target) from exc
    except Exception as exc:
        raise HarnessRunFailure(phase, exc, target) from exc
    finally:
        if source_connection is not None:
            source_connection.close()

    created_at = _now_text()
    try:
        phase = "MANIFEST"
        dataset_emission = emit_dataset_manifest(
            target.dataset_manifest_path,
            dataset_identity=target.identity,
            created_at=created_at,
            transform=DATASET_TRANSFORM,
            schema_version="dataset-manifest-v2",
            origin=DATASET_ORIGIN,
        )
        closed_at = _now_text()
        partition_emission = emit_partition_manifest(
            target.partition_manifest_path,
            materialization,
            dataset_identity=target.identity,
            dataset_root=target.dataset_root,
            partition_key=target.partition_key,
            revision=1,
            rel_path=target.rel_path,
            created_at=created_at,
            closed_at=closed_at if closed_at >= created_at else created_at,
            producer=PRODUCER_ID,
            code_ref=PRODUCER_ID,
        )
    except Exception as exc:
        raise HarnessRunFailure(phase, exc, target) from exc

    try:
        phase = "COVERAGE"
        coverage_input = build_bybit_trade_v1_source_extract_coverage(
            dataset_identity=target.identity,
            coverage_id=f"bybit-{target.golden.instrument.lower()}-{target.day}-source-extract-v1",
            intent_start=target.golden.interval_start,
            intent_end=target.golden.interval_end,
            assertion_id=f"bybit-{target.golden.instrument.lower()}-{target.day}-complete-v1",
            assertion_start=target.golden.interval_start,
            assertion_end=target.golden.interval_end,
            partition_key=target.partition_key,
            revision=1,
            source_extract_detail=source_evidence.detail,
            created_at=_now_text(),
            producer=PRODUCER_ID,
            code_ref=PRODUCER_ID,
        )
        coverage_emission = emit_coverage_manifest(
            target.coverage_manifest_path,
            dataset_identity=target.identity,
            partition_manifests=[partition_emission.document],
            **coverage_input,
        )
    except Exception as exc:
        raise HarnessRunFailure(phase, exc, target) from exc

    try:
        phase = "S13"
        catalog_connection = _connect_catalog(config.dsn)
        profile = BybitTradeV1CertificationProfile(CERTIFIER_CODE_REF)
        certification = PublicationCertification(
            CatalogPublicationWriter(catalog_connection), profile
        ).run(
            SealedPartitionEvidence(
                target.dataset_manifest_path,
                target.partition_manifest_path,
                (target.coverage_manifest_path,),
                target.artifact_path,
                config.storage_root_id.strip(),
            )
        )
        s13_status = certification.certification.status
        if s13_status != "pass":
            raise HarnessFailure(
                f"S13 certification status is not pass: {s13_status!r}"
            )
    except Exception as exc:
        if catalog_connection is not None:
            try:
                catalog_connection.close()
            except Exception:
                pass
        raise HarnessRunFailure(phase, exc, target) from exc

    try:
        phase = "S14"
        eligibility = PublicationEligibilityBridge(
            PublicationEligibilityCatalog(catalog_connection)
        ).publish(
            PublicationEligibilityEvidence(
                target.dataset_manifest_path,
                target.partition_manifest_path,
                (target.coverage_manifest_path,),
                config.storage_root_id.strip(),
                BYBIT_TRADE_V1_CERTIFICATION_PROFILE,
                BYBIT_TRADE_V1_CHECK_SUITE,
            )
        )
    except Exception as exc:
        raise HarnessRunFailure(phase, exc, target) from exc
    finally:
        if catalog_connection is not None:
            catalog_connection.close()

    return RunReport(
        target=target,
        source_row_count=source_evidence.source_row_count,
        source_fingerprint_sha256=source_evidence.source_fingerprint_sha256,
        materialization=materialization,
        dataset_manifest_sha256=dataset_emission.manifest_sha256,
        partition_manifest_sha256=partition_emission.manifest_sha256,
        coverage_manifest_sha256=coverage_emission.manifest_sha256,
        certification=certification,
        eligibility=eligibility,
    )


def inspect_vertical(config: HarnessConfig) -> tuple[Target, Any, tuple[Any, ...], tuple[dict[str, Any], ...]]:
    """Inspect catalog-owned and durable evidence without writing anything."""

    target = _target_from_golden(config)
    connection = _connect_catalog(config.dsn)
    try:
        catalog = Catalog(connection=connection)
        dataset = catalog.resolve_dataset(target.identity)
        partitions = tuple(
            partition
            for partition in catalog.select_partitions(
                dataset, target.start, target.end, ALL_PARTITION_STATES
            )
            if partition.natural_identity.partition_key == target.partition_key
        )
        if not partitions:
            raise HarnessFailure("catalog has no target partition for the Golden day")

        manifests: list[dict[str, Any]] = []
        dataset_root_by_partition: dict[str, Path] = {}
        for partition in partitions:
            dataset_root = Path(partition.storage_root).joinpath(
                *dataset.rel_root.split("/")
            )
            dataset_root_by_partition[partition.catalog_partition_id] = dataset_root
            for kind, path in (
                ("dataset", dataset_root / "dataset-manifest.json"),
                ("partition", dataset_root / "partition-manifest.json"),
                ("coverage", dataset_root / "coverage-manifest.json"),
            ):
                try:
                    path.stat()
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    raise HarnessFailure(
                        f"durable {kind} manifest cannot be read at {path}: {exc}"
                    ) from exc
                if not path.is_file():
                    raise HarnessFailure(
                        f"durable {kind} manifest is not a readable file: {path}"
                    )
                try:
                    document = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise HarnessFailure(
                        f"durable {kind} manifest is malformed or unreadable at {path}: {exc}"
                    ) from exc
                manifests.append(
                    {
                        "kind": kind,
                        "path": str(path),
                        "document": document,
                        "partition_id": partition.catalog_partition_id,
                    }
                )
        return target, dataset, partitions, tuple(manifests)
    finally:
        connection.close()


def verify_vertical(config: HarnessConfig) -> tuple[Target, Any, Any]:
    """Verify the published vertical through DataGateway.scan only.

    When the Golden fixture declares a ``candle`` expectation, the same
    DataGateway scan is additionally bridged into the accepted D03 historical
    candle seam (see ``observe_scan_with_candles``); a fixture without one
    keeps trade-only verification exactly as before this capability existed.
    """

    target = _target_from_golden(config)
    connection = _connect_catalog(config.dsn)
    scan = None
    candle_result = None
    try:
        request = DataRequest(
            target.identity,
            target.start,
            target.end,
            schema_requirement=target.identity.record_schema_id,
            lifecycle_policy=LifecyclePolicy.VALID_ONLY,
            coverage_policy="strict",
            ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
        )
        gateway = DataGateway(
            Catalog(connection=connection),
            ordering_providers=(BYBIT_ORDERING_PROVIDER,),
        )
        scan = gateway.scan(request, batch_size=config.batch_size)
        if target.golden.candle is None:
            observation = observe_scan(scan)
        else:
            observation, candle_result = observe_scan_with_candles(scan, target.golden.candle)
    finally:
        if scan is not None and getattr(scan, "completed_metadata", None) is None:
            scan.close()
        connection.close()

    mismatches = golden_field_mismatches(target.golden, observation)
    if target.golden.candle is not None:
        mismatches = mismatches + candle_field_mismatches(target.golden.candle, candle_result)
    if mismatches:
        raise VerificationMismatch(target, observation, mismatches, candle_result)
    return target, observation, format_observation(observation, target.golden, candle_result)


__all__ = [
    "ALL_PARTITION_STATES",
    "CERTIFIER_CODE_REF",
    "DATASET_ORIGIN",
    "DATASET_TRANSFORM",
    "PRODUCER_ID",
    "Check",
    "HarnessConfig",
    "HarnessFailure",
    "HarnessRunFailure",
    "PreflightResult",
    "RunReport",
    "Target",
    "VerificationMismatch",
    "candle_field_mismatches",
    "collect_preflight",
    "format_observation",
    "inspect_vertical",
    "observe_scan_with_candles",
    "run_vertical",
    "verify_vertical",
]
