"""Regression guard for the DG-M canonical feature identity design contract (ADR-0073)."""

from fractions import Fraction
from decimal import Decimal
from pathlib import Path
import random
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.admitted_input import AdmittedInputManifestV1  # noqa: E402
from quant_platform.features.h01_imbalance import (  # noqa: E402
    diagonal_imbalance_definition,
    stacked_imbalance_definition,
)
from quant_platform.representation.candles import CandleDefinitionV1  # noqa: E402
from quant_platform.representation.footprints import FootprintDefinitionV1  # noqa: E402

ADR = ROOT / "docs/decisions/ADR-0073-canonical-feature-identity-for-deck-results-v1.md"

PINNED_IDENTITIES = {
    "candle": "candle-definition-v1:sha256:94b3a5a99c873f0d2805f4d197b772047f8c13e8753ae8fb4314aafebf1440cc",
    "footprint": "footprint-definition-v1:sha256:3f551c3e5676985a942acda5d398843ae0145cae4b3212cd1b4817fed7d1fca5",
    "diagonal": "feature-definition-v1:sha256:53e24318c041359d9ce5f52f82718b8b85e3aee305856619cb5cd265cd996de7",
    "stacked": "feature-definition-v1:sha256:7666e798a7db6d4bf7cc7c76c952c175a05b9a5e712be00916bde7d4c4f64167",
}


def adr_text() -> str:
    return ADR.read_text(encoding="utf-8")


def manifest(definitions: tuple[str, ...]) -> AdmittedInputManifestV1:
    return AdmittedInputManifestV1(
        logical_input_identities=("dataset-v1:canonical:trades:bybit:BTCUSDT:trade-v1",),
        natural_partition_identities=("partition-v1:" + "ab" * 32,),
        schema_identity="trade-v1",
        schema_version="1",
        schema_hash="schema-sha256:abc",
        manifest_hashes=("manifest-sha256:abc",),
        content_hashes=("content-sha256:abc",),
        declared_coverage={"start": "2024-01-15T00:00:00Z", "end": "2024-01-16T00:00:00Z"},
        request_identity="request-v1:fake",
        result_identity=None,
        definition_identities=definitions,
        implementation_identity=None,
        git_identity="0" * 40,
        operation_identity="operation:test",
        profile_identity=None,
    )


def signal(o: Decimal, h: Decimal, low: Decimal, c: Decimal, buy: Decimal, sell: Decimal,
           i: Decimal, w: Decimal) -> int:
    """The ADR-0073 section 4 rule, division-free and exact."""
    total, delta, span = buy + sell, buy - sell, h - low
    if delta >= i * total and (h - max(o, c)) > w * span and c < o:
        return 1
    if -delta >= i * total and (min(o, c) - low) > w * span and c > o:
        return -1
    return 0


def ratio_signal(o: Fraction, h: Fraction, low: Fraction, c: Fraction, buy: Fraction, sell: Fraction,
                 i: Fraction, w: Fraction) -> int:
    """The research notation: the imbalance ratio I = (Vb - Vs) / (Vb + Vs), then the same conditions."""
    ratio = (buy - sell) / (buy + sell)
    span = h - low
    if ratio >= i and (h - max(o, c)) > w * span and c < o:
        return 1
    if ratio <= -i and (min(o, c) - low) > w * span and c > o:
        return -1
    return 0


