#!/usr/bin/env python3
"""E02 FeatureDefinition v1 semantic foundation proof."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.features import (  # noqa: E402
    FeatureAvailabilitySemantics,
    FeatureDefinition,
    FeatureDefinitionError,
    FeatureFinalitySemantics,
    FeatureKeyGovernance,
    FeatureObservation,
    InitializationSemantics,
    InputContractShape,
    InputContractV1,
    InputMaturity,
    NonObservation,
    NonObservationReason,
    NumericalEquivalence,
    NumericalEquivalenceKind,
    ObservationLifecycle,
    OutputContract,
    OutputDimension,
    OutputValueKind,
    ParameterCollectionKind,
    SemanticParameterSpec,
    SemanticParameterType,
    SupportIdentity,
    SupportReference,
)


def footprint_contract(version: str = "1") -> InputContractV1:
    return InputContractV1(
        "footprint.price_level",
        version,
        ("sell_volume", "buy_volume"),
    )


def output_contract(
    *,
    equivalence: NumericalEquivalence | None = None,
) -> OutputContract:
    return OutputContract(
        OutputValueKind.NUMERIC,
        "scalar",
        OutputDimension.DIMENSIONLESS,
        equivalence or NumericalEquivalence.exact(),
    )


def schema() -> tuple[SemanticParameterSpec, ...]:
    return (
        SemanticParameterSpec(
            "threshold",
            SemanticParameterType.INTEGER,
            default=3,
            min_value=2,
        ),
        SemanticParameterSpec(
            "lags",
            SemanticParameterType.INTEGER,
            default=(1, 2),
            collection=ParameterCollectionKind.ORDERED_SEQUENCE,
        ),
        SemanticParameterSpec(
            "sides",
            SemanticParameterType.STRING,
            default=("ask", "bid"),
            collection=ParameterCollectionKind.UNORDERED_SET,
        ),
        SemanticParameterSpec(
            "tag",
            SemanticParameterType.STRING,
            allows_null=True,
        ),
    )


def definition(**overrides) -> FeatureDefinition:
    fields = {
        "feature_key": "order_flow.imbalance",
        "semantic_version": "1",
        "parameter_schema": schema(),
        "parameters": {},
        "input_contract": footprint_contract(),
        "support": (SupportReference.window(-1, 3),),
        "input_maturity": InputMaturity.AVAILABLE,
        "availability": FeatureAvailabilitySemantics(),
        "finality": FeatureFinalitySemantics(),
        "output_contract": output_contract(),
        "initialization": InitializationSemantics(),
    }
    fields.update(overrides)
    return FeatureDefinition(**fields)


class FeatureDefinitionV1Tests(unittest.TestCase):
    def test_feature_key_governance_normalizes_aliases_before_construction(self):
        governance = FeatureKeyGovernance(
            "order_flow.imbalance",
            aliases=("imbalance", "diagonal-imbalance"),
        )
        self.assertEqual("order_flow.imbalance", governance.normalize_external("imbalance"))
        self.assertEqual("order_flow.imbalance", governance.normalize_external("order_flow.imbalance"))

        canonical = definition()
        alias_spelling = governance.normalize_external("diagonal-imbalance")
        same = definition(feature_key=alias_spelling)
        self.assertEqual(canonical.identity, same.identity)
        with self.assertRaises(FeatureDefinitionError):
            definition(feature_key="DiagonalImbalance")

    def test_defaults_types_collection_order_and_null_semantics_are_canonical(self):
        omitted_default = definition()
        explicit_default = definition(parameters={"threshold": 3})
        self.assertEqual(omitted_default.identity, explicit_default.identity)

        with self.assertRaisesRegex(FeatureDefinitionError, "integer"):
            definition(parameters={"threshold": 3.0})
        with self.assertRaisesRegex(FeatureDefinitionError, "below"):
            definition(parameters={"threshold": 1})

        ordered = definition(parameters={"lags": [1, 2]})
        reordered_ordered = definition(parameters={"lags": [2, 1]})
        self.assertNotEqual(ordered.identity, reordered_ordered.identity)

        unordered = definition(parameters={"sides": ["ask", "bid"]})
        reordered_unordered = definition(parameters={"sides": ["bid", "ask"]})
        self.assertEqual(unordered.identity, reordered_unordered.identity)

        omitted_null = definition()
        explicit_null = definition(parameters={"tag": None})
        self.assertNotEqual(omitted_null.identity, explicit_null.identity)

        null_equivalent_schema = (
            SemanticParameterSpec(
                "tag",
                SemanticParameterType.STRING,
                allows_null=True,
                null_equivalent_to_omitted=True,
            ),
        )
        self.assertEqual(
            definition(parameter_schema=null_equivalent_schema).identity,
            definition(parameter_schema=null_equivalent_schema, parameters={"tag": None}).identity,
        )

    def test_identity_is_independent_of_map_schema_and_runtime_order(self):
        first = definition(
            parameter_schema=tuple(reversed(schema())),
            parameters={"sides": ["bid", "ask"], "threshold": 3, "lags": [1, 2]},
        )
        second = definition(
            parameter_schema=schema(),
            parameters={"lags": [1, 2], "threshold": 3, "sides": ["ask", "bid"]},
        )

        self.assertEqual(first.identity, second.identity)
        payload_text = first.canonical_utf8_serialization
        for runtime_locator in ("dataset", "venue", "instrument", "grain", "path", "backend"):
            self.assertNotIn(runtime_locator, payload_text)
        with self.assertRaises(FrozenInstanceError):
            first.feature_key = "other"  # type: ignore[misc]

    def test_semantically_distinct_definition_parts_change_identity(self):
        base = definition()
        cases = (
            definition(input_contract=footprint_contract("2")),
            definition(support=(SupportReference.current(),)),
            definition(finality=FeatureFinalitySemantics("same_observation_final_only")),
            definition(initialization=InitializationSemantics(minimum_history=5, rule="seeded_sma")),
            definition(
                output_contract=output_contract(
                    equivalence=NumericalEquivalence(
                        NumericalEquivalenceKind.QUANTIZED,
                        "1",
                        {"quantum": "0.01"},
                    )
                )
            ),
        )

        for candidate in cases:
            with self.subTest(candidate=candidate.canonical_payload()):
                self.assertNotEqual(base.identity, candidate.identity)

    def test_input_contract_shape_is_versioned_and_composite_dependencies_are_identity_bearing(self):
        trades = InputContractV1("trades", "1", ("price", "size"))
        candles = InputContractV1("candle", "1", ("close",))
        composite = InputContractV1(
            "trade_candle_join",
            "1",
            ("price", "close"),
            shape=InputContractShape.COMPOSITE,
            constituent_contracts=(candles, trades),
        )
        same_reordered = InputContractV1(
            "trade_candle_join",
            "1",
            ("close", "price"),
            shape=InputContractShape.COMPOSITE,
            constituent_contracts=(trades, candles),
        )
        changed_dependency = InputContractV1(
            "trade_candle_join",
            "1",
            ("price", "close"),
            shape=InputContractShape.COMPOSITE,
            constituent_contracts=(trades, InputContractV1("candle", "2", ("close",))),
        )

        self.assertEqual(composite.identity, same_reordered.identity)
        self.assertNotEqual(composite.identity, changed_dependency.identity)
        with self.assertRaises(FeatureDefinitionError):
            InputContractV1("bad", "1", (), shape=InputContractShape.COMPOSITE)

    def test_support_selectors_are_closed_logical_observation_semantics(self):
        self.assertEqual(
            {
                SupportReference.current().stable_dict()["kind"],
                SupportReference.point(-1).stable_dict()["kind"],
                SupportReference.window(-1, 20).stable_dict()["kind"],
            },
            {"current", "point", "window"},
        )
        self.assertEqual("logical_observation", SupportReference.window(-1, 20).stable_dict()["unit"])

        with self.assertRaises(FeatureDefinitionError):
            SupportReference("current", offset=0)
        with self.assertRaises(FeatureDefinitionError):
            SupportReference.window(-1, 0)
        with self.assertRaises(FeatureDefinitionError):
            SupportReference.window(-1, "5m")  # type: ignore[arg-type]
        with self.assertRaises(FeatureDefinitionError):
            SupportReference("wall_clock_window")  # type: ignore[arg-type]

    def test_invalid_semantic_definitions_fail_explicitly(self):
        with self.assertRaises(FeatureDefinitionError):
            SemanticParameterSpec("threshold", SemanticParameterType.INTEGER, required=True, default=3)
        with self.assertRaises(FeatureDefinitionError):
            definition(parameters={"runtime_path": "C:/cache"})
        with self.assertRaises(FeatureDefinitionError):
            definition(input_maturity="SOMETIMES")  # type: ignore[arg-type]
        with self.assertRaises(FeatureDefinitionError):
            OutputContract(OutputValueKind.NUMERIC, "scalar", OutputDimension.PRICE)

    def test_feature_observation_identity_excludes_value_and_lifecycle(self):
        feature = definition()
        support = SupportIdentity(
            feature.input_contract.identity,
            "bar:2024-01-01T00:00:00Z",
            SupportReference.current(),
        )
        provisional = FeatureObservation(
            feature.definition_id,
            support,
            value=1.5,
            lifecycle=ObservationLifecycle.PROVISIONAL,
            causal_available_at="2024-01-01T00:00:01Z",
        )
        final = FeatureObservation(
            feature.definition_id,
            support,
            value=2.0,
            lifecycle=ObservationLifecycle.FINAL,
            causal_available_at="2024-01-01T00:00:01Z",
            observed_finalized_at="2024-01-01T00:00:02Z",
        )

        self.assertEqual(provisional.identity, final.identity)
        self.assertIsNone(provisional.observed_available_at)
        self.assertIsNone(provisional.observed_finalized_at)
        promoted = provisional.transition_to(
            ObservationLifecycle.FINAL,
            value=2.0,
            observed_finalized_at="2024-01-01T00:00:02Z",
        )
        self.assertEqual(final.identity, promoted.identity)
        with self.assertRaisesRegex(FeatureDefinitionError, "regress"):
            final.transition_to(ObservationLifecycle.PROVISIONAL)

    def test_temporal_evidence_validation_and_non_observation_are_explicit(self):
        feature = definition()
        support = SupportIdentity(
            feature.input_contract.identity,
            "bar:2024-01-01T00:00:00Z",
            SupportReference.current(),
        )
        with self.assertRaisesRegex(FeatureDefinitionError, "causal"):
            FeatureObservation(
                feature.definition_id,
                support,
                value=1.0,
                lifecycle=ObservationLifecycle.PROVISIONAL,
                causal_available_at="2024-01-01T00:00:01Z",
                observed_available_at="2024-01-01T00:00:00Z",
            )
        with self.assertRaisesRegex(FeatureDefinitionError, "PROVISIONAL"):
            FeatureObservation(
                feature.definition_id,
                support,
                value=1.0,
                lifecycle=ObservationLifecycle.PROVISIONAL,
                causal_available_at="2024-01-01T00:00:01Z",
                observed_finalized_at="2024-01-01T00:00:02Z",
            )

        insufficient = NonObservation(
            NonObservationReason.INSUFFICIENT_SUPPORT,
            feature.definition_id,
            "window requires three logical observations",
        )
        self.assertIsInstance(insufficient, NonObservation)
        self.assertNotIsInstance(insufficient, FeatureObservation)


if __name__ == "__main__":
    result = unittest.main(verbosity=2, exit=False)
    raise SystemExit(0 if result.result.wasSuccessful() else 1)
