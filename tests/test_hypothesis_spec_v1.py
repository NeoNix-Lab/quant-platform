#!/usr/bin/env python3
"""F01 HypothesisSpec v1 semantic foundation proof."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.features import FeatureDefinitionId  # noqa: E402
from quant_platform.research import (  # noqa: E402
    HypothesisSpec,
    HypothesisSpecError,
    HypothesisSpecId,
    ObservableReference,
)


def observable(key: str) -> ObservableReference:
    return ObservableReference(FeatureDefinitionId.from_payload({"feature_key": key}))


MID_PRICE = observable("mid_price")
SPREAD = observable("spread")
IMBALANCE = observable("book_imbalance")


def hypothesis(**overrides) -> HypothesisSpec:
    kwargs = dict(
        hypothesis_key="wide_spread_precedes_imbalance_reversal",
        semantic_version="1",
        statement=(
            "When bid-ask spread widens beyond its recent regime, book "
            "imbalance tends to revert within the following observations."
        ),
        observable_references=(SPREAD, IMBALANCE),
    )
    kwargs.update(overrides)
    return HypothesisSpec(**kwargs)


class HypothesisSpecIdentityTests(unittest.TestCase):
    def test_same_semantic_declaration_yields_same_identity(self):
        a = hypothesis()
        b = hypothesis()
        self.assertEqual(a.identity, b.identity)
        self.assertIsInstance(a.spec_id, HypothesisSpecId)

    def test_construction_order_of_observable_references_does_not_affect_identity(self):
        forward = hypothesis(observable_references=(SPREAD, IMBALANCE))
        reversed_order = hypothesis(observable_references=(IMBALANCE, SPREAD))
        self.assertEqual(forward.identity, reversed_order.identity)
        self.assertEqual(forward.observable_references, reversed_order.observable_references)

    def test_duplicate_observable_references_collapse(self):
        spec = hypothesis(observable_references=(SPREAD, SPREAD, IMBALANCE))
        self.assertEqual((IMBALANCE, SPREAD), tuple(sorted(
            spec.observable_references,
            key=lambda ref: str(ref.feature_definition_id),
        )))
        self.assertEqual(2, len(spec.observable_references))

    def test_changed_observable_reference_yields_distinct_identity(self):
        baseline = hypothesis(observable_references=(SPREAD, IMBALANCE))
        changed = hypothesis(observable_references=(SPREAD, MID_PRICE))
        self.assertNotEqual(baseline.identity, changed.identity)

    def test_changed_semantic_field_yields_distinct_identity(self):
        baseline = hypothesis()
        variants = [
            hypothesis(hypothesis_key="other_key"),
            hypothesis(semantic_version="2"),
            hypothesis(statement="A materially different claim about the same observables."),
        ]
        for variant in variants:
            with self.subTest(variant=variant.hypothesis_key):
                self.assertNotEqual(baseline.identity, variant.identity)

    def test_notes_are_administrative_and_excluded_from_identity(self):
        plain = hypothesis()
        annotated = hypothesis(notes="drafted during 2026-09 research sprint; see notebook 17")
        self.assertEqual(plain.identity, annotated.identity)
        self.assertEqual(
            "drafted during 2026-09 research sprint; see notebook 17", annotated.notes
        )
        self.assertIsNone(plain.notes)

    def test_identity_excludes_from_canonical_payload(self):
        annotated = hypothesis(notes="internal only")
        self.assertNotIn("notes", annotated.canonical_payload())


class HypothesisSpecValidationTests(unittest.TestCase):
    def test_hypothesis_is_frozen(self):
        spec = hypothesis()
        with self.assertRaises(FrozenInstanceError):
            spec.statement = "mutated"  # type: ignore[misc]

    def test_empty_hypothesis_key_is_refused(self):
        with self.assertRaises(HypothesisSpecError):
            hypothesis(hypothesis_key="")

    def test_non_canonical_hypothesis_key_is_refused(self):
        with self.assertRaises(HypothesisSpecError):
            hypothesis(hypothesis_key="Not A Valid Key")

    def test_empty_statement_is_refused(self):
        with self.assertRaises(HypothesisSpecError):
            hypothesis(statement="   ")

    def test_missing_observable_references_is_refused(self):
        with self.assertRaises(HypothesisSpecError):
            hypothesis(observable_references=())

    def test_non_observable_reference_entries_are_refused(self):
        with self.assertRaises(HypothesisSpecError):
            hypothesis(observable_references=(SPREAD, "mid_price"))

    def test_observable_reference_requires_feature_definition_id(self):
        with self.assertRaises(HypothesisSpecError):
            ObservableReference(object())

    def test_observable_reference_accepts_string_form_of_feature_definition_id(self):
        reference = ObservableReference(str(MID_PRICE.feature_definition_id))
        self.assertEqual(MID_PRICE.feature_definition_id, reference.feature_definition_id)

    def test_invalid_semantic_version_is_refused(self):
        with self.assertRaises(HypothesisSpecError):
            hypothesis(semantic_version="0")
        with self.assertRaises(HypothesisSpecError):
            hypothesis(semantic_version="v1")


class HypothesisSpecIdTests(unittest.TestCase):
    def test_id_requires_v1_domain_prefix(self):
        with self.assertRaises(HypothesisSpecError):
            HypothesisSpecId("feature-definition-v1:sha256:" + "a" * 64)

    def test_id_requires_full_sha256_hex_length(self):
        with self.assertRaises(HypothesisSpecError):
            HypothesisSpecId("hypothesis-spec-v1:sha256:abc123")


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
