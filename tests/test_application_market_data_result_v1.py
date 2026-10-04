#!/usr/bin/env python3
"""C03 -- ASS-02 result and error translation for trades@1.

Proves that a semantic query executed against an injected access capability
yields either the stable consumer envelope or one of the frozen Consumer
API errors, with no catalog, storage, path, policy or raw-exception detail
crossing the boundary.

Reuses the accepted bounded-read fake seam rather than re-proving DataGateway
behaviour, and does not rerun Golden or PostgreSQL evidence.
"""

from __future__ import annotations

import dataclasses
from enum import Enum
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from test_bounded_datagateway_read_v1 import (  # noqa: E402
    IDENTITY,
    LEFT,
    RIGHT,
    FakeBatchReader,
    FakeCatalog,
    trade,
)

from quant_platform.access.gateway import DataGateway  # noqa: E402
from quant_platform.application import (  # noqa: E402
    ConsumerApiError,
    ConsumerErrorCode,
    ConsumerMarketDataQuery,
    RepresentationRef,
    UnsupportedOption,
    UnsupportedRepresentation,
    UnsupportedVenue,
    execute_market_data_query,
    resolve_market_data_request,
)
from quant_platform.data.models import (  # noqa: E402
    CatalogConflict,
    CorruptContent,
    DataGatewayError,
    DataIntegrityError,
    DatasetNotFound,
    Instant,
    InvalidPartitionState,
    InvalidRequest,
    NoCoverage,
    SchemaMismatch,
    StorageResolutionError,
    UnsupportedDatasetKind,
    UnsupportedSchema,
)
from quant_platform.source_adapters.bybit import BYBIT_ORDERING_PROVIDER  # noqa: E402


TRADES_V1 = RepresentationRef("trades", 1)
START = "2024-01-01T00:00:00Z"
END = "2024-01-01T02:00:00Z"
LEFT_PATH = LEFT.rel_path
RIGHT_PATH = RIGHT.rel_path
SECRET_PATH = "/catalog-root/canonical/trades/bybit/BTCUSDT/trade-v1/dt=2024-01-01/part-000.parquet"


def query(**overrides) -> ConsumerMarketDataQuery:
    base = {
        "venue": "bybit",
        "instrument": "BTCUSDT",
        "start": START,
        "end": END,
        "representation": TRADES_V1,
    }
    base.update(overrides)
    return ConsumerMarketDataQuery(**base)


def gateway(partitions, batches_by_path, *, path_resolver=None) -> DataGateway:
    return DataGateway(
        FakeCatalog(list(partitions)),
        batch_reader=FakeBatchReader(batches_by_path),
        path_resolver=path_resolver or (lambda _root, _dataset_root, rel_path: rel_path),
        ordering_providers=(BYBIT_ORDERING_PROVIDER,),
    )


def covered_gateway(batches_by_path=None, *, path_resolver=None) -> DataGateway:
    """Both partitions present, so [START, END) is fully covered."""
    return gateway([LEFT, RIGHT], batches_by_path or {}, path_resolver=path_resolver)


class OpenRaisingGateway:
    def __init__(self, exc: Exception):
        self.exc = exc

    def scan(self, request, **_kwargs):
        raise self.exc


class _RaisingScan:
    def __init__(self, exc: Exception):
        self.exc = exc
        self.completed_metadata = None

    def __iter__(self):
        return self

    def __next__(self):
        raise self.exc


class DrainRaisingGateway:
    def __init__(self, exc: Exception):
        self.exc = exc

    def scan(self, request, **_kwargs):
        return _RaisingScan(self.exc)


def collect_strings(value, seen=None) -> list[str]:
    """Every string reachable from a rendered consumer object."""
    seen = seen if seen is not None else []
    if isinstance(value, str):
        seen.append(value)
    elif isinstance(value, Enum):
        collect_strings(value.value, seen)
    elif dataclasses.is_dataclass(value) and not isinstance(value, type):
        for f in dataclasses.fields(value):
            collect_strings(getattr(value, f.name), seen)
    elif isinstance(value, dict):
        for key, item in value.items():
            collect_strings(key, seen)
            collect_strings(item, seen)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            collect_strings(item, seen)
    elif value is not None and not isinstance(value, (bool, int, float)):
        seen.append(str(value))
    return seen


