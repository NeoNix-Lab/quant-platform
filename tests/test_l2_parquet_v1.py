"""Hermetic tests for the canonical L2 Parquet materializer and scanner (A14b,
ADR-0070 §3-§4).

The live excerpt fixture is the 40-message real capture added by A14a.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pyarrow as pa  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402

from quant_platform.data.l2_materializer import materialize_l2_book_event_v1  # noqa: E402
from quant_platform.data.l2_parquet import (  # noqa: E402
    L2_COLUMNS,
    L2_NULLABLE_COLUMNS,
    L2ContentHasher,
    UINT64_MAX,
    l2_book_event_arrow_schema,
    l2_canonical_content_hash_v1,
    read_l2_book_event_v1,
    scan_l2_book_event_v1,
    scan_l2_book_event_v1_all,
)
from quant_platform.data.manifests import (  # noqa: E402
    emit_dataset_manifest,
    emit_partition_manifest,
)
from quant_platform.data.models import (  # noqa: E402
    DataIntegrityError,
    DatasetIdentity,
    InvalidRequest,
    Instant,
    L2BookEvent,
    L2LevelChange,
    StorageResolutionError,
    canonical_content_hash_v1,
    l2_book_event_identity_v1,
)
from quant_platform.ordering import OrderingProvider  # noqa: E402
from quant_platform.source_adapters.bybit_l2 import (  # noqa: E402
    BYBIT_L2_ORDERING_PROVIDER,
    L2CertificationParameters,
    OUTCOME_ELIGIBLE,
    certify_bybit_l2_book_events,
)
from quant_platform.source_adapters.l2 import normalize_bybit_l2_message  # noqa: E402

IDENTITY = DatasetIdentity("canonical", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
BASE_NS = 1_705_276_800_000_000_000
EXCERPT = (
    ROOT
    / "fixtures"
    / "l2-book-event-v1"
    / "bybit-orderbook-50-btcusdt-live-excerpt-2026-10-14.jsonl"
)


def up(side, price, size="1", order_count=None):
    return L2LevelChange(side, price, size, "upsert", order_count)


def rm(side, price):
    return L2LevelChange(side, price, "0", "delete")


def make(seq, u, kind="delta", *, ns=None, bids=(), asks=(), **overrides):
    values = dict(
        venue="bybit",
        market_type="linear_perp",
        instrument="BTCUSDT",
        native_symbol="BTCUSDT",
        source_channel="orderbook.200.BTCUSDT",
        acquisition_mode="historical_archive",
        event_type=kind,
        exchange_ts=Instant(BASE_NS + (ns if ns is not None else (seq if isinstance(seq, int) else 0))),
        provider_ts=None,
        receive_ts=None,
        source_depth_limit=200,
        native_sequence=str(seq),
        native_update_id=str(u),
        bids=bids or (up("bid", "100.0"),),
        asks=asks,
    )
    values.update(overrides)
    return L2BookEvent(**values)


def sample_events():
    return [
        make(
            1000,
            7,
            "snapshot",
            bids=(up("bid", "100.0", "5", "3"), up("bid", "99.5", "7")),
            asks=(up("ask", "101.0", "4"), up("ask", "100.5", "9", "12")),  # unsorted on purpose
            provider_ts=Instant(BASE_NS + 1_000 + 5_000_000),
            continuity_token="seq:1000|u:7",
        ),
        make(
            1500,
            8,
            bids=(rm("bid", "99.5"), up("bid", "100.25", "0.5")),
            asks=(),
            provider_ts=Instant(BASE_NS + 1_500 + 2_000_000),
            receive_ts=Instant(BASE_NS + 1_500 + 9_000_000),
            native_prev_sequence="1000",
        ),
        make(1501, 9, asks=(rm("ask", "101.0"),), bids=(up("bid", "99.0", "2"),)),
    ]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="l2-parquet-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, events, name="part-001.parquet", **kwargs):
        kwargs.setdefault("dataset_identity", IDENTITY)
        kwargs.setdefault("ordering_provider", BYBIT_L2_ORDERING_PROVIDER)
        return materialize_l2_book_event_v1(self.tmp / name, events, **kwargs)

    def read_all(self, path):
        return [event for batch in scan_l2_book_event_v1_all(path) for event in batch]


class RoundTripTests(Base):
    def test_events_round_trip_exactly(self):
        events = sample_events()
        result = self.write(events)
        self.assertEqual(result.row_count, 3)
        self.assertEqual(result.first_exchange_ts, events[0].exchange_ts)
        self.assertEqual(result.last_exchange_ts, events[-1].exchange_ts)
        back = self.read_all(result.path)
        self.assertEqual([e.stable_dict() for e in back], [e.stable_dict() for e in events])
        self.assertEqual(
            [l2_book_event_identity_v1(e) for e in back],
            [l2_book_event_identity_v1(e) for e in events],
        )
        # unsorted levels keep their source order; nullable fields stay null
        self.assertEqual([c.price for c in back[0].asks], ["101.0", "100.5"])
        self.assertIsNone(back[2].provider_ts)
        self.assertEqual(back[0].bids[0].order_count, "3")
        self.assertIsNone(back[0].bids[1].order_count)
        self.assertEqual(back[1].native_prev_sequence, "1000")

    def test_physical_layout_is_the_adr_layout(self):
        result = self.write(sample_events())
        schema = pq.read_schema(str(result.path))
        self.assertEqual(schema.names, list(L2_COLUMNS))
        self.assertTrue(pa.types.is_uint64(schema.field("native_sequence").type))
        self.assertTrue(pa.types.is_uint64(schema.field("native_update_id").type))
        self.assertTrue(pa.types.is_int32(schema.field("source_depth_limit").type))
        self.assertEqual(schema.field("exchange_ts").type, pa.timestamp("ns", tz="UTC"))
        self.assertFalse(schema.field("exchange_ts").nullable)
        self.assertTrue(schema.field("provider_ts").nullable)
        self.assertTrue(pa.types.is_list(schema.field("bids").type))
        self.assertEqual(
            pq.ParquetFile(str(result.path)).metadata.row_group(0).column(0).compression, "ZSTD"
        )

    def test_uint64_bounds_round_trip_as_canonical_decimal_strings(self):
        for value in (str(2**63 + 1), str(UINT64_MAX), "0"):
            with self.subTest(value=value):
                events = [make(value, value, "snapshot")]
                back = self.read_all(self.write(events, f"u{value}.parquet").path)
                self.assertEqual(back[0].native_sequence, value)
                self.assertEqual(back[0].native_update_id, value)

    def test_sequence_and_update_id_are_validated(self):
        for bad in (str(UINT64_MAX + 1), "007", "1e3", "-1", ""):
            with self.subTest(sequence=bad):
                event = make(1, 1)
                object.__setattr__(event, "native_sequence", bad)
                with self.assertRaises(DataIntegrityError):
                    self.write([event], "bad.parquet")
        missing = make(1, 1)
        object.__setattr__(missing, "native_update_id", None)
        with self.assertRaises(DataIntegrityError):
            self.write([missing], "missing.parquet")
        self.assertFalse((self.tmp / "bad.parquet").exists())

    def test_multiple_row_groups_keep_order(self):
        events = [make(100 + i, i) for i in range(25)]
        result = self.write(events, row_group_size=10)
        metadata = pq.ParquetFile(str(result.path)).metadata
        self.assertEqual(
            [metadata.row_group(i).num_rows for i in range(metadata.num_row_groups)], [10, 10, 5]
        )
        back = self.read_all(result.path)
        self.assertEqual([e.native_sequence for e in back], [str(100 + i) for i in range(25)])

    def test_zero_events_materialize_and_scan_empty(self):
        result = self.write([], "empty.parquet")
        self.assertEqual(result.row_count, 0)
        self.assertIsNone(result.first_exchange_ts)
        self.assertIsNone(result.last_exchange_ts)
        self.assertEqual(self.read_all(result.path), [])


class ReproducibilityAndAtomicityTests(Base):
    def test_same_input_gives_the_same_bytes_regardless_of_name_or_run(self):
        first = self.write(sample_events(), "a.parquet")
        second = self.write(sample_events(), "nested-b.parquet")
        self.assertEqual(first.sha256, second.sha256)
        self.assertEqual(first.path.read_bytes(), second.path.read_bytes())
        self.assertEqual(first.canonical_content_hash_v1, second.canonical_content_hash_v1)

    def test_out_of_order_and_duplicate_input_is_refused_without_a_target(self):
        for name, events in (
            ("regress.parquet", [make(10, 1), make(9, 2)]),
            ("dup.parquet", [make(10, 1), make(10, 2)]),
        ):
            with self.subTest(name=name):
                with self.assertRaises(DataIntegrityError):
                    self.write(events, name)
                self.assertFalse((self.tmp / name).exists())
        self.assertEqual([p.name for p in self.tmp.iterdir()], [])

    def test_a_failure_leaves_an_existing_target_untouched(self):
        good = self.write(sample_events(), "keep.parquet")
        before = good.path.read_bytes()
        with self.assertRaises(DataIntegrityError):
            self.write([make(10, 1), make(10, 2)], "keep.parquet")
        self.assertEqual(good.path.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), ["keep.parquet"])

    def test_a_failing_iterator_leaves_no_temporary_file(self):
        def boom():
            yield make(1, 1, "snapshot")
            raise RuntimeError("source failed")

        with self.assertRaises(RuntimeError):
            self.write(boom(), "boom.parquet")
        self.assertEqual([p.name for p in self.tmp.iterdir()], [])

    def test_unsupported_codec_is_refused_before_a_temporary_exists(self):
        with self.assertRaises(Exception):
            self.write(sample_events(), "codec.parquet", compression="not-a-codec")
        self.assertEqual([p.name for p in self.tmp.iterdir()], [])

    def test_input_is_consumed_once_and_streamed(self):
        pulled = []

        def source():
            for i in range(30):
                pulled.append(i)
                yield make(100 + i, i)

        result = self.write(source(), "stream.parquet", row_group_size=7)
        self.assertEqual(len(pulled), 30)
        self.assertEqual(result.row_count, 30)


class Uint64Tests(unittest.TestCase):
    def test_canonical_uint64_is_strict(self):
        from quant_platform.data.l2_parquet import canonical_uint64

        self.assertEqual(canonical_uint64("0", "f"), 0)
        self.assertEqual(canonical_uint64(str(UINT64_MAX), "f"), UINT64_MAX)
        for bad in (str(UINT64_MAX + 1), "007", "00", "1e3", "-1", "", " 1", "1 ", None, 5):
            with self.subTest(value=bad):
                with self.assertRaises(DataIntegrityError):
                    canonical_uint64(bad, "f")  # type: ignore[arg-type]


class ValidationTests(Base):
    def test_identity_provider_and_path_are_validated(self):
        events = [make(1, 1)]
        for identity in (
            DatasetIdentity("raw", "l2", "bybit", "BTCUSDT", "l2-book-event-v1"),
            DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "l2-book-event-v1"),
            DatasetIdentity("canonical", "l2", "bybit", "BTCUSDT", "trade-v1"),
        ):
            with self.subTest(identity=identity.stable_dict()):
                with self.assertRaises(InvalidRequest):
                    self.write(events, "x.parquet", dataset_identity=identity)
        with self.assertRaises(InvalidRequest):
            self.write(events, "x.txt")
        with self.assertRaises(InvalidRequest):
            self.write(events, "x.parquet", row_group_size=0)
        with self.assertRaises(InvalidRequest):
            self.write(events, "x.parquet", ordering_provider="not a provider")

    def test_the_materializer_checks_the_identity_itself_not_only_the_provider(self):
        permissive = OrderingProvider(
            identity="p",
            satisfies_requirements=frozenset({"l2-book-events@1-canonical-total-order-v1"}),
            key=lambda e: (int(e.native_sequence),),
            applies_to=lambda identity: True,
        )
        for identity in (
            DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "l2-book-event-v1"),
            DatasetIdentity("raw", "l2", "bybit", "BTCUSDT", "l2-book-event-v1"),
            DatasetIdentity("canonical", "l2", "bybit", "BTCUSDT", "trade-v1"),
        ):
            with self.subTest(identity=identity.stable_dict()):
                with self.assertRaises(InvalidRequest):
                    self.write(
                        [make(1, 1)], "x.parquet", dataset_identity=identity, ordering_provider=permissive
                    )

    def test_provider_must_apply_and_satisfy_the_l2_requirement(self):
        inapplicable = OrderingProvider(
            identity="p",
            satisfies_requirements=frozenset({"l2-book-events@1-canonical-total-order-v1"}),
            key=lambda e: (int(e.native_sequence),),
            applies_to=lambda identity: False,
        )
        wrong_requirement = OrderingProvider(
            identity="p",
            satisfies_requirements=frozenset({"trades@1-canonical-total-order-v1"}),
            key=lambda e: (int(e.native_sequence),),
            applies_to=lambda identity: True,
        )
        for provider in (inapplicable, wrong_requirement):
            with self.assertRaises(InvalidRequest):
                self.write([make(1, 1)], "x.parquet", ordering_provider=provider)

    def test_record_identity_mismatch_and_foreign_values_are_refused(self):
        with self.assertRaises(DataIntegrityError):
            self.write([make(1, 1, instrument="ETHUSDT", native_symbol="ETHUSDT")], "a.parquet")
        with self.assertRaises(DataIntegrityError):
            self.write([make(1, 1, venue="okx")], "b.parquet")
        with self.assertRaises(DataIntegrityError):
            self.write(["not an event"], "c.parquet")  # type: ignore[list-item]
        self.assertEqual([p.name for p in self.tmp.iterdir()], [])


class ScannerTests(Base):
    def setUp(self):
        super().setUp()
        # sub-microsecond spacing: 1 ns apart, to prove ns-granular boundaries
        self.events = [make(10 + i, i + 1, ns=i) for i in range(6)]
        self.path = self.write(self.events).path
        self.t0 = BASE_NS

    def seqs(self, start, end, batch_size=65_536):
        return [
            e.native_sequence
            for batch in scan_l2_book_event_v1(self.path, Instant(start), Instant(end), batch_size)
            for e in batch
        ]

    def test_half_open_interval_at_nanosecond_granularity(self):
        self.assertEqual(self.seqs(self.t0 + 1, self.t0 + 4), ["11", "12", "13"])
        self.assertEqual(self.seqs(self.t0, self.t0 + 1), ["10"])
        self.assertEqual(self.seqs(self.t0 + 5, self.t0 + 6), ["15"])
        self.assertEqual(self.seqs(self.t0 + 3, self.t0 + 3), [])
        self.assertEqual(self.seqs(self.t0 + 100, self.t0 + 200), [])

    def test_microsecond_aligned_bounds_also_filter_correctly(self):
        spaced = [make(10 + i, i + 1, ns=i * 1_000_000) for i in range(5)]
        path = self.write(spaced, "spaced.parquet").path
        got = [
            e.native_sequence
            for batch in scan_l2_book_event_v1(
                path, Instant(BASE_NS + 1_000_000), Instant(BASE_NS + 3_000_000)
            )
            for e in batch
        ]
        self.assertEqual(got, ["11", "12"])

    def test_batch_size_bounds_every_batch_and_order_is_physical(self):
        batches = list(scan_l2_book_event_v1_all(self.path, batch_size=2))
        self.assertTrue(all(len(batch) <= 2 for batch in batches))
        self.assertEqual(
            [e.native_sequence for batch in batches for e in batch], [str(10 + i) for i in range(6)]
        )
        with self.assertRaises(ValueError):
            list(scan_l2_book_event_v1_all(self.path, batch_size=0))

    def test_read_helper_and_missing_file(self):
        self.assertEqual(
            len(read_l2_book_event_v1(self.path, Instant(self.t0), Instant(self.t0 + 6))), 6
        )
        with self.assertRaises(StorageResolutionError):
            list(scan_l2_book_event_v1_all(self.tmp / "absent.parquet"))


class CorruptLayoutTests(Base):
    """Each case changes exactly one property; nullability is kept correct so
    only the targeted rule can fire."""

    def valid_columns(self):
        schema = l2_book_event_arrow_schema(pa)
        event = make(5, 1, "snapshot")
        row = {
            "venue": ["bybit"],
            "market_type": ["linear_perp"],
            "instrument": ["BTCUSDT"],
            "native_symbol": ["BTCUSDT"],
            "source_channel": ["orderbook.200.BTCUSDT"],
            "acquisition_mode": ["historical_archive"],
            "event_type": ["snapshot"],
            "exchange_ts": [event.exchange_ts.epoch_ns],
            "provider_ts": [None],
            "receive_ts": [None],
            "source_depth_limit": [200],
            "native_sequence": [5],
            "native_prev_sequence": [None],
            "native_update_id": [1],
            "continuity_token": [None],
            "bids": [[{"price": "100.0", "size": "1", "action": "upsert", "order_count": None}]],
            "asks": [[]],
        }
        columns = {}
        for name in L2_COLUMNS:
            field_type = schema.field(name).type
            if name in ("exchange_ts", "provider_ts", "receive_ts"):
                columns[name] = pa.array(row[name], type=pa.int64()).cast(field_type)
            else:
                columns[name] = pa.array(row[name], type=field_type)
        return columns, schema

    def write_columns(self, columns, name, *, nullable=None, order=None):
        """Write ``columns`` with a schema that mirrors the canonical
        nullability unless ``nullable`` overrides a column."""

        nullable = nullable or {}
        names = order or list(columns)
        fields = [
            pa.field(
                column,
                columns[column].type,
                nullable=nullable.get(column, column in L2_NULLABLE_COLUMNS),
            )
            for column in names
        ]
        table = pa.Table.from_arrays([columns[column] for column in names], schema=pa.schema(fields))
        path = self.tmp / name
        pq.write_table(table, str(path))
        return path

    def assertRefused(self, path):
        with self.assertRaises(DataIntegrityError):
            list(scan_l2_book_event_v1_all(path))

    def test_the_hand_built_valid_layout_reads(self):
        columns, _ = self.valid_columns()
        events = self.read_all(self.write_columns(columns, "ok.parquet"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].native_sequence, "5")

    def test_a_missing_column_is_refused(self):
        columns, _ = self.valid_columns()
        del columns["asks"]
        self.assertRefused(self.write_columns(columns, "missing.parquet"))

    def test_an_extra_column_is_refused(self):
        columns, _ = self.valid_columns()
        columns["surprise"] = pa.array([None], type=pa.string())
        path = self.write_columns(columns, "extra.parquet", nullable={"surprise": True})
        self.assertRefused(path)

    def test_reordered_columns_are_refused(self):
        columns, _ = self.valid_columns()
        order = list(reversed(list(columns)))
        self.assertRefused(self.write_columns(columns, "reordered.parquet", order=order))

    def test_a_retyped_column_is_refused(self):
        columns, _ = self.valid_columns()
        columns["native_sequence"] = pa.array(["5"], type=pa.string())
        self.assertRefused(self.write_columns(columns, "retyped.parquet"))
        columns, _ = self.valid_columns()
        columns["exchange_ts"] = pa.array([1], type=pa.int64())
        self.assertRefused(self.write_columns(columns, "ts-int.parquet"))

    def test_the_wrong_nullability_is_refused(self):
        columns, _ = self.valid_columns()
        self.assertRefused(
            self.write_columns(columns, "nullable-ts.parquet", nullable={"exchange_ts": True})
        )
        columns, schema = self.valid_columns()
        columns["provider_ts"] = columns["exchange_ts"]  # non-null values
        self.assertRefused(
            self.write_columns(columns, "required-provider.parquet", nullable={"provider_ts": False})
        )

    def test_an_invalid_row_is_a_data_integrity_error(self):
        columns, _ = self.valid_columns()
        columns["bids"] = pa.array(
            [[{"price": "0", "size": "1", "action": "upsert", "order_count": None}]],
            type=schema_level_list(),
        )
        self.assertRefused(self.write_columns(columns, "badprice.parquet"))


def schema_level_list():
    return l2_book_event_arrow_schema(pa).field("bids").type


class ContentHashTests(Base):
    def test_hash_is_deterministic_order_and_field_sensitive(self):
        events = sample_events()
        base = l2_canonical_content_hash_v1(events)
        self.assertEqual(base, l2_canonical_content_hash_v1(list(events)))
        self.assertTrue(base.startswith("canonical-content-hash-v1:sha256:"))
        self.assertNotEqual(base, l2_canonical_content_hash_v1(list(reversed(events))))
        self.assertNotEqual(base, l2_canonical_content_hash_v1(events[:2]))
        changed = [make(1000, 7, "snapshot", bids=(up("bid", "100.0", "5", "3"),))] + events[1:]
        self.assertNotEqual(base, l2_canonical_content_hash_v1(changed))
        # exact decimal text counts: "1.0" is not "1"
        self.assertNotEqual(
            l2_canonical_content_hash_v1([make(1, 1, bids=(up("bid", "100.0", "1"),))]),
            l2_canonical_content_hash_v1([make(1, 1, bids=(up("bid", "100.0", "1.0"),))]),
        )

    def test_empty_hash_differs_from_the_trade_hash(self):
        self.assertNotEqual(l2_canonical_content_hash_v1([]), canonical_content_hash_v1([]))

    def test_incremental_hasher_matches_the_function(self):
        events = sample_events()
        hasher = L2ContentHasher()
        for event in events:
            hasher.update(event)
        self.assertEqual(hasher.count, 3)
        self.assertEqual(hasher.hexdigest_identity(), l2_canonical_content_hash_v1(events))

    def test_materialization_hash_equals_the_function_of_the_read_back_events(self):
        result = self.write(sample_events())
        self.assertEqual(
            result.canonical_content_hash_v1,
            l2_canonical_content_hash_v1(self.read_all(result.path)),
        )

    def test_hash_refuses_foreign_values(self):
        with self.assertRaises(InvalidRequest):
            l2_canonical_content_hash_v1(["x"])  # type: ignore[list-item]


class ManifestIntegrationTests(Base):
    def test_existing_manifest_emitters_accept_l2_unchanged(self):
        dataset_root = self.tmp / "bybit"
        part_dir = dataset_root / "dt=2024-01-15"
        part_dir.mkdir(parents=True)
        result = materialize_l2_book_event_v1(
            part_dir / "part-001.parquet",
            sample_events(),
            dataset_identity=IDENTITY,
            ordering_provider=BYBIT_L2_ORDERING_PROVIDER,
        )
        raw = DatasetIdentity("raw", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
        dataset = emit_dataset_manifest(
            self.tmp / "dataset-manifest.json",
            dataset_identity=IDENTITY,
            created_at="2026-10-15T00:00:00Z",
            derived_from=[raw],
            transform="canonicalize-l2-book-events-v1",
        )
        self.assertEqual(
            dataset.document["rel_root"], "canonical/l2/bybit/BTCUSDT/l2-book-event-v1"
        )
        self.assertEqual(dataset.document["derived_from"][0]["dataset_kind"], "l2")
        # a source-acquired canonical L2 dataset (v2 lineage) is also accepted
        acquired = emit_dataset_manifest(
            self.tmp / "dataset-manifest-v2.json",
            dataset_identity=IDENTITY,
            created_at="2026-10-15T00:00:00Z",
            schema_version="dataset-manifest-v2",
            origin="source_acquired",
            transform="acquire-bybit-orderbook-archive-v1",
        )
        self.assertEqual(acquired.document["origin"], "source_acquired")
        # and the raw L2 dataset (the A14g input layer) needs no lineage
        raw_manifest = emit_dataset_manifest(
            self.tmp / "dataset-manifest-raw.json",
            dataset_identity=raw,
            created_at="2026-10-15T00:00:00Z",
        )
        self.assertEqual(raw_manifest.document["rel_root"], "raw/l2/bybit/BTCUSDT/l2-book-event-v1")
        partition = emit_partition_manifest(
            self.tmp / "partition-manifest.json",
            result,
            dataset_identity=IDENTITY,
            dataset_root=dataset_root,
            partition_key="dt=2024-01-15",
            revision=1,
            rel_path="dt=2024-01-15/part-001.parquet",
            created_at="2026-10-15T00:00:00Z",
            closed_at="2026-10-15T00:00:01Z",
            producer="test-l2-materializer",
            code_ref="test-ref",
        )
        document = partition.document
        self.assertEqual(document["dataset_kind"], "l2")
        self.assertEqual(document["record_schema_id"], "l2-book-event-v1")
        self.assertEqual(document["row_count"], 3)
        self.assertEqual(document["sha256"], result.sha256)
        self.assertEqual(document["first_exchange_ts"], result.first_exchange_ts.isoformat())
        self.assertEqual(partition.canonical_content_hash_v1, result.canonical_content_hash_v1)


class RealExcerptTests(Base):
    def test_live_excerpt_round_trips_and_certifies_eligible_before_and_after(self):
        events = [
            normalize_bybit_l2_message(json.loads(line), acquisition_mode="live_websocket")
            for line in EXCERPT.read_text(encoding="utf-8").splitlines()
        ]
        params = L2CertificationParameters(
            source_channel="orderbook.50.BTCUSDT", source_depth_limit=50, effective_depth=50
        )
        before = certify_bybit_l2_book_events(IDENTITY, events, parameters=params)
        self.assertEqual(before.outcome, OUTCOME_ELIGIBLE)
        result = self.write(events, "excerpt.parquet")
        self.assertEqual(result.row_count, 40)
        back = self.read_all(result.path)
        self.assertEqual([e.stable_dict() for e in back], [e.stable_dict() for e in events])
        after = certify_bybit_l2_book_events(IDENTITY, back, parameters=params)
        self.assertEqual(after.outcome, OUTCOME_ELIGIBLE)
        self.assertEqual(before.stable_dict(), after.stable_dict())


class ScaleTests(Base):
    def test_a_few_thousand_events_stream_and_round_trip(self):
        events = [make(1, 1, "snapshot")] + [
            make(2 + i, 2 + i, bids=(up("bid", f"{100 - (i % 30) * 0.5:.1f}", "1"),)) for i in range(4000)
        ]
        result = self.write(events, "scale.parquet", row_group_size=1000)
        self.assertEqual(result.row_count, 4001)
        self.assertEqual(
            l2_canonical_content_hash_v1(self.read_all(result.path)),
            result.canonical_content_hash_v1,
        )


if __name__ == "__main__":
    unittest.main()
