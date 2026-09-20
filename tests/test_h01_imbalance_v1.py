#!/usr/bin/env python3
"""E06 H01 canonical integration v1 runtime proof (ADR-0035).

Credits and does not re-prove: E05 diagonal/stacked numeric semantics
(tests/test_feature_imbalance_v1.py), E02 FeatureDefinition identity/support
semantics (tests/test_feature_definition_v1.py), E04 FeatureArtifact
finality/binding semantics (tests/test_feature_artifact_v1.py), D06
FootprintDefinition v1 FINAL aggregation (tests/test_historical_footprints_v1.py)
and the modular-monolith dependency direction (tests/test_package_boundaries_v1.py).
"""

from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import (  # noqa: E402
    CoverageInterval,
    DatasetIdentity,
    Instant,
    NaturalPartitionIdentity,
    TradeRecord,
)
from quant_platform.ordering import TRADES_CANONICAL_TOTAL_ORDER_V1  # noqa: E402
from quant_platform.representation import (  # noqa: E402
    FootprintDefinitionV1,
    HistoricalFootprintSourceEvidence,
    build_historical_footprint_result,
)
from quant_platform.features import (  # noqa: E402
    BoundOutputPartition,
    BoundSourceDataset,
    BoundSourcePartition,
    ConstituentFeatureOutput,
    FeatureArtifactContentIdentity,
    FeatureArtifactError,
    FeatureDefinition,
    FeatureDefinitionError,
    InputMaturity,
    ObservationLifecycle,
    OutputValueKind,
    SupportReference,
    SupportShape,
    require_final_observations,
)
from quant_platform.features.h01_imbalance import (  # noqa: E402
    FOOTPRINT_PRICE_LEVEL_CONTRACT,
    H01_IMBALANCE_FEATURE_SET_IDENTITY,
    H01BucketInput,
    H01LevelInput,
    diagonal_imbalance_definition,
    evaluate_diagonal_imbalance,
    evaluate_stacked_imbalance,
    stacked_imbalance_definition,
)
from quant_platform.application.h01_composition import (  # noqa: E402
    H01CompositionError,
    H01Evaluation,
    bucket_observation_identity,
    evaluate_h01_imbalance,
    materialize_h01_feature_artifact,
)


IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
OTHER_IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "ETHUSDT", "trade-v1")
IMPLEMENTATION = "quant_platform.representation.footprints:d06-v1"


def instant(value: str) -> Instant:
    return Instant.parse(value)


def interval(start: str, end: str) -> CoverageInterval:
    return CoverageInterval(instant(start), instant(end))


def trade(ts: str, price: str, size: str, side: str = "buy") -> TradeRecord:
    return TradeRecord(
        venue="bybit", instrument="BTCUSDT", exchange_ts=instant(ts),
        price=price, size=size, aggressor_side=side,
    )


def source_evidence(support: tuple[CoverageInterval, ...], **overrides) -> HistoricalFootprintSourceEvidence:
    fields = {
        "dataset_identity": IDENTITY,
        "record_schema_id": "trade-v1",
        "schema_version": 1,
        "schema_hash": "schema-sha",
        "source_ordering_policy": TRADES_CANONICAL_TOTAL_ORDER_V1,
        "eligible_source_coverage": support,
        "finalized_source_intervals": support,
        "finalization_evidence": ("attested",),
        "source_result_identity": "source-result:sha256:" + "a" * 64,
        "observed_available_at": support[-1].end if support else None,
    }
    fields.update(overrides)
    return HistoricalFootprintSourceEvidence(**fields)


def footprint_result(
    trades: list[TradeRecord], start: str, end: str, *,
    duration: str = "60s", tick: str = "1", **evidence_overrides,
):
    definition = FootprintDefinitionV1.from_duration_and_tick(duration, tick)
    support = (interval(start, end),)
    evidence = source_evidence(support, **evidence_overrides)
    return build_historical_footprint_result(
        trades, definition, instant(start), instant(end),
        source_evidence=evidence, implementation_identity=IMPLEMENTATION,
    )


