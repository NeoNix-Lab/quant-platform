#!/usr/bin/env python3
"""Contract-only checks for the proposed CandleDefinition v1 record schema.

This test intentionally does not aggregate trades or implement a candle
runtime. It protects the exact closed-record boundary while the semantic
contract is pending independent review.
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    sys.exit("missing dependency: install tests/requirements.txt")


ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = ROOT / "schemas" / "candle-v1.json"


def load_schema():
    with SCHEMA_PATH.open(encoding="utf-8") as handle:
        schema = json.load(handle)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


class CandleDefinitionV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validator = load_schema()

    def assert_valid(self, record):
        errors = list(self.validator.iter_errors(record))
        self.assertEqual(errors, [], errors)

    def assert_invalid(self, record):
        self.assertTrue(list(self.validator.iter_errors(record)), record)

    def test_closed_record_has_exact_decimal_fields_and_no_state(self):
        self.assert_valid(
            {
                "bucket_start": "2024-01-01T00:00:00Z",
                "bucket_end": "2024-01-01T00:05:00Z",
                "open": "100",
                "high": "101.25",
                "low": "99.5",
                "close": "100.1",
                "volume": "1.25",
                "trade_count": "3",
            }
        )

    def test_partial_state_is_envelope_semantics_not_a_record_field(self):
        record = {
            "bucket_start": "2024-01-01T00:00:00Z",
            "bucket_end": "2024-01-01T00:05:00Z",
            "open": "100",
            "high": "100",
            "low": "100",
            "close": "100",
            "volume": "1",
            "trade_count": "1",
            "state": "CLOSED",
        }
        self.assert_invalid(record)

    def test_binary_float_and_noncanonical_decimal_spellings_are_rejected(self):
        base = {
            "bucket_start": "2024-01-01T00:00:00Z",
            "bucket_end": "2024-01-01T00:05:00Z",
            "open": "100",
            "high": "101",
            "low": "99",
            "close": "100",
            "volume": "1",
            "trade_count": "3",
        }
        for field, value in (("open", 100.0), ("volume", "1.0"), ("high", "1e2"), ("trade_count", 3)):
            with self.subTest(field=field, value=value):
                invalid = dict(base)
                invalid[field] = value
                self.assert_invalid(invalid)

    def test_zero_or_empty_ohlcv_is_not_an_empty_bucket_record(self):
        base = {
            "bucket_start": "2024-01-01T00:00:00Z",
            "bucket_end": "2024-01-01T00:05:00Z",
            "open": "100",
            "high": "100",
            "low": "100",
            "close": "100",
            "volume": "1",
            "trade_count": "1",
        }
        for field, value in (("open", "0"), ("volume", "0"), ("trade_count", "0")):
            with self.subTest(field=field):
                invalid = dict(base)
                invalid[field] = value
                self.assert_invalid(invalid)


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