class ConsumerRequestIdentityTests(unittest.TestCase):
    def test_identity_is_deterministic_and_venue_spelling_independent(self):
        canonical = execute_market_data_query(query(), gateway=covered_gateway()).request_identity
        for spelling in ("bybit", "Bybit", "BYBIT", "  bybit  "):
            with self.subTest(venue=spelling):
                result = execute_market_data_query(
                    query(venue=spelling), gateway=covered_gateway()
                )
                self.assertEqual(canonical, result.request_identity)

    def test_identity_payload_excludes_internal_dataset_policy_and_storage_identity(self):
        result = execute_market_data_query(query(), gateway=covered_gateway())
        payload = result.request.stable_dict()
        self.assertEqual(
            {"identity_domain", "venue", "instrument", "interval", "representation"},
            set(payload),
        )
        rendered = repr(payload)
        for forbidden in (
            "canonical", "dataset_kind", "trade-v1", "record_schema",
            "lifecycle", "coverage_policy", "ordering", "catalog", "storage", "rel_path",
        ):
            self.assertNotIn(forbidden, rendered, forbidden)

    def test_consumer_identity_differs_from_access_request_identity(self):
        result = execute_market_data_query(query(), gateway=covered_gateway())
        access_identity = resolve_market_data_request(query()).request_identity
        self.assertNotEqual(access_identity, result.request_identity)
        self.assertEqual(result.request.request_identity, result.request_identity)

    def test_different_semantics_change_the_identity(self):
        base = execute_market_data_query(query(), gateway=covered_gateway()).request_identity
        other = execute_market_data_query(
            query(end="2024-01-01T01:00:00Z"), gateway=gateway([LEFT], {})
        ).request_identity
        self.assertNotEqual(base, other)


class SuccessfulResultTests(unittest.TestCase):
    def batches(self):
        return {
            LEFT_PATH: [(trade("2024-01-01T00:10:00Z", "1"), trade("2024-01-01T00:20:00Z", "2"))],
            RIGHT_PATH: [(trade("2024-01-01T01:10:00Z", "3"),)],
        }

    def test_reference_query_returns_the_frozen_envelope(self):
        result = execute_market_data_query(query(), gateway=covered_gateway(self.batches()))

        self.assertEqual(3, result.row_count)
        self.assertEqual(3, len(result.data))
        self.assertEqual(("1", "2", "3"), tuple(record.trade_id for record in result.data))

        self.assertEqual("bybit", result.request.venue)
        self.assertEqual("BTCUSDT", result.request.instrument)
        self.assertEqual(("trades", 1), (result.representation.kind, result.representation.version))

        self.assertEqual(Instant.parse(START), result.requested_interval.start)
        self.assertEqual(Instant.parse(END), result.requested_interval.end)
        self.assertIsNotNone(result.returned_temporal_bounds)
        self.assertEqual(
            Instant.parse("2024-01-01T00:10:00Z"), result.returned_temporal_bounds.first
        )
        self.assertEqual(
            Instant.parse("2024-01-01T01:10:00Z"), result.returned_temporal_bounds.last
        )

        self.assertTrue(result.coverage.complete)
        self.assertEqual((), result.coverage.gaps)
        self.assertTrue(result.coverage.covered_intervals)

        self.assertEqual(IDENTITY, result.provenance.dataset_identity)
        self.assertEqual("trade-v1", result.provenance.record_schema_id)
        self.assertEqual(1, result.provenance.schema_version)
        self.assertTrue(result.provenance.schema_hash)
        self.assertEqual(2, len(result.provenance.natural_partitions))
        self.assertTrue(result.provenance.manifest_hashes)
        self.assertTrue(result.provenance.content_hashes)

    def test_full_coverage_with_zero_records_is_a_successful_empty_result(self):
        result = execute_market_data_query(query(), gateway=covered_gateway({}))
        self.assertEqual(0, result.row_count)
        self.assertEqual((), result.data)
        self.assertIsNone(result.returned_temporal_bounds)
        self.assertTrue(result.coverage.complete)
        self.assertEqual((), result.coverage.gaps)

    def test_coverage_is_never_inferred_from_row_count(self):
        empty = execute_market_data_query(query(), gateway=covered_gateway({}))
        populated = execute_market_data_query(query(), gateway=covered_gateway(self.batches()))
        self.assertEqual(populated.coverage.complete, empty.coverage.complete)
        self.assertEqual(populated.coverage.covered_intervals, empty.coverage.covered_intervals)

    def test_result_exposes_no_catalog_storage_or_path_locator(self):
        result = execute_market_data_query(query(), gateway=covered_gateway(self.batches()))
        strings = collect_strings(result)
        for exact in ("dataset-a", "partition-a", "partition-b", "hot", "/catalog-root"):
            self.assertNotIn(exact, strings, exact)
        for fragment in ("/catalog-root", ".parquet", "part-000"):
            for value in strings:
                self.assertNotIn(fragment, value, f"{fragment} leaked via {value!r}")