def h01_bucket(levels: tuple[H01LevelInput, ...], *, obs_id: str = "bucket:test") -> H01BucketInput:
    return H01BucketInput(
        bucket_observation_identity=obs_id,
        causal_available_at=instant("2024-01-01T00:01:00Z"),
        levels=levels,
    )


def sources(*, dataset_identity: DatasetIdentity = IDENTITY, token: str = "1") -> tuple[BoundSourceDataset, ...]:
    return (
        BoundSourceDataset(
            dataset_identity,
            (
                BoundSourcePartition(
                    natural_identity=NaturalPartitionIdentity(dataset_identity, "dt=2024-01-01", 1),
                    content_sha256=token * 64,
                    manifest_sha256=token * 64,
                ),
            ),
        ),
    )


def content_identity(result, *, token: str = "a") -> FeatureArtifactContentIdentity:
    output_dataset = DatasetIdentity(
        "features", "h01_imbalance", "bybit", "BTCUSDT", "feature-v1",
        feature_set_slug="h01_imbalance", feature_set_version=1,
    )
    return FeatureArtifactContentIdentity(
        output_partitions=(
            BoundOutputPartition(
                natural_identity=NaturalPartitionIdentity(output_dataset, "dt=2024-01-01", 1),
                content_sha256=token * 64,
                manifest_sha256=(token * 63 + "0"),
            ),
        ),
        declared_support=SupportShape(intervals=result.coverage.covered_intervals),
    )


class H01DefinitionTests(unittest.TestCase):
    def test_canonical_definitions_match_adr0035_exactly(self):
        diagonal = diagonal_imbalance_definition()
        stacked = stacked_imbalance_definition()

        self.assertEqual("order_flow.diagonal_imbalance", diagonal.feature_key)
        self.assertEqual("order_flow.stacked_imbalance", stacked.feature_key)
        self.assertEqual("1", diagonal.semantic_version)
        self.assertEqual("1", stacked.semantic_version)
        self.assertEqual(
            ("buy_volume", "level_index", "sell_volume"), diagonal.input_contract.required_observables,
        )
        self.assertEqual(FOOTPRINT_PRICE_LEVEL_CONTRACT.identity, diagonal.input_contract.identity)
        self.assertEqual(FOOTPRINT_PRICE_LEVEL_CONTRACT.identity, stacked.input_contract.identity)
        self.assertEqual((SupportReference.current(),), diagonal.support)
        self.assertEqual((SupportReference.current(),), stacked.support)
        self.assertEqual(InputMaturity.FINAL_ONLY, diagonal.input_maturity)
        self.assertEqual(InputMaturity.FINAL_ONLY, stacked.input_maturity)
        self.assertEqual(0, diagonal.initialization.minimum_history)
        self.assertEqual(0, stacked.initialization.minimum_history)
        self.assertEqual(OutputValueKind.RECORD, diagonal.output_contract.value_kind)
        self.assertEqual("bucket_level_diagonal_imbalance_v1", diagonal.output_contract.shape)
        self.assertEqual(OutputValueKind.RECORD, stacked.output_contract.value_kind)
        self.assertEqual("bucket_level_stacked_imbalance_v1", stacked.output_contract.shape)

    def test_both_definitions_share_frozen_input_contract_but_distinct_ids(self):
        diagonal = diagonal_imbalance_definition()
        stacked = stacked_imbalance_definition()
        self.assertEqual(diagonal.input_contract.identity, stacked.input_contract.identity)
        self.assertNotEqual(diagonal.definition_id, stacked.definition_id)

    def test_parameter_defaults_are_identity_bearing(self):
        default_diagonal = diagonal_imbalance_definition()
        explicit_diagonal = diagonal_imbalance_definition(imbalance_ratio="3")
        self.assertEqual(default_diagonal.identity, explicit_diagonal.identity)
        different_diagonal = diagonal_imbalance_definition(imbalance_ratio="4")
        self.assertNotEqual(default_diagonal.identity, different_diagonal.identity)

        default_stacked = stacked_imbalance_definition()
        explicit_stacked = stacked_imbalance_definition(imbalance_ratio="3", stacked_min_levels=3)
        self.assertEqual(default_stacked.identity, explicit_stacked.identity)
        different_stacked = stacked_imbalance_definition(stacked_min_levels=4)
        self.assertNotEqual(default_stacked.identity, different_stacked.identity)

    def test_imbalance_ratio_must_be_strictly_greater_than_one(self):
        for ratio in ("1", "0", "-1", "1.0"):
            with self.subTest(ratio=ratio):
                with self.assertRaises(FeatureDefinitionError):
                    diagonal_imbalance_definition(imbalance_ratio=ratio)
                with self.assertRaises(FeatureDefinitionError):
                    stacked_imbalance_definition(imbalance_ratio=ratio)
        diagonal_imbalance_definition(imbalance_ratio="1.0001")

    def test_stacked_min_levels_must_be_at_least_two(self):
        with self.assertRaises(FeatureDefinitionError):
            stacked_imbalance_definition(stacked_min_levels=1)
        stacked_imbalance_definition(stacked_min_levels=2)

    def test_h01_imbalance_feature_set_identity(self):
        self.assertEqual("h01_imbalance", H01_IMBALANCE_FEATURE_SET_IDENTITY.slug)
        self.assertEqual(1, H01_IMBALANCE_FEATURE_SET_IDENTITY.version)


