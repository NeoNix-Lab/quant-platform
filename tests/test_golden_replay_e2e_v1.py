#!/usr/bin/env python3
"""Unit tests for quant_platform.application.golden_replay: the Donchian
breakout signal, strategy/spec construction, and the CLI's own input
resolution helper. The actual replay-against-real-data proof itself is an
operator-run integration step (issue #146), not a unit test.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

from quant_platform.application import (
    CANONICAL_BTCUSDT_DATASET,
    MinimalBreakoutFeatureProvider,
    build_golden_replay_spec,
    minimal_breakout_strategy,
)
from quant_platform.data.models import Instant
from quant_platform.strategy import StrategySpec

import golden_replay_e2e as golden_cli


@dataclass(frozen=True)
class _FakeRecord:
    exchange_ts: Instant
    price: str


class MinimalBreakoutFeatureProviderTests(unittest.TestCase):
    def test_no_signal_while_window_is_empty(self):
        provider = MinimalBreakoutFeatureProvider(lookback=3)
        record = _FakeRecord(exchange_ts=Instant.parse("2024-01-15T00:00:00Z"), price="100")

        entry, exit_ = provider(record, context=None)

        self.assertFalse(entry.value)
        self.assertFalse(exit_.value)

    def test_entry_fires_on_breakout_above_lookback_max(self):
        provider = MinimalBreakoutFeatureProvider(lookback=3)
        prices = ["100", "101", "99"]
        for index, price in enumerate(prices):
            provider(_FakeRecord(exchange_ts=Instant(index), price=price), context=None)

        entry, exit_ = provider(_FakeRecord(exchange_ts=Instant(99), price="102"), context=None)

        self.assertTrue(entry.value)
        self.assertFalse(exit_.value)

    def test_exit_fires_on_breakdown_below_lookback_min(self):
        provider = MinimalBreakoutFeatureProvider(lookback=3)
        prices = ["100", "101", "99"]
        for index, price in enumerate(prices):
            provider(_FakeRecord(exchange_ts=Instant(index), price=price), context=None)

        entry, exit_ = provider(_FakeRecord(exchange_ts=Instant(99), price="98"), context=None)

        self.assertFalse(entry.value)
        self.assertTrue(exit_.value)

    def test_window_is_bounded_by_lookback_and_never_looks_ahead(self):
        provider = MinimalBreakoutFeatureProvider(lookback=2)
        provider(_FakeRecord(exchange_ts=Instant(0), price="1000"), context=None)
        provider(_FakeRecord(exchange_ts=Instant(1), price="50"), context=None)
        provider(_FakeRecord(exchange_ts=Instant(2), price="51"), context=None)

        # Window is now [50, 51] (the "1000" fell out) -- 52 must breakout.
        entry, exit_ = provider(_FakeRecord(exchange_ts=Instant(3), price="52"), context=None)
        self.assertTrue(entry.value)

    def test_rejects_non_positive_lookback(self):
        with self.assertRaises(ValueError):
            MinimalBreakoutFeatureProvider(lookback=0)

    def test_two_independent_instances_are_deterministic(self):
        prices = ["100", "105", "95", "110", "90"]

        def run(provider):
            results = []
            for index, price in enumerate(prices):
                entry, exit_ = provider(_FakeRecord(exchange_ts=Instant(index), price=price), context=None)
                results.append((entry.value, exit_.value))
            return results

        first = run(MinimalBreakoutFeatureProvider(lookback=2))
        second = run(MinimalBreakoutFeatureProvider(lookback=2))
        self.assertEqual(first, second)


class StrategyAndSpecConstructionTests(unittest.TestCase):
    def test_minimal_breakout_strategy_is_a_valid_strategy_spec(self):
        strategy = minimal_breakout_strategy()
        self.assertIsInstance(strategy, StrategySpec)
        self.assertTrue(strategy.strategy_identity.startswith("strategy-spec-v1:sha256:"))

    def test_build_spec_uses_the_canonical_bybit_btcusdt_dataset_and_real_ordering_policy(self):
        spec = build_golden_replay_spec(
            start="2024-01-15T00:00:00Z", end="2024-01-16T00:00:00Z", initial_capital="10000", batch_size=1000
        )
        self.assertEqual(CANONICAL_BTCUSDT_DATASET, spec.dataset)
        self.assertEqual("bybit-trade-v1-exchange-ts-trade-id-v1", spec.ordering_policy)
        self.assertEqual(1000, spec.batch_size)
        self.assertTrue(spec.identity.startswith("replay-spec-v1:sha256:"))


class CliInputResolutionTests(unittest.TestCase):
    def test_cli_value_wins_over_everything(self):
        self.assertEqual(
            "cli",
            golden_cli._resolved_input(
                "cli", env_names=("GOLDEN_REPLAY_E2E_TEST_VAR",), default="default", field="x"
            ),
        )

    def test_env_value_used_when_cli_is_none(self):
        import os

        os.environ["GOLDEN_REPLAY_E2E_TEST_VAR"] = "from-env"
        try:
            self.assertEqual(
                "from-env",
                golden_cli._resolved_input(
                    None, env_names=("GOLDEN_REPLAY_E2E_TEST_VAR",), default="default", field="x"
                ),
            )
        finally:
            del os.environ["GOLDEN_REPLAY_E2E_TEST_VAR"]

    def test_default_used_when_neither_cli_nor_env_set(self):
        self.assertEqual(
            "default",
            golden_cli._resolved_input(
                None, env_names=("GOLDEN_REPLAY_E2E_NEVER_SET",), default="default", field="x"
            ),
        )

    def test_raises_when_required_and_unset(self):
        with self.assertRaises(SystemExit):
            golden_cli._resolved_input(None, env_names=("GOLDEN_REPLAY_E2E_NEVER_SET",), field="dsn")


if __name__ == "__main__":
    unittest.main()
