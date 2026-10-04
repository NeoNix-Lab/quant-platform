from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.golden_replay import minimal_breakout_strategy  # noqa: E402
from quant_platform.application.market_data import ConsumerApiError, ConsumerErrorCode  # noqa: E402
from quant_platform.application.strategy_consumer import (  # noqa: E402
    StrategyComposeRequest,
    execute_strategy_compose,
)
from quant_platform.data.models import Instant  # noqa: E402
from quant_platform.strategy import StrategyInput  # noqa: E402


DECISION_TIME = Instant.parse("2024-01-02T12:00:00Z")


def request(*, value: object = True, identity: str | None = None) -> StrategyComposeRequest:
    spec = minimal_breakout_strategy()
    return StrategyComposeRequest(
        strategy_spec=spec,
        strategy_identity=spec.strategy_identity if identity is None else identity,
        inputs=(StrategyInput("signal.entry", value, DECISION_TIME, "feature:breakout"),),
        decision_time=DECISION_TIME,
        instrument="BTCUSDT",
    )


class StrategyConsumerApiV1Tests(unittest.TestCase):
    def test_returns_the_existing_composition_result_without_transport(self) -> None:
        result = execute_strategy_compose(request())

        self.assertIsNotNone(result.decision_intent)
        self.assertEqual(result.decision_intent.strategy_identity, minimal_breakout_strategy().strategy_identity)

    def test_request_identity_is_deterministic_and_binds_canonical_inputs(self) -> None:
        self.assertEqual(request().request_identity, request().request_identity)
        self.assertNotEqual(request().request_identity, request(value=False).request_identity)

    def test_invalid_semantic_input_is_a_stable_consumer_error(self) -> None:
        spec = minimal_breakout_strategy()
        mismatched = StrategyComposeRequest(
            strategy_spec=spec,
            strategy_identity="strategy-spec-v1:sha256:" + "0" * 64,
            inputs=(),
            decision_time=DECISION_TIME,
            instrument="BTCUSDT",
        )
        with self.assertRaises(ConsumerApiError) as caught:
            execute_strategy_compose(mismatched)
        self.assertEqual(ConsumerErrorCode.INVALID_REQUEST, caught.exception.code)
        with self.assertRaises(ConsumerApiError) as caught:
            execute_strategy_compose(object())  # type: ignore[arg-type]
        self.assertEqual(ConsumerErrorCode.INVALID_REQUEST, caught.exception.code)

    def test_unavailable_input_is_no_coverage_not_a_domain_exception(self) -> None:
        unavailable = StrategyComposeRequest(
            strategy_spec=minimal_breakout_strategy(),
            strategy_identity=minimal_breakout_strategy().strategy_identity,
            inputs=(StrategyInput(
                "signal.entry",
                True,
                Instant.parse("2024-01-02T12:00:01Z"),
                "feature:breakout",
            ),),
            decision_time=DECISION_TIME,
            instrument="BTCUSDT",
        )
        with self.assertRaises(ConsumerApiError) as caught:
            execute_strategy_compose(unavailable)
        self.assertEqual(ConsumerErrorCode.NO_COVERAGE, caught.exception.code)


if __name__ == "__main__":
    unittest.main()