class H01FeatureOwnedEvaluationTests(unittest.TestCase):
    def test_isolated_level_normalizes_absent_neighbor_nan_to_null(self):
        definition = diagonal_imbalance_definition()
        bucket = h01_bucket((H01LevelInput(level_index=5, buy_volume="10", sell_volume="1"),))
        observation = evaluate_diagonal_imbalance(definition, bucket)
        self.assertEqual(ObservationLifecycle.FINAL, observation.lifecycle)
        record = observation.value[0]
        self.assertIsNone(record["ask_imbalance_ratio"])
        self.assertIsNone(record["bid_imbalance_ratio"])
        self.assertIsNone(record["imbalance_side"])

    def test_positive_infinity_ratio_survives_canonical_projection(self):
        definition = diagonal_imbalance_definition()
        bucket = h01_bucket((
            H01LevelInput(level_index=0, buy_volume="0", sell_volume="0"),
            H01LevelInput(level_index=1, buy_volume="5", sell_volume="0"),
        ))
        observation = evaluate_diagonal_imbalance(definition, bucket)
        records = {record["level_index"]: record for record in observation.value}
        self.assertEqual(math.inf, records[1]["ask_imbalance_ratio"])

    def test_diagonal_record_contains_only_canonical_fields(self):
        definition = diagonal_imbalance_definition()
        bucket = h01_bucket((H01LevelInput(level_index=1, buy_volume="10", sell_volume="1"),))
        observation = evaluate_diagonal_imbalance(definition, bucket)
        self.assertEqual(
            {"level_index", "ask_imbalance_ratio", "bid_imbalance_ratio", "imbalance_side"},
            set(observation.value[0]),
        )

    def test_stacked_record_excludes_diagonal_and_footprint_fields(self):
        definition = stacked_imbalance_definition()
        bucket = h01_bucket((H01LevelInput(level_index=1, buy_volume="10", sell_volume="1"),))
        observation = evaluate_stacked_imbalance(definition, bucket)
        self.assertEqual(
            {"level_index", "imbalance_side", "stacked_imbalance", "stacked_run_length"},
            set(observation.value[0]),
        )

    def test_sparse_level_input_is_passed_without_synthesis(self):
        definition = stacked_imbalance_definition(stacked_min_levels=2)
        bucket = h01_bucket((
            H01LevelInput(level_index=0, buy_volume="10", sell_volume="1"),
            H01LevelInput(level_index=1, buy_volume="10", sell_volume="1"),
            H01LevelInput(level_index=3, buy_volume="10", sell_volume="1"),
        ))
        observation = evaluate_stacked_imbalance(definition, bucket)
        level_indexes = [record["level_index"] for record in observation.value]
        self.assertEqual([0, 1, 3], level_indexes)

    def test_observation_binds_the_bucket_as_concrete_support_coordinate(self):
        definition = diagonal_imbalance_definition()
        bucket = h01_bucket((H01LevelInput(level_index=1, buy_volume="10", sell_volume="1"),), obs_id="bucket:xyz")
        observation = evaluate_diagonal_imbalance(definition, bucket)
        self.assertEqual("bucket:xyz", observation.support_identity.observation_identity)

    def test_evaluate_diagonal_imbalance_rejects_stacked_definition(self):
        stacked = stacked_imbalance_definition()
        bucket = h01_bucket((H01LevelInput(level_index=1, buy_volume="10", sell_volume="1"),))
        with self.assertRaises(FeatureDefinitionError):
            evaluate_diagonal_imbalance(stacked, bucket)

    def test_evaluate_stacked_imbalance_rejects_diagonal_definition(self):
        diagonal = diagonal_imbalance_definition()
        bucket = h01_bucket((H01LevelInput(level_index=1, buy_volume="10", sell_volume="1"),))
        with self.assertRaises(FeatureDefinitionError):
            evaluate_stacked_imbalance(diagonal, bucket)

    def test_evaluate_diagonal_imbalance_rejects_noncanonical_definition_version(self):
        canonical = diagonal_imbalance_definition()
        noncanonical = FeatureDefinition(
            feature_key=canonical.feature_key,
            semantic_version="2",
            parameter_schema=canonical.parameter_schema,
            parameters={"imbalance_ratio": "3"},
            input_contract=canonical.input_contract,
            support=canonical.support,
            input_maturity=canonical.input_maturity,
            availability=canonical.availability,
            finality=canonical.finality,
            output_contract=canonical.output_contract,
        )
        bucket = h01_bucket((H01LevelInput(level_index=1, buy_volume="10", sell_volume="1"),))
        with self.assertRaises(FeatureDefinitionError):
            evaluate_diagonal_imbalance(noncanonical, bucket)

    def test_evaluate_stacked_imbalance_rejects_noncanonical_definition_version(self):
        canonical = stacked_imbalance_definition()
        noncanonical = FeatureDefinition(
            feature_key=canonical.feature_key,
            semantic_version="2",
            parameter_schema=canonical.parameter_schema,
            parameters={"imbalance_ratio": "3", "stacked_min_levels": 3},
            input_contract=canonical.input_contract,
            support=canonical.support,
            input_maturity=canonical.input_maturity,
            availability=canonical.availability,
            finality=canonical.finality,
            output_contract=canonical.output_contract,
        )
        bucket = h01_bucket((H01LevelInput(level_index=1, buy_volume="10", sell_volume="1"),))
        with self.assertRaises(FeatureDefinitionError):
            evaluate_stacked_imbalance(noncanonical, bucket)


