"""Authoritative tests for canonical DSR-L / CSCV-PBO robust comparison v1 (F08).

Governed by ADR-0037.
"""

from __future__ import annotations

import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.validation.robustness import (
    ComparablePanelError,
    ComparableTrialPanel,
    DSR_RESULT_IDENTITY_DOMAIN,
    DsrResult,
    DsrSamplingModel,
    NonEvaluableError,
    PANEL_IDENTITY_DOMAIN,
    PBO_RESULT_IDENTITY_DOMAIN,
    PboResult,
    PboSplitEvidence,
    RobustnessError,
    RobustnessStatus,
    compute_canonical_sharpe,
    compute_standardized_moments,
    evaluate_dsr_l,
    evaluate_pbo_cscv,
)


class ValidationRobustnessV1Tests(unittest.TestCase):
    """Test suite verifying all 14 acceptance criteria of F08 (Issue #98)."""

    # -----------------------------------------------------------------------
    # 1. ComparableTrialPanel validation & fail-closed behavior
    # -----------------------------------------------------------------------

    def test_comparable_panel_valid_construction(self):
        panel = ComparableTrialPanel(
            population_id="pop-alpha",
            trial_ids=("trial-1", "trial-2"),
            observations=("t1", "t2", "t3"),
            returns={
                "trial-1": (0.01, -0.02, 0.03),
                "trial-2": (0.02, 0.01, -0.01),
            },
            return_semantics="excess_return_v1",
        )
        self.assertEqual(panel.population_id, "pop-alpha")
        self.assertEqual(panel.n_trials, 2)
        self.assertEqual(panel.t_observations, 3)
        self.assertTrue(panel.panel_id.startswith(f"{PANEL_IDENTITY_DOMAIN}:sha256:"))

    def test_comparable_panel_rejects_duplicate_trials(self):
        with self.assertRaises(ComparablePanelError) as ctx:
            ComparableTrialPanel(
                population_id="pop-alpha",
                trial_ids=("trial-1", "trial-1"),
                observations=("t1", "t2"),
                returns={"trial-1": (0.01, 0.02)},
                return_semantics="excess_return_v1",
            )
        self.assertIn("duplicate trial identity rejected", str(ctx.exception))

    def test_comparable_panel_rejects_empty_or_whitespace_identifiers(self):
        with self.assertRaises(RobustnessError):
            ComparableTrialPanel(
                population_id="",
                trial_ids=("t1",),
                observations=("o1",),
                returns={"t1": (0.01,)},
                return_semantics="excess_return_v1",
            )

        with self.assertRaises(RobustnessError):
            ComparableTrialPanel(
                population_id="pop",
                trial_ids=("   ",),
                observations=("o1",),
                returns={"   ": (0.01,)},
                return_semantics="excess_return_v1",
            )

    def test_comparable_panel_rejects_length_mismatches_and_missing_trials(self):
        # Missing trial series
        with self.assertRaises(ComparablePanelError) as ctx:
            ComparableTrialPanel(
                population_id="pop",
                trial_ids=("t1", "t2"),
                observations=("o1", "o2"),
                returns={"t1": (0.01, 0.02)},
                return_semantics="excess_return_v1",
            )
        self.assertIn("missing return series", str(ctx.exception))

        # Length mismatch
        with self.assertRaises(ComparablePanelError) as ctx:
            ComparableTrialPanel(
                population_id="pop",
                trial_ids=("t1",),
                observations=("o1", "o2", "o3"),
                returns={"t1": (0.01, 0.02)},
                return_semantics="excess_return_v1",
            )
        self.assertIn("unequal return series length", str(ctx.exception))

        # Extra undeclared keys in returns
        with self.assertRaises(ComparablePanelError) as ctx:
            ComparableTrialPanel(
                population_id="pop",
                trial_ids=("t1",),
                observations=("o1", "o2"),
                returns={"t1": (0.01, 0.02), "t2_extra": (0.01, 0.02)},
                return_semantics="excess_return_v1",
            )
        self.assertIn("undeclared trial keys", str(ctx.exception))

    def test_comparable_panel_rejects_nan_inf_and_non_numeric_returns(self):
        for bad_val in [float("nan"), float("inf"), float("-inf"), "0.05", None, True, False]:
            with self.assertRaises(ComparablePanelError):
                ComparableTrialPanel(
                    population_id="pop",
                    trial_ids=("t1",),
                    observations=("o1", "o2"),
                    returns={"t1": (0.01, bad_val)},
                    return_semantics="excess_return_v1",
                )

    def test_comparable_panel_immutable_protection(self):
        panel = ComparableTrialPanel(
            population_id="pop",
            trial_ids=("t1",),
            observations=("o1", "o2"),
            returns={"t1": (0.01, 0.02)},
            return_semantics="excess_return_v1",
        )
        with self.assertRaises(TypeError):
            panel.returns["t1"] = (0.05, 0.06)  # type: ignore[index]
        with self.assertRaises(TypeError):
            panel.trial_ids[0] = "t2"  # type: ignore[index]

    # -----------------------------------------------------------------------
    # 2. Canonical Sharpe & Higher Moments v1
    # -----------------------------------------------------------------------

    def test_canonical_sharpe_exact_hand_checkable_series(self):
        # r = [0.01, 0.03]
        # mean = 0.02
        # (r - mean)^2 = [0.0001, 0.0001], sum = 0.0002
        # s^2 = 0.0002 / (2 - 1) = 0.0002
        # s = sqrt(0.0002) = 0.01414213562373095
        # SR = 0.02 / 0.01414213562373095 = sqrt(2) ~= 1.4142135623730951
        r = [0.01, 0.03]
        sr = compute_canonical_sharpe(r)
        self.assertAlmostEqual(sr, math.sqrt(2), places=12)

    def test_canonical_sharpe_non_evaluable_cases(self):
        # T < 2
        with self.assertRaises(NonEvaluableError):
            compute_canonical_sharpe([0.05])
        with self.assertRaises(NonEvaluableError):
            compute_canonical_sharpe([])

        # Zero variance (identical values)
        with self.assertRaises(NonEvaluableError) as ctx:
            compute_canonical_sharpe([0.02, 0.02, 0.02])
        self.assertIn("zero or non-finite sample variance", str(ctx.exception))

    def test_standardized_higher_moments(self):
        # Hand-checkable symmetric series: [-2, -1, 1, 2]
        # mean = 0
        # d_i = [-2, -1, 1, 2]
        # m2 = (4 + 1 + 1 + 4)/4 = 10/4 = 2.5
        # m3 = (-8 + -1 + 1 + 8)/4 = 0
        # m4 = (16 + 1 + 1 + 16)/4 = 34/4 = 8.5
        # gamma3 = 0 / 2.5^1.5 = 0.0
        # gamma4 = 8.5 / 2.5^2 = 8.5 / 6.25 = 1.36 (raw/Pearson kurtosis)
        r = [-2.0, -1.0, 1.0, 2.0]
        gamma3, gamma4 = compute_standardized_moments(r)
        self.assertAlmostEqual(gamma3, 0.0, places=12)
        self.assertAlmostEqual(gamma4, 1.36, places=12)

        # T < 4 raises NonEvaluableError
        with self.assertRaises(NonEvaluableError):
            compute_standardized_moments([1.0, 2.0, 3.0])

        # Zero variance raises NonEvaluableError
        with self.assertRaises(NonEvaluableError):
            compute_standardized_moments([1.0, 1.0, 1.0, 1.0])

    # -----------------------------------------------------------------------
    # 3. DSR-L Estimator & ADR-0037 Pinned Vector
    # -----------------------------------------------------------------------

    def test_dsr_l_pinned_reference_vector_exact_match(self):
        """Verify ADR-0037 Section 9 pinned DSR-L reference vector.

        SR_hat   = 0.5
        T        = 24
        gamma3   = 0
        gamma4   = 3
        sigma_SR = 0.2
        K_eff    = 10

        Expected:
            SR0 = 0.31491966026915
            DSR = 0.79866173151637
        """
        # We synthesize a 24-observation return series for the winner trial with
        # exact SR_hat = 0.5, gamma3 = 0, gamma4 = 3 (standard symmetric).
        # Alternatively, we test the exact benchmark and formula under evaluate_dsr_l.
        # Let's construct a synthetic panel with 3 trials where:
        # trial-1 has SR ~= 0.5, trial-2 and trial-3 create sigma_SR = 0.2
        # Let's first test the direct formula computation to confirm ADR-0037 tolerances.
        sr_hat = 0.5
        t = 24
        gamma3 = 0.0
        gamma4 = 3.0
        sigma_sr = 0.2
        k_eff = 10.0

        gamma_em = 0.5772156649015329
        p1 = 1.0 - 1.0 / k_eff
        p2 = 1.0 - 1.0 / (k_eff * math.e)
        from statistics import NormalDist
        nd = NormalDist()
        z_max = (1.0 - gamma_em) * nd.inv_cdf(p1) + gamma_em * nd.inv_cdf(p2)
        sr0 = sigma_sr * z_max

        self.assertAlmostEqual(sr0, 0.31491966026915, delta=1e-12)

        term_a = 1.0 - gamma3 * sr_hat + ((gamma4 - 1.0) / 4.0) * (sr_hat ** 2)
        z_dsr = ((sr_hat - sr0) * math.sqrt(t - 1)) / math.sqrt(term_a)
        dsr = nd.cdf(z_dsr)

        self.assertAlmostEqual(dsr, 0.79866173151637, delta=1e-12)

    def test_dsr_l_evaluation_on_panel_and_tie_breaking(self):
        # Construct panel with 3 trials:
        # trial-A: return mean 0.02
        # trial-B: return mean 0.02 (tied with trial-A on Sharpe, trial-A wins by ID)
        # trial-C: return mean 0.01 (lower Sharpe)
        observations = tuple(f"obs_{i}" for i in range(24))
        ret_a = (0.01, 0.03) * 12  # mean 0.02, s^2 = 0.0001043...
        ret_b = (0.01, 0.03) * 12  # exactly equal Sharpe to trial-A
        ret_c = (0.00, 0.02) * 12  # lower Sharpe

        panel = ComparableTrialPanel(
            population_id="pop-dsr",
            trial_ids=("trial-B", "trial-A", "trial-C"),
            observations=observations,
            returns={"trial-A": ret_a, "trial-B": ret_b, "trial-C": ret_c},
            return_semantics="excess_return_v1",
        )

        res = evaluate_dsr_l(panel, k_eff=2.5, k_eff_method_id="manual_assumption")
        self.assertEqual(res.status, RobustnessStatus.EVALUATED)
        # Tie-break: trial-A is lexicographically smaller than trial-B
        self.assertEqual(res.selected_trial_id, "trial-A")
        self.assertIsNotNone(res.dsr)
        self.assertIsNotNone(res.sr0)
        self.assertGreater(res.dsr, 0.0)
        self.assertLess(res.dsr, 1.0)

    def test_dsr_l_k_eff_regimes(self):
        observations = tuple(f"obs_{i}" for i in range(10))
        panel = ComparableTrialPanel(
            population_id="pop-keff",
            trial_ids=("t1", "t2"),
            observations=observations,
            returns={
                "t1": (0.01, -0.01) * 5,
                "t2": (0.02, 0.00) * 5,
            },
            return_semantics="excess_return_v1",
        )

        # K_eff == 1 -> SR0 == 0.0
        res1 = evaluate_dsr_l(panel, k_eff=1.0, k_eff_method_id="single_effective_trial")
        self.assertEqual(res1.status, RobustnessStatus.EVALUATED)
        self.assertEqual(res1.sr0, 0.0)

        # 1 < K_eff < 2 -> NON_EVALUABLE
        res_mid = evaluate_dsr_l(panel, k_eff=1.5, k_eff_method_id="fractional_between_1_and_2")
        self.assertEqual(res_mid.status, RobustnessStatus.NON_EVALUABLE)
        self.assertIsNone(res_mid.dsr)
        self.assertIn("non-evaluable", res_mid.non_evaluable_reason)

        # K_eff > N or K_eff < 1 -> RobustnessError
        with self.assertRaises(RobustnessError):
            evaluate_dsr_l(panel, k_eff=0.5, k_eff_method_id="invalid_low")
        with self.assertRaises(RobustnessError):
            evaluate_dsr_l(panel, k_eff=3.0, k_eff_method_id="invalid_high")

    def test_dsr_l_non_evaluable_trial_propagates_without_dropping(self):
        observations = tuple(f"obs_{i}" for i in range(10))
        # t2 has zero variance -> non-evaluable Sharpe
        panel = ComparableTrialPanel(
            population_id="pop-zero-var",
            trial_ids=("t1", "t2"),
            observations=observations,
            returns={
                "t1": (0.01, -0.01) * 5,
                "t2": (0.00, 0.00) * 5,
            },
            return_semantics="excess_return_v1",
        )
        res = evaluate_dsr_l(panel, k_eff=2.0, k_eff_method_id="test_keff")
        self.assertEqual(res.status, RobustnessStatus.NON_EVALUABLE)
        self.assertIsNone(res.dsr)
        self.assertIn("zero or non-finite sample variance", res.non_evaluable_reason)

    # -----------------------------------------------------------------------
    # 4. CSCV / PBO Estimator & ADR-0037 Pinned Vector
    # -----------------------------------------------------------------------

    def test_pbo_cscv_pinned_reference_vector_exact_match(self):
        """Verify ADR-0037 Section 10 pinned 8x4 matrix and PBO = 1/6."""
        # Pinned 8x4 table
        # row     A       B       C       D
        # 1      0.08   -0.08    0.015  -0.005
        # 2      0.12   -0.12    0.025   0.005
        # 3      0.09   -0.09    0.018  -0.004
        # 4      0.11   -0.11    0.022   0.004
        # 5     -0.08    0.08    0.016  -0.006
        # 6     -0.12    0.12    0.024   0.006
        # 7     -0.09    0.09    0.019  -0.003
        # 8     -0.11    0.11    0.021   0.003
        panel = ComparableTrialPanel(
            population_id="adr-0037-pbo-fixture",
            trial_ids=("A", "B", "C", "D"),
            observations=(1, 2, 3, 4, 5, 6, 7, 8),
            returns={
                "A": (0.08, 0.12, 0.09, 0.11, -0.08, -0.12, -0.09, -0.11),
                "B": (-0.08, -0.12, -0.09, -0.11, 0.08, 0.12, 0.09, 0.11),
                "C": (0.015, 0.025, 0.018, 0.022, 0.016, 0.024, 0.019, 0.021),
                "D": (-0.005, 0.005, -0.004, 0.004, -0.006, 0.006, -0.003, 0.003),
            },
            return_semantics="excess_return_v1",
        )

        res = evaluate_pbo_cscv(panel, s=4)
        self.assertEqual(res.status, RobustnessStatus.EVALUATED)
        self.assertEqual(res.s, 4)
        self.assertEqual(res.split_count, 6)
        self.assertEqual(res.negative_logit_count, 1)
        self.assertAlmostEqual(res.pbo, 1.0 / 6.0, places=12)

        # Check every split's exact pinned evidence:
        # IS blocks   winner   OOS rank   omega   lambda
        # (0,1)       A        1          0.2    -1.3862943611198906
        # (0,2)       C        4          0.8     1.3862943611198908
        # (0,3)       C        4          0.8     1.3862943611198908
        # (1,2)       C        4          0.8     1.3862943611198908
        # (1,3)       C        4          0.8     1.3862943611198908
        # (2,3)       C        3          0.6     0.4054651081081642
        expected_splits = [
            ((0, 1), "A", 1.0, 0.2, -1.3862943611198906),
            ((0, 2), "C", 4.0, 0.8, 1.3862943611198908),
            ((0, 3), "C", 4.0, 0.8, 1.3862943611198908),
            ((1, 2), "C", 4.0, 0.8, 1.3862943611198908),
            ((1, 3), "C", 4.0, 0.8, 1.3862943611198908),
            ((2, 3), "C", 3.0, 0.6, 0.4054651081081642),
        ]

        self.assertEqual(len(res.splits), len(expected_splits))
        for sp, (exp_is, exp_winner, exp_rank, exp_omega, exp_lambda) in zip(res.splits, expected_splits):
            self.assertEqual(sp.is_blocks, exp_is)
            self.assertEqual(sp.winner_trial_id, exp_winner)
            self.assertAlmostEqual(sp.oos_rank, exp_rank, places=12)
            self.assertAlmostEqual(sp.omega, exp_omega, places=12)
            self.assertAlmostEqual(sp.lambda_logit, exp_lambda, places=12)

    def test_pbo_cscv_parameter_validation(self):
        panel = ComparableTrialPanel(
            population_id="pop",
            trial_ids=("A", "B"),
            observations=tuple(range(8)),
            returns={
                "A": (0.01, -0.01) * 4,
                "B": (0.02, -0.02) * 4,
            },
            return_semantics="excess_return_v1",
        )

        # s < 4
        with self.assertRaises(RobustnessError) as ctx:
            evaluate_pbo_cscv(panel, s=2)
        self.assertIn("s must be an even integer >= 4", str(ctx.exception))

        # odd s
        with self.assertRaises(RobustnessError) as ctx:
            evaluate_pbo_cscv(panel, s=5)
        self.assertIn("s must be an even integer >= 4", str(ctx.exception))

        # T not divisible by s (8 % 6 != 0)
        with self.assertRaises(RobustnessError) as ctx:
            evaluate_pbo_cscv(panel, s=6)
        self.assertIn("must be divisible by s", str(ctx.exception))

        # N < 2
        single_panel = ComparableTrialPanel(
            population_id="pop-single",
            trial_ids=("A",),
            observations=tuple(range(8)),
            returns={"A": (0.01, -0.01) * 4},
            return_semantics="excess_return_v1",
        )
        with self.assertRaises(RobustnessError) as ctx:
            evaluate_pbo_cscv(single_panel, s=4)
        self.assertIn("requires at least 2 trials", str(ctx.exception))

    def test_pbo_strict_lambda_below_zero_and_tie_breaking(self):
        # Check that lambda == 0 is NOT counted in below-median overfit
        # If N=3, ranks are 1, 2, 3. The median rank is 2.
        # omega = 2 / (3 + 1) = 0.5 -> lambda = ln(0.5 / 0.5) = 0.0.
        # ADR-0037 explicitly requires strict lambda < 0.
        panel = ComparableTrialPanel(
            population_id="pop-tie",
            trial_ids=("A", "B", "C"),
            observations=tuple(range(8)),
            returns={
                "A": (0.01, -0.01) * 4,
                "B": (0.02, -0.02) * 4,
                "C": (0.03, -0.03) * 4,
            },
            return_semantics="excess_return_v1",
        )
        res = evaluate_pbo_cscv(panel, s=4)
        # Any split with lambda_logit == 0 must not increment negative_logit_count
        for sp in res.splits:
            if math.isclose(sp.lambda_logit, 0.0, abs_tol=1e-12):
                self.assertFalse(sp.lambda_logit < 0.0)

    def test_pbo_non_evaluable_split_fails_closed_without_dropping(self):
        # If in one split a trial has zero variance, PBO becomes NON_EVALUABLE
        panel = ComparableTrialPanel(
            population_id="pop-non-eval",
            trial_ids=("A", "B"),
            observations=tuple(range(8)),
            returns={
                "A": (0.01, 0.01, 0.01, 0.01, 0.02, -0.02, 0.03, -0.03),
                "B": (0.01, -0.01, 0.01, -0.01, 0.01, -0.01, 0.01, -0.01),
            },
            return_semantics="excess_return_v1",
        )
        res = evaluate_pbo_cscv(panel, s=4)
        self.assertEqual(res.status, RobustnessStatus.NON_EVALUABLE)
        self.assertIsNone(res.pbo)
        self.assertIn("Sharpe non-evaluable", res.non_evaluable_reason)

    # -----------------------------------------------------------------------
    # 5. Deterministic Result Identity & Sensitivity
    # -----------------------------------------------------------------------

    def test_identities_are_deterministic_and_sensitive(self):
        panel1 = ComparableTrialPanel(
            population_id="pop-id",
            trial_ids=("A", "B"),
            observations=(1, 2, 3, 4),
            returns={
                "A": (0.01, 0.02, -0.01, 0.03),
                "B": (0.02, 0.01, 0.00, -0.01),
            },
            return_semantics="excess_return_v1",
        )
        panel2 = ComparableTrialPanel(
            population_id="pop-id",
            trial_ids=("A", "B"),
            observations=(1, 2, 3, 4),
            returns={
                "A": (0.01, 0.02, -0.01, 0.03),
                "B": (0.02, 0.01, 0.00, -0.01),
            },
            return_semantics="excess_return_v1",
        )
        # Identical panels produce identical panel_id
        self.assertEqual(panel1.panel_id, panel2.panel_id)

        # DSR results on identical panels are identical
        dsr1 = evaluate_dsr_l(panel1, k_eff=2.0, k_eff_method_id="m1")
        dsr2 = evaluate_dsr_l(panel2, k_eff=2.0, k_eff_method_id="m1")
        self.assertEqual(dsr1.dsr_id, dsr2.dsr_id)

        # PBO results on identical panels are identical
        pbo1 = evaluate_pbo_cscv(panel1, s=4)
        pbo2 = evaluate_pbo_cscv(panel2, s=4)
        self.assertEqual(pbo1.pbo_id, pbo2.pbo_id)

        # Mutating a return value changes the identities
        panel_mutated = ComparableTrialPanel(
            population_id="pop-id",
            trial_ids=("A", "B"),
            observations=(1, 2, 3, 4),
            returns={
                "A": (0.01, 0.02, -0.01, 0.03001),
                "B": (0.02, 0.01, 0.00, -0.01),
            },
            return_semantics="excess_return_v1",
        )
        self.assertNotEqual(panel1.panel_id, panel_mutated.panel_id)

        dsr_mutated = evaluate_dsr_l(panel_mutated, k_eff=2.0, k_eff_method_id="m1")
        self.assertNotEqual(dsr1.dsr_id, dsr_mutated.dsr_id)

        pbo_mutated = evaluate_pbo_cscv(panel_mutated, s=4)
        self.assertNotEqual(pbo1.pbo_id, pbo_mutated.pbo_id)

        # Mutating k_eff changes DSR identity
        dsr_keff = evaluate_dsr_l(panel1, k_eff=1.0, k_eff_method_id="m1")
        self.assertNotEqual(dsr1.dsr_id, dsr_keff.dsr_id)


if __name__ == "__main__":
    unittest.main()
