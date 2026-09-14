#!/usr/bin/env python3
"""Operator CLI for the Application-owned Bybit conformity vertical."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application import (  # noqa: E402
    Check,
    HarnessConfig,
    HarnessFailure,
    HarnessRunFailure,
    PreflightResult,
    RunReport,
    Target,
    VerificationMismatch,
    collect_preflight,
    format_observation,
    inspect_vertical,
    run_vertical,
    verify_vertical,
)
from quant_platform.application.conformity import _target_from_golden  # noqa: E402

_MISSING = object()


def _env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _resolved_input(
    cli_value: Any,
    *,
    env_names: tuple[str, ...] = (),
    default: Any = _MISSING,
    field: str,
) -> Any:
    if cli_value is not None:
        return cli_value
    value = _env_value(*env_names)
    if value is not None:
        return value
    if default is not _MISSING:
        return default
    raise HarnessFailure(f"{field} is not configured")


def _config_from_namespace(args: argparse.Namespace) -> HarnessConfig:
    sqlite_value = _resolved_input(
        args.sqlite,
        env_names=("CONFORMITY_E2E_SQLITE", "BYBIT_HISTORICAL_SQLITE"),
        default=None,
        field="sqlite",
    )
    dsn_value = _resolved_input(
        args.dsn,
        env_names=("CONFORMITY_E2E_DSN", "DATA_GATEWAY_TEST_DSN"),
        default="",
        field="dsn",
    )
    storage_value = _resolved_input(
        args.storage_root,
        env_names=("CONFORMITY_E2E_STORAGE_ROOT", "MARKETDATA_STORAGE_ROOT"),
        default=None,
        field="storage_root",
    )
    return HarnessConfig(
        sqlite_path=None if sqlite_value is None else Path(sqlite_value),
        dsn=dsn_value,
        storage_root=None if storage_value is None else Path(storage_value),
        storage_root_id=_resolved_input(
            args.storage_root_id,
            env_names=("CONFORMITY_E2E_STORAGE_ROOT_ID",),
            default="hot",
            field="storage_root_id",
        ),
        golden_path=Path(
            _resolved_input(
                args.golden,
                default=str(ROOT / "fixtures" / "conformity" / "golden-bybit-btcusdt-2024-01-15.json"),
                field="golden",
            )
        ),
        batch_size=_resolved_input(args.batch_size, default=65_536, field="batch_size"),
        legacy_source=args.legacy_source,
    )


def _print_preflight(result: PreflightResult) -> None:
    for check in result.checks:
        print(f"{check.name + ':':12} {'OK' if check.passed else 'FAIL'}  {check.detail}")
    print()
    print("PREFLIGHT: PASS" if result.passed else "PREFLIGHT: FAIL")


def _print_run(report: RunReport) -> None:
    materialization = report.materialization
    print("RUN: PASS")
    print(f"source rows:       {report.source_row_count}")
    print(f"source fingerprint: {report.source_fingerprint_sha256}")
    print(f"artifact:           {materialization.path}")
    print(f"artifact sha256:    {materialization.physical_artifact_hash}")
    print(f"canonical hash:     {materialization.canonical_content_hash_v1}")
    print(f"dataset manifest:   {report.dataset_manifest_sha256}")
    print(f"partition manifest: {report.partition_manifest_sha256}")
    print(f"coverage manifest:  {report.coverage_manifest_sha256}")
    print(f"S13:                {report.certification.certification.status}")
    print(f"S14:                {report.eligibility.state}")


def _print_run_failure(failure: HarnessRunFailure) -> None:
    print("RUN: FAIL", file=sys.stderr)
    print(f"phase: {failure.phase}", file=sys.stderr)
    print(f"reason: {failure.cause}", file=sys.stderr)
    if failure.target is not None:
        print("durable evidence available:", file=sys.stderr)
        for path in (
            failure.target.artifact_path,
            failure.target.dataset_manifest_path,
            failure.target.partition_manifest_path,
            failure.target.coverage_manifest_path,
        ):
            print(f"  {'PRESENT' if path.exists() else 'absent'} {path}", file=sys.stderr)


def _print_inspect(
    target: Target,
    dataset: Any,
    partitions: tuple[Any, ...],
    manifests: tuple[dict[str, Any], ...],
) -> None:
    print("INSPECT: PASS")
    print(f"dataset identity:  {dataset.identity.stable_dict()}")
    print(f"dataset id:        {dataset.catalog_dataset_id}")
    print(f"dataset rel_root:  {dataset.rel_root}")
    print(f"dataset manifest:  {dataset.manifest_sha256}")
    for partition in partitions:
        print("PARTITION")
        print(f"  id:               {partition.catalog_partition_id}")
        print(f"  key/revision:     {partition.natural_identity.partition_key} / {partition.natural_identity.revision}")
        print(f"  state:             {partition.state}")
        print(f"  storage root:      {partition.storage_root_id}")
        print(f"  storage locator:   {Path(partition.storage_root) / dataset.rel_root / partition.rel_path}")
        print(f"  row count:         {partition.row_count}")
        print(f"  content sha256:    {partition.content_sha256}")
        print(f"  manifest sha256:   {partition.manifest_sha256}")
        print(f"  producer:          {partition.producer}")
        print(f"  code_ref:          {partition.code_ref}")
        print(f"  declared coverage: {partition.ts_start} .. {partition.ts_end}")
    for item in manifests:
        print(f"manifest {item['kind']}: {item['path']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="conformity_e2e",
        description="Human E2E operator harness for Bybit BTCUSDT trade-v1.",
    )
    parser.add_argument("command", choices=("preflight", "run", "inspect", "verify"))
    parser.add_argument("--sqlite", default=None, help="read-only Bybit historical SQLite source")
    parser.add_argument("--dsn", default=None, help="PostgreSQL DSN; otherwise standard PG environment is used")
    parser.add_argument("--storage-root", default=None, help="absolute storage root registered in the catalog")
    parser.add_argument("--storage-root-id", default=None, help="catalog storage_root_id (default: hot)")
    parser.add_argument("--golden", default=None, help="Golden expectation fixture")
    parser.add_argument("--batch-size", type=int, default=None, help="bounded DataGateway batch size")
    parser.add_argument(
        "--legacy-source",
        action="store_true",
        help="opt into the reviewed legacy Bybit SQLite source access path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = _config_from_namespace(args)
    try:
        if args.command == "preflight":
            result = collect_preflight(config)
            _print_preflight(result)
            return 0 if result.passed else 1
        if args.command == "run":
            _print_run(run_vertical(config))
            return 0
        if args.command == "inspect":
            _print_inspect(*inspect_vertical(config))
            return 0
        _target, _observation, rendered = verify_vertical(config)
        print(rendered)
        print("GOLDEN E2E: PASS")
        return 0
    except VerificationMismatch as exc:
        print(format_observation(exc.observation, exc.target.golden), file=sys.stderr)
        print("GOLDEN E2E: FAIL", file=sys.stderr)
        for mismatch in exc.mismatches:
            print(f"  - {mismatch}", file=sys.stderr)
        return 1
    except HarnessRunFailure as exc:
        _print_run_failure(exc)
        return 1
    except Exception as exc:
        label = args.command.upper()
        print(f"{label}: FAIL", file=sys.stderr)
        print(f"reason: {exc}", file=sys.stderr)
        return 1


__all__ = [
    "Check",
    "HarnessConfig",
    "HarnessFailure",
    "HarnessRunFailure",
    "PreflightResult",
    "RunReport",
    "Target",
    "VerificationMismatch",
    "_config_from_namespace",
    "_target_from_golden",
    "build_parser",
    "collect_preflight",
    "inspect_vertical",
    "main",
    "run_vertical",
    "verify_vertical",
]


if __name__ == "__main__":
    raise SystemExit(main())