class H01CompositionTests(unittest.TestCase):
    def _four_level_trades(self) -> list[TradeRecord]:
        trades = []
        for price in ("100", "101", "102", "103"):
            trades.append(trade(f"2024-01-01T00:00:0{int(price) - 99}Z", price, "10", "buy"))
            trades.append(trade(f"2024-01-01T00:00:1{int(price) - 99}Z", price, "1", "sell"))
        return trades

    def test_normal_final_bucket_yields_two_final_observations_with_ordered_levels(self):
        result = footprint_result(self._four_level_trades(), "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z")
        self.assertEqual(1, result.bucket_count)
        self.assertEqual(4, result.row_count)

        evaluation = evaluate_h01_imbalance(result)
        self.assertIsInstance(evaluation, H01Evaluation)
        self.assertEqual(2, len(evaluation.observations))

        diagonal_obs = next(
            o for o in evaluation.observations if o.definition_id == evaluation.diagonal_definition.definition_id
        )
        stacked_obs = next(
            o for o in evaluation.observations if o.definition_id == evaluation.stacked_definition.definition_id
        )
        self.assertEqual(ObservationLifecycle.FINAL, diagonal_obs.lifecycle)
        self.assertEqual(ObservationLifecycle.FINAL, stacked_obs.lifecycle)

        bucket_id = bucket_observation_identity(result, result.buckets[0])
        self.assertEqual(bucket_id, diagonal_obs.support_identity.observation_identity)
        self.assertEqual(bucket_id, stacked_obs.support_identity.observation_identity)

        level_indexes = [record["level_index"] for record in diagonal_obs.value]
        self.assertEqual(sorted(level_indexes), level_indexes)
        self.assertEqual([100, 101, 102, 103], level_indexes)

        sides = {record["level_index"]: record["imbalance_side"] for record in diagonal_obs.value}
        self.assertIsNone(sides[100])
        self.assertEqual("ask", sides[101])
        self.assertEqual("ask", sides[102])
        self.assertEqual("ask", sides[103])

        stacked_by_level = {record["level_index"]: record for record in stacked_obs.value}
        self.assertEqual([100, 101, 102, 103], [record["level_index"] for record in stacked_obs.value])
        self.assertFalse(stacked_by_level[100]["stacked_imbalance"])
        self.assertTrue(stacked_by_level[101]["stacked_imbalance"])
        self.assertTrue(stacked_by_level[102]["stacked_imbalance"])
        self.assertTrue(stacked_by_level[103]["stacked_imbalance"])
        self.assertEqual(3, stacked_by_level[101]["stacked_run_length"])

    def test_bucket_identity_differs_across_datasets_with_same_interval_and_definition(self):
        result_btc = footprint_result([], "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z")
        result_eth = footprint_result(
            [], "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z", dataset_identity=OTHER_IDENTITY,
        )
        self.assertEqual(result_btc.buckets[0].bucket_start, result_eth.buckets[0].bucket_start)
        self.assertEqual(result_btc.buckets[0].bucket_end, result_eth.buckets[0].bucket_end)
        self.assertEqual(result_btc.definition_identity, result_eth.definition_identity)

        id_btc = bucket_observation_identity(result_btc, result_btc.buckets[0])
        id_eth = bucket_observation_identity(result_eth, result_eth.buckets[0])
        self.assertNotEqual(id_btc, id_eth)

    def test_covered_empty_final_bucket_yields_two_final_empty_observations(self):
        result = footprint_result([], "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z")
        self.assertEqual(1, result.bucket_count)
        self.assertEqual((), result.buckets[0].levels)

        evaluation = evaluate_h01_imbalance(result)
        self.assertEqual(2, len(evaluation.observations))
        for observation in evaluation.observations:
            self.assertEqual(ObservationLifecycle.FINAL, observation.lifecycle)
            self.assertEqual([], observation.value)

    def test_missing_support_does_not_fabricate_observation(self):
        result = footprint_result([], "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z")
        absent = replace(result, buckets=())
        evaluation = evaluate_h01_imbalance(absent)
        self.assertEqual((), evaluation.observations)
        self.assertEqual((), evaluation.expected_observation_identities)

    def test_expected_universe_equals_emitted_identities_across_multiple_buckets(self):
        trades = [
            trade("2024-01-01T00:00:01Z", "100", "10", "buy"),
            trade("2024-01-01T00:01:01Z", "100", "10", "buy"),
        ]
        result = footprint_result(trades, "2024-01-01T00:00:00Z", "2024-01-01T00:02:00Z")
        self.assertEqual(2, result.bucket_count)

        evaluation = evaluate_h01_imbalance(result)
        self.assertEqual(4, len(evaluation.observations))
        emitted = {observation.identity for observation in evaluation.observations}
        expected = {identity.identity for identity in evaluation.expected_observation_identities}
        self.assertEqual(expected, emitted)

    def test_removing_or_adding_observation_breaks_e04_finality_universe_proof(self):
        result = footprint_result(
            [trade("2024-01-01T00:00:01Z", "100", "10", "buy")],
            "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z",
        )
        evaluation = evaluate_h01_imbalance(result)
        constituents = (
            ConstituentFeatureOutput(
                evaluation.diagonal_definition.definition_id, evaluation.diagonal_definition.output_contract,
            ),
            ConstituentFeatureOutput(
                evaluation.stacked_definition.definition_id, evaluation.stacked_definition.output_contract,
            ),
        )
        support_shape = SupportShape(intervals=result.coverage.covered_intervals)

        require_final_observations(
            evaluation.observations,
            expected_observation_identities=evaluation.expected_observation_identities,
            constituent_output_contracts=constituents,
            declared_materialized_support=support_shape,
        )

        with self.assertRaises(FeatureArtifactError):
            require_final_observations(
                evaluation.observations[:-1],
                expected_observation_identities=evaluation.expected_observation_identities,
                constituent_output_contracts=constituents,
                declared_materialized_support=support_shape,
            )

        extra_bucket = h01_bucket(
            (H01LevelInput(level_index=999, buy_volume="1", sell_volume="1"),), obs_id="bucket:extra",
        )
        extra_observation = evaluate_diagonal_imbalance(evaluation.diagonal_definition, extra_bucket)
        with self.assertRaises(FeatureArtifactError):
            require_final_observations(
                evaluation.observations + (extra_observation,),
                expected_observation_identities=evaluation.expected_observation_identities,
                constituent_output_contracts=constituents,
                declared_materialized_support=support_shape,
            )

    def test_in_memory_evaluation_does_not_require_materialization(self):
        result = footprint_result(
            [trade("2024-01-01T00:00:01Z", "100", "10", "buy")],
            "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z",
        )
        evaluation = evaluate_h01_imbalance(result)
        self.assertIsInstance(evaluation, H01Evaluation)
        self.assertTrue(evaluation.observations)


