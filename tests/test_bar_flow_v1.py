"""M01: ADR-0073 representation, exact numerics and CLOSED source evidence."""

from dataclasses import replace
from decimal import localcontext
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jsonschema import Draft202012Validator  # noqa: E402
from quant_platform.canonical import canonical_bytes  # noqa: E402
from quant_platform.data.models import (  # noqa: E402
    CoverageInterval, DatasetIdentity, Instant, InvalidRequest, NaturalPartitionIdentity, TradeRecord,
)
from quant_platform.ordering import TRADES_CANONICAL_TOTAL_ORDER_V1  # noqa: E402
from quant_platform import representation as public  # noqa: E402
from quant_platform.representation import (  # noqa: E402
    BarFlowAggressorSideError, BarFlowCoverageError, BarFlowDefinitionV1,
    BarFlowFinalizationError, BarFlowInputError, BarFlowOrderingError,
    BarFlowProvenanceError, BarFlowRecord, HistoricalBarFlowSourceEvidence,
    CandleDefinitionV1, aggregate_historical_bar_flow, aggregate_historical_candles,
    build_historical_bar_flow_result, required_bar_flow_bucket_support,
)
from quant_platform.representation import bar_flow  # noqa: E402


START = "2024-01-01T00:00:00Z"
END = "2024-01-01T00:01:00Z"
SUPPORT = CoverageInterval(Instant.parse(START), Instant.parse(END))
DATASET = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
DEFINITION = BarFlowDefinitionV1("1m")


def trade(ts=START, *, price="100", size="1", side="buy", **kwargs):
    return TradeRecord("bybit", "BTCUSDT", Instant.parse(ts), price, size, side, **kwargs)


def evidence(**overrides):
    values = dict(
        dataset_identity=DATASET, record_schema_id="trade-v1", schema_version=1,
        schema_hash="schema:test", source_ordering_policy=TRADES_CANONICAL_TOTAL_ORDER_V1,
        eligible_source_coverage=(SUPPORT,), finalized_source_intervals=(SUPPORT,),
        finalization_evidence=("immutable:test",), source_result_identity="source:test",
    )
    return HistoricalBarFlowSourceEvidence(**(values | overrides))


def build(rows=(), *, source=None, start=START, end=END, implementation="m01:test"):
    return build_historical_bar_flow_result(
        rows, DEFINITION, start, end, source_evidence=source or evidence(),
        implementation_identity=implementation,
    )


class BarFlowDefinitionTests(unittest.TestCase):
    def test_payload_serialization_and_identity_match_golden(self):
        golden = json.loads((ROOT / "fixtures/bar-flow-definition-v1/golden-1m.json").read_text())
        for duration in ("1m", "60s", "00:01:00", "60000000000ns", 60_000_000_000):
            definition = BarFlowDefinitionV1.from_duration(duration)
            self.assertEqual(golden["canonical_payload"], definition.canonical_payload)
            self.assertEqual(golden["canonical_utf8_serialization"], definition.canonical_utf8_serialization)
            self.assertEqual(golden["expected_definition_identity"], definition.definition_identity)
        self.assertEqual(
            "bar-flow-definition-v1:sha256:0832fa7366bb5918ada993c0f14750d90ec7da41c1010ecdc2f4a6685611e1f2",
            DEFINITION.definition_identity,
        )
        expected = CandleDefinitionV1("1m").canonical_payload
        expected.update(representation_kind="bar_flow", output_record_schema="bar-flow-v1")
        expected["aggregation"].update(
            taker_buy_volume="sum_exact_decimal_size_where_aggressor_side_buy",
            taker_sell_volume="sum_exact_decimal_size_where_aggressor_side_sell",
        )
        expected["aggressor_side"] = {"accepted": ["buy", "sell"], "missing": "refuse_bucket", "unknown": "refuse_bucket"}
        self.assertEqual(expected, DEFINITION.canonical_payload)
        self.assertNotEqual(DEFINITION.definition_identity, CandleDefinitionV1("1m").definition_identity)
        self.assertNotEqual(DEFINITION.definition_identity, BarFlowDefinitionV1("2m").definition_identity)

    def test_duration_bounds_and_invalid_spellings(self):
        for value in ("1s", 1_000_000_000, "1d", 86_400_000_000_000):
            BarFlowDefinitionV1(value)
        for value in ("999999999ns", "86400000000001ns", "2d", 0, -1, True, 1.0, "", "bad"):
            with self.subTest(value=value), self.assertRaises(InvalidRequest):
                BarFlowDefinitionV1(value)

    def test_package_exports_are_importable_and_match_module(self):
        for name in bar_flow.__all__:
            self.assertIs(getattr(public, name), getattr(bar_flow, name))