class ResultTooLargeTests(unittest.TestCase):
    """ADR-0050 Amendment 1 (#247): refuse a result over max_result_rows with a
    typed error, rather than returning an arbitrarily large ConsumerMarketDataResult
    that J02 would then try to serialize into an oversized wire frame."""

    def batches(self):
        return {
            LEFT_PATH: [(trade("2024-01-01T00:10:00Z", "1"), trade("2024-01-01T00:20:00Z", "2"))],
            RIGHT_PATH: [(trade("2024-01-01T01:10:00Z", "3"),)],
        }

    def test_documented_v1_default_is_50_000(self):
        from quant_platform.application import DEFAULT_MAX_RESULT_ROWS

        self.assertEqual(50_000, DEFAULT_MAX_RESULT_ROWS)

    def test_result_over_the_bound_is_refused_with_a_typed_error(self):
        with self.assertRaises(ConsumerApiError) as caught:
            execute_market_data_query(
                query(), gateway=covered_gateway(self.batches()), max_result_rows=2
            )
        self.assertEqual(ConsumerErrorCode.RESULT_TOO_LARGE, caught.exception.code)
        self.assertEqual("3", caught.exception.context["row_count"])
        self.assertEqual("2", caught.exception.context["max_result_rows"])
        self.assertIn("requested_interval", caught.exception.context)
        # Resolution succeeded, so the consumer identity exists.
        self.assertIsNotNone(caught.exception.request_identity)

    def test_result_exactly_at_the_bound_succeeds(self):
        result = execute_market_data_query(
            query(), gateway=covered_gateway(self.batches()), max_result_rows=3
        )
        self.assertEqual(3, result.row_count)

    def test_default_bound_does_not_refuse_an_ordinary_small_result(self):
        result = execute_market_data_query(query(), gateway=covered_gateway(self.batches()))
        self.assertEqual(3, result.row_count)

    def test_oversized_query_stops_reading_as_soon_as_the_bound_is_exceeded(self):
        """Review-caught blocker: the first version of this fix only refused to
        *return* an oversized result after the scan had already been fully
        drained -- it did not bound the read itself, so a pathologically large
        query still cost a full unbounded scan. Proven here by counting the
        actual batches pulled from the source reader, not just the outcome."""
        reader = FakeBatchReader({
            LEFT_PATH: [
                (trade("2024-01-01T00:10:00Z", "1"),),
                (trade("2024-01-01T00:11:00Z", "2"),),
            ],
            RIGHT_PATH: [
                (trade("2024-01-01T01:10:00Z", "3"),),
                (trade("2024-01-01T01:11:00Z", "4"),),
            ],
        })
        instance = DataGateway(
            FakeCatalog([LEFT, RIGHT]),
            batch_reader=reader,
            path_resolver=lambda _root, _dataset_root, rel_path: rel_path,
            ordering_providers=(BYBIT_ORDERING_PROVIDER,),
        )

        with self.assertRaises(ConsumerApiError) as caught:
            execute_market_data_query(query(), gateway=instance, batch_size=1, max_result_rows=1)

        self.assertEqual(ConsumerErrorCode.RESULT_TOO_LARGE, caught.exception.code)
        self.assertEqual("2", caught.exception.context["row_count"])
        # Exactly 2 of the 4 available batches were pulled (one to reach the
        # bound, one more to exceed it) -- RIGHT_PATH's reader was never even
        # opened (reader.calls records one call per path, regardless of how
        # many batches that path's generator ultimately yields).
        self.assertEqual([(LEFT_PATH, 0), (LEFT_PATH, 1)], reader.yields)
        self.assertEqual(1, len(reader.calls))
        self.assertEqual(LEFT_PATH, reader.calls[0][0])


class CoverageRefusalTests(unittest.TestCase):
    def test_incomplete_strict_coverage_is_no_coverage_not_an_empty_success(self):
        with self.assertRaises(ConsumerApiError) as caught:
            execute_market_data_query(query(), gateway=gateway([LEFT], {}))
        self.assertEqual(ConsumerErrorCode.NO_COVERAGE, caught.exception.code)
        self.assertIn("requested_interval", caught.exception.context)

    def test_absent_coverage_is_no_coverage(self):
        with self.assertRaises(ConsumerApiError) as caught:
            execute_market_data_query(query(), gateway=gateway([], {}))
        self.assertEqual(ConsumerErrorCode.NO_COVERAGE, caught.exception.code)


