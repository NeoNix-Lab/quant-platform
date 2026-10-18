"""M02 (issue #462): the canonical failed-aggression FeatureDefinition and its exact evaluator (ADR-0073 sections 1, 3, 4)."""

from __future__ import annotations

from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
import random
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import Instant, TradeRecord  # noqa: E402
from quant_platform.features import failed_aggression as module  # noqa: E402
from quant_platform.features.definitions import (  # noqa: E402
    FeatureDefinitionError,
    InputMaturity,
    ObservationLifecycle,
    OutputValueKind,
)
from quant_platform.features.failed_aggression import (  # noqa: E402
    BAR_FLOW_BAR_CONTRACT,
    FailedAggressionBarInput,
    evaluate_failed_aggression,
    evaluate_failed_aggression_bars,
    failed_aggression_definition,
)
from quant_platform.features.h01_imbalance import diagonal_imbalance_definition  # noqa: E402
from quant_platform.representation.bar_flow import BarFlowDefinitionV1, aggregate_historical_bar_flow  # noqa: E402
from quant_platform.research.events import (  # noqa: E402
    ComparisonOperator,
    EventSpec,
    ThresholdPredicate,
    detect_events,
)
from quant_platform.research.hypothesis import HypothesisSpec, ObservableReference  # noqa: E402

DEFAULT_IDENTITY = "feature-definition-v1:sha256:80230bfe832fb8f17f7112a5112f93e3079f29dc0cdb6c2985d4edf65b460ae3"
START, END = "2024-01-15T00:00:00Z", "2024-01-15T00:01:00Z"


def bar(open_="108", high="120", low="100", close="105", buy="6.5", sell="3.5", start=START, end=END, **extra):
    return FailedAggressionBarInput(start, end, open_, high, low, close, buy, sell, **extra)


def bars_at(minute: int, **values) -> FailedAggressionBarInput:
    base = Instant.parse("2024-01-15T00:00:00Z").epoch_ns + minute * 60_000_000_000
    return bar(start=Instant(base), end=Instant(base + 60_000_000_000), **values)


def value(definition, **values) -> int:
    return evaluate_failed_aggression(definition, bar(**values)).value


class FailedAggressionDefinitionTests(unittest.TestCase):
    def test_the_default_definition_is_pinned_and_has_the_adr_shape(self) -> None:
        definition = failed_aggression_definition()
        self.assertEqual(DEFAULT_IDENTITY, definition.identity)
        self.assertEqual("order_flow.failed_aggression", definition.feature_key)
        self.assertEqual("1", definition.semantic_version)
        self.assertEqual(InputMaturity.FINAL_ONLY, definition.input_maturity)
        self.assertEqual(["current"], [item.kind.value for item in definition.support])
        self.assertEqual(0, definition.initialization.minimum_history)
        contract = definition.input_contract
        self.assertEqual(("bar_flow.bar", "1", "atomic"), (contract.contract_key, contract.contract_version, contract.shape.value))
        self.assertEqual(
            ("close", "high", "low", "open", "taker_buy_volume", "taker_sell_volume"), contract.required_observables,
        )
        self.assertIs(BAR_FLOW_BAR_CONTRACT, contract)
        output = definition.output_contract
        self.assertEqual(
            (OutputValueKind.NUMERIC, "failed_aggression_signal_v1", "dimensionless", "exact"),
            (output.value_kind, output.shape, output.dimension.value, output.numerical_equivalence.kind.value),
        )
        self.assertEqual(
            {"imbalance_threshold": "0.3", "wick_ratio": "0.5"},
            {parameter.name: parameter.value for parameter in definition.canonical_parameters},
        )
        self.assertEqual("after_required_support_available", definition.availability.causal_floor_rule)

    def test_identity_is_deterministic_and_parameter_sensitive(self) -> None:
        self.assertEqual(DEFAULT_IDENTITY, failed_aggression_definition(imbalance_threshold="0.30", wick_ratio="0.50").identity)
        self.assertEqual(DEFAULT_IDENTITY, failed_aggression_definition(imbalance_threshold=Decimal("0.3")).identity)
        others = {
            failed_aggression_definition(imbalance_threshold="0.25").identity,
            failed_aggression_definition(wick_ratio="0.4").identity,
            failed_aggression_definition(imbalance_threshold="1", wick_ratio="0").identity,
            diagonal_imbalance_definition().identity,
        }
        self.assertEqual(4, len(others))
        self.assertNotIn(DEFAULT_IDENTITY, others)

    def test_parameter_domains_are_enforced_by_the_canonical_constructor(self) -> None:
        for accepted in ({"imbalance_threshold": "1"}, {"imbalance_threshold": "0.0001"}, {"wick_ratio": "0"}, {"wick_ratio": "0.9999"}):
            failed_aggression_definition(**accepted)
        for refused in (
            {"imbalance_threshold": "0"}, {"imbalance_threshold": "-0.1"}, {"imbalance_threshold": "1.0001"},
            {"wick_ratio": "1"}, {"wick_ratio": "-0.0001"}, {"wick_ratio": "2"},
            {"imbalance_threshold": 0.3}, {"wick_ratio": "x"}, {"imbalance_threshold": None}, {"wick_ratio": True},
        ):
            with self.subTest(refused=refused), self.assertRaises(FeatureDefinitionError):
                failed_aggression_definition(**refused)


class FailedAggressionRuleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definition = failed_aggression_definition()

    def test_golden_vectors_for_both_signs(self) -> None:
        # Buyers trapped: D = 3.0 = 0.3 * T, upper wick 12 > 0.5 * 20, close below open.
        self.assertEqual(1, value(self.definition))
        # Sellers trapped: the mirror image.
        self.assertEqual(-1, value(self.definition, open_="112", close="114", buy="3.5", sell="6.5"))
        # No signal: balanced flow, or the wrong close direction.
        self.assertEqual(0, value(self.definition, buy="5", sell="5"))
        self.assertEqual(0, value(self.definition, open_="105", close="108"))
        self.assertEqual(0, value(self.definition, open_="112", close="114", buy="6.5", sell="3.5"))

    def test_boundaries_follow_the_adr(self) -> None:
        d = self.definition
        self.assertEqual(1, value(d, buy="6.5", sell="3.5"), "D == i*T counts (>=)")
        self.assertEqual(0, value(d, buy="6.4", sell="3.6"), "just under the threshold")
        self.assertEqual(0, value(d, open_="110", close="105"), "a wick of exactly w*range does not count (>)")
        self.assertEqual(1, value(d, open_="109.9", close="105"), "a wick just above w*range counts")
        self.assertEqual(0, value(d, high="100", low="100", open_="100", close="100"), "H == L never satisfies the wick test")
        self.assertEqual(1, value(d, buy="9", sell="0"), "one-sided volume is valid")
        self.assertEqual(1, value(failed_aggression_definition(imbalance_threshold="1"), buy="9", sell="0"))
        self.assertEqual(0, value(failed_aggression_definition(imbalance_threshold="1"), buy="9", sell="0.001"))
        self.assertEqual(1, value(failed_aggression_definition(wick_ratio="0"), open_="119", close="105"))

    def test_directions_are_exclusive(self) -> None:
        d = self.definition
        for buy, sell in (("9", "1"), ("1", "9"), ("5", "5"), ("0", "4"), ("4", "0")):
            for open_, close in (("108", "105"), ("105", "108"), ("112", "114"), ("114", "112")):
                outcome = value(d, buy=buy, sell=sell, open_=open_, close=close)
                self.assertIn(outcome, (-1, 0, 1))
        self.assertEqual(0, value(d, buy="5", sell="5", open_="112", close="114"))

    def test_arithmetic_is_exact_and_independent_of_the_decimal_context(self) -> None:
        d = self.definition
        # 21 significant digits around the boundary: a float or a 3-digit context would put these on the wrong side.
        at_boundary = dict(buy="6.5000000000000000000001", sell="3.4999999999999999999999")
        below = dict(buy="6.4999999999999999999999", sell="3.5000000000000000000001")
        self.assertEqual(1, value(d, **at_boundary))
        self.assertEqual(0, value(d, **below))
        with localcontext() as ctx:
            ctx.prec = 3
            self.assertEqual(1, value(d, **at_boundary))
            self.assertEqual(0, value(d, **below))
        wick_edge = dict(open_="110.0000000000000000001", close="105")
        self.assertEqual(0, value(d, **wick_edge))
        self.assertEqual(1, value(d, open_="109.9999999999999999999", close="105"))

    def test_the_division_free_rule_matches_the_research_ratio_rule(self) -> None:
        rng = random.Random(462)
        outcomes = set()
        for _ in range(3000):
            low = Decimal(rng.randint(1000, 2000)) / 10
            span = Decimal(rng.randint(0, 40)) / 10
            high = low + span
            steps = int(span * 10)
            open_ = low + Decimal(rng.randint(0, steps)) / 10
            close = low + Decimal(rng.randint(0, steps)) / 10
            buy, sell = Decimal(rng.randint(0, 50)) / 10, Decimal(rng.randint(0, 50)) / 10
            if buy + sell == 0:
                continue
            i, w = rng.choice(("0.1", "0.3", "0.5", "1")), rng.choice(("0", "0.25", "0.5", "0.75"))
            definition = failed_aggression_definition(imbalance_threshold=i, wick_ratio=w)
            actual = evaluate_failed_aggression(
                definition, bar(*(str(x) for x in (open_, high, low, close, buy, sell))),
            ).value
            fi, fw = Fraction(i), Fraction(w)
            ratio = Fraction(buy - sell) / Fraction(buy + sell)
            range_ = Fraction(high - low)
            expected = 0
            if ratio >= fi and Fraction(high - max(open_, close)) > fw * range_ and close < open_:
                expected = 1
            elif ratio <= -fi and Fraction(min(open_, close) - low) > fw * range_ and close > open_:
                expected = -1
            self.assertEqual(expected, actual, (open_, high, low, close, buy, sell, i, w))
            outcomes.add(actual)
        self.assertEqual({-1, 0, 1}, outcomes)

    def test_the_rule_has_no_float_and_no_division(self) -> None:
        source = (ROOT / "src/quant_platform/features/failed_aggression.py").read_text(encoding="utf-8")
        body = source[source.index("def _signal("):source.index("def evaluate_failed_aggression(")]
        for forbidden in ("float(", " / ", "//", "ctx.divide", ".quantize(", "round("):
            self.assertNotIn(forbidden, body)

    def test_a_mutated_wick_comparison_is_detected_by_the_boundary_vectors(self) -> None:
        def mutant(b, threshold, wick):  # ">" relaxed to ">=" in the wick test
            buy, sell = Decimal(b.taker_buy_volume), Decimal(b.taker_sell_volume)
            high, low, open_, close = (Decimal(b.high), Decimal(b.low), Decimal(b.open), Decimal(b.close))
            if buy - sell >= threshold * (buy + sell) and high - max(open_, close) >= wick * (high - low) and close < open_:
                return 1
            return 0

        boundary = bar(open_="110", close="105")
        self.assertEqual(0, evaluate_failed_aggression(self.definition, boundary).value)
        with mock.patch.object(module, "_signal", mutant):
            self.assertEqual(1, evaluate_failed_aggression(self.definition, boundary).value)


class FailedAggressionEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definition = failed_aggression_definition()

    def test_the_observation_is_final_causal_and_deterministic(self) -> None:
        observation = evaluate_failed_aggression(
            self.definition, bar(observed_available_at="2024-01-15T00:01:02Z", observed_finalized_at="2024-01-15T00:01:05Z"),
        )
        self.assertEqual(ObservationLifecycle.FINAL, observation.lifecycle)
        self.assertEqual(self.definition.definition_id, observation.definition_id)
        self.assertEqual(Instant.parse(END), observation.causal_available_at)
        self.assertEqual(Instant.parse("2024-01-15T00:01:02Z"), observation.observed_available_at)
        self.assertEqual(Instant.parse("2024-01-15T00:01:05Z"), observation.observed_finalized_at)
        self.assertEqual("bar:2024-01-15T00:00:00Z", observation.support_identity.observation_identity)
        self.assertEqual(self.definition.input_contract.identity, observation.support_identity.input_contract_identity)
        self.assertEqual(observation.identity, evaluate_failed_aggression(self.definition, bar()).identity)
        self.assertIsNone(evaluate_failed_aggression(self.definition, bar()).observed_available_at, "never fabricated")
        with self.assertRaises(FeatureDefinitionError):
            evaluate_failed_aggression(self.definition, bar(observed_available_at="2024-01-15T00:00:59Z"))

    def test_a_non_canonical_definition_is_refused(self) -> None:
        with self.assertRaises(FeatureDefinitionError):
            evaluate_failed_aggression(diagonal_imbalance_definition(), bar())
        with self.assertRaises(FeatureDefinitionError):
            evaluate_failed_aggression("not a definition", bar())  # type: ignore[arg-type]
        with self.assertRaises(FeatureDefinitionError):
            evaluate_failed_aggression(self.definition, "not a bar")  # type: ignore[arg-type]
        with self.assertRaises(FeatureDefinitionError):
            evaluate_failed_aggression_bars(diagonal_imbalance_definition(), ())

    def test_malformed_bars_are_refused(self) -> None:
        for refused in (
            dict(buy="-1"), dict(sell="-0.1"), dict(buy="0", sell="0"), dict(buy=6.5), dict(buy="1e3"), dict(buy=""),
            dict(open_="0"), dict(close="130"), dict(open_="99"), dict(high="90"), dict(low="-1"), dict(open_="NaN"),
            dict(start=END, end=START), dict(start=START, end=START),
        ):
            with self.subTest(refused=refused), self.assertRaises(FeatureDefinitionError):
                bar(**refused)

    def test_an_omitted_bar_yields_no_observation(self) -> None:
        observations = evaluate_failed_aggression_bars(
            self.definition, (bars_at(0), bars_at(2, open_="112", close="114", buy="3.5", sell="6.5"), bars_at(5, buy="5", sell="5")),
        )
        self.assertEqual([1, -1, 0], [item.value for item in observations])
        self.assertEqual(
            ["bar:2024-01-15T00:00:00Z", "bar:2024-01-15T00:02:00Z", "bar:2024-01-15T00:05:00Z"],
            [item.support_identity.observation_identity for item in observations],
        )
        self.assertEqual((), evaluate_failed_aggression_bars(self.definition, ()))

    def test_bars_must_be_increasing_and_non_overlapping(self) -> None:
        with self.assertRaises(FeatureDefinitionError):
            evaluate_failed_aggression_bars(self.definition, (bars_at(1), bars_at(0)))
        with self.assertRaises(FeatureDefinitionError):
            evaluate_failed_aggression_bars(self.definition, (bars_at(0), bars_at(0)))
        with self.assertRaises(FeatureDefinitionError):
            evaluate_failed_aggression_bars(self.definition, (bars_at(0), "bar"))  # type: ignore[arg-type]
        overlapping = bar(start="2024-01-15T00:00:30Z", end="2024-01-15T00:01:30Z")
        with self.assertRaises(FeatureDefinitionError):
            evaluate_failed_aggression_bars(self.definition, (bars_at(0), overlapping))


