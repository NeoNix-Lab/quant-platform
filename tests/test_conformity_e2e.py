#!/usr/bin/env python3
"""Focused command and architecture tests for the human E2E harness."""

from __future__ import annotations

import ast
from contextlib import contextmanager
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
import sys

sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "src"))

import conformity_e2e as harness  # noqa: E402
from quant_platform.data.models import DatasetNotFound  # noqa: E402


class _CatalogConnection:
    def close(self):
        pass


class _PreflightCatalog:
    def __init__(self, connection):
        pass

    def resolve_dataset(self, identity):
        if identity.layer == "raw":
            return object()
        raise DatasetNotFound("not present in controlled catalog")


def _config(
    storage_root: Path,
    sqlite_path: Path | None = None,
    *,
    legacy_source: bool = False,
) -> harness.HarnessConfig:
    return harness.HarnessConfig(
        sqlite_path=sqlite_path,
        dsn="controlled-test-dsn",
        storage_root=storage_root,
        storage_root_id="hot",
        golden_path=ROOT / "fixtures" / "conformity" / "golden-bybit-btcusdt-2024-01-15.json",
        legacy_source=legacy_source,
    )


def _make_source(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            "CREATE TABLE trades (category TEXT, symbol TEXT, trade_id TEXT, "
            "trade_time_ms INTEGER, trade_time_utc TEXT, side TEXT, size TEXT, price TEXT)"
        )
        connection.commit()
    finally:
        connection.close()


@contextmanager
def _preflight_dependencies():
    with patch.object(harness, "_connect_catalog", return_value=_CatalogConnection()), \
         patch.object(harness, "Catalog", _PreflightCatalog):
        yield