class ErrorTranslationTests(unittest.TestCase):
    def test_request_phase_mapping(self):
        cases = [
            (query(representation=RepresentationRef("candle", 1)),
             ConsumerErrorCode.UNSUPPORTED_REPRESENTATION, UnsupportedRepresentation),
            (query(options={"projection": ["price"]}),
             ConsumerErrorCode.INVALID_REQUEST, UnsupportedOption),
            (query(venue="binance"),
             ConsumerErrorCode.SOURCE_NOT_FOUND, UnsupportedVenue),
            (query(start=END, end=START),
             ConsumerErrorCode.INVALID_REQUEST, InvalidRequest),
        ]
        for bad, code, cause in cases:
            with self.subTest(code=code.value):
                with self.assertRaises(ConsumerApiError) as caught:
                    execute_market_data_query(bad, gateway=covered_gateway())
                self.assertEqual(code, caught.exception.code)
                self.assertIsInstance(caught.exception.__cause__, cause)
                # C02-stage failures legitimately have no consumer identity.
                self.assertIsNone(caught.exception.request_identity)

    def test_malformed_option_shapes_are_invalid_request_not_raw_type_error(self):
        cases = [
            query(options=1),
            query(representation=RepresentationRef("trades", 1, definition=1)),
        ]
        for bad in cases:
            with self.subTest(query=bad):
                with self.assertRaises(ConsumerApiError) as caught:
                    execute_market_data_query(bad, gateway=covered_gateway())
                error = caught.exception
                self.assertEqual(ConsumerErrorCode.INVALID_REQUEST, error.code)
                self.assertIsInstance(error.__cause__, InvalidRequest)
                self.assertEqual({}, error.context)
                rendered = collect_strings(error.message) + collect_strings(error.context)
                self.assertNotIn("'int' object is not iterable", rendered)
                self.assertNotIn("TypeError", rendered)

    def test_malformed_option_shape_is_caught_even_when_the_other_field_refuses_first(self):
        # A valid non-empty definition would refuse first on semantics.  The
        # malformed options shape must still be validated before that refusal
        # is interpreted, or the context builder iterates an unchecked value.
        cases = [
            ("scalar", query(representation=RepresentationRef("trades", 1, {"a": 1}), options=1)),
            ("iterable", query(representation=RepresentationRef("trades", 1, {"a": 1}), options=["x"])),
        ]
        for label, bad in cases:
            with self.subTest(shape=label):
                with self.assertRaises(ConsumerApiError) as caught:
                    execute_market_data_query(bad, gateway=covered_gateway())
                error = caught.exception
                self.assertEqual(ConsumerErrorCode.INVALID_REQUEST, error.code)
                self.assertIsInstance(error.__cause__, InvalidRequest)
                self.assertEqual({}, error.context)
                rendered = collect_strings(error.message) + collect_strings(error.context)
                self.assertNotIn("'int' object is not iterable", rendered)
                self.assertNotIn("TypeError", rendered)
                # Elements of a malformed list are never option names, and the
                # valid field's keys are not reported for a shape failure.
                for value in rendered:
                    self.assertNotIn("x", value)
                    self.assertNotIn("unsupported_options", value)

    def test_gateway_phase_mapping(self):
        cases = [
            (DatasetNotFound("x"), ConsumerErrorCode.SOURCE_NOT_FOUND),
            (NoCoverage("x"), ConsumerErrorCode.NO_COVERAGE),
            (SchemaMismatch("x"), ConsumerErrorCode.SCHEMA_INCOMPATIBLE),
            (UnsupportedSchema("x"), ConsumerErrorCode.INTEGRITY_FAILURE),
            (UnsupportedDatasetKind("x"), ConsumerErrorCode.INTEGRITY_FAILURE),
            (CatalogConflict("x"), ConsumerErrorCode.INTEGRITY_FAILURE),
            (InvalidPartitionState("x"), ConsumerErrorCode.INTEGRITY_FAILURE),
            (CorruptContent("x"), ConsumerErrorCode.INTEGRITY_FAILURE),
            (DataIntegrityError("x"), ConsumerErrorCode.INTEGRITY_FAILURE),
            (StorageResolutionError("x"), ConsumerErrorCode.INTEGRITY_FAILURE),
            (InvalidRequest("x"), ConsumerErrorCode.INTEGRITY_FAILURE),
            (DataGatewayError("x"), ConsumerErrorCode.INTEGRITY_FAILURE),
        ]
        for exc, code in cases:
            with self.subTest(failure=type(exc).__name__):
                with self.assertRaises(ConsumerApiError) as caught:
                    execute_market_data_query(query(), gateway=OpenRaisingGateway(exc))
                self.assertEqual(code, caught.exception.code)
                # Resolution succeeded, so the consumer identity exists.
                self.assertIsNotNone(caught.exception.request_identity)

    def test_all_seven_codes_remain_distinguishable(self):
        """Seven, not six: ADR-0050 Amendment 1 (#247) added RESULT_TOO_LARGE."""
        self.assertEqual(
            {
                "invalid_request", "unsupported_representation", "source_not_found",
                "no_coverage", "schema_incompatible", "integrity_failure",
                "result_too_large",
            },
            {code.value for code in ConsumerErrorCode},
        )

    def test_integrity_failures_carry_no_context(self):
        with self.assertRaises(ConsumerApiError) as caught:
            execute_market_data_query(query(), gateway=OpenRaisingGateway(CatalogConflict("x")))
        self.assertEqual({}, caught.exception.context)


