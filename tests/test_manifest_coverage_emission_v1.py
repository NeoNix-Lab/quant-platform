#!/usr/bin/env python3
"""Functional and structural tests for manifest + coverage emission v1."""

from __future__ import annotations

import ast
import hashlib
import json
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from semantic_validator import (  # noqa: E402
    _parse_ts,
    check_coverage_manifest,
    check_dataset_manifest,
    check_partition_manifest,
    reconstruct_catalog_coverage,
)
from quant_platform.data import (  # noqa: E402
    DataIntegrityError,
    DatasetIdentity,
    Instant,
    ManifestValidationError,
    TradeRecord,
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
    materialize_trade_v1,
)
from quant_platform.data.materializer import ParquetMaterialization  # noqa: E402
from quant_platform.ordering import OrderingProvider  # noqa: E402
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BybitTradeV1EligibilityError,
    build_bybit_trade_v1_source_extract_coverage,
    materialize_bybit_trade_v1,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
KRAKEN_IDENTITY = DatasetIdentity("canonical", "trades", "kraken", "XBT-USD", "trade-v1")
CREATED = "2026-08-31T10:00:00Z"
START = "2024-01-15T00:00:00Z"
END = "2024-01-16T00:00:00Z"


def trade(identity: DatasetIdentity, timestamp: str, trade_id: str = "1") -> TradeRecord:
    return TradeRecord(
        venue=identity.venue,
        instrument=identity.instrument,
        exchange_ts=Instant.parse(timestamp),
        price="100.00",
        size="0.5000",
        aggressor_side="buy",
        receive_ts=None,
        trade_id=trade_id,
        sequence=None,
    )


def source_extract_coverage(
    *,
    coverage_id: str = "bybit-day",
    start: str = START,
    end: str = END,
    partition_key: str = "dt=2024-01-15",
    revision: int = 1,
    evidence: str = "explicit deterministic source extract",
) -> dict[str, object]:
    return {
        "coverage_id": coverage_id,
        "supersedes": None,
        "created_at": CREATED,
        "acquisition": {
            "basis": "source_extract",
            "intent_start": START,
            "intent_end": END,
            "source_semantics": "bybit-public-trades-sqlite-v1",
            "mapping": "bybit-sqlite-day-extract-v1",
        },
        "assertions": [
            {
                "assertion_id": "day-complete",
                "start": start,
                "end": end,
                "status": "complete",
                "partitions": [{"partition_key": partition_key, "revision": revision}],
                "evidence": [{"kind": "deterministic_source_extract", "detail": evidence}],
            }
        ],
        "producer": "test-source-extractor",
        "code_ref": "test-ref",
    }