class ConformityE2ETest(unittest.TestCase):
    def test_default_preflight_uses_only_the_ordinary_source_opener(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            source = root / "source.sqlite"
            _make_source(source)
            config = _config(root / "storage", source)
            config.storage_root.mkdir()
            with _preflight_dependencies(), \
                 patch.object(
                     harness,
                     "open_bybit_historical_source",
                     wraps=harness.open_bybit_historical_source,
                 ) as ordinary, \
                 patch.object(harness, "open_bybit_historical_legacy_source") as legacy:
                result = harness.collect_preflight(config)

            self.assertTrue(result.passed)
            ordinary.assert_called_once_with(source)
            legacy.assert_not_called()

    def test_explicit_legacy_preflight_uses_only_the_legacy_source_opener(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            source = root / "source.sqlite"
            _make_source(source)
            config = _config(root / "storage", source, legacy_source=True)
            config.storage_root.mkdir()
            with _preflight_dependencies(), \
                 patch.object(
                     harness,
                     "open_bybit_historical_legacy_source",
                     wraps=harness.open_bybit_historical_legacy_source,
                 ) as legacy, \
                 patch.object(harness, "open_bybit_historical_source") as ordinary:
                result = harness.collect_preflight(config)

            self.assertTrue(result.passed)
            legacy.assert_called_once_with(source)
            ordinary.assert_not_called()
            self.assertFalse((config.storage_root / "canonical").exists())

    def test_preflight_does_not_resolve_a_fake_raw_parent(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            source = root / "source.sqlite"
            _make_source(source)
            config = _config(root / "storage", source)
            config.storage_root.mkdir()
            resolved = []

            class Catalog:
                def __init__(self, connection):
                    pass

                def resolve_dataset(self, identity):
                    resolved.append(identity)
                    raise DatasetNotFound("target is absent")

            with patch.object(harness, "_connect_catalog", return_value=_CatalogConnection()), \
                 patch.object(harness, "Catalog", Catalog):
                result = harness.collect_preflight(config)

        self.assertTrue(result.passed)
        self.assertEqual([item.layer for item in resolved], ["canonical"])

    def test_cli_legacy_source_is_explicit_and_defaults_off(self):
        ordinary_config = harness._config_from_namespace(
            harness.build_parser().parse_args(["preflight"])
        )
        legacy_config = harness._config_from_namespace(
            harness.build_parser().parse_args(["preflight", "--legacy-source"])
        )
        self.assertFalse(ordinary_config.legacy_source)
        self.assertTrue(legacy_config.legacy_source)

    def test_preflight_success_is_bounded_and_does_not_mutate_source(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            source = root / "source.sqlite"
            _make_source(source)
            config = _config(root / "storage", source)
            config.storage_root.mkdir()
            with _preflight_dependencies():
                result = harness.collect_preflight(config)

            self.assertTrue(result.passed)
            self.assertFalse((config.storage_root / "canonical").exists())
            connection = harness.open_bybit_historical_source(source)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    connection.execute("CREATE TABLE must_remain_read_only (x INTEGER)")
            finally:
                connection.close()

    def test_preflight_missing_source_fails_without_writing(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            config = _config(root / "storage", root / "missing.sqlite")
            config.storage_root.mkdir()
            with _preflight_dependencies():
                result = harness.collect_preflight(config)

            self.assertFalse(result.passed)
            source_check = next(check for check in result.checks if check.name == "source")
            self.assertFalse(source_check.passed)
            self.assertEqual(tuple(config.storage_root.iterdir()), ())

    def test_ambiguous_existing_output_is_refused_before_mutation(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            config = _config(root / "storage")
            target = harness._target_from_golden(config)
            target.dataset_manifest_path.parent.mkdir(parents=True)
            target.dataset_manifest_path.write_text("existing", encoding="utf-8")
            passed = harness.PreflightResult(
                target,
                (harness.Check("rerun", True, "controlled"),),
            )
            with patch.object(harness, "collect_preflight", return_value=passed):
                with self.assertRaises(harness.HarnessRunFailure) as caught:
                    harness.run_vertical(config)
            self.assertEqual(caught.exception.phase, "PREFLIGHT")
            self.assertEqual(target.dataset_manifest_path.read_text(encoding="utf-8"), "existing")

    def test_run_calls_existing_seams_in_order(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            config = _config(root / "storage", root / "source.sqlite")
            target = harness._target_from_golden(config)
            passed = harness.PreflightResult(
                target,
                (harness.Check("all", True, "controlled"),),
            )
            calls: list[str] = []
            dataset_manifest_kwargs = {}

            class Accumulator:
                def evidence(self, *args):
                    calls.append("source-evidence")
                    return SimpleNamespace(
                        source_row_count=2,
                        source_fingerprint_sha256="f" * 64,
                        detail="controlled source evidence",
                    )

            class Materialization:
                path = target.artifact_path
                row_count = 2
                physical_artifact_hash = "a" * 64
                canonical_content_hash_v1 = "c" * 64

            class Emission:
                manifest_sha256 = "m" * 64
                document = {"controlled": True}

            class FakeSourceConnection:
                def close(self):
                    calls.append("source-close")

            class FakeS13:
                def __init__(self, *args):
                    calls.append("s13-init")

                def run(self, evidence):
                    calls.append("s13-run")
                    return SimpleNamespace(certification=SimpleNamespace(status="pass"))

            class FakeS14:
                def __init__(self, *args):
                    calls.append("s14-init")

                def publish(self, evidence):
                    calls.append("s14-publish")
                    return SimpleNamespace(state="valid")

            def fake_iterator(*args, **kwargs):
                calls.append("source-iterator")
                yield "row-1"
                yield "row-2"

            def fake_materialize(path, records, **kwargs):
                calls.append("materialize")
                tuple(records)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"controlled artifact")
                return Materialization()

            def fake_emit_dataset(*args, **kwargs):
                calls.append("dataset-manifest")
                dataset_manifest_kwargs.update(kwargs)
                return Emission()

            def fake_emit_partition(*args, **kwargs):
                calls.append("partition-manifest")
                return Emission()

            def fake_build_coverage(*args, **kwargs):
                calls.append("coverage-builder")
                return {"source_dataset_identity": target.identity, "coverage_id": "controlled", "supersedes": None, "created_at": "2026-09-03T00:00:00Z", "acquisition": {}, "assertions": (), "producer": "controlled", "code_ref": "controlled"}

            def fake_emit_coverage(*args, **kwargs):
                calls.append("coverage-manifest")
                return Emission()

            with patch.object(harness, "collect_preflight", return_value=passed), \
                 patch.object(harness, "BybitHistoricalExtractAccumulator", Accumulator), \
                 patch.object(
                     harness,
                     "open_bybit_historical_source",
                     return_value=FakeSourceConnection(),
                 ) as ordinary_opener, \
                 patch.object(harness, "open_bybit_historical_legacy_source") as legacy_opener, \
                 patch.object(harness, "iter_bybit_historical_trade_rows", fake_iterator), \
                 patch.object(harness, "canonicalize_bybit_historical_trade_v1", lambda row: row), \
                 patch.object(harness, "materialize_bybit_trade_v1", fake_materialize), \
                 patch.object(harness, "emit_dataset_manifest", fake_emit_dataset), \
                 patch.object(harness, "emit_partition_manifest", fake_emit_partition), \
                 patch.object(harness, "build_bybit_trade_v1_source_extract_coverage", fake_build_coverage), \
                 patch.object(harness, "emit_coverage_manifest", fake_emit_coverage), \
                 patch.object(harness, "_connect_catalog", return_value=FakeSourceConnection()), \
                 patch.object(harness, "PublicationCertification", FakeS13), \
                 patch.object(harness, "PublicationEligibilityBridge", FakeS14):
                report = harness.run_vertical(config)

            self.assertEqual(report.eligibility.state, "valid")
            self.assertEqual(dataset_manifest_kwargs["schema_version"], "dataset-manifest-v2")
            self.assertEqual(dataset_manifest_kwargs["origin"], "source_acquired")
            self.assertNotIn("derived_from", dataset_manifest_kwargs)
            self.assertEqual(dataset_manifest_kwargs["transform"], "canonicalize-trades-v1")
            ordinary_opener.assert_called_once_with(config.sqlite_path)
            legacy_opener.assert_not_called()
            self.assertLess(calls.index("materialize"), calls.index("dataset-manifest"))
            self.assertLess(calls.index("dataset-manifest"), calls.index("coverage-builder"))
            self.assertLess(calls.index("coverage-manifest"), calls.index("s13-init"))
            self.assertLess(calls.index("s13-run"), calls.index("s14-init"))
            self.assertLess(calls.index("s14-init"), calls.index("s14-publish"))

    def test_legacy_run_drains_the_source_inside_the_legacy_context(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            config = _config(root / "storage", root / "source.sqlite", legacy_source=True)
            target = harness._target_from_golden(config)
            passed = harness.PreflightResult(
                target, (harness.Check("all", True, "controlled"),)
            )
            calls: list[str] = []
            context_active = False

            class Accumulator:
                def evidence(self, *args):
                    return SimpleNamespace(
                        source_row_count=2,
                        source_fingerprint_sha256="f" * 64,
                        detail="controlled source evidence",
                    )

            class Materialization:
                path = target.artifact_path
                row_count = 2
                physical_artifact_hash = "a" * 64
                canonical_content_hash_v1 = "c" * 64

            class Emission:
                manifest_sha256 = "m" * 64
                document = {"controlled": True}

            class Connection:
                def close(self):
                    calls.append("source-close")

            class LegacyContext:
                def __enter__(self):
                    nonlocal context_active
                    context_active = True
                    calls.append("legacy-enter")
                    return Connection()

                def __exit__(self, exc_type, exc_value, traceback):
                    nonlocal context_active
                    calls.append("legacy-exit")
                    context_active = False
                    return False

            def source_iterator(*args, **kwargs):
                self.assertTrue(context_active)
                calls.append("source-iterator")
                yield "row-1"
                self.assertTrue(context_active)
                yield "row-2"

            def materialize(path, records, **kwargs):
                self.assertTrue(context_active)
                calls.append("materialize-start")
                tuple(records)
                self.assertTrue(context_active)
                calls.append("materialize-drained")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"controlled artifact")
                return Materialization()

            def build_coverage(*args, **kwargs):
                return {
                    "source_dataset_identity": target.identity,
                    "coverage_id": "controlled",
                    "supersedes": None,
                    "created_at": "2026-09-03T00:00:00Z",
                    "acquisition": {},
                    "assertions": (),
                    "producer": "controlled",
                    "code_ref": "controlled",
                }

            class FakeS13:
                def __init__(self, *args):
                    calls.append("s13-init")

                def run(self, evidence):
                    calls.append("s13-run")
                    return SimpleNamespace(certification=SimpleNamespace(status="pass"))

            class FakeS14:
                def __init__(self, *args):
                    calls.append("s14-init")

                def publish(self, evidence):
                    calls.append("s14-publish")
                    return SimpleNamespace(state="valid")

            with patch.object(harness, "collect_preflight", return_value=passed), \
                 patch.object(harness, "BybitHistoricalExtractAccumulator", Accumulator), \
                 patch.object(harness, "bybit_historical_legacy_read", return_value=LegacyContext()) as legacy_read, \
                 patch.object(harness, "open_bybit_historical_source") as ordinary_opener, \
                 patch.object(harness, "iter_bybit_historical_trade_rows", source_iterator), \
                 patch.object(harness, "canonicalize_bybit_historical_trade_v1", lambda row: row), \
                 patch.object(harness, "materialize_bybit_trade_v1", materialize), \
                 patch.object(harness, "emit_dataset_manifest", lambda *a, **k: Emission()), \
                 patch.object(harness, "emit_partition_manifest", lambda *a, **k: Emission()), \
                 patch.object(harness, "build_bybit_trade_v1_source_extract_coverage", build_coverage), \
                 patch.object(harness, "emit_coverage_manifest", lambda *a, **k: Emission()), \
                 patch.object(harness, "_connect_catalog", return_value=Connection()), \
                 patch.object(harness, "PublicationCertification", FakeS13), \
                 patch.object(harness, "PublicationEligibilityBridge", FakeS14):
                report = harness.run_vertical(config)

            self.assertEqual(report.eligibility.state, "valid")
            legacy_read.assert_called_once_with(config.sqlite_path)
            ordinary_opener.assert_not_called()
            self.assertLess(calls.index("legacy-enter"), calls.index("materialize-drained"))
            self.assertLess(calls.index("materialize-drained"), calls.index("legacy-exit"))
            self.assertLess(calls.index("legacy-exit"), calls.index("s13-init"))

    def test_legacy_drift_fails_in_source_phase_before_downstream_phases(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            config = _config(root / "storage", root / "source.sqlite", legacy_source=True)
            target = harness._target_from_golden(config)
            passed = harness.PreflightResult(
                target, (harness.Check("all", True, "controlled"),)
            )
            downstream_calls: list[str] = []

            class Accumulator:
                def evidence(self, *args):
                    return SimpleNamespace(
                        source_row_count=0,
                        source_fingerprint_sha256="f" * 64,
                        detail="controlled source evidence",
                    )

            class Connection:
                def close(self):
                    pass

            class DriftingContext:
                def __enter__(self):
                    return Connection()

                def __exit__(self, exc_type, exc_value, traceback):
                    raise harness.BybitHistoricalSourceError("controlled source drift")

            class Materialization:
                path = target.artifact_path
                row_count = 0

            def source_iterator(*args, **kwargs):
                if False:
                    yield None

            def materialize(path, records, **kwargs):
                tuple(records)
                return Materialization()

            with patch.object(harness, "collect_preflight", return_value=passed), \
                 patch.object(harness, "BybitHistoricalExtractAccumulator", Accumulator), \
                 patch.object(harness, "bybit_historical_legacy_read", return_value=DriftingContext()), \
                 patch.object(harness, "iter_bybit_historical_trade_rows", source_iterator), \
                 patch.object(harness, "canonicalize_bybit_historical_trade_v1", lambda row: row), \
                 patch.object(harness, "materialize_bybit_trade_v1", materialize), \
                 patch.object(harness, "emit_dataset_manifest", lambda *a, **k: downstream_calls.append("manifest")), \
                 patch.object(harness, "build_bybit_trade_v1_source_extract_coverage", lambda *a, **k: downstream_calls.append("coverage")), \
                 patch.object(harness, "PublicationCertification", lambda *a, **k: downstream_calls.append("s13")), \
                 patch.object(harness, "PublicationEligibilityBridge", lambda *a, **k: downstream_calls.append("s14")):
                with self.assertRaises(harness.HarnessRunFailure) as caught:
                    harness.run_vertical(config)

            self.assertEqual(caught.exception.phase, "SOURCE")
            self.assertIn("controlled source drift", str(caught.exception.cause))
            self.assertEqual(downstream_calls, [])

    def test_legacy_preflight_open_failure_is_visible_and_read_only(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            source = root / "source.sqlite"
            _make_source(source)
            config = _config(root / "storage", source, legacy_source=True)
            config.storage_root.mkdir()
            with _preflight_dependencies(), \
                 patch.object(
                     harness,
                     "open_bybit_historical_legacy_source",
                     side_effect=harness.BybitHistoricalSourceError(
                         "controlled legacy open failure"
                     ),
                 ) as legacy, \
                 patch.object(harness, "open_bybit_historical_source") as ordinary:
                result = harness.collect_preflight(config)

            self.assertFalse(result.passed)
            source_check = next(check for check in result.checks if check.name == "source")
            self.assertFalse(source_check.passed)
            self.assertIn("controlled legacy open failure", source_check.detail)
            legacy.assert_called_once_with(source)
            ordinary.assert_not_called()
            self.assertEqual(tuple(config.storage_root.iterdir()), ())

    def _run_with_s13_status(self, status):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            config = _config(root / "storage", root / "source.sqlite")
            target = harness._target_from_golden(config)
            passed = harness.PreflightResult(
                target, (harness.Check("all", True, "controlled"),)
            )
            calls = []

            class Accumulator:
                def evidence(self, *args):
                    return SimpleNamespace(
                        source_row_count=0,
                        source_fingerprint_sha256="f" * 64,
                        detail="controlled source evidence",
                    )

            class Materialization:
                path = target.artifact_path
                row_count = 0
                physical_artifact_hash = "a" * 64
                canonical_content_hash_v1 = "c" * 64

            class Emission:
                manifest_sha256 = "m" * 64
                document = {}

            class Connection:
                def close(self):
                    pass

            class FakeS13:
                def __init__(self, *args):
                    calls.append("s13-init")

                def run(self, evidence):
                    calls.append("s13-run")
                    return SimpleNamespace(
                        certification=SimpleNamespace(status=status)
                    )

            class FakeS14:
                def __init__(self, *args):
                    calls.append("s14-init")

                def publish(self, evidence):
                    calls.append("s14-publish")
                    return SimpleNamespace(state="valid")

            def source_iterator(*args, **kwargs):
                if False:
                    yield None

            def materialize(path, records, **kwargs):
                tuple(records)
                return Materialization()

            def build_coverage(*args, **kwargs):
                return {
                    "source_dataset_identity": target.identity,
                    "coverage_id": "controlled",
                    "supersedes": None,
                    "created_at": "2026-09-03T00:00:00Z",
                    "acquisition": {},
                    "assertions": (),
                    "producer": "controlled",
                    "code_ref": "controlled",
                }

            with patch.object(harness, "collect_preflight", return_value=passed), \
                 patch.object(harness, "BybitHistoricalExtractAccumulator", Accumulator), \
                 patch.object(harness, "open_bybit_historical_source", return_value=Connection()), \
                 patch.object(harness, "iter_bybit_historical_trade_rows", source_iterator), \
                 patch.object(harness, "canonicalize_bybit_historical_trade_v1", lambda row: row), \
                 patch.object(harness, "materialize_bybit_trade_v1", materialize), \
                 patch.object(harness, "emit_dataset_manifest", lambda *a, **k: Emission()), \
                 patch.object(harness, "emit_partition_manifest", lambda *a, **k: Emission()), \
                 patch.object(harness, "build_bybit_trade_v1_source_extract_coverage", build_coverage), \
                 patch.object(harness, "emit_coverage_manifest", lambda *a, **k: Emission()), \
                 patch.object(harness, "_connect_catalog", return_value=Connection()), \
                 patch.object(harness, "PublicationCertification", FakeS13), \
                 patch.object(harness, "PublicationEligibilityBridge", FakeS14):
                try:
                    result = harness.run_vertical(config)
                except harness.HarnessRunFailure as exc:
                    return exc, calls
                return result, calls

    def test_s13_pass_allows_exactly_one_s14_call(self):
        result, calls = self._run_with_s13_status("pass")
        self.assertNotIsInstance(result, harness.HarnessRunFailure)
        self.assertEqual(calls.count("s14-init"), 1)

    def test_s13_fail_stops_before_s14_and_reports_s13(self):
        result, calls = self._run_with_s13_status("fail")
        self.assertIsInstance(result, harness.HarnessRunFailure)
        self.assertEqual(result.phase, "S13")
        self.assertNotIn("s14-init", calls)

    def test_s13_unexpected_status_fails_closed_before_s14(self):
        result, calls = self._run_with_s13_status("future-status")
        self.assertIsInstance(result, harness.HarnessRunFailure)
        self.assertEqual(result.phase, "S13")
        self.assertNotIn("s14-init", calls)

    def test_s13_failure_main_returns_nonzero_without_run_success(self):
        failure = harness.HarnessRunFailure("S13", ValueError("certification status is fail"))
        stdout = StringIO()
        stderr = StringIO()
        with patch.object(harness, "run_vertical", side_effect=failure), \
             redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = harness.main(["run"])
        self.assertNotEqual(exit_code, 0)
        self.assertIn("RUN: FAIL", stderr.getvalue())
        self.assertIn("phase: S13", stderr.getvalue())
        self.assertNotIn("RUN: PASS", stdout.getvalue() + stderr.getvalue())

    def test_run_failure_reports_phase_and_does_not_continue(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            config = _config(root / "storage", root / "source.sqlite")
            target = harness._target_from_golden(config)
            passed = harness.PreflightResult(target, (harness.Check("all", True, "controlled"),))
            with patch.object(harness, "collect_preflight", return_value=passed), \
                 patch.object(harness, "BybitHistoricalExtractAccumulator"), \
                 patch.object(harness, "open_bybit_historical_source", return_value=_CatalogConnection()), \
                 patch.object(harness, "materialize_bybit_trade_v1", side_effect=ValueError("controlled materializer failure")), \
                 patch.object(harness, "emit_dataset_manifest") as emit_dataset:
                with self.assertRaises(harness.HarnessRunFailure) as caught:
                    harness.run_vertical(config)
            self.assertEqual(caught.exception.phase, "MATERIALIZE")
            emit_dataset.assert_not_called()

    def test_verify_uses_datagateway_scan_and_rejects_golden_mismatch(self):
        config = _config(Path.cwd())
        calls: list[str] = []

        class Scan:
            completed_metadata = object()

            def close(self):
                calls.append("scan-close")

        class FakeGateway:
            def __init__(self, *args, **kwargs):
                pass

            def scan(self, request, **kwargs):
                calls.append("gateway-scan")
                return Scan()

        observation = object()
        with patch.object(harness, "_connect_catalog", return_value=_CatalogConnection()), \
             patch.object(harness, "DataGateway", FakeGateway), \
             patch.object(harness, "observe_scan", return_value=observation), \
             patch.object(harness, "format_observation", return_value="controlled observation"), \
             patch.object(harness, "golden_field_mismatches", return_value=("row_count",)):
            with self.assertRaises(harness.VerificationMismatch) as caught:
                harness.verify_vertical(config)
        self.assertEqual(calls, ["gateway-scan"])
        self.assertEqual(caught.exception.mismatches, ("row_count",))

    def test_verify_success_is_only_reported_after_scan_comparison(self):
        config = _config(Path.cwd())
        calls: list[str] = []

        class Scan:
            completed_metadata = object()

        class FakeGateway:
            def __init__(self, *args, **kwargs):
                pass

            def scan(self, request, **kwargs):
                calls.append("gateway-scan")
                return Scan()

        with patch.object(harness, "_connect_catalog", return_value=_CatalogConnection()), \
             patch.object(harness, "DataGateway", FakeGateway), \
             patch.object(harness, "observe_scan", return_value=object()), \
             patch.object(harness, "format_observation", return_value="controlled observation"), \
             patch.object(harness, "golden_field_mismatches", return_value=()):
            _target, _observation, rendered = harness.verify_vertical(config)
        self.assertEqual(rendered, "controlled observation")
        self.assertEqual(calls, ["gateway-scan"])

    def test_inspect_reads_catalog_and_durable_manifests_without_writing(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            config = _config(root / "storage")
            target = harness._target_from_golden(config)
            target.dataset_root.mkdir(parents=True)
            dataset = SimpleNamespace(
                identity=target.identity,
                catalog_dataset_id="dataset-id",
                rel_root="canonical/trades/bybit/BTCUSDT/trade-v1",
                manifest_sha256="d" * 64,
            )
            partition = SimpleNamespace(
                natural_identity=SimpleNamespace(partition_key=target.partition_key),
                catalog_partition_id="partition-id",
                storage_root_id="hot",
                storage_root=str(root / "storage"),
                rel_path=target.rel_path,
                row_count=0,
                content_sha256="a" * 64,
                manifest_sha256="m" * 64,
                state="valid",
                producer="producer",
                code_ref="code",
                ts_start=target.start,
                ts_end=target.end,
            )
            for name in ("dataset", "partition", "coverage"):
                (target.dataset_root / f"{name}-manifest.json").write_text(
                    '{"controlled": true}', encoding="utf-8"
                )

            class FakeCatalog:
                def __init__(self, connection):
                    pass

                def resolve_dataset(self, identity):
                    return dataset

                def select_partitions(self, *args):
                    return [partition]

            with patch.object(harness, "_connect_catalog", return_value=_CatalogConnection()), \
                 patch.object(harness, "Catalog", FakeCatalog), \
                 patch.object(harness, "emit_dataset_manifest") as emit_dataset, \
                 patch.object(harness, "emit_partition_manifest") as emit_partition, \
                 patch.object(harness, "emit_coverage_manifest") as emit_coverage:
                _target, _dataset, partitions, _manifests = harness.inspect_vertical(config)
            self.assertEqual(len(partitions), 1)
            self.assertEqual({item["kind"] for item in _manifests}, {"dataset", "partition", "coverage"})
            emit_dataset.assert_not_called()
            emit_partition.assert_not_called()
            emit_coverage.assert_not_called()

    def test_inspect_missing_manifest_remains_absent_not_failure(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            config = _config(root / "storage")
            target = harness._target_from_golden(config)
            target.dataset_root.mkdir(parents=True)
            dataset = SimpleNamespace(
                identity=target.identity,
                catalog_dataset_id="dataset-id",
                rel_root="canonical/trades/bybit/BTCUSDT/trade-v1",
                manifest_sha256="d" * 64,
            )
            partition = SimpleNamespace(
                natural_identity=SimpleNamespace(
                    partition_key=target.partition_key, revision=1
                ),
                catalog_partition_id="partition-id",
                storage_root_id="hot",
                storage_root=str(root / "storage"),
                rel_path=target.rel_path,
                row_count=0,
                content_sha256="a" * 64,
                manifest_sha256="m" * 64,
                state="valid",
                producer="producer",
                code_ref="code",
                ts_start=target.start,
                ts_end=target.end,
            )

            class FakeCatalog:
                def __init__(self, connection):
                    pass

                def resolve_dataset(self, identity):
                    return dataset

                def select_partitions(self, *args):
                    return [partition]

            with patch.object(harness, "_connect_catalog", return_value=_CatalogConnection()), \
                 patch.object(harness, "Catalog", FakeCatalog):
                _target, _dataset, _partitions, manifests = harness.inspect_vertical(config)
            self.assertEqual(manifests, ())

    def test_inspect_malformed_manifest_main_fails_with_visible_reason(self):
        with tempfile.TemporaryDirectory() as holder:
            root = Path(holder)
            config = _config(root / "storage")
            target = harness._target_from_golden(config)
            target.dataset_root.mkdir(parents=True)
            (target.dataset_root / "dataset-manifest.json").write_text(
                "{malformed", encoding="utf-8"
            )
            dataset = SimpleNamespace(
                identity=target.identity,
                catalog_dataset_id="dataset-id",
                rel_root="canonical/trades/bybit/BTCUSDT/trade-v1",
                manifest_sha256="d" * 64,
            )
            partition = SimpleNamespace(
                natural_identity=SimpleNamespace(
                    partition_key=target.partition_key, revision=1
                ),
                catalog_partition_id="partition-id",
                storage_root_id="hot",
                storage_root=str(root / "storage"),
                rel_path=target.rel_path,
                row_count=0,
                content_sha256="a" * 64,
                manifest_sha256="m" * 64,
                state="valid",
                producer="producer",
                code_ref="code",
                ts_start=target.start,
                ts_end=target.end,
            )

            class FakeCatalog:
                def __init__(self, connection):
                    pass

                def resolve_dataset(self, identity):
                    return dataset

                def select_partitions(self, *args):
                    return [partition]

            stdout = StringIO()
            stderr = StringIO()
            with patch.object(harness, "_connect_catalog", return_value=_CatalogConnection()), \
                 patch.object(harness, "Catalog", FakeCatalog), \
                 redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = harness.main(
                    ["inspect", "--storage-root", str(root / "storage"), "--dsn", "controlled"]
                )
            self.assertNotEqual(exit_code, 0)
            self.assertIn("INSPECT: FAIL", stderr.getvalue())
            self.assertIn("dataset-manifest.json", stderr.getvalue())
            self.assertIn("malformed or unreadable", stderr.getvalue())
            self.assertNotIn("INSPECT: PASS", stdout.getvalue() + stderr.getvalue())

    def test_harness_has_no_duplicate_semantics_or_physical_verify_reader(self):
        path = ROOT / "tools" / "conformity_e2e.py"
        source = path.read_text(encoding="utf-8")
        ast.parse(source)
        self.assertIn("quant_platform.source_adapters.bybit_historical", source)
        self.assertIn("DataGateway", source)
        self.assertIn("gateway.scan", source)
        for forbidden in ("pyarrow", "ParquetFile", "read_table", "scan_trade_v1", "fetchall"):
            self.assertNotIn(forbidden, source)
        for golden_literal in ("1105145", "553875", "551270"):
            self.assertNotIn(golden_literal, source)

    def test_read_only_commands_do_not_name_mutating_emitters(self):
        tree = ast.parse((ROOT / "tools" / "conformity_e2e.py").read_text(encoding="utf-8"))
        command_functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
        for name in ("collect_preflight", "inspect_vertical", "verify_vertical"):
            text = ast.get_source_segment((ROOT / "tools" / "conformity_e2e.py").read_text(encoding="utf-8"), command_functions[name])
            self.assertIsNotNone(text)
            self.assertNotIn("emit_dataset_manifest", text)
            self.assertNotIn("emit_partition_manifest", text)
            self.assertNotIn("emit_coverage_manifest", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