class DrainPhaseTranslationTests(unittest.TestCase):
    def test_integrity_failure_raised_while_iterating_is_translated(self):
        duplicate = trade("2024-01-01T00:10:00Z", "1")
        instance = covered_gateway({LEFT_PATH: [(duplicate, duplicate)]})
        with self.assertRaises(ConsumerApiError) as caught:
            execute_market_data_query(query(), gateway=instance)
        self.assertEqual(ConsumerErrorCode.INTEGRITY_FAILURE, caught.exception.code)
        self.assertIsInstance(caught.exception.__cause__, DataIntegrityError)

    def test_storage_resolution_failure_raised_while_iterating_is_translated(self):
        def exploding_resolver(_root, _dataset_root, _rel_path):
            raise StorageResolutionError(f"catalogued partition file is not readable: {SECRET_PATH}")

        instance = covered_gateway({}, path_resolver=exploding_resolver)
        with self.assertRaises(ConsumerApiError) as caught:
            execute_market_data_query(query(), gateway=instance)
        self.assertEqual(ConsumerErrorCode.INTEGRITY_FAILURE, caught.exception.code)
        self.assertIsInstance(caught.exception.__cause__, StorageResolutionError)

    def test_drain_phase_failure_is_not_a_silently_empty_success(self):
        instance = DrainRaisingGateway(DataIntegrityError("boom"))
        with self.assertRaises(ConsumerApiError):
            execute_market_data_query(query(), gateway=instance)


class SafeContextTests(unittest.TestCase):
    def test_absolute_path_in_a_raw_message_never_reaches_the_consumer(self):
        raw = StorageResolutionError(f"catalogued partition file is not readable: {SECRET_PATH}")
        self.assertIn(SECRET_PATH, str(raw))  # the raw failure really does carry the path

        with self.assertRaises(ConsumerApiError) as caught:
            execute_market_data_query(query(), gateway=OpenRaisingGateway(raw))
        error = caught.exception
        self.assertEqual(ConsumerErrorCode.INTEGRITY_FAILURE, error.code)
        self.assertEqual({}, error.context)
        for value in collect_strings(error.message) + collect_strings(error.context):
            self.assertNotIn(SECRET_PATH, value)
            self.assertNotIn("/catalog-root", value)
        self.assertNotIn(SECRET_PATH, str(error))

    def test_lower_layer_context_is_never_copied_through(self):
        raw = SchemaMismatch(
            "requested schema does not match",
            context={"catalog_dataset_id": "dataset-a", "rel_path": "dt=x/part-000.parquet"},
        )
        with self.assertRaises(ConsumerApiError) as caught:
            execute_market_data_query(query(), gateway=OpenRaisingGateway(raw))
        rendered = collect_strings(caught.exception.context) + [caught.exception.message]
        for value in rendered:
            self.assertNotIn("dataset-a", value)
            self.assertNotIn(".parquet", value)

    def test_safe_contexts_expose_only_caller_supplied_semantics(self):
        with self.assertRaises(ConsumerApiError) as caught:
            execute_market_data_query(query(venue="binance"), gateway=covered_gateway())
        self.assertEqual(
            {"venue": "binance", "instrument": "BTCUSDT", "representation": "trades@1"},
            caught.exception.context,
        )


if __name__ == "__main__":
    unittest.main()
