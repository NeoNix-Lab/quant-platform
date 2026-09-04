#!/usr/bin/env python3
"""Contract, runtime, and semantic tests for dataset-manifest-v2."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from quant_platform.data.manifests import (  # noqa: E402
    DATASET_MANIFEST_V1,
    DATASET_MANIFEST_V2,
    ManifestValidationError,
    emit_dataset_manifest,
    _validate_dataset_document,
)
from quant_platform.data.models import DatasetIdentity  # noqa: E402
from semantic_validator import check_dataset_manifest  # noqa: E402


V2_SCHEMA = json.loads((ROOT / "schemas" / "dataset-manifest-v2.json").read_text(encoding="utf-8"))
V2_VALIDATOR = Draft202012Validator(V2_SCHEMA, format_checker=FormatChecker())

RAW = DatasetIdentity("raw", "trades", "genericvenue", "BTC-USD", "trade-v1")
CANONICAL = DatasetIdentity("canonical", "trades", "genericvenue", "BTC-USD", "trade-v1")
FEATURES = DatasetIdentity(
    "features", "trade_microstructure", "genericvenue", "BTC-USD", "trade-v1",
    "microstructure", 1,
)


def document(identity: DatasetIdentity, **fields):
    rel_root_parts = [identity.layer, identity.dataset_kind, identity.venue, identity.instrument]
    if identity.layer == "features":
        rel_root_parts.extend(("microstructure", "v1"))
    rel_root_parts.append(identity.record_schema_id)
    result = {
        "schema_version": DATASET_MANIFEST_V2,
        **identity.stable_dict(),
        "rel_root": "/".join(rel_root_parts),
        "created_at": "2026-09-04T00:00:00Z",
    }
    result.update(fields)
    return result


def source_acquired(**fields):
    return document(CANONICAL, origin="source_acquired", transform="canonicalize-trades-v1", **fields)


class DatasetManifestV2Test(unittest.TestCase):
    def assert_schema_valid(self, value):
        self.assertEqual(list(V2_VALIDATOR.iter_errors(value)), [])

    def assert_schema_invalid(self, value):
        self.assertTrue(list(V2_VALIDATOR.iter_errors(value)))

    def assert_runtime_valid(self, value):
        _validate_dataset_document(value)
        self.assertEqual(check_dataset_manifest(value), [])

    def assert_runtime_invalid(self, value):
        with self.assertRaises(ManifestValidationError):
            _validate_dataset_document(value)

    def test_valid_topologies(self):
        values = (
            document(RAW),
            document(
                CANONICAL,
                origin="dataset_derived",
                derived_from=[RAW.stable_dict()],
                transform="canonicalize-trades-v1",
            ),
            source_acquired(),
            document(
                FEATURES,
                origin="dataset_derived",
                derived_from=[CANONICAL.stable_dict()],
                transform="derive-features-v1",
            ),
        )
        for value in values:
            with self.subTest(value=value):
                self.assert_schema_valid(value)
                self.assert_runtime_valid(value)

    def test_v2_emitter_is_deliberate_and_preserves_rel_root(self):
        with tempfile.TemporaryDirectory() as holder:
            path = Path(holder) / "dataset-manifest.json"
            emission = emit_dataset_manifest(
                path,
                dataset_identity=CANONICAL,
                created_at="2026-09-04T00:00:00Z",
                schema_version=DATASET_MANIFEST_V2,
                origin="source_acquired",
                transform="canonicalize-trades-v1",
            )
            value = emission.document
        self.assertEqual(value["schema_version"], DATASET_MANIFEST_V2)
        self.assertEqual(value["origin"], "source_acquired")
        self.assertNotIn("derived_from", value)
        self.assertEqual(value["rel_root"], "canonical/trades/genericvenue/BTC-USD/trade-v1")
        self.assertEqual(CANONICAL.stable_dict().keys(), {"layer", "dataset_kind", "venue", "instrument", "record_schema_id"})

    def test_raw_topology_fields_are_forbidden(self):
        for value in (
            document(RAW, origin="source_acquired"),
            document(RAW, transform="canonicalize-trades-v1"),
            document(RAW, derived_from=[]),
        ):
            with self.subTest(value=value):
                self.assert_schema_invalid(value)
                self.assert_runtime_invalid(value)

    def test_canonical_origin_and_derived_requirements(self):
        values = (
            document(CANONICAL, transform="canonicalize-trades-v1"),
            document(CANONICAL, origin="dataset_derived", transform="canonicalize-trades-v1"),
            document(CANONICAL, origin="dataset_derived", derived_from=[], transform="canonicalize-trades-v1"),
            document(CANONICAL, origin="dataset_derived", derived_from=[RAW.stable_dict()]),
        )
        for value in values:
            with self.subTest(value=value):
                self.assert_schema_invalid(value)
                self.assert_runtime_invalid(value)

    def test_source_acquired_requires_absent_lineage_and_transform(self):
        values = (
            source_acquired(derived_from=[RAW.stable_dict()]),
            document(CANONICAL, origin="source_acquired"),
            source_acquired(derived_from=[]),
            document(CANONICAL, origin="source_acquired", transform=" "),
        )
        for value in values:
            with self.subTest(value=value):
                self.assert_schema_invalid(value)
                self.assert_runtime_invalid(value)

    def test_features_only_allow_dataset_derived(self):
        value = document(
            FEATURES,
            origin="source_acquired",
            transform="derive-features-v1",
        )
        self.assert_schema_invalid(value)
        self.assert_runtime_invalid(value)

    def test_unknown_origin_and_version_fail_closed_everywhere(self):
        unknown_origin = document(CANONICAL, origin="archive", transform="canonicalize-trades-v1")
        unsupported = source_acquired(schema_version="dataset-manifest-v9")
        for value in (unknown_origin, unsupported):
            with self.subTest(value=value):
                self.assert_schema_invalid(value)
                self.assert_runtime_invalid(value)
        self.assertEqual([item.code for item in check_dataset_manifest(unsupported)], [
            "UNSUPPORTED_DATASET_MANIFEST_VERSION",
        ])

    def test_v1_direct_source_shape_remains_invalid_and_v2_fields_are_not_v1(self):
        direct_source = {
            **document(CANONICAL, origin="source_acquired"),
            "schema_version": DATASET_MANIFEST_V1,
        }
        v1_with_origin = {
            **document(CANONICAL, origin="dataset_derived", derived_from=[RAW.stable_dict()], transform="canonicalize-trades-v1"),
            "schema_version": DATASET_MANIFEST_V1,
        }
        for value in (direct_source, v1_with_origin):
            with self.subTest(value=value):
                self.assert_runtime_invalid(value)

        v1_without_parents = {
            key: value for key, value in document(CANONICAL, transform="canonicalize-trades-v1").items()
            if key not in {"origin", "derived_from"}
        }
        v1_without_parents["schema_version"] = DATASET_MANIFEST_V1
        self.assert_runtime_invalid(v1_without_parents)


if __name__ == "__main__":
    unittest.main(verbosity=2)