class ManifestCoverageEmissionV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def materialized(
        self,
        identity: DatasetIdentity = IDENTITY,
        records: list[TradeRecord] | None = None,
    ) -> tuple[ParquetMaterialization, object]:
        data_path = self.root / identity.venue / "dt=2024-01-15" / "part-000.parquet"
        if identity == IDENTITY:
            materialization = materialize_bybit_trade_v1(
                data_path,
                records or [],
                dataset_identity=identity,
            )
        else:
            provider = OrderingProvider(
                identity="kraken-trade-v1-test-order-v1",
                satisfies_requirements=frozenset({"trades@1-canonical-total-order-v1"}),
                key=lambda item: (item.exchange_ts, item.trade_id),
                applies_to=lambda candidate: candidate == identity,
            )
            materialization = materialize_trade_v1(
                data_path,
                records or [],
                dataset_identity=identity,
                ordering_provider=provider,
            )
        partition = emit_partition_manifest(
            self.root / identity.venue / "partition-manifest.json",
            materialization,
            dataset_identity=identity,
            dataset_root=self.root / identity.venue,
            partition_key="dt=2024-01-15",
            revision=1,
            rel_path="dt=2024-01-15/part-000.parquet",
            created_at=CREATED,
            closed_at="2026-08-31T10:00:01Z",
            producer="test-materializer",
            code_ref="test-ref",
        )
        return materialization, partition

    def emit_coverage(self, partition: object, **overrides: object) -> object:
        partition_manifests = overrides.pop("partition_manifests", None)
        values = source_extract_coverage(**overrides)
        return emit_coverage_manifest(
            self.root / "coverage-manifest.json",
            dataset_identity=IDENTITY,
            source_dataset_identity=IDENTITY,
            partition_manifests=partition_manifests or [partition.document],  # type: ignore[union-attr]
            **values,
        )

    def test_dataset_and_partition_outputs_match_frozen_schema_and_semantics(self):
        dataset = emit_dataset_manifest(
            self.root / "dataset-manifest.json",
            dataset_identity=IDENTITY,
            created_at=CREATED,
            derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        _, partition = self.materialized(
            records=[trade(IDENTITY, "2024-01-15T12:00:00.123456789Z")]
        )
        self.assert_schema("dataset-manifest-v1", dataset.document)
        self.assert_schema("partition-manifest-v1", partition.document)
        self.assertEqual(check_dataset_manifest(dataset.document), [])
        self.assertEqual(check_partition_manifest(partition.document, dataset.document), [])
        self.assertEqual(partition.document["state"], "closed")
        self.assertEqual(partition.document["row_count"], 1)
        self.assertNotIn("canonical_content_hash_v1", partition.document)
        self.assertIsNotNone(partition.canonical_content_hash_v1)
        self.assertEqual(
            partition.manifest_sha256,
            hashlib.sha256((self.root / "bybit" / "partition-manifest.json").read_bytes()).hexdigest(),
        )

    def test_invalid_dataset_lineage_is_rejected_before_final_file_publication(self):
        parent = DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")
        cases = [
            ("canonical-missing-transform", IDENTITY, [parent], None),
            ("raw-with-transform", parent, None, "raw-transform-v1"),
            ("duplicate-parent", IDENTITY, [parent, parent], "canonicalize-trades-v1"),
            ("self-lineage", IDENTITY, [IDENTITY], "canonicalize-trades-v1"),
        ]
        for name, identity, lineage, transform in cases:
            path = self.root / f"{name}.json"
            with self.subTest(name=name), self.assertRaises(ManifestValidationError):
                emit_dataset_manifest(
                    path,
                    dataset_identity=identity,
                    created_at=CREATED,
                    derived_from=lineage,
                    transform=transform,
                )
            self.assertFalse(path.exists())

    def test_zero_row_partition_and_complete_zero_event_coverage(self):
        materialization, partition = self.materialized()
        self.assertEqual(materialization.row_count, 0)
        self.assertIsNone(partition.document["first_exchange_ts"])
        self.assertIsNone(partition.document["last_exchange_ts"])
        coverage = self.emit_coverage(partition)
        self.assert_schema("coverage-manifest-v1", coverage.document)
        self.assertEqual(check_coverage_manifest(coverage.document), [])
        self.assertEqual(coverage.document["assertions"][0]["status"], "complete")

    def test_observed_bounds_are_checked_after_catalog_coverage_reconstruction(self):
        _, partition = self.materialized(
            records=[
                trade(IDENTITY, "2024-01-15T01:00:00Z", "1"),
                trade(IDENTITY, "2024-01-15T23:00:00Z", "2"),
            ]
        )
        first = emit_coverage_manifest(
            self.root / "coverage-a.json",
            dataset_identity=IDENTITY,
            source_dataset_identity=IDENTITY,
            partition_manifests=[partition.document],
            **source_extract_coverage(
                coverage_id="coverage-a",
                start=START,
                end="2024-01-15T12:00:00Z",
            ),
        )
        second = emit_coverage_manifest(
            self.root / "coverage-b.json",
            dataset_identity=IDENTITY,
            source_dataset_identity=IDENTITY,
            partition_manifests=[partition.document],
            **source_extract_coverage(
                coverage_id="coverage-b",
                start="2024-01-15T12:00:00Z",
                end=END,
            ),
        )
        self.assertTrue(first.path.exists())
        self.assertTrue(second.path.exists())
        folded, violations = reconstruct_catalog_coverage(
            [first.document, second.document], [partition.document]
        )
        self.assertEqual(violations, [])
        self.assertEqual(
            folded[("dt=2024-01-15", 1)],
            (_parse_ts(START), _parse_ts(END)),
        )

        partial = emit_coverage_manifest(
            self.root / "partial-coverage.json",
            dataset_identity=IDENTITY,
            source_dataset_identity=IDENTITY,
            partition_manifests=[partition.document],
            **source_extract_coverage(
                coverage_id="partial-coverage",
                start=START,
                end="2024-01-15T12:00:00Z",
            ),
        )
        _, partial_violations = reconstruct_catalog_coverage(
            [partial.document], [partition.document]
        )
        self.assertEqual(
            [violation.code for violation in partial_violations],
            ["OBSERVED_OUTSIDE_DECLARED"],
        )

    def test_invalid_degenerate_and_submicrosecond_coverage_fails_closed(self):
        _, partition = self.materialized()
        with self.assertRaises(ManifestValidationError):
            self.emit_coverage(partition, start=START, end=START)
        with self.assertRaises(ManifestValidationError):
            self.emit_coverage(partition, start="2024-01-15T00:00:00.0000001Z")
        with self.assertRaises(ManifestValidationError):
            self.emit_coverage(partition, evidence="")

    def test_complete_coverage_requires_explicit_partition_and_source_evidence(self):
        with self.assertRaises(ManifestValidationError):
            emit_coverage_manifest(
                self.root / "missing-partition.json",
                dataset_identity=IDENTITY,
                coverage_id="missing-partition",
                supersedes=None,
                created_at=CREATED,
                acquisition=source_extract_coverage()["acquisition"],
                assertions=source_extract_coverage()["assertions"],
                producer="test-source-extractor",
                code_ref="test-ref",
                source_dataset_identity=IDENTITY,
            )
        with self.assertRaises(ManifestValidationError):
            emit_coverage_manifest(
                self.root / "missing-evidence.json",
                dataset_identity=IDENTITY,
                coverage_id="missing-evidence",
                supersedes=None,
                created_at=CREATED,
                acquisition=source_extract_coverage()["acquisition"],
                assertions=[{
                    **source_extract_coverage()["assertions"][0],  # type: ignore[index]
                    "evidence": [],
                }],
                producer="test-source-extractor",
                code_ref="test-ref",
                source_dataset_identity=IDENTITY,
            )

    def test_identity_revision_and_artifact_evidence_mismatches_fail(self):
        materialization, partition = self.materialized()
        with self.assertRaises(ManifestValidationError):
            emit_partition_manifest(
                self.root / "revision-zero.json",
                materialization,
                dataset_identity=IDENTITY,
                dataset_root=self.root / IDENTITY.venue,
                partition_key="dt=2024-01-15",
                revision=0,
                rel_path="dt=2024-01-15/part-000.parquet",
                created_at=CREATED,
                closed_at=CREATED,
                producer="test-materializer",
                code_ref="test-ref",
            )
        with self.assertRaises(ManifestValidationError):
            emit_partition_manifest(
                self.root / "bad-size.json",
                replace(materialization, file_size_bytes=materialization.file_size_bytes + 1),
                dataset_identity=IDENTITY,
                dataset_root=self.root / IDENTITY.venue,
                partition_key="dt=2024-01-15",
                revision=1,
                rel_path="dt=2024-01-15/part-000.parquet",
                created_at=CREATED,
                closed_at=CREATED,
                producer="test-materializer",
                code_ref="test-ref",
            )
        with self.assertRaises(ManifestValidationError):
            emit_partition_manifest(
                self.root / "bad-hash.json",
                replace(materialization, sha256="0" * 64),
                dataset_identity=IDENTITY,
                dataset_root=self.root / IDENTITY.venue,
                partition_key="dt=2024-01-15",
                revision=1,
                rel_path="dt=2024-01-15/part-000.parquet",
                created_at=CREATED,
                closed_at=CREATED,
                producer="test-materializer",
                code_ref="test-ref",
            )
        kraken_partition = self.materialized(
            KRAKEN_IDENTITY, [trade(KRAKEN_IDENTITY, "2024-01-15T12:00:00Z")]
        )[1]
        with self.assertRaises(ManifestValidationError):
            self.emit_coverage(partition, partition_manifests=[kraken_partition.document])

    def test_partition_manifest_requires_matching_materialization_identity(self):
        materialization, _ = self.materialized()
        self.assertEqual(materialization.dataset_identity, IDENTITY)
        path = self.root / "bybit-as-kraken-partition.json"
        with self.assertRaisesRegex(ManifestValidationError, "materialization dataset identity"):
            emit_partition_manifest(
                path,
                materialization,
                dataset_identity=KRAKEN_IDENTITY,
                dataset_root=self.root / "bybit",
                partition_key="dt=2024-01-15",
                revision=1,
                rel_path="dt=2024-01-15/part-000.parquet",
                created_at=CREATED,
                closed_at=CREATED,
                producer="test-materializer",
                code_ref="test-ref",
            )
        self.assertFalse(path.exists())

    def test_rel_path_must_resolve_to_the_verified_materialization_artifact(self):
        data_path = self.root / "bybit" / "dt=2024-01-15" / "part-actual.parquet"
        materialization = materialize_bybit_trade_v1(
            data_path,
            [trade(IDENTITY, "2024-01-15T12:00:00Z")],
            dataset_identity=IDENTITY,
        )
        manifest_path = self.root / "mismatched-path.json"
        with self.assertRaises(ManifestValidationError):
            emit_partition_manifest(
                manifest_path,
                materialization,
                dataset_identity=IDENTITY,
                dataset_root=self.root / "bybit",
                partition_key="dt=2024-01-15",
                revision=1,
                rel_path="dt=2024-01-15/part-other.parquet",
                created_at=CREATED,
                closed_at=CREATED,
                producer="test-materializer",
                code_ref="test-ref",
            )
        self.assertFalse(manifest_path.exists())

    def test_no_coverage_is_inferred_from_partition_key_or_observed_bounds(self):
        _, partition = self.materialized(
            records=[trade(IDENTITY, "2024-01-15T12:00:00Z")]
        )
        values = source_extract_coverage()
        values["assertions"] = [{
            "assertion_id": "no-coverage",
            "start": START,
            "end": END,
            "status": "uncertain",
            "partitions": [],
            "evidence": [{"kind": "deterministic_source_extract", "detail": "not a completeness claim"}],
        }]
        coverage = emit_coverage_manifest(
            self.root / "uncertain.json",
            dataset_identity=IDENTITY,
            source_dataset_identity=IDENTITY,
            partition_manifests=[partition.document],
            **values,
        )
        self.assertEqual(coverage.document["assertions"][0]["partitions"], [])
        self.assertEqual(coverage.document["assertions"][0]["status"], "uncertain")

    def test_identical_explicit_inputs_have_identical_manifest_bytes_and_hash(self):
        first = emit_dataset_manifest(
            self.root / "dataset-a.json",
            dataset_identity=IDENTITY,
            created_at=CREATED,
            derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        second = emit_dataset_manifest(
            self.root / "dataset-b.json",
            dataset_identity=IDENTITY,
            created_at=CREATED,
            derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
            transform="canonicalize-trades-v1",
        )
        self.assertEqual(first.persisted_bytes, second.persisted_bytes)
        self.assertEqual(first.manifest_sha256, second.manifest_sha256)
        self.assertEqual(first.persisted_bytes, (self.root / "dataset-a.json").read_bytes())

    def test_manifest_emission_document_is_a_defensive_decoded_copy(self):
        _, partition = self.materialized()
        emission = self.emit_coverage(partition)
        document = emission.document
        document["venue"] = "kraken"
        document["assertions"][0]["status"] = "known_gap"

        self.assertEqual(emission.document["venue"], "bybit")
        self.assertEqual(emission.document["assertions"][0]["status"], "complete")
        self.assertEqual(emission.persisted_bytes, emission.path.read_bytes())
        self.assertEqual(
            emission.manifest_sha256,
            hashlib.sha256(emission.persisted_bytes).hexdigest(),
        )

    def test_generic_emitter_proves_second_venue_without_venue_branch(self):
        materialization, partition = self.materialized(
            KRAKEN_IDENTITY,
            [trade(KRAKEN_IDENTITY, "2024-01-15T12:00:00Z")],
        )
        self.assertEqual(materialization.dataset_identity, KRAKEN_IDENTITY)
        values = source_extract_coverage(
            coverage_id="kraken-day",
            evidence="fake Kraken provider explicitly proved its finite source extract",
        )
        values["acquisition"] = {
            **values["acquisition"],
            "source_semantics": "kraken-test-extract-v1",
            "mapping": "kraken-test-trades-v1",
        }
        coverage = emit_coverage_manifest(
            self.root / "kraken-coverage.json",
            dataset_identity=KRAKEN_IDENTITY,
            source_dataset_identity=KRAKEN_IDENTITY,
            partition_manifests=[partition.document],
            **values,
        )
        self.assert_schema("coverage-manifest-v1", coverage.document)
        self.assertEqual(check_coverage_manifest(coverage.document), [])
        self.assertEqual(coverage.document["venue"], "kraken")

    def test_complete_coverage_accepts_only_frozen_eligible_partition_states(self):
        _, partition = self.materialized(
            records=[trade(IDENTITY, "2024-01-15T12:00:00Z")]
        )
        for state in ("closed", "valid", "degraded"):
            evidence = partition.document.copy()
            evidence["state"] = state
            with self.subTest(state=state):
                coverage = self.emit_coverage(
                    partition,
                    partition_manifests=[evidence],
                )
                self.assertEqual(coverage.document["assertions"][0]["status"], "complete")
        for state in ("writing", "invalid", "superseded"):
            evidence = partition.document.copy()
            evidence["state"] = state
            with self.subTest(state=state), self.assertRaises(ManifestValidationError):
                self.emit_coverage(partition, partition_manifests=[evidence])

    def test_inverted_supplied_sequence_bounds_are_rejected_numerically(self):
        _, partition = self.materialized(
            records=[trade(IDENTITY, "2024-01-15T12:00:00Z")]
        )
        evidence = partition.document.copy()
        evidence.update(first_sequence="10", last_sequence="9")
        with self.assertRaisesRegex(ManifestValidationError, "first_sequence"):
            self.emit_coverage(partition, partition_manifests=[evidence])

    def test_bybit_helper_requires_explicit_source_extract_evidence(self):
        payload = build_bybit_trade_v1_source_extract_coverage(
            coverage_id="bybit-day",
            dataset_identity=IDENTITY,
            intent_start=START,
            intent_end=END,
            assertion_id="day-complete",
            assertion_start=START,
            assertion_end=END,
            partition_key="dt=2024-01-15",
            revision=1,
            source_extract_detail="SELECT ... ORDER BY ts, trade_id; explicit finite extract",
            created_at=CREATED,
            producer="bybit-sqlite-importer",
            code_ref="test-ref",
        )
        self.assertIs(payload["source_dataset_identity"], IDENTITY)
        self.assertEqual(payload["acquisition"]["basis"], "source_extract")  # type: ignore[index]
        self.assertEqual(payload["assertions"][0]["status"], "complete")  # type: ignore[index]
        with self.assertRaises(BybitTradeV1EligibilityError):
            build_bybit_trade_v1_source_extract_coverage(
                dataset_identity=KRAKEN_IDENTITY,
                coverage_id="kraken-day",
                intent_start=START,
                intent_end=END,
                assertion_id="day-complete",
                assertion_start=START,
                assertion_end=END,
                partition_key="dt=2024-01-15",
                revision=1,
                source_extract_detail="wrong source identity",
                created_at=CREATED,
                producer="test",
                code_ref="test-ref",
            )

    def test_bybit_bound_source_evidence_cannot_be_emitted_for_kraken(self):
        payload = build_bybit_trade_v1_source_extract_coverage(
            dataset_identity=IDENTITY,
            coverage_id="bybit-bound",
            intent_start=START,
            intent_end=END,
            assertion_id="day-complete",
            assertion_start=START,
            assertion_end=END,
            partition_key="dt=2024-01-15",
            revision=1,
            source_extract_detail="explicit Bybit extract evidence",
            created_at=CREATED,
            producer="bybit-source",
            code_ref="test-ref",
        )
        with self.assertRaisesRegex(ManifestValidationError, "identity"):
            emit_coverage_manifest(
                self.root / "bybit-as-kraken.json",
                dataset_identity=KRAKEN_IDENTITY,
                partition_manifests=[],
                **payload,
            )
        self.assertFalse((self.root / "bybit-as-kraken.json").exists())

    def test_source_identity_binding_is_required(self):
        path = self.root / "missing-source-identity.json"
        values = source_extract_coverage()
        with self.assertRaises(TypeError):
            emit_coverage_manifest(
                path,
                dataset_identity=IDENTITY,
                partition_manifests=[],
                **values,
            )
        self.assertFalse(path.exists())

    def test_stripped_bybit_source_identity_cannot_be_emitted_for_kraken(self):
        payload = build_bybit_trade_v1_source_extract_coverage(
            dataset_identity=IDENTITY,
            coverage_id="stripped-bybit",
            intent_start=START,
            intent_end=END,
            assertion_id="day-complete",
            assertion_start=START,
            assertion_end=END,
            partition_key="dt=2024-01-15",
            revision=1,
            source_extract_detail="explicit Bybit extract evidence",
            created_at=CREATED,
            producer="bybit-source",
            code_ref="test-ref",
        )
        payload.pop("source_dataset_identity")
        _, kraken_partition = self.materialized(
            KRAKEN_IDENTITY,
            [trade(KRAKEN_IDENTITY, "2024-01-15T12:00:00Z")],
        )
        path = self.root / "stripped-bybit-as-kraken.json"
        with self.assertRaises(TypeError):
            emit_coverage_manifest(
                path,
                dataset_identity=KRAKEN_IDENTITY,
                partition_manifests=[kraken_partition.document],
                **payload,
            )
        self.assertFalse(path.exists())

    def test_generic_data_code_does_not_import_source_adapters(self):
        data_root = ROOT / "src" / "quant_platform" / "data"
        for path in data_root.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertFalse(
                        node.module and "source_adapters" in node.module,
                        f"generic data module imports source adapters: {path}",
                    )
                elif isinstance(node, ast.Import):
                    self.assertFalse(
                        any("source_adapters" in alias.name for alias in node.names),
                        f"generic data module imports source adapters: {path}",
                    )

    def assert_schema(self, name: str, document: dict[str, object]) -> None:
        schema = json.loads((ROOT / "schemas" / f"{name}.json").read_text(encoding="utf-8"))
        errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(document))
        self.assertEqual(errors, [], [error.message for error in errors])


if __name__ == "__main__":
    unittest.main()