class BarFlowKernelTests(unittest.TestCase):
    def test_one_pass_matches_candle_ohlcv_without_a_tick_grid(self):
        rows = (
            trade(price="100.12345", size="0.1", trade_id="20"),
            trade(price="99.00001", size="0.02", side="sell", trade_id="100"),
            trade("2024-01-01T00:00:59.999999999Z", price="101.11111", size="1", trade_id="9"),
        )

        class Once:
            count = 0

            def __iter__(self):
                self.count += 1
                if self.count > 1:
                    raise AssertionError("source iterated twice")
                yield from rows

        source = Once()
        result = build(source)
        self.assertEqual(1, source.count)
        record = result.records[0]
        self.assertEqual({
            "bucket_start": START, "bucket_end": END, "open": "100.12345", "high": "101.11111",
            "low": "99.00001", "close": "101.11111", "volume": "1.12", "trade_count": "3",
            "taker_buy_volume": "1.1", "taker_sell_volume": "0.02",
        }, record.stable_dict())
        candle = aggregate_historical_candles(rows, CandleDefinitionV1("1m"), START, END)[0]
        self.assertEqual(candle.stable_dict(), {k: v for k, v in record.stable_dict().items() if not k.startswith("taker_")})

    def test_exact_sums_ignore_decimal_context_and_preserve_high_precision(self):
        buy = "123456789012345678901234567890.12345678901234567890123456789"
        sell = "0.00000000000000000000000000001"
        with localcontext() as ctx:
            ctx.prec = 2
            row = build((trade(size=buy), trade(size=sell, side="sell"))).records[0]
        self.assertEqual(Fraction(buy) + Fraction(sell), Fraction(row.volume))
        self.assertEqual(buy, row.taker_buy_volume)
        self.assertEqual(sell, row.taker_sell_volume)

    def test_half_open_epoch_alignment_and_non_aligned_query_support(self):
        rows = (trade(), trade("2024-01-01T00:00:59.999999999Z", side="sell"), trade(END))
        end = "2024-01-01T00:02:00Z"
        records = aggregate_historical_bar_flow(rows, DEFINITION, START, end)
        self.assertEqual(["2", "1"], [r.trade_count for r in records])
        support = required_bar_flow_bucket_support(DEFINITION, "2024-01-01T00:00:30Z", "2024-01-01T00:01:01Z")
        self.assertEqual((CoverageInterval(Instant.parse(START), Instant.parse(end)),), support)
        result = build(rows[:2], start="2024-01-01T00:00:30Z", end="2024-01-01T00:00:45Z")
        self.assertEqual(START, result.records[0].bucket_start)
        self.assertEqual((SUPPORT,), result.required_bucket_support)
        self.assertTrue(result.coverage.complete)
        # Epoch floor, including negative epochs, never local wall-clock alignment.
        pre = aggregate_historical_bar_flow((trade("1969-12-31T23:59:59.999999999Z"),), DEFINITION,
                                           "1969-12-31T23:59:59Z", "1970-01-01T00:00:00Z")
        self.assertEqual("1969-12-31T23:59:00Z", pre[0].bucket_start)

    def test_covered_empty_buckets_are_omitted_and_one_sided_zero_is_valid(self):
        empty = build()
        self.assertEqual((), empty.records)
        self.assertTrue(empty.coverage.complete)
        self.assertIsNone(empty.returned_record_bounds)
        for side, volumes in (("buy", ("1", "0")), ("sell", ("0", "1"))):
            row = build((trade(side=side),)).records[0]
            self.assertEqual(volumes, (row.taker_buy_volume, row.taker_sell_volume))
        self.assertEqual((), aggregate_historical_bar_flow((), DEFINITION, START, START))
        with self.assertRaises(BarFlowInputError):
            aggregate_historical_bar_flow((trade(),), DEFINITION, START, START)

    def test_bad_side_refuses_the_entire_result_before_reading_more_input(self):
        for side in (None, "", "unknown", "BUY", "other"):
            def rows():
                yield trade()
                yield trade(side=side)
                raise AssertionError("read beyond the invalid aggression evidence")
            with self.subTest(side=side), self.assertRaises(BarFlowAggressorSideError):
                build(rows())

    def test_input_order_support_and_numeric_refusals(self):
        for rows, error in (
            ((trade(END), trade()), BarFlowOrderingError),
            ((trade("2023-12-31T23:59:59Z"),), BarFlowInputError),
            ((trade(END),), BarFlowInputError),
            ((object(),), BarFlowInputError),
            ((trade(price="NaN"),), BarFlowInputError),
            ((trade(size="0"),), BarFlowInputError),
            ((trade(size="1e-3"),), BarFlowInputError),
            ((trade(size=1.0),), BarFlowInputError),
        ):
            end = "2024-01-01T00:02:00Z" if error is BarFlowOrderingError else END
            with self.subTest(rows=rows), self.assertRaises(error):
                aggregate_historical_bar_flow(rows, DEFINITION, START, end)

    def test_record_constructor_enforces_conservation_and_nonnegative_volumes(self):
        row = build((trade(),)).records[0]
        for fields in (
            {"taker_buy_volume": "2"}, {"volume": "2"}, {"taker_sell_volume": "-1"},
            {"taker_buy_volume": "NaN"}, {"taker_buy_volume": 1},
            {"trade_count": "0"}, {"open": "200"}, {"bucket_end": START},
        ):
            with self.subTest(fields=fields), self.assertRaises(BarFlowInputError):
                replace(row, **fields)
        canonical = replace(row, taker_buy_volume="1.000", taker_sell_volume="0.00")
        self.assertEqual(row, canonical)
        self.assertEqual(row, BarFlowRecord(**row.stable_dict()))

    def test_schema_accepts_output_and_rejects_noncanonical_or_extra_values(self):
        validator = Draft202012Validator(json.loads((ROOT / "schemas/bar-flow-v1.json").read_text()))
        Draft202012Validator.check_schema(validator.schema)
        row = build((trade(),)).records[0].stable_dict()
        validator.validate(row)
        for mutation in ({"taker_buy_volume": "1.00"}, {"taker_sell_volume": "-1"}, {"state": "PARTIAL"}):
            self.assertTrue(list(validator.iter_errors(row | mutation)))