class H01MaterializationTests(unittest.TestCase):
    def test_materialization_binds_exact_d06_result_identity_as_provenance(self):
        result = footprint_result(
            [trade("2024-01-01T00:00:01Z", "100", "10", "buy")],
            "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z",
        )
        evaluation = evaluate_h01_imbalance(result)
        artifact = materialize_h01_feature_artifact(
            evaluation, result,
            sources=sources(),
            implementation_code_identity="impl-code-v1",
            content_identity=content_identity(result),
        )
        self.assertEqual(result.result_identity, artifact.bound_input_evidence.provenance_identity)
        ids = {str(item.definition_id) for item in artifact.constituent_output_contracts}
        self.assertEqual(
            {str(evaluation.diagonal_definition.definition_id), str(evaluation.stacked_definition.definition_id)},
            ids,
        )

    def test_changing_d06_source_evidence_changes_downstream_artifact_identity(self):
        trades = [trade("2024-01-01T00:00:01Z", "100", "10", "buy")]
        result_a = footprint_result(trades, "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z")
        result_b = footprint_result(
            trades, "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z", schema_hash="different-schema-sha",
        )
        self.assertNotEqual(result_a.result_identity, result_b.result_identity)

        evaluation_a = evaluate_h01_imbalance(result_a)
        evaluation_b = evaluate_h01_imbalance(result_b)
        artifact_a = materialize_h01_feature_artifact(
            evaluation_a, result_a, sources=sources(),
            implementation_code_identity="impl", content_identity=content_identity(result_a),
        )
        artifact_b = materialize_h01_feature_artifact(
            evaluation_b, result_b, sources=sources(),
            implementation_code_identity="impl", content_identity=content_identity(result_b),
        )
        self.assertNotEqual(
            artifact_a.bound_input_evidence.identity, artifact_b.bound_input_evidence.identity,
        )
        self.assertNotEqual(artifact_a.identity, artifact_b.identity)

    def test_materialize_requires_sources_to_match_d06_source_dataset_identity(self):
        result = footprint_result(
            [trade("2024-01-01T00:00:01Z", "100", "10", "buy")],
            "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z",
        )
        evaluation = evaluate_h01_imbalance(result)
        wrong_identity = DatasetIdentity("canonical", "trades", "kraken", "BTCUSDT", "trade-v1")
        with self.assertRaises(H01CompositionError):
            materialize_h01_feature_artifact(
                evaluation, result,
                sources=sources(dataset_identity=wrong_identity),
                implementation_code_identity="impl",
                content_identity=content_identity(result),
            )

    def test_materialize_rejects_evaluation_from_a_different_footprint_result(self):
        result_a = footprint_result(
            [trade("2024-01-01T00:00:01Z", "100", "10", "buy")],
            "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z",
        )
        result_b = footprint_result(
            [trade("2024-01-01T00:00:01Z", "200", "10", "buy")],
            "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z",
        )
        # Same dataset identity and same declared support -- exactly the
        # scenario where a mismatched evaluation/footprint_result pair must
        # still be rejected, since the two results have different content.
        self.assertEqual(result_a.source_evidence.dataset_identity, result_b.source_evidence.dataset_identity)
        self.assertEqual(result_a.coverage.covered_intervals, result_b.coverage.covered_intervals)
        self.assertNotEqual(result_a.result_identity, result_b.result_identity)

        evaluation_a = evaluate_h01_imbalance(result_a)
        with self.assertRaises(H01CompositionError):
            materialize_h01_feature_artifact(
                evaluation_a, result_b,
                sources=sources(),
                implementation_code_identity="impl",
                content_identity=content_identity(result_b),
            )

    def test_materialize_rejects_evaluation_with_forged_footprint_result_identity(self):
        result_a = footprint_result(
            [trade("2024-01-01T00:00:01Z", "100", "10", "buy")],
            "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z",
        )
        result_b = footprint_result(
            [trade("2024-01-01T00:00:01Z", "200", "10", "buy")],
            "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z",
        )
        self.assertEqual(result_a.source_evidence.dataset_identity, result_b.source_evidence.dataset_identity)
        self.assertEqual(result_a.coverage.covered_intervals, result_b.coverage.covered_intervals)
        self.assertNotEqual(result_a.result_identity, result_b.result_identity)

        evaluation_a = evaluate_h01_imbalance(result_a)
        # Forge footprint_result_identity to match result_b.result_identity:
        # the simple identity check alone would pass, but the observation verification rejects it.
        tampered = replace(evaluation_a, footprint_result_identity=result_b.result_identity)
        with self.assertRaises(H01CompositionError):
            materialize_h01_feature_artifact(
                tampered, result_b,
                sources=sources(),
                implementation_code_identity="impl",
                content_identity=content_identity(result_b),
            )

    def test_materialize_derives_universe_from_footprint_result_not_tampered_evaluation(self):
        result = footprint_result(
            [trade("2024-01-01T00:00:01Z", "100", "10", "buy")],
            "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z",
        )
        evaluation = evaluate_h01_imbalance(result)
        tampered = replace(evaluation, expected_observation_identities=())
        artifact = materialize_h01_feature_artifact(
            tampered, result,
            sources=sources(),
            implementation_code_identity="impl",
            content_identity=content_identity(result),
        )
        self.assertIsNotNone(artifact.identity)

    def test_materialize_rejects_tampered_extra_observation_despite_caller_universe(self):
        result = footprint_result(
            [trade("2024-01-01T00:00:01Z", "100", "10", "buy")],
            "2024-01-01T00:00:00Z", "2024-01-01T00:01:00Z",
        )
        evaluation = evaluate_h01_imbalance(result)
        extra_bucket = h01_bucket(
            (H01LevelInput(level_index=999, buy_volume="1", sell_volume="1"),), obs_id="bucket:extra",
        )
        extra_observation = evaluate_diagonal_imbalance(evaluation.diagonal_definition, extra_bucket)
        tampered = replace(
            evaluation,
            observations=evaluation.observations + (extra_observation,),
            expected_observation_identities=evaluation.expected_observation_identities
            + (extra_observation.observation_identity,),
        )
        with self.assertRaises(H01CompositionError):
            materialize_h01_feature_artifact(
                tampered, result,
                sources=sources(),
                implementation_code_identity="impl",
                content_identity=content_identity(result),
            )


if __name__ == "__main__":
    unittest.main()
