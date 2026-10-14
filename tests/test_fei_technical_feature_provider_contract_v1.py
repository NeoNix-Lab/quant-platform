"""Regression guard for the accepted DG-L FEI technical feature provider contract."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
ADR = ROOT / "docs/decisions/ADR-0071-fei-technical-feature-provider-v1.md"


class FeiTechnicalFeatureProviderContractV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.adr = " ".join(ADR.read_text(encoding="utf-8").split())

    def assertAllIn(self, statements: tuple[str, ...]) -> None:
        for statement in statements:
            self.assertIn(" ".join(statement.split()), self.adr)

    def test_accepted_adr_has_no_open_placeholder(self) -> None:
        self.assertIn("**Status:** ACCEPTED", self.adr)
        self.assertNotIn("[TBD]", self.adr)
        self.assertNotIn("[DA CONFERMARE", self.adr)

    def test_shape_is_one_feature_set_of_independent_definitions(self) -> None:
        self.assertAllIn(
            (
                "one `FeatureSetDefinition` (`slug = fei`, `version = 1`)",
                "not as a single composite `FeatureDefinition`",
                "`technical.ichimoku_cloud`",
                "`technical.fei_static`",
                "`technical.fei_dynamics`",
                "`technical.fei_rhythm`",
                "`technical.fei_axes`",
            )
        )

    def test_scales_are_epoch_aligned_derivations_of_one_base_contract(self) -> None:
        self.assertAllIn(
            (
                "contract_key = candle.closed_ohlc",
                "`InputContractV1` is unchanged",
                "`[k*D*m, (k+1)*D*m)` on the UTC Unix epoch",
                "Grouping `m` consecutive base candles by position from the start of a series, "
                "or of a request, is rejected.",
                "it then equals the `CandleDefinition v1` record of duration `D*m` built directly",
                "A covered base interval with no trades contributes nothing",
                "a scale uses only derived buckets with `bucket_end <= c.bucket_end`",
                "A `PARTIAL` candle is never consumed",
                "The Feature package gains no dependency on Representation",
            )
        )

    def test_boundary_excludes_decisions(self) -> None:
        self.assertAllIn(
            (
                "POSTURE and urgency",
                "corridor LONG/SHORT, corridor shift and target",
                "The corridor *thinness* observable of `T05` is a feature",
            )
        )

    def test_finite_window_support_and_non_observation(self) -> None:
        self.assertAllIn(
            (
                "The value at an evaluation coordinate is a pure function of the inputs inside "
                "the window that ends at that coordinate.",
                "The recursion is seeded at the window start with the first defined input in the window.",
                "`minimum_history = N`",
                "`initialization_required`",
                "Changing a default requires a new `semantic_version`.",
                "`0`, `NaN`, `inf` and `null` are never used to mean \"undefined\" or \"unavailable\"",
                "A quotient with a zero denominator is a non-observation",
                "No new non-observation reason is introduced.",
                "`T02` and everything that consumes it require every declared scale",
                "Weights are not renormalized",
                "Numeric rhythm and polarity are non-observations unless `rst = REAL`",
            )
        )

    def test_numerics_are_exact_with_one_rounding_at_publication(self) -> None:
        self.assertAllIn(
            (
                "Binary floating point is not used in the value path.",
                "Intermediate rounding is forbidden",
                "`NumericalEquivalence EXACT`",
                "`NumericalEquivalence QUANTIZED`",
                "`0.000000000001`",
                "A feature-on-feature input is the *published* upstream value.",
                "E04 equivalence carries no truncation residual and E04 is unchanged",
                "A `TOLERANT` bound belongs to the conformity proof (`T06`)",
            )
        )

    def test_parity_forbids_array_length_dependence(self) -> None:
        self.assertAllIn(
            (
                "A computation that depends on array length is forbidden.",
                "`min(n-1, i+phi_s)`",
                "A feature output is never stamped at a coordinate later than its evaluation coordinate",
                "Truncating the tail of the series leaves every earlier output byte-identical.",
            )
        )

    def test_provider_boundary_is_minimal(self) -> None:
        self.assertAllIn(
            (
                "one explicit, closed mapping from canonical `feature_key` to evaluator",
                "an unknown key is refused",
                "It introduces no discovery, entry points, dynamic import, third-party registration",
                "`feature-provider-v1`",
                "Implementation remains unauthorized until a governance reconciliation",
            )
        )


if __name__ == "__main__":
    unittest.main()