class BarFlowEvidenceTests(unittest.TestCase):
    def test_closure_requires_coverage_finalization_and_immutable_provenance_before_iteration(self):
        for overrides, error in (
            ({"eligible_source_coverage": ()}, BarFlowCoverageError),
            ({"finalized_source_intervals": ()}, BarFlowFinalizationError),
            ({"finalization_evidence": ()}, BarFlowFinalizationError),
            ({"source_result_identity": None}, BarFlowProvenanceError),
            ({"source_ordering_policy": "unknown"}, BarFlowOrderingError),
            ({"record_schema_id": "other"}, BarFlowProvenanceError),
            ({"source_representation": "candles@1"}, BarFlowProvenanceError),
            ({"observed_available_at": START}, BarFlowFinalizationError),
            ({"observed_finalized_at": START}, BarFlowFinalizationError),
        ):
            def unread():
                raise AssertionError("invalid evidence must be rejected before source iteration")
                yield
            with self.subTest(overrides=overrides), self.assertRaises(error):
                build(unread(), source=evidence(**overrides))

    def test_partition_path_can_replace_snapshot_but_binds_the_same_dataset(self):
        source = evidence(source_result_identity=None, natural_partitions=(NaturalPartitionIdentity(DATASET, "dt=2024-01-01", 1),),
                          content_hashes=("content:test",), manifest_hashes=("manifest:test",))
        self.assertEqual(1, build((trade(),), source=source).row_count)
        with self.assertRaises(BarFlowProvenanceError):
            build((trade(),), source=replace(source, content_hashes=()))
        with self.assertRaises(BarFlowProvenanceError):
            build((replace(trade(), instrument="ETHUSDT"),))
        other = replace(DATASET, instrument="ETHUSDT")
        with self.assertRaises(BarFlowProvenanceError):
            evidence(natural_partitions=(NaturalPartitionIdentity(other, "dt=2024-01-01", 1),))

    def test_causal_floor_observed_times_and_result_identity(self):
        unknown = build((trade(),))
        self.assertEqual(Instant.parse(END), unknown.records[0].causal_floor)
        self.assertIsNone(unknown.source_evidence.observed_available_at)
        self.assertIsNone(unknown.source_evidence.observed_finalized_at)
        known = build((trade(),), source=evidence(observed_available_at=END, observed_finalized_at=END))
        self.assertEqual("CLOSED", known.state)
        self.assertEqual("bar-flow-v1", known.output_record_schema)
        self.assertEqual(END, known.source_evidence.stable_dict()["observed_finalized_at"])
        self.assertNotEqual(unknown.result_identity, known.result_identity)
        self.assertEqual(unknown.result_identity, build(iter((trade(),))).result_identity)
        self.assertNotEqual(unknown.result_identity, build((trade(),), implementation="another").result_identity)
        digest = hashlib.sha256(canonical_bytes({"domain": public.BAR_FLOW_RESULT_FINGERPRINT_V1_DOMAIN,
                                                **known.stable_dict()}, profile="sorted-compact-ascii-v1")).hexdigest()
        self.assertEqual(f"historical-bar-flow-result-v1:sha256:{digest}", known.result_identity)

    def test_non_aligned_query_requires_full_support_not_only_the_query_interval(self):
        partial = CoverageInterval(Instant.parse("2024-01-01T00:00:30Z"), Instant.parse(END))
        with self.assertRaises(BarFlowCoverageError):
            build((), start=partial.start, source=evidence(eligible_source_coverage=(partial,)))

    def test_mutation_of_accumulator_volume_is_detected_by_record_invariant(self):
        original = bar_flow._candle._BucketAccumulator.record

        def corrupt(accumulator):
            return replace(original(accumulator), volume="999")

        with patch.object(bar_flow._candle._BucketAccumulator, "record", corrupt):
            with self.assertRaisesRegex(BarFlowInputError, "must equal volume"):
                build((trade(),))


if __name__ == "__main__":
    unittest.main()
