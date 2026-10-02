#!/usr/bin/env python3
"""C05 -- typed Application config and concrete market-data composition."""

from __future__ import annotations

import dataclasses
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import quant_platform.application.composition as composition  # noqa: E402
from quant_platform.application import (  # noqa: E402
    DEFAULT_MARKET_DATA_BATCH_SIZE,
    DEFAULT_MAX_RESULT_ROWS,
    MarketDataApplication,
    MarketDataApplicationConfig,
    compose_market_data_application,
)
from quant_platform.source_adapters.bybit import BYBIT_ORDERING_PROVIDER  # noqa: E402


class MarketDataApplicationCompositionTests(unittest.TestCase):
    def test_application_config_is_typed_immutable_and_capability_specific(self):
        config = MarketDataApplicationConfig(
            catalog_dsn="postgresql://catalog", batch_size=7, max_result_rows=11
        )

        self.assertTrue(dataclasses.is_dataclass(config))
        self.assertEqual(
            {"catalog_dsn", "batch_size", "max_result_rows"},
            {field.name for field in dataclasses.fields(config)},
        )
        self.assertEqual("postgresql://catalog", config.catalog_dsn)
        self.assertEqual(7, config.batch_size)
        self.assertEqual(11, config.max_result_rows)

        with self.assertRaises(dataclasses.FrozenInstanceError):
            config.batch_size = 8  # type: ignore[misc]

    def test_declared_application_defaults_are_explicit_resolved_values(self):
        config = MarketDataApplicationConfig()

        self.assertEqual("", config.catalog_dsn)
        self.assertEqual(DEFAULT_MARKET_DATA_BATCH_SIZE, config.batch_size)
        self.assertEqual(DEFAULT_MAX_RESULT_ROWS, config.max_result_rows)

    def test_config_rejects_unresolved_or_invalid_values(self):
        for bad_dsn in (None, 1, object()):
            with self.subTest(bad_dsn=bad_dsn):
                with self.assertRaises(TypeError):
                    MarketDataApplicationConfig(catalog_dsn=bad_dsn)  # type: ignore[arg-type]

        for bad_batch_size in (0, -1, True, 1.5, "10"):
            with self.subTest(bad_batch_size=bad_batch_size):
                with self.assertRaises(ValueError):
                    MarketDataApplicationConfig(batch_size=bad_batch_size)  # type: ignore[arg-type]

        for bad_max_result_rows in (0, -1, True, 1.5, "10"):
            with self.subTest(bad_max_result_rows=bad_max_result_rows):
                with self.assertRaises(ValueError):
                    MarketDataApplicationConfig(max_result_rows=bad_max_result_rows)  # type: ignore[arg-type]

    def test_composer_accepts_only_the_application_config(self):
        with self.assertRaises(TypeError):
            compose_market_data_application({"catalog_dsn": "dsn"})  # type: ignore[arg-type]

    def test_composed_application_owns_catalog_gateway_and_provider_construction(self):
        calls = {}

        class FakeCatalog:
            def __init__(self, *, dsn):
                calls["dsn"] = dsn

        class FakeGateway:
            def __init__(self, catalog, *, ordering_providers):
                calls["catalog"] = catalog
                calls["ordering_providers"] = ordering_providers

        def fake_execute(query, *, gateway, batch_size, max_result_rows):
            calls["query"] = query
            calls["gateway"] = gateway
            calls["batch_size"] = batch_size
            calls["max_result_rows"] = max_result_rows
            return "controlled-result"

        with patch.object(composition, "Catalog", FakeCatalog), \
             patch.object(composition, "DataGateway", FakeGateway), \
             patch.object(composition, "execute_market_data_query", fake_execute):
            config = MarketDataApplicationConfig(
                catalog_dsn="controlled-dsn",
                batch_size=3,
                max_result_rows=13,
            )
            app = compose_market_data_application(config)
            result = app.execute("controlled-query")  # type: ignore[arg-type]

        self.assertIsInstance(app, MarketDataApplication)
        self.assertEqual("controlled-result", result)
        self.assertEqual("controlled-dsn", calls["dsn"])
        self.assertIsInstance(calls["catalog"], FakeCatalog)
        self.assertIsInstance(calls["gateway"], FakeGateway)
        self.assertEqual((BYBIT_ORDERING_PROVIDER,), calls["ordering_providers"])
        self.assertEqual("controlled-query", calls["query"])
        self.assertEqual(3, calls["batch_size"])
        self.assertEqual(13, calls["max_result_rows"])

    def test_application_composition_does_not_acquire_cli_or_environment(self):
        source = Path(composition.__file__).read_text(encoding="utf-8")

        for forbidden in ("os.environ", "getenv", "sys.argv", "argparse"):
            self.assertNotIn(forbidden, source)

    def test_no_generic_config_or_provider_framework_is_introduced(self):
        source = Path(composition.__file__).read_text(encoding="utf-8")

        for forbidden in (
            "dict[str, Any]",
            "Mapping",
            "service locator",
            "container",
            "registry",
            "plugin",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
