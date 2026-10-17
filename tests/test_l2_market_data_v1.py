#!/usr/bin/env python3
"""Hermetic tests for the L2 DataGateway and the ``l2_book_events@1`` consumer
read (A14e, ADR-0070 §6 and Amendment 1)."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.access import (  # noqa: E402
    CatalogDataset,
    CatalogPartition,
    CoveragePolicy,
    DataGateway,
    DataRequest,
    LiveStreamRequest,
)
from quant_platform.access.l2_gateway import L2DataGateway  # noqa: E402
from quant_platform.canonical import canonical_bytes  # noqa: E402
from quant_platform.application import composition  # noqa: E402
from quant_platform.application.api_transport_server import (  # noqa: E402
    encode_consumer_result,
    handle_api_transport_message,
)
from quant_platform.application.composition import (  # noqa: E402
    MarketDataApplication,
    MarketDataApplicationConfig,
)
from quant_platform.application.l2_market_data import (  # noqa: E402
    DEFAULT_L2_MAX_RESULT_BYTES,
    L2_BOOK_EVENTS_KIND,
    _event_bytes,
    execute_l2_book_events_query,
    l2_events_anchored,
    resolve_l2_book_events_request,
)
from quant_platform.application.market_data import (  # noqa: E402
    ConsumerApiError,
    ConsumerErrorCode,
    ConsumerMarketDataQuery,
    RepresentationRef,
)
from quant_platform.data import DatasetIdentity, Instant, NaturalPartitionIdentity  # noqa: E402
from quant_platform.data.l2_materializer import materialize_l2_book_event_v1  # noqa: E402
from quant_platform.data.models import (  # noqa: E402
    DataIntegrityError,
    L2BookEvent,
    L2LevelChange,
    NoCoverage,
    UnsupportedDatasetKind,
    UnsupportedSchema,
)
from quant_platform.ordering import OrderingProvider  # noqa: E402
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ORDERING_PROVIDER,
    BYBIT_TRADE_V1_ORDERING_POLICY,
)
from quant_platform.source_adapters.bybit_l2 import (  # noqa: E402
    BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
    BYBIT_L2_ORDERING_PROVIDER,
)

IDENTITY = DatasetIdentity("canonical", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
REL_ROOT = "canonical/l2/bybit/BTCUSDT/l2-book-event-v1"
DATASET = CatalogDataset(IDENTITY, "dataset-l2", REL_ROOT, "b" * 64, 1, "a" * 64)
DAY = "2024-01-15"
NEXT = "2024-01-16"
BASE = Instant.parse(f"{DAY}T00:00:01Z").epoch_ns


def up(side, price, size="1"):
    return L2LevelChange(side, price, size, "upsert")


def event(seq, u, kind="delta", bids=(), asks=()):
    t = BASE + seq * 1_000_000  # millisecond resolution, like Bybit cts
    return L2BookEvent(
        venue="bybit", market_type="linear_perp", instrument="BTCUSDT", native_symbol="BTCUSDT",
        source_channel="orderbook.200.BTCUSDT", acquisition_mode="historical_archive",
        event_type=kind, exchange_ts=Instant(t), provider_ts=Instant(t + 2_000_000),
        source_depth_limit=200, native_sequence=str(seq), native_update_id=str(u),
        bids=bids or (up("bid", "100.0", str(seq)),), asks=asks,
    )


def snap(seq, u):
    return event(seq, u, "snapshot", bids=(up("bid", "100.0", "5"),), asks=(up("ask", "101.0", "4"),))


RUN1 = [snap(1, 10), event(2, 11), event(3, 12)]
RUN2 = [snap(6, 1), event(7, 2), event(8, 3)]


def ceil_us(ns):
    return (ns // 1000 + 1) * 1000


def floor_us(ns):
    return ns // 1000 * 1000


GAP_START = Instant(ceil_us(RUN1[-1].exchange_ts.epoch_ns))
GAP_END = Instant(floor_us(RUN2[0].exchange_ts.epoch_ns))


class FakeCatalog:
    def __init__(self, dataset, partitions):
        self.dataset = dataset
        self.partitions = partitions

    def resolve_dataset(self, identity):
        return self.dataset

    def select_partitions(self, dataset, start, end, states):
        return [
            p for p in self.partitions
            if p.state in states and p.coverage is not None and p.ts_end > start and p.ts_start < end
        ]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="l2-read-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.partitions = [
            self.partition(1, Instant.parse(f"{DAY}T00:00:00Z"), GAP_START, RUN1),
            self.partition(2, GAP_END, Instant.parse(f"{NEXT}T00:00:00Z"), RUN2),
        ]
        self.catalog = FakeCatalog(DATASET, self.partitions)
        self.gateway = L2DataGateway(self.catalog, ordering_providers=(BYBIT_L2_ORDERING_PROVIDER,))

    def partition(self, n, start, end, events, *, state="valid"):
        key = f"dt={DAY}/seg={n:03d}"
        rel = f"{key}/part-001.parquet"
        materialize_l2_book_event_v1(
            self.tmp / REL_ROOT / rel, events, dataset_identity=IDENTITY,
            ordering_provider=BYBIT_L2_ORDERING_PROVIDER,
        )
        return CatalogPartition(
            NaturalPartitionIdentity(IDENTITY, key, 1), f"partition-{n}", "hot", str(self.tmp),
            REL_ROOT, rel, start, end, len(events), "c" * 64, "d" * 64, state, "fixture", "test",
        )

    def request(self, start, end, *, coverage=CoveragePolicy.ALLOW_PARTIAL):
        return DataRequest(
            IDENTITY, start, end, schema_requirement="l2-book-event-v1",
            coverage_policy=coverage, ordering_policy=BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
        )

    def query(self, start=f"{DAY}T00:00:00Z", end=f"{NEXT}T00:00:00Z", **kwargs):
        values = dict(
            venue="bybit", instrument="BTCUSDT", start=start, end=end,
            representation=RepresentationRef(L2_BOOK_EVENTS_KIND, 1),
        )
        values.update(kwargs)
        return ConsumerMarketDataQuery(**values)

    @staticmethod
    def seqs(events):
        return [e.native_sequence for e in events]


class GatewayTests(Base):
    def test_a_scan_across_partitions_returns_ordered_events_with_the_gap(self):
        slice_ = self.gateway.read(self.request(f"{DAY}T00:00:00Z", f"{NEXT}T00:00:00Z"))
        self.assertEqual(self.seqs(slice_.records), ["1", "2", "3", "6", "7", "8"])
        self.assertFalse(slice_.metadata.coverage_complete)
        self.assertEqual(
            [(g.start, g.end) for g in slice_.metadata.coverage_gaps], [(GAP_START, GAP_END)]
        )
        self.assertEqual(slice_.metadata.row_count, 6)
        self.assertEqual(slice_.metadata.record_schema_id, "l2-book-event-v1")
        self.assertEqual(
            [p.partition_key for p in slice_.metadata.natural_partitions],
            [f"dt={DAY}/seg=001", f"dt={DAY}/seg=002"],
        )

    def test_a_read_inside_one_run_is_complete_and_half_open(self):
        start = Instant(BASE + 2_000_000)  # exactly event 2
        end = Instant(BASE + 3_000_000)  # exactly event 3 (excluded)
        slice_ = self.gateway.read(self.request(start, end))
        self.assertEqual(self.seqs(slice_.records), ["2"])
        self.assertTrue(slice_.metadata.coverage_complete)
        self.assertEqual(slice_.metadata.coverage_gaps, ())

    def test_strict_coverage_refuses_a_read_across_the_gap(self):
        with self.assertRaises(NoCoverage):
            self.gateway.read(
                self.request(f"{DAY}T00:00:00Z", f"{NEXT}T00:00:00Z", coverage=CoveragePolicy.STRICT)
            )

    def test_a_read_wholly_inside_the_gap_has_no_coverage(self):
        with self.assertRaises(NoCoverage):
            self.gateway.read(self.request(GAP_START, GAP_END))

    def test_the_scan_validations_apply_to_l2_events(self):
        # a partition whose declared coverage ends before its last event
        bad = self.partition(
            1, Instant.parse(f"{DAY}T00:00:00Z"), Instant(RUN1[-1].exchange_ts.epoch_ns), RUN1
        )
        gateway = L2DataGateway(
            FakeCatalog(DATASET, [bad]), ordering_providers=(BYBIT_L2_ORDERING_PROVIDER,)
        )
        with self.assertRaises(DataIntegrityError):
            gateway.read(self.request(f"{DAY}T00:00:00Z", f"{NEXT}T00:00:00Z"))

    def test_each_gateway_refuses_the_other_kind(self):
        trades_request = DataRequest(
            DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1"),
            f"{DAY}T00:00:00Z", f"{NEXT}T00:00:00Z", ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
        )
        with self.assertRaises(UnsupportedDatasetKind):
            self.gateway.scan(trades_request)
        plain = DataGateway(self.catalog, ordering_providers=(BYBIT_ORDERING_PROVIDER,))
        with self.assertRaises(UnsupportedDatasetKind):
            plain.scan(self.request(f"{DAY}T00:00:00Z", f"{NEXT}T00:00:00Z"))

    def test_other_layers_and_kinds_are_refused_by_the_gateway_itself(self):
        window = (f"{DAY}T00:00:00Z", f"{NEXT}T00:00:00Z")
        permissive = OrderingProvider(
            identity=BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
            satisfies_requirements=frozenset({"l2-book-events@1-canonical-total-order-v1"}),
            key=lambda e: (1,), applies_to=lambda identity: True,
        )
        gateway = L2DataGateway(self.catalog, ordering_providers=(permissive,))
        for identity in (
            DatasetIdentity("raw", "l2", "bybit", "BTCUSDT", "l2-book-event-v1"),
            DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "l2-book-event-v1"),
        ):
            with self.subTest(identity=identity.stable_dict()):
                with self.assertRaises(UnsupportedDatasetKind):
                    gateway.scan(
                        DataRequest(identity, *window, ordering_policy=BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY)
                    )

    def test_schema_provider_and_live_are_refused(self):
        window = (f"{DAY}T00:00:00Z", f"{NEXT}T00:00:00Z")
        wrong_schema = DataRequest(
            IDENTITY, *window, schema_requirement="trade-v1",
            ordering_policy=BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
        )
        with self.assertRaises(UnsupportedSchema):
            self.gateway.scan(wrong_schema)
        with self.assertRaises(UnsupportedSchema):  # no provider supplied
            L2DataGateway(self.catalog).scan(self.request(*window))
        trade_only = OrderingProvider(
            identity=BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
            satisfies_requirements=frozenset({"trades@1-canonical-total-order-v1"}),
            key=lambda e: (1,), applies_to=lambda identity: True,
        )
        with self.assertRaises(UnsupportedSchema):
            L2DataGateway(self.catalog, ordering_providers=(trade_only,)).scan(self.request(*window))
        inapplicable = OrderingProvider(
            identity=BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
            satisfies_requirements=frozenset({"l2-book-events@1-canonical-total-order-v1"}),
            key=lambda e: (1,), applies_to=lambda identity: False,
        )
        with self.assertRaises(UnsupportedDatasetKind):
            L2DataGateway(self.catalog, ordering_providers=(inapplicable,)).scan(self.request(*window))
        with self.assertRaises(UnsupportedDatasetKind):
            self.gateway.live_stream(LiveStreamRequest(IDENTITY, ordering_policy="p"))


class RepresentationTests(Base):
    def test_resolution_is_canonical_l2_with_allow_partial_coverage(self):
        request = resolve_l2_book_events_request(self.query(venue=" Bybit "))
        self.assertEqual(request.dataset_selector, IDENTITY)
        self.assertEqual(request.schema_requirement, "l2-book-event-v1")
        self.assertEqual(request.coverage_policy, CoveragePolicy.ALLOW_PARTIAL)
        self.assertEqual(request.ordering_policy, BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY)

    def assertRefused(self, query, code):
        with self.assertRaises(ConsumerApiError) as caught:
            execute_l2_book_events_query(query, gateway=self.gateway)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def test_unsupported_requests_are_refused_with_stable_codes(self):
        err = self.assertRefused(
            self.query(representation=RepresentationRef("trades", 1)),
            ConsumerErrorCode.UNSUPPORTED_REPRESENTATION,
        )
        self.assertEqual(err.context["supported"], "l2_book_events@1")
        self.assertRefused(
            self.query(representation=RepresentationRef(L2_BOOK_EVENTS_KIND, 2)),
            ConsumerErrorCode.UNSUPPORTED_REPRESENTATION,
        )
        self.assertRefused(
            self.query(representation=RepresentationRef(L2_BOOK_EVENTS_KIND, True)),  # type: ignore[arg-type]
            ConsumerErrorCode.UNSUPPORTED_REPRESENTATION,
        )
        err = self.assertRefused(
            self.query(representation=RepresentationRef(L2_BOOK_EVENTS_KIND, 1, {"depth": 50})),
            ConsumerErrorCode.INVALID_REQUEST,
        )
        self.assertEqual(err.context, {"unsupported_options": ["depth"]})
        self.assertRefused(self.query(options={"x": 1}), ConsumerErrorCode.INVALID_REQUEST)
        self.assertRefused(self.query(options=["x"]), ConsumerErrorCode.INVALID_REQUEST)  # type: ignore[arg-type]
        self.assertRefused(self.query(venue="okx"), ConsumerErrorCode.SOURCE_NOT_FOUND)
        self.assertRefused(self.query(instrument="ETHUSDT"), ConsumerErrorCode.SOURCE_NOT_FOUND)
        self.assertRefused(self.query(venue=""), ConsumerErrorCode.INVALID_REQUEST)
        self.assertRefused(
            self.query(start=f"{NEXT}T00:00:00Z", end=f"{DAY}T00:00:00Z"),
            ConsumerErrorCode.INVALID_REQUEST,
        )

    def test_a_successful_read_carries_events_gaps_and_provenance(self):
        result = execute_l2_book_events_query(self.query(), gateway=self.gateway)
        self.assertEqual(result.representation, RepresentationRef(L2_BOOK_EVENTS_KIND, 1))
        self.assertEqual(self.seqs(result.data), ["1", "2", "3", "6", "7", "8"])
        self.assertEqual(result.row_count, 6)
        self.assertFalse(result.coverage.complete)
        self.assertEqual(
            [(g.start, g.end) for g in result.coverage.gaps], [(GAP_START, GAP_END)]
        )
        self.assertEqual(len(result.coverage.covered_intervals), 2)
        self.assertEqual(result.provenance.record_schema_id, "l2-book-event-v1")
        self.assertEqual(result.provenance.dataset_identity, IDENTITY)
        self.assertEqual(result.returned_temporal_bounds.first, RUN1[0].exchange_ts)
        self.assertEqual(result.returned_temporal_bounds.last, RUN2[-1].exchange_ts)

    def test_the_consumer_identity_is_distinct_from_trades(self):
        trades = ConsumerMarketDataQuery(
            "bybit", "BTCUSDT", f"{DAY}T00:00:00Z", f"{NEXT}T00:00:00Z", RepresentationRef("trades", 1)
        )
        from quant_platform.application.market_data import NormalizedMarketDataQuery

        def identity(query):
            return NormalizedMarketDataQuery(
                "bybit", "BTCUSDT", Instant.parse(query.start), Instant.parse(query.end),
                query.representation.kind, query.representation.version,
            ).request_identity

        self.assertNotEqual(identity(trades), identity(self.query()))
        result = execute_l2_book_events_query(self.query(), gateway=self.gateway)
        self.assertEqual(result.request_identity, identity(self.query()))

    def test_anchored_is_derived_from_the_first_event(self):
        whole = execute_l2_book_events_query(self.query(), gateway=self.gateway)
        self.assertTrue(l2_events_anchored(whole.data))
        mid = execute_l2_book_events_query(
            self.query(start=Instant(BASE + 2_000_000), end=Instant(BASE + 4_000_000)),
            gateway=self.gateway,
        )
        self.assertEqual(self.seqs(mid.data), ["2", "3"])
        self.assertFalse(l2_events_anchored(mid.data))
        self.assertTrue(l2_events_anchored(()))

    def test_no_coverage_and_integrity_failures_are_translated(self):
        err = self.assertRefused(
            self.query(start=GAP_START, end=GAP_END), ConsumerErrorCode.NO_COVERAGE
        )
        self.assertIn("requested_interval", err.context)
        # corrupt the first partition's file: a typed integrity failure with no detail
        path = self.tmp / REL_ROOT / self.partitions[0].rel_path
        path.write_bytes(b"not a parquet file")
        err = self.assertRefused(self.query(), ConsumerErrorCode.INTEGRITY_FAILURE)
        self.assertEqual(err.context, {})
        self.assertIsNotNone(err.request_identity)

    def test_max_result_bytes_must_be_a_positive_integer(self):
        for bad in (0, -1, True, 1.5):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    execute_l2_book_events_query(
                        self.query(), gateway=self.gateway, max_result_bytes=bad  # type: ignore[arg-type]
                    )


class ByteBoundTests(Base):
    def total(self):
        return sum(_event_bytes(e) for e in RUN1 + RUN2)

    def test_the_size_of_an_event_is_its_compact_json_plus_a_separator(self):
        event_ = RUN1[0]
        self.assertEqual(
            _event_bytes(event_),
            len(canonical_bytes(event_.stable_dict(), profile="sorted-compact-ascii-v1", allow_nan=False)) + 1,
        )

    def test_the_bound_is_in_bytes_and_exact(self):
        total = self.total()
        ok = execute_l2_book_events_query(self.query(), gateway=self.gateway, max_result_bytes=total)
        self.assertEqual(ok.row_count, 6)
        with self.assertRaises(ConsumerApiError) as caught:
            execute_l2_book_events_query(self.query(), gateway=self.gateway, max_result_bytes=total - 1)
        error = caught.exception
        self.assertEqual(error.code, ConsumerErrorCode.RESULT_TOO_LARGE)
        self.assertEqual(
            set(error.context), {"row_count", "max_result_bytes", "requested_interval"}
        )
        self.assertEqual(error.context["max_result_bytes"], str(total - 1))
        self.assertEqual(error.context["row_count"], "6")
        self.assertIsNotNone(error.request_identity)

    def test_reading_stops_as_soon_as_the_bound_is_exceeded(self):
        pulled = []
        closed = []
        original = self.gateway.scan

        def counting_scan(request, *, batch_size):
            scan = original(request, batch_size=batch_size)

            def batches():
                for batch in scan:
                    pulled.append(len(batch))
                    yield batch

            class Wrapped:
                def __iter__(self_inner):
                    return batches()

                def close(self_inner):
                    closed.append(True)
                    scan.close()

                @property
                def completed_metadata(self_inner):
                    return scan.completed_metadata

            return Wrapped()

        gateway = mock.Mock()
        gateway.scan = counting_scan
        first_two = sum(_event_bytes(e) for e in RUN1[:2])
        with self.assertRaises(ConsumerApiError):
            execute_l2_book_events_query(
                self.query(), gateway=gateway, batch_size=1, max_result_bytes=first_two - 1
            )
        # one event per batch: the third batch is the first to exceed the bound
        self.assertEqual(pulled, [1, 1])
        self.assertEqual(closed, [True])  # the scan was aborted, not drained

    def test_the_default_bound_is_under_the_j02_ceiling(self):
        self.assertEqual(DEFAULT_L2_MAX_RESULT_BYTES, 12_000_000)
        self.assertLess(DEFAULT_L2_MAX_RESULT_BYTES, 16 * 1024 * 1024)


class J02WireTests(Base):
    def message(self, **query):
        payload = {
            "schema_version": "j02-request-v1",
            "request_id": "request-l2",
            "query": {
                "venue": "bybit", "instrument": "BTCUSDT",
                "start": f"{DAY}T00:00:00Z", "end": f"{NEXT}T00:00:00Z",
                "representation": {"kind": L2_BOOK_EVENTS_KIND, "version": 1, "definition": {}},
                "options": {},
            },
        }
        payload["query"].update(query)
        return json.dumps(payload)

    def test_the_v1_envelope_carries_the_stable_events_without_a_new_field(self):
        from quant_platform.application.api_transport_server import J02_REQUEST_SCHEMA_VERSION

        direct = execute_l2_book_events_query(self.query(), gateway=self.gateway)
        message = self.message().replace("j02-request-v1", J02_REQUEST_SCHEMA_VERSION)
        response = json.loads(
            handle_api_transport_message(message, execute=lambda q: direct)
        )
        self.assertEqual(response["status"], "ok")
        result = response["result"]
        self.assertEqual(result, encode_consumer_result(direct))
        self.assertEqual(result["data"][0], RUN1[0].stable_dict())
        self.assertEqual([row["native_sequence"] for row in result["data"]], ["1", "2", "3", "6", "7", "8"])
        self.assertEqual(result["representation"], {"kind": L2_BOOK_EVENTS_KIND, "version": 1})
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(len(result["coverage"]["gaps"]), 1)
        self.assertEqual(
            set(result),
            {"request", "request_identity", "representation", "data", "requested_interval",
             "returned_temporal_bounds", "coverage", "provenance", "row_count"},
        )

    def test_result_too_large_surfaces_as_the_typed_error(self):
        from quant_platform.application.api_transport_server import J02_REQUEST_SCHEMA_VERSION

        def execute(query):
            return execute_l2_book_events_query(query, gateway=self.gateway, max_result_bytes=10)

        message = self.message().replace("j02-request-v1", J02_REQUEST_SCHEMA_VERSION)
        response = json.loads(handle_api_transport_message(message, execute=execute))
        self.assertEqual(response["status"], "error")
        self.assertEqual(response["error"]["code"], "result_too_large")
        self.assertEqual(response["error"]["context"]["max_result_bytes"], "10")


class DispatchTests(Base):
    def application(self, **config):
        return MarketDataApplication(MarketDataApplicationConfig(catalog_dsn="unused", **config))

    def test_the_l2_kind_is_routed_and_other_kinds_follow_the_trades_path(self):
        with mock.patch.object(composition, "Catalog", lambda dsn: self.catalog):
            result = self.application().execute(self.query())
            self.assertEqual(self.seqs(result.data), ["1", "2", "3", "6", "7", "8"])
            # an unknown kind is refused by the unchanged trades path
            with self.assertRaises(ConsumerApiError) as caught:
                self.application().execute(
                    self.query(representation=RepresentationRef("candles", 1))
                )
            self.assertEqual(caught.exception.code, ConsumerErrorCode.UNSUPPORTED_REPRESENTATION)
            self.assertEqual(caught.exception.context["supported"], "trades@1")

    def test_the_documented_byte_bound_is_the_one_applied(self):
        with mock.patch.object(composition, "Catalog", lambda dsn: self.catalog):
            with mock.patch.object(composition, "DEFAULT_L2_MAX_RESULT_BYTES", 10):
                with self.assertRaises(ConsumerApiError) as caught:
                    self.application().execute(self.query())
        self.assertEqual(caught.exception.code, ConsumerErrorCode.RESULT_TOO_LARGE)
        self.assertEqual(caught.exception.context["max_result_bytes"], "10")

    def test_the_trades_config_is_unchanged(self):
        self.assertEqual(
            {"catalog_dsn", "batch_size", "max_result_rows"},
            {f.name for f in __import__("dataclasses").fields(MarketDataApplicationConfig())},
        )

    def test_a_non_l2_query_is_handed_to_the_trades_path_untouched(self):
        sentinel = object()
        calls = []

        def stub(query, **kwargs):
            calls.append(query)
            return sentinel

        with mock.patch.object(composition, "Catalog", lambda dsn: self.catalog):
            with mock.patch.object(composition, "execute_market_data_query", stub):
                self.assertIs(self.application().execute("opaque-query"), sentinel)  # type: ignore[arg-type]
                trades = self.query(representation=RepresentationRef("trades", 1))
                self.assertIs(self.application().execute(trades), sentinel)
        self.assertEqual(calls, ["opaque-query", trades])


if __name__ == "__main__":
    unittest.main()