class FailedAggressionEventTests(unittest.TestCase):
    """ADR-0073 section 1: events are a hypothesis-bound threshold over the observation value."""

    def setUp(self) -> None:
        self.definition = failed_aggression_definition()
        self.hypothesis = HypothesisSpec(
            "failed_aggression_reversal", "1", "A failed aggression reverses.",
            (ObservableReference(self.definition.identity),),
        )
        self.observations = evaluate_failed_aggression_bars(
            self.definition,
            (
                bars_at(0), bars_at(1, buy="5", sell="5"), bars_at(2, open_="112", close="114", buy="3.5", sell="6.5"),
                bars_at(4), bars_at(5, buy="5", sell="5"),
            ),
        )

    def spec(self, operator, threshold) -> EventSpec:
        return EventSpec(
            "failed_aggression_event", "1", self.hypothesis.identity, self.definition.identity,
            ThresholdPredicate(operator, threshold),
        )

    def test_the_reference_predicates_detect_each_direction(self) -> None:
        buyers = detect_events(self.spec(ComparisonOperator.EQUAL, 1), self.observations)
        sellers = detect_events(self.spec(ComparisonOperator.EQUAL, -1), self.observations)
        either = detect_events(self.spec(ComparisonOperator.NOT_EQUAL, 0), self.observations)
        self.assertEqual(["2024-01-15T00:00:00Z", "2024-01-15T00:04:00Z"], [e.event_time.isoformat() for e in buyers])
        self.assertEqual(["2024-01-15T00:02:00Z"], [e.event_time.isoformat() for e in sellers])
        self.assertEqual(3, len(either))
        self.assertEqual(
            ["2024-01-15T00:01:00Z", "2024-01-15T00:03:00Z", "2024-01-15T00:05:00Z"],
            [e.causal_available_at.isoformat() for e in either],
            "an event is causally available at the bar's end, never before",
        )
        self.assertEqual(1, buyers[0].match_evidence["value"])

    def test_detection_is_bound_to_this_feature(self) -> None:
        other = EventSpec(
            "other_event", "1", self.hypothesis.identity, diagonal_imbalance_definition().identity,
            ThresholdPredicate(ComparisonOperator.EQUAL, 1),
        )
        with self.assertRaises(Exception):
            detect_events(other, self.observations)


