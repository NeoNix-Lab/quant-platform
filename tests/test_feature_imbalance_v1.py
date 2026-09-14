#!/usr/bin/env python3
"""E05 pure H01 imbalance kernel proof."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.features.imbalance import (  # noqa: E402
    ImbalanceConfig,
    ImbalanceInputError,
    PriceLevelInput,
    _ratio,
    compute_diagonal_imbalance,
    compute_stacked_imbalance,
)


def level(
    bar_id: str,
    level_index: int,
    buy_volume: float,
    sell_volume: float,
) -> PriceLevelInput:
    return PriceLevelInput(bar_id, level_index, buy_volume, sell_volume)


class FeatureImbalanceV1Tests(unittest.TestCase):
    def test_ratio_preserves_absent_zero_and_zero_zero_counterparty_semantics(self):
        self.assertTrue(math.isnan(_ratio(9.0, None)))
        self.assertTrue(math.isnan(_ratio(9.0, math.nan)))
        self.assertTrue(math.isinf(_ratio(9.0, 0.0)))
        self.assertEqual(0.0, _ratio(0.0, 0.0))
        self.assertEqual(4.5, _ratio(9.0, 2.0))

    def test_diagonal_imbalance_uses_adjacent_level_and_not_same_level(self):
        out = compute_diagonal_imbalance(
            (
                level("t0", 200, 1.0, 2.0),
                level("t0", 201, 9.0, 9.0),
            ),
            ImbalanceConfig(imbalance_ratio=3.0),
        )

        by_level = {row.level_index: row for row in out}
        self.assertEqual(4.5, by_level[201].ask_imbalance_ratio)
        self.assertEqual("ask", by_level[201].imbalance_side)
        self.assertEqual(18.0, by_level[201].total_volume)
        self.assertEqual(0.0, by_level[201].delta)
        with self.assertRaises(FrozenInstanceError):
            by_level[201].imbalance_side = None  # type: ignore[misc]

    def test_edge_of_range_and_no_volume_levels_do_not_become_imbalance(self):
        edge_rows = compute_diagonal_imbalance(
            (
                level("t0", 200, 9.0, 1.0),
                level("t0", 201, 1.0, 9.0),
            )
        )
        by_level = {row.level_index: row for row in edge_rows}
        self.assertTrue(math.isnan(by_level[200].ask_imbalance_ratio))
        self.assertTrue(math.isnan(by_level[201].bid_imbalance_ratio))
        self.assertIsNone(by_level[200].imbalance_side)
        self.assertIsNone(by_level[201].imbalance_side)

        zero_rows = compute_diagonal_imbalance(
            (
                level("t0", 200, 0.0, 0.0),
                level("t0", 201, 0.0, 0.0),
            )
        )
        self.assertEqual(0.0, zero_rows[1].ask_imbalance_ratio)
        self.assertIsNone(zero_rows[1].imbalance_side)

    def test_duplicate_levels_fail_explicitly(self):
        with self.assertRaisesRegex(ImbalanceInputError, "more than one row"):
            compute_diagonal_imbalance(
                (
                    level("t0", 200, 1.0, 1.0),
                    level("t0", 200, 2.0, 2.0),
                )
            )

    def test_stacked_imbalance_marks_three_consecutive_same_side_levels(self):
        config = ImbalanceConfig(imbalance_ratio=3.0, stacked_min_levels=3)

        out = compute_stacked_imbalance(
            (
                level("t0", 200, 1.0, 1.0),
                level("t0", 201, 9.0, 1.0),
                level("t0", 202, 9.0, 1.0),
                level("t0", 203, 9.0, 1.0),
            ),
            config,
        )

        stacked = [row for row in out if row.stacked_imbalance]
        self.assertEqual([201, 202, 203], [row.level_index for row in stacked])
        self.assertEqual({3}, {row.stacked_run_length for row in stacked})

    def test_stacked_runs_break_on_price_gaps_and_bar_boundaries(self):
        config = ImbalanceConfig(imbalance_ratio=3.0, stacked_min_levels=3)

        gap = compute_stacked_imbalance(
            (
                level("t0", 200, 1.0, 1.0),
                level("t0", 201, 9.0, 1.0),
                level("t0", 202, 9.0, 1.0),
                level("t0", 204, 1.0, 1.0),
                level("t0", 205, 9.0, 1.0),
                level("t0", 206, 9.0, 1.0),
            ),
            config,
        )
        self.assertEqual(4, [row.imbalance_side for row in gap].count("ask"))
        self.assertFalse(any(row.stacked_imbalance for row in gap))

        two_bars = compute_stacked_imbalance(
            (
                level("t0", 200, 1.0, 1.0),
                level("t0", 201, 9.0, 1.0),
                level("t0", 202, 9.0, 1.0),
                level("t1", 202, 1.0, 1.0),
                level("t1", 203, 9.0, 1.0),
            ),
            config,
        )
        self.assertEqual(3, [row.imbalance_side for row in two_bars].count("ask"))
        self.assertFalse(any(row.stacked_imbalance for row in two_bars))

    def test_kernel_rejects_invalid_thresholds_and_inputs_without_runtime_dependencies(self):
        with self.assertRaises(ImbalanceInputError):
            ImbalanceConfig(imbalance_ratio=1.0)
        with self.assertRaises(ImbalanceInputError):
            ImbalanceConfig(stacked_min_levels=1)
        with self.assertRaises(ImbalanceInputError):
            PriceLevelInput("t0", 1, -1.0, 0.0)
        with self.assertRaises(ImbalanceInputError):
            compute_diagonal_imbalance((object(),))  # type: ignore[arg-type]


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
