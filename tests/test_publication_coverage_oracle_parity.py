#!/usr/bin/env python3
"""Differential parity checks for production coverage reconstruction."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from quant_platform.data.coverage import reconstruct_catalog_coverage  # noqa: E402
from semantic_validator import reconstruct_catalog_coverage as oracle  # noqa: E402


IDENTITY = {
    "layer": "canonical", "dataset_kind": "trades", "venue": "bybit",
    "instrument": "BTCUSDT", "record_schema_id": "trade-v1",
}


def partition(key="dt=2024-01-15", revision=1, *, state="closed", row_count=1,
              first="2024-01-15T01:00:00Z", last="2024-01-15T23:00:00Z", **identity):
    return {
        **IDENTITY, **identity, "partition_key": key, "revision": revision,
        "state": state, "row_count": row_count,
        "first_exchange_ts": first, "last_exchange_ts": last,
    }


def assertion(assertion_id, start, end, *, status="complete", key="dt=2024-01-15", revision=1):
    return {
        "assertion_id": assertion_id, "start": start, "end": end,
        "status": status,
        "partitions": [{"partition_key": key, "revision": revision}] if status == "complete" else [],
        "evidence": [{"kind": "deterministic_source_extract", "detail": assertion_id}],
    }


def coverage(coverage_id, assertions, *, supersedes=None, intent_start="2024-01-15T00:00:00Z",
             intent_end="2024-01-16T00:00:00Z", created_at="2026-09-01T00:00:00Z"):
    return {
        **IDENTITY, "coverage_id": coverage_id, "supersedes": supersedes,
        "created_at": created_at,
        "acquisition": {
            "intent_start": intent_start, "intent_end": intent_end,
        },
        "assertions": assertions,
    }


def signature(result):
    coverage_result, violations = result
    normalized_coverage = {
        key: (_epoch_ns(value[0]), _epoch_ns(value[1]))
        for key, value in coverage_result.items()
    }
    normalized_violations = tuple(sorted((item.code, getattr(item, "where", "")) for item in violations))
    return normalized_coverage, normalized_violations


def _epoch_ns(value):
    return value if isinstance(value, int) else value.epoch_ns


class PublicationCoverageOracleParityTests(unittest.TestCase):
    def assert_parity(self, coverages, partitions):
        production = signature(reconstruct_catalog_coverage(coverages, partitions))
        reference = signature(oracle(coverages, partitions))
        self.assertEqual(production, reference)

    def test_historic_adjacent_fold(self):
        self.assert_parity([
            coverage("a", [assertion("a1", "2024-01-15T00:00:00Z", "2024-01-15T12:00:00Z")]),
            coverage("b", [assertion("b1", "2024-01-15T12:00:00Z", "2024-01-16T00:00:00Z")]),
        ], [partition()])

    def test_single_partial_complete_assertion(self):
        self.assert_parity([coverage("partial", [assertion("p", "2024-01-15T00:00:00Z", "2024-01-15T12:00:00Z")])], [partition()])

    def test_zero_row_complete_coverage(self):
        self.assert_parity([coverage("silent", [assertion("s", "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z")])], [partition(row_count=0, first=None, last=None)])

    def test_complete_vs_known_gap_contradiction(self):
        self.assert_parity([
            coverage("complete", [assertion("c", "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z")]),
            coverage("gap", [assertion("g", "2024-01-15T12:00:00Z", "2024-01-15T13:00:00Z", status="known_gap")]),
        ], [partition()])

    def test_non_contiguous_effective_coverage(self):
        self.assert_parity([
            coverage("left", [assertion("l", "2024-01-15T00:00:00Z", "2024-01-15T12:00:00Z")]),
            coverage("right", [assertion("r", "2024-01-15T13:00:00Z", "2024-01-16T00:00:00Z")]),
        ], [partition()])

    def test_explicit_supersession(self):
        self.assert_parity([
            coverage("old", [assertion("old-a", "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z")]),
            coverage("new", [assertion("new-a", "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z")], supersedes="old"),
        ], [partition()])

    def test_missing_partition_reference(self):
        self.assert_parity([coverage("missing", [assertion("m", "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z", key="dt=missing")])], [partition()])

    def test_ambiguous_partition_reference(self):
        self.assert_parity([coverage("ambiguous", [assertion("a", "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z")])], [partition(), partition()])

    def test_mixed_dataset_identity(self):
        self.assert_parity([coverage("mixed", [assertion("m", "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z")])], [partition(venue="kraken", instrument="XBTUSD")])

    def test_duplicate_partition_manifest(self):
        self.assert_parity([coverage("duplicate", [assertion("d", "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z")])], [partition(), partition()])

    def test_multiple_live_revisions(self):
        self.assert_parity([coverage("revisions", [assertion("r", "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z", revision=1)])], [partition(revision=1), partition(revision=2)])

    def test_ineligible_partition_state(self):
        self.assert_parity([coverage("writing", [assertion("w", "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z")])], [partition(state="writing")])

    def test_created_at_is_irrelevant(self):
        documents = [coverage("stable", [assertion("s", "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z")], created_at="2020-01-01T00:00:00Z")]
        later = [coverage("stable", [assertion("s", "2024-01-15T00:00:00Z", "2024-01-16T00:00:00Z")], created_at="2030-01-01T00:00:00Z")]
        self.assertEqual(signature(reconstruct_catalog_coverage(documents, [partition()])), signature(reconstruct_catalog_coverage(later, [partition()])))
        self.assert_parity(documents, [partition()])


if __name__ == "__main__":
    unittest.main()
