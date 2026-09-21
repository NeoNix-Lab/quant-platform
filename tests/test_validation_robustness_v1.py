#!/usr/bin/env python3
"""F08 canonical DSR-L / full-CSCV PBO robust comparison proof (ADR-0037)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.validation.robustness import (  # noqa: E402
    ComparableTrialPanel,
    DSRResult,
    EffectiveTrialCountEvidence,
    EvaluationStatus,
    PBOResult,
    RobustnessError,
    compute_moments_v1,
    compute_sharpe_v1,
    evaluate_dsr_v1,
    evaluate_pbo_v1,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _panel(**overrides) -> ComparableTrialPanel:
    fields = {
        "population_id": "population-v1:sha256:" + "a" * 64,
        "trial_ids": ("T1", "T2"),
        "observation_ids": ("o0", "o1", "o2", "o3"),
        "returns": {
            "T1": (0.01, 0.02, -0.01, 0.03),
            "T2": (0.00, 0.01, 0.02, -0.02),
        },
        "return_semantics_id": "excess-return-v1",
    }
    fields.update(overrides)
    return ComparableTrialPanel(**fields)


# ADR-0037 s.10 pinned 8x4 CSCV/PBO reference fixture.
_PBO_FIXTURE_RETURNS = {
    "A": (0.08, 0.12, 0.09, 0.11, -0.08, -0.12, -0.09, -0.11),
    "B": (-0.08, -0.12, -0.09, -0.11, 0.08, 0.12, 0.09, 0.11),
    "C": (0.015, 0.025, 0.018, 0.022, 0.016, 0.024, 0.019, 0.021),
    "D": (-0.005, 0.005, -0.004, 0.004, -0.006, 0.006, -0.003, 0.003),
}


def _pbo_fixture_panel() -> ComparableTrialPanel:
    return ComparableTrialPanel(
        population_id="population-v1:sha256:" + "b" * 64,
        trial_ids=("A", "B", "C", "D"),
        observation_ids=tuple(f"row{i}" for i in range(1, 9)),
        returns=_PBO_FIXTURE_RETURNS,
        return_semantics_id="excess-return-v1",
    )


# ---------------------------------------------------------------------------
# ComparableTrialPanel v1
# ---------------------------------------------------------------------------


class ComparableTrialPanelTests(unittest.TestCase):
    def test_valid_panel_normalizes_order_and_content(self):
        panel = _panel()
        self.assertEqual(("T1", "T2"), panel.trial_ids)
        self.assertEqual(4, panel.observation_count)
        self.assertEqual(2, panel.trial_count)
        self.assertEqual((0.01, 0.02, -0.01, 0.03), panel.returns["T1"])

    def test_panel_is_immutable(self):
        panel = _panel()
        with self.assertRaises(FrozenInstanceError):
            panel.population_id = "other"  # type: ignore[misc]

    def test_duplicate_trial_identity_rejected(self):
        with self.assertRaises(RobustnessError):
            _panel(
                trial_ids=("T1", "T1"),
                returns={"T1": (0.01, 0.02, -0.01, 0.03)},
            )

    def test_non_finite_return_rejected(self):
        with self.assertRaises(RobustnessError):
            _panel(returns={"T1": (0.01, float("nan"), -0.01, 0.03), "T2": (0.0, 0.01, 0.02, -0.02)})

    def test_unequal_length_series_rejected(self):
        with self.assertRaises(RobustnessError):
            _panel(returns={"T1": (0.01, 0.02, -0.01), "T2": (0.0, 0.01, 0.02, -0.02)})

    def test_missing_trial_series_rejected(self):
        with self.assertRaises(RobustnessError):
            _panel(trial_ids=("T1", "T2", "T3"))

    def test_extra_return_series_rejected(self):
        with self.assertRaises(RobustnessError):
            _panel(
                trial_ids=("T1",),
                returns={
                    "T1": (0.01, 0.02, -0.01, 0.03),
                    "T2": (0.0, 0.01, 0.02, -0.02),
                },
            )

    def test_empty_panel_rejected(self):
        with self.assertRaises(RobustnessError):
            _panel(trial_ids=(), returns={})

    def test_duplicate_observation_identity_rejected(self):
        with self.assertRaises(RobustnessError):
            _panel(observation_ids=("o0", "o0", "o2", "o3"))


# ---------------------------------------------------------------------------
# Canonical Sharpe v1
# ---------------------------------------------------------------------------


class SharpeV1Tests(unittest.TestCase):
    def test_hand_checkable_sharpe_vector(self):
        # mean = 2, s^2 = ((1-2)^2 + (2-2)^2 + (3-2)^2) / 2 = 1, SR = 2 / 1 = 2
        result = compute_sharpe_v1((1.0, 2.0, 3.0))
        self.assertIs(EvaluationStatus.EVALUABLE, result.status)
        self.assertAlmostEqual(2.0, result.value, places=12)

    def test_single_observation_is_non_evaluable(self):
        result = compute_sharpe_v1((1.0,))
        self.assertIs(EvaluationStatus.NON_EVALUABLE, result.status)
        self.assertEqual("observation_count_below_2", result.reason)

    def test_zero_variance_is_non_evaluable(self):
        result = compute_sharpe_v1((1.0, 1.0, 1.0))
        self.assertIs(EvaluationStatus.NON_EVALUABLE, result.status)
        self.assertEqual("zero_variance", result.reason)

    def test_non_finite_input_fails_closed(self):
        with self.assertRaises(RobustnessError):
            compute_sharpe_v1((1.0, float("inf"), 2.0))


class MomentsV1Tests(unittest.TestCase):
    def test_hand_checkable_moments_vector(self):
        # values: -3, -1, 1, 3 -> mean 0, m2=5, m3=0, m4=41
        # gamma3 = 0 / 5^1.5 = 0; gamma4 = 41 / 25 = 1.64
        result = compute_moments_v1((-3.0, -1.0, 1.0, 3.0))
        self.assertIs(EvaluationStatus.EVALUABLE, result.status)
        self.assertAlmostEqual(0.0, result.gamma3, places=12)
        self.assertAlmostEqual(1.64, result.gamma4, places=12)

    def test_below_four_observations_is_non_evaluable(self):
        result = compute_moments_v1((1.0, 2.0, 3.0))
        self.assertIs(EvaluationStatus.NON_EVALUABLE, result.status)
        self.assertEqual("observation_count_below_4", result.reason)

    def test_zero_second_moment_is_non_evaluable(self):
        result = compute_moments_v1((1.0, 1.0, 1.0, 1.0))
        self.assertIs(EvaluationStatus.NON_EVALUABLE, result.status)
        self.assertEqual("second_moment_zero", result.reason)


# ---------------------------------------------------------------------------
# DSR-L v1
# ---------------------------------------------------------------------------


class DSRV1Tests(unittest.TestCase):
    def test_selected_trial_tie_break_is_lexicographically_smallest(self):
        # T2 and T1 have identical full-panel Sharpe by construction; T1 < T2.
        panel = _panel(
            trial_ids=("T2", "T1"),
            returns={
                "T2": (1.0, 2.0, 3.0, 4.0),
                "T1": (1.0, 2.0, 3.0, 4.0),
            },
        )
        evidence = EffectiveTrialCountEvidence(k_eff=2.0, evidence_id="evidence-v1:manual")
        result = evaluate_dsr_v1(panel, evidence)
        self.assertIs(EvaluationStatus.EVALUABLE, result.status)
        self.assertEqual("T1", result.selected_trial_id)

    def test_k_eff_out_of_domain_is_non_evaluable(self):
        panel = _panel()
        evidence = EffectiveTrialCountEvidence(k_eff=3.0, evidence_id="evidence-v1:manual")
        result = evaluate_dsr_v1(panel, evidence)
        self.assertIs(EvaluationStatus.NON_EVALUABLE, result.status)
        self.assertEqual("k_eff_out_of_domain", result.reason)

    def test_k_eff_open_interval_one_two_is_non_evaluable(self):
        panel = _panel()
        evidence = EffectiveTrialCountEvidence(k_eff=1.5, evidence_id="evidence-v1:manual")
        result = evaluate_dsr_v1(panel, evidence)
        self.assertIs(EvaluationStatus.NON_EVALUABLE, result.status)
        self.assertEqual("k_eff_in_open_interval_one_two", result.reason)

    def test_k_eff_equal_one_forces_zero_benchmark(self):
        panel = _panel(
            returns={
                "T1": (0.05, 0.03, -0.02, 0.04),
                "T2": (0.01, 0.02, -0.01, 0.03),
            }
        )
        evidence = EffectiveTrialCountEvidence(k_eff=1.0, evidence_id="evidence-v1:manual")
        result = evaluate_dsr_v1(panel, evidence)
        self.assertIs(EvaluationStatus.EVALUABLE, result.status)
        self.assertEqual(0.0, result.sr0)

    def test_required_trial_sharpe_non_evaluable_propagates(self):
        panel = _panel(
            returns={
                "T1": (1.0, 1.0, 1.0, 1.0),  # zero variance -> non-evaluable Sharpe
                "T2": (0.0, 0.01, 0.02, -0.02),
            }
        )
        evidence = EffectiveTrialCountEvidence(k_eff=2.0, evidence_id="evidence-v1:manual")
        result = evaluate_dsr_v1(panel, evidence)
        self.assertIs(EvaluationStatus.NON_EVALUABLE, result.status)
        self.assertIn("trial_sharpe_non_evaluable:T1", result.reason)

    def test_invalid_radicand_domain_is_non_evaluable(self):
        # A two-point return distribution always saturates the kurtosis floor
        # gamma4 == gamma3^2 + 1, which makes
        # A = 1 - gamma3*SR_hat + ((gamma4-1)/4)*SR_hat^2 == (1 - gamma3*SR_hat/2)^2.
        # For T=4 (one high observation h, three low observations l),
        # gamma3 = (T-2)/sqrt(T-1) = 2/sqrt(3), so choosing SR_hat = 2/gamma3
        # drives A to exactly 0 (non-positive), the frozen non-evaluable domain.
        import math

        t_obs = 4
        gamma3 = (t_obs - 2) / math.sqrt(t_obs - 1)
        target_sharpe = 2.0 / gamma3
        s = 1.0  # target ddof=1 sample std
        k = s * math.sqrt(t_obs)
        mean = target_sharpe * s
        low = mean - k / t_obs
        high = low + k
        series = (high, low, low, low)

        panel = _panel(
            trial_ids=("T1",),
            returns={"T1": series},
            observation_ids=("o0", "o1", "o2", "o3"),
        )
        sharpe = compute_sharpe_v1(series)
        self.assertAlmostEqual(target_sharpe, sharpe.value, places=9)

        evidence = EffectiveTrialCountEvidence(k_eff=1.0, evidence_id="evidence-v1:manual")
        result = evaluate_dsr_v1(panel, evidence)
        self.assertIs(EvaluationStatus.NON_EVALUABLE, result.status, result.reason)
        self.assertEqual("dsr_denominator_domain_invalid", result.reason)

    def test_adr_0037_pinned_dsr_reference_vector(self):
        # Primitive formula fixture (ADR-0037 s.9): SR_hat=0.5, T=24, gamma3=0,
        # gamma4=3, sigma_SR=0.2, K_eff=10 -> SR0=0.31491966026915,
        # DSR=0.79866173151637.
        #
        # gamma3/gamma4 are scale- and location-invariant, so a fixed
        # zero-mean "shape" template with exact gamma3=0, gamma4=3 is affinely
        # rescaled (shift + positive scale) to hit any target mean/ddof=1
        # sample variance, hence any target Sharpe, without disturbing the
        # moments. Shape: 3 pairs of +/-1 plus one pair +/-t solves
        # t^4 - 18*t^2 - 15 = 0 for gamma4 == 3 exactly (see derivation in the
        # module-level comment below).
        import math

        observation_count = 24
        target_sharpe = 0.5
        target_sigma_sr = 0.2

        t_squared = (18.0 + math.sqrt(384.0)) / 2.0
        t = math.sqrt(t_squared)
        shape = (1.0, 1.0, 1.0, -1.0, -1.0, -1.0, t, -t)
        repeats = observation_count // len(shape)
        self.assertEqual(observation_count, repeats * len(shape))
        full_shape = shape * repeats
        # ddof=1 sample variance must use the final series length: repeating
        # a zero-mean shape changes sum-of-squares and (T-1) by different
        # factors, so this cannot be derived from the base 8-point shape.
        shape_sample_variance = sum(x * x for x in full_shape) / (len(full_shape) - 1)

        def _shape_series(mean: float, sharpe: float) -> tuple[float, ...]:
            std = mean / sharpe
            scale = std / math.sqrt(shape_sample_variance)
            return tuple(mean + scale * x for x in full_shape)

        # Two-point alternating template (kurtosis irrelevant for non-selected
        # trials): scaled to sample std == 1, so mean == sharpe exactly.
        alternating = tuple(1.0 if i % 2 == 0 else -1.0 for i in range(observation_count))
        alternating_std = math.sqrt(
            sum(x * x for x in alternating) / (len(alternating) - 1)
        )

        def _two_point_series(sharpe: float) -> tuple[float, ...]:
            scale = 1.0 / alternating_std
            return tuple(sharpe + scale * x for x in alternating)

        # Cross-trial dispersion: one outlier trial at target_sharpe and
        # (trial_count-1) trials pinned at a common value `low_sharpe`, solved
        # so the ddof=1 sample stdev over all trial_count Sharpes is exactly
        # target_sigma_sr (see module docstring derivation).
        trial_count = 10
        n_low = trial_count - 1
        # variance = [ ((n_low/trial_count)*d)^2 + n_low*(d/trial_count)^2 ] / (trial_count-1)
        #          = d^2 * n_low * (n_low + 1) / trial_count^2 / (trial_count - 1)
        variance_coefficient = (n_low * trial_count) / (trial_count ** 2 * (trial_count - 1))
        d = math.sqrt((target_sigma_sr ** 2) / variance_coefficient)
        low_sharpe = target_sharpe - d

        panel_trial_ids = tuple(f"T{i}" for i in range(1, trial_count + 1))
        returns = {"T1": _shape_series(mean=1.0, sharpe=target_sharpe)}
        for trial_id in panel_trial_ids[1:]:
            returns[trial_id] = _two_point_series(low_sharpe)

        panel = _panel(
            trial_ids=panel_trial_ids,
            observation_ids=tuple(f"o{i}" for i in range(observation_count)),
            returns=returns,
        )

        sharpe_t1 = compute_sharpe_v1(returns["T1"])
        self.assertAlmostEqual(target_sharpe, sharpe_t1.value, places=9)
        moments_t1 = compute_moments_v1(returns["T1"])
        self.assertAlmostEqual(0.0, moments_t1.gamma3, places=9)
        self.assertAlmostEqual(3.0, moments_t1.gamma4, places=9)
        for trial_id in panel_trial_ids[1:]:
            self.assertAlmostEqual(low_sharpe, compute_sharpe_v1(returns[trial_id]).value, places=9)

        evidence = EffectiveTrialCountEvidence(k_eff=10.0, evidence_id="evidence-v1:manual")
        result = evaluate_dsr_v1(panel, evidence)

        self.assertIs(EvaluationStatus.EVALUABLE, result.status, result.reason)
        self.assertEqual("T1", result.selected_trial_id)
        self.assertAlmostEqual(target_sigma_sr, result.sigma_sr, places=9)
        self.assertAlmostEqual(0.31491966026915, result.sr0, delta=1e-9)
        self.assertAlmostEqual(0.79866173151637, result.dsr, delta=1e-9)

    def test_result_identity_is_deterministic_and_sensitive_to_input_changes(self):
        panel = _panel()
        evidence = EffectiveTrialCountEvidence(k_eff=2.0, evidence_id="evidence-v1:manual")
        result_a = evaluate_dsr_v1(panel, evidence)
        result_b = evaluate_dsr_v1(panel, evidence)
        self.assertEqual(result_a.result_id, result_b.result_id)

        other_evidence = EffectiveTrialCountEvidence(k_eff=2.0, evidence_id="evidence-v1:other")
        result_c = evaluate_dsr_v1(panel, other_evidence)
        self.assertNotEqual(result_a.result_id, result_c.result_id)

        mutated_panel = _panel(returns={"T1": (0.02, 0.02, -0.01, 0.03), "T2": (0.00, 0.01, 0.02, -0.02)})
        result_d = evaluate_dsr_v1(mutated_panel, evidence)
        self.assertNotEqual(result_a.result_id, result_d.result_id)


# ---------------------------------------------------------------------------
# Full CSCV / PBO v1
# ---------------------------------------------------------------------------


class PBOV1Tests(unittest.TestCase):
    def test_invalid_block_count_odd_or_below_four_rejected(self):
        panel = _pbo_fixture_panel()
        with self.assertRaises(RobustnessError):
            evaluate_pbo_v1(panel, 3)
        with self.assertRaises(RobustnessError):
            evaluate_pbo_v1(panel, 2)

    def test_indivisible_observation_count_rejected(self):
        panel = _pbo_fixture_panel()
        with self.assertRaises(RobustnessError):
            evaluate_pbo_v1(panel, 6)

    def test_insufficient_trial_count_rejected(self):
        panel = _panel(
            trial_ids=("T1",),
            returns={"T1": (0.01, 0.02, -0.01, 0.03)},
        )
        with self.assertRaises(RobustnessError):
            evaluate_pbo_v1(panel, 4)

    def test_combination_count_and_complement_construction(self):
        panel = _pbo_fixture_panel()
        result = evaluate_pbo_v1(panel, 4)
        self.assertIs(EvaluationStatus.EVALUABLE, result.status)
        self.assertEqual(6, result.split_count)
        expected_is_blocks = [(0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3)]
        self.assertEqual(expected_is_blocks, [s.in_sample_blocks for s in result.splits])

    def test_adr_0037_pinned_pbo_fixture(self):
        panel = _pbo_fixture_panel()
        result = evaluate_pbo_v1(panel, 4)

        self.assertIs(EvaluationStatus.EVALUABLE, result.status)
        expected = [
            ((0, 1), "A", 1.0, 0.2, -1.3862943611198906),
            ((0, 2), "C", 4.0, 0.8, 1.3862943611198908),
            ((0, 3), "C", 4.0, 0.8, 1.3862943611198908),
            ((1, 2), "C", 4.0, 0.8, 1.3862943611198908),
            ((1, 3), "C", 4.0, 0.8, 1.3862943611198908),
            ((2, 3), "C", 3.0, 0.6, 0.4054651081081642),
        ]
        for split, (blocks, winner, rank, omega, logit) in zip(result.splits, expected):
            self.assertEqual(blocks, split.in_sample_blocks)
            self.assertEqual(winner, split.winner_trial_id)
            self.assertAlmostEqual(rank, split.oos_rank, places=12)
            self.assertAlmostEqual(omega, split.omega, places=12)
            self.assertAlmostEqual(logit, split.logit, places=9)

        self.assertEqual(6, result.split_count)
        self.assertEqual(1, result.negative_logit_count)
        self.assertAlmostEqual(1.0 / 6.0, result.pbo, places=12)

    def test_oos_average_rank_tie_behavior(self):
        # All four trials share the exact same return series, so every IS and
        # every OOS Sharpe is tied across trials in every split: the winner is
        # always the lexicographically smallest trial id, and its OOS rank is
        # always the average of ranks 1..4, i.e. 2.5.
        shared = (0.05, 0.09, -0.02, 0.04, 0.07, -0.03, 0.02, 0.06)
        panel = _panel(
            trial_ids=("T1", "T2", "T3", "T4"),
            observation_ids=tuple(f"o{i}" for i in range(8)),
            returns={"T1": shared, "T2": shared, "T3": shared, "T4": shared},
        )
        result = evaluate_pbo_v1(panel, 4)
        self.assertIs(EvaluationStatus.EVALUABLE, result.status, result.reason)
        for split in result.splits:
            self.assertEqual("T1", split.winner_trial_id)
            self.assertAlmostEqual(2.5, split.oos_rank, places=12)

    def test_strict_lambda_less_than_zero_excludes_exact_zero(self):
        # With N=3 trials, omega=0.5 occurs exactly when the IS winner lands
        # at the middle OOS rank (2 of 3) -- no ties required.
        panel = _panel(
            trial_ids=("T1", "T2", "T3"),
            observation_ids=tuple(f"o{i}" for i in range(8)),
            returns={
                "T1": (-0.0731, 0.0695, 0.0528, -0.049, -0.0009, -0.0101, 0.0303, 0.0577),
                "T2": (-0.0812, -0.0943, 0.0672, -0.0134, 0.0525, -0.0996, -0.0109, 0.0443),
                "T3": (-0.0542, 0.0891, 0.0803, -0.0939, -0.0949, 0.0083, 0.0878, -0.0238),
            },
        )
        result = evaluate_pbo_v1(panel, 4)
        self.assertIs(EvaluationStatus.EVALUABLE, result.status, result.reason)
        median_splits = [split for split in result.splits if split.omega == 0.5]
        self.assertTrue(
            median_splits,
            "fixture must exercise the exact-median boundary: "
            + repr([(s.winner_trial_id, s.oos_rank) for s in result.splits]),
        )
        for split in median_splits:
            self.assertEqual(0.0, split.logit)

    def test_required_split_non_evaluable_propagates_without_dropping(self):
        panel = _panel(
            trial_ids=("T1", "T2"),
            observation_ids=tuple(f"o{i}" for i in range(8)),
            returns={
                "T1": (1.0, 1.0, 1.0, 1.0, 0.01, 0.02, -0.01, 0.03),  # zero-variance IS block
                "T2": (0.01, 0.02, -0.01, 0.03, 0.0, 0.01, 0.02, -0.02),
            },
        )
        result = evaluate_pbo_v1(panel, 4)
        self.assertIs(EvaluationStatus.NON_EVALUABLE, result.status)
        self.assertIn("non_evaluable:split_0:T1", result.reason)
        self.assertEqual((), result.splits)

    def test_result_identity_is_deterministic_and_sensitive_to_input_changes(self):
        panel = _pbo_fixture_panel()
        result_a = evaluate_pbo_v1(panel, 4)
        result_b = evaluate_pbo_v1(panel, 4)
        self.assertEqual(result_a.result_id, result_b.result_id)

        mutated_returns = dict(_PBO_FIXTURE_RETURNS)
        mutated_returns["A"] = tuple(v + 0.001 for v in mutated_returns["A"])
        mutated_panel = ComparableTrialPanel(
            population_id="population-v1:sha256:" + "b" * 64,
            trial_ids=("A", "B", "C", "D"),
            observation_ids=tuple(f"row{i}" for i in range(1, 9)),
            returns=mutated_returns,
            return_semantics_id="excess-return-v1",
        )
        result_c = evaluate_pbo_v1(mutated_panel, 4)
        self.assertNotEqual(result_a.result_id, result_c.result_id)


# ---------------------------------------------------------------------------
# Package boundary evidence for the new module.
# ---------------------------------------------------------------------------


class PackageBoundaryEvidenceTests(unittest.TestCase):
    def test_robustness_module_is_registered_as_validation_owner(self):
        sys.path.insert(0, str(ROOT / "tests"))
        from test_package_boundaries_v1 import OWNERS

        self.assertEqual("validation", OWNERS["quant_platform.validation.robustness"])


if __name__ == "__main__":
    unittest.main()