class FailedAggressionOnBarFlowTests(unittest.TestCase):
    """M01 and M02 together: trades -> bar-flow records -> observations."""

    def trades(self) -> list[TradeRecord]:
        base = Instant.parse("2024-01-15T00:00:00Z").epoch_ns

        def trade(index, second, price, size, side):
            return TradeRecord("bybit", "BTCUSDT", Instant(base + second * 1_000_000_000), price, size, side, None, str(index), None)

        return [
            # Minute 0: buyers push up to 120, are absorbed and the bar closes at 105 under its open of 108.
            trade(1, 1, "108.0", "1.0", "buy"), trade(2, 10, "120.0", "5.5", "buy"), trade(3, 20, "100.0", "3.5", "sell"),
            trade(4, 59, "105.0", "0.5", "buy"),
            # Minute 1: balanced.
            trade(5, 61, "105.0", "1.0", "buy"), trade(6, 100, "106.0", "1.0", "sell"),
        ]

    def test_a_trapped_buyers_bar_is_detected_from_trades(self) -> None:
        records = aggregate_historical_bar_flow(
            self.trades(), BarFlowDefinitionV1("1m"), "2024-01-15T00:00:00Z", "2024-01-15T00:02:00Z",
        )
        self.assertEqual(2, len(records))
        inputs = tuple(
            FailedAggressionBarInput(
                r.bucket_start, r.bucket_end, r.open, r.high, r.low, r.close, r.taker_buy_volume, r.taker_sell_volume,
            )
            for r in records
        )
        observations = evaluate_failed_aggression_bars(failed_aggression_definition(), inputs)
        self.assertEqual([1, 0], [item.value for item in observations])
        self.assertEqual("7", records[0].taker_buy_volume)
        self.assertEqual("3.5", records[0].taker_sell_volume)


if __name__ == "__main__":
    unittest.main()