class CanonicalFeatureIdentityContractV1Tests(unittest.TestCase):
    def test_the_adr_is_accepted_and_names_its_amendments(self) -> None:
        text = adr_text()
        for required_text in (
            "# ADR-0073 - Canonical feature identity for deck results v1 (DG-M)",
            "**Status:** ACCEPTED",
            "Governance issues #453, #454; acceptance #457.",
            "Amends ADR-0072 and ADR-0069 only where section 10 says so",
            "### 10. Amendments to accepted ADRs (effective only on acceptance of this ADR)",
            "dated, append-only amendments",
        ):
            self.assertIn(required_text, text)
        for forbidden_text in ("[TBD]", "[DA CONFERMARE]"):
            self.assertNotIn(forbidden_text, text)

    def test_every_decision_of_the_gate_has_its_section(self) -> None:
        text = adr_text()
        for heading in (
            "### 1. Decomposition: one canonical feature; the event is not server-known",
            "### 2. Input: a new additive representation, `bar-flow-v1`",
            "### 3. Availability and finality",
            "### 4. The feature: `order_flow.failed_aggression@1`",
            "### 5. Server carriage: the deck names, the server canonicalizes and seals",
            "### 6. K13 and the deck builder",
            "### 7. Research alignment",
            "### 8. Proof: what \"the server passes feature identity\" means",
            "### 9. What this does not need",
        ):
            self.assertIn(heading, text)

    def test_existing_identity_bytes_are_pinned_in_the_adr_and_in_the_code(self) -> None:
        text = adr_text()
        actual = {
            "candle": CandleDefinitionV1.from_duration("1m").definition_identity,
            "footprint": FootprintDefinitionV1.from_duration_and_tick("1m", "0.1").definition_identity,
            "diagonal": diagonal_imbalance_definition().identity,
            "stacked": stacked_imbalance_definition().identity,
        }
        self.assertEqual(PINNED_IDENTITIES, actual, "an existing identity changed: the ADR forbids it")
        for identity in PINNED_IDENTITIES.values():
            self.assertIn(identity, text)

    def test_the_new_representation_and_feature_are_specified_without_open_values(self) -> None:
        text = adr_text()
        for required_text in (
            "BarFlowDefinitionV1(duration_ns)",
            "bar-flow-definition-v1:sha256:<hex>",
            "taker_buy_volume  = sum_exact_decimal_size_where_aggressor_side_buy",
            "taker_sell_volume = sum_exact_decimal_size_where_aggressor_side_sell",
            "`taker_buy_volume + taker_sell_volume ==",
            "**1 second to 1 day** inclusive",
            "feature_key      = \"order_flow.failed_aggression\"",
            "input_contract   = bar_flow.bar @ 1, atomic,",
            "close, high, low, open, taker_buy_volume, taker_sell_volume",
            "parameters       = imbalance_threshold  decimal, default \"0.3\", domain 0 < x <= 1",
            "wick_ratio           decimal, default \"0.5\", domain 0 <= x < 1",
            "+1  (taker buyers trapped)   if  D >= i * T",
            "-1  (taker sellers trapped)  if -D >= i * T",
            "no rounding, no float and no division",
            "An omitted bucket (no trades) yields **no observation**",
        ):
            self.assertIn(required_text, text)

    def test_the_division_free_rule_is_the_research_ratio_rule(self) -> None:
        rng = random.Random(73)
        signs = set()
        for _ in range(4000):
            low = Decimal(rng.randint(1000, 2000)) / 10
            span = Decimal(rng.randint(0, 40)) / 10
            high = low + span
            o = low + Decimal(rng.randint(0, int(span * 10))) / 10
            c = low + Decimal(rng.randint(0, int(span * 10))) / 10
            buy = Decimal(rng.randint(0, 50)) / 10
            sell = Decimal(rng.randint(1, 50)) / 10 if buy == 0 else Decimal(rng.randint(0, 50)) / 10
            i = Decimal(rng.choice(("0.1", "0.3", "0.5", "1")))
            w = Decimal(rng.choice(("0", "0.25", "0.5", "0.75")))
            exact = signal(o, high, low, c, buy, sell, i, w)
            fractions = tuple(Fraction(x) for x in (o, high, low, c, buy, sell, i, w))
            self.assertEqual(exact, ratio_signal(*fractions), (o, high, low, c, buy, sell, i, w))
            signs.add(exact)
        self.assertEqual({-1, 0, 1}, signs, "the sampled bars must exercise every outcome")

    def test_the_rule_boundaries_and_exclusivity(self) -> None:
        d = Decimal
        i, w = d("0.3"), d("0.5")
        # Imbalance exactly at the threshold counts (>=): D = 0.3 * T.
        self.assertEqual(1, signal(d("108"), d("120"), d("100"), d("105"), d("6.5"), d("3.5"), i, w))
        self.assertEqual(0, signal(d("108"), d("120"), d("100"), d("105"), d("6.4"), d("3.6"), i, w))
        # The mirror case: sellers trapped, a lower wick of 12 over a range of 20, close above open.
        self.assertEqual(-1, signal(d("112"), d("120"), d("100"), d("114"), d("3.5"), d("6.5"), i, w))
        # A wick exactly half the range does not count (>): (H - max(O, C)) == 0.5 * (H - L).
        self.assertEqual(0, signal(d("110"), d("120"), d("100"), d("105"), d("8"), d("2"), i, w))
        # A zero range cannot satisfy the wick comparison.
        self.assertEqual(0, signal(d("100"), d("100"), d("100"), d("100"), d("8"), d("2"), i, w))
        # The two imbalance conditions cannot both hold for a positive threshold and a positive total.
        for buy, sell in ((d("9"), d("1")), (d("1"), d("9")), (d("5"), d("5")), (d("0"), d("4"))):
            total, delta = buy + sell, buy - sell
            self.assertFalse(delta >= i * total and -delta >= i * total, (buy, sell))

    def test_the_server_carriage_rests_on_facts_the_code_already_has(self) -> None:
        # The manifest already carries the field in its canonical payload, sorted and deduplicated.
        feature = "feature-definition-v1:sha256:" + "a" * 64
        bars = "bar-flow-definition-v1:sha256:" + "b" * 64
        self.assertEqual(
            manifest((feature, bars)).manifest_digest, manifest((bars, feature)).manifest_digest,
        )
        self.assertEqual(sorted((feature, bars)), manifest((feature, bars)).canonical_fields()["definition_identities"])
        # A request without definitions seals the manifest it seals today; one with definitions is another admission.
        self.assertNotEqual(manifest(()).admission_id, manifest((feature, bars)).admission_id)
        self.assertNotEqual(manifest((feature, bars)).admission_id, manifest((bars,)).admission_id)
        with self.assertRaises(ValueError):
            manifest((bars, bars))
        authority = (ROOT / "src/quant_platform/application/admitted_input_authority.py").read_text(encoding="utf-8")
        self.assertIn("definition_identities=()", authority)
        transport = (ROOT / "src/quant_platform/application/admitted_input_transport.py").read_text(encoding="utf-8")
        self.assertIn(
            'def _only(body: Any, required: set[str], optional: set[str] = frozenset())', transport,
        )
        text = adr_text()
        for required_text in (
            "`admit` gains one **optional** field, `definitions` (array, at most **8** entries",
            "`j02-admitted-input-v2` and its request version stay as",
            "byte-identical to today's (empty list)",
            "`definitions_exceed_bound`",
            "`definition_unknown`",
            "`definition_parameters_invalid`",
            "`definition_bar_duration_invalid`",
            "`definition_interval_misaligned`",
            "`definition_duplicate`",
            "`definition_input_unavailable`",
            "It attests that it *admitted a request that named these definitions*",
        ):
            self.assertIn(required_text, text)

    def test_k13_is_unchanged_and_the_builder_rule_is_stated(self) -> None:
        text = adr_text()
        for required_text in (
            "`_validate_run_compatibility` is **unchanged**",
            "`definition_not_declared`",
            "`feature-definition`",
            "`representation-definition`",
            "A run that declares **more** definitions than the manifest\nadmitted is not refused",
        ):
            self.assertIn(required_text, text)
        service = (ROOT / "src/quant_platform/application/governed_result_import.py").read_text(encoding="utf-8")
        self.assertIn("RunIdentity definition identities do not cover admitted input", service)
        self.assertIn("declared_definitions.issubset(run_references)", service)

    def test_adrs_cited_by_the_text_say_what_it_relies_on(self) -> None:
        adr72 = (ROOT / "docs/decisions/ADR-0072-server-deck-deployment-composition-v1.md").read_text(encoding="utf-8")
        self.assertIn("| `definition_identities` | empty (raw trades have no representation definition) |", adr72)
        self.assertIn("covers `definition_identities` (the `K13` compatibility checks)", adr72)
        self.assertIn("### 11. How a strategy is tested while only dataset selectors are admissible", adr72)
        self.assertIn("a bundle names one admission", adr72)
        adr66 = (ROOT / "docs/decisions/ADR-0066-transport-evolution-v1.md").read_text(encoding="utf-8")
        self.assertIn("New families and optional fields are\nadditive only", adr66)
        adr27 = (ROOT / "docs/decisions/ADR-0027-footprint-definition-v1.md").read_text(encoding="utf-8")
        self.assertIn("### Aggressor side", adr27)

    def test_the_gate_stays_inside_its_scope(self) -> None:
        text = adr_text()
        for required_text in (
            "**No generic provider extension (`E07`) and no plugin framework:**",
            "**No server-side feature materialization and no precomputed features as deck inputs:**",
            "**No change to any existing identity:**",
            "**No new transport family and no credential change.**",
            "The attestation is of *name*, not of *computation*",
            "`M03` Failed-aggression event definition | **superseded**",
            "Order: `M01` → `M02` → `M04` → `M05`.",
        ):
            self.assertIn(required_text, text)
        self.assertEqual(
            1, len(re.findall(r"^\*\*Status:\*\* ", text, flags=re.MULTILINE)), "one status line",
        )


if __name__ == "__main__":
    unittest.main()
