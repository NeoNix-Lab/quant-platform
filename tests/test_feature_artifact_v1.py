#!/usr/bin/env python3
"""E04 FeatureArtifact v1 runtime foundation proof."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.data.models import CoverageInterval, DatasetIdentity, Instant, NaturalPartitionIdentity  # noqa: E402
from quant_platform.features import (  # noqa: E402
    FEATURE_ARTIFACT_MODEL_VERSION,
    ArtifactRegistrationOutcome,
    BoundInputEvidence,
    BoundOutputPartition,
    BoundSourceDataset,
    BoundSourcePartition,
    ConstituentFeatureOutput,
    FeatureArtifact,
    FeatureArtifactContentIdentity,
    FeatureArtifactError,
    FeatureArtifactIdentity,
    FeatureArtifactLifecycle,
    FeatureAvailabilitySemantics,
    FeatureDefinition,
    FeatureFinalitySemantics,
    FeatureObservation,
    FeatureSetDefinitionIdentity,
    InputContractV1,
    InputMaturity,
    NumericalEquivalence,
    NumericalEquivalenceKind,
    ObservationLifecycle,
    OutputContract,
    OutputDimension,
    OutputValueKind,
    SupportIdentity,
    SupportReference,
    SupportShape,
    classify_registration,
    recomputation_equivalent,
    rehydrate_feature_artifact,
    require_final_observations,
    seal_feature_artifact,
    values_semantically_equivalent,
    verify_matches_request,
)


DATASET_IDENTITY = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
OTHER_DATASET_IDENTITY = DatasetIdentity("canonical", "trades", "kraken", "BTCUSDT", "trade-v1")
SUPPORT = CoverageInterval(Instant.parse("2024-01-15T00:00:00Z"), Instant.parse("2024-01-16T00:00:00Z"))
OTHER_SUPPORT = CoverageInterval(Instant.parse("2024-01-16T00:00:00Z"), Instant.parse("2024-01-17T00:00:00Z"))
TRADES_CONTRACT = InputContractV1("trades", "1", ("price", "size"))


def output_dataset_identity(slug: str = "trade_microstructure", version: int = 1) -> DatasetIdentity:
    return DatasetIdentity(
        "features", slug, "bybit", "BTCUSDT", "feature-v1",
        feature_set_slug=slug, feature_set_version=version,
    )


def natural(
    revision: int = 1, partition_key: str = "dt=2024-01-15", *, dataset_identity: DatasetIdentity = DATASET_IDENTITY,
) -> NaturalPartitionIdentity:
    return NaturalPartitionIdentity(dataset_identity, partition_key, revision)


def source_partition(
    *, token: str = "1", revision: int = 1, partition_key: str = "dt=2024-01-15",
    dataset_identity: DatasetIdentity = DATASET_IDENTITY,
) -> BoundSourcePartition:
    return BoundSourcePartition(
        natural_identity=natural(revision, partition_key, dataset_identity=dataset_identity),
        content_sha256=token * 64,
        manifest_sha256=token * 64,
    )


def output_partition(
    *, token: str = "a", revision: int = 1, partition_key: str = "dt=2024-01-15",
    slug: str = "trade_microstructure", version: int = 1,
) -> BoundOutputPartition:
    return BoundOutputPartition(
        natural_identity=NaturalPartitionIdentity(output_dataset_identity(slug, version), partition_key, revision),
        content_sha256=token * 64,
        manifest_sha256=(token * 63 + "0"),
    )


def content_identity(
    token: str = "a", *, support: CoverageInterval = SUPPORT,
    slug: str = "trade_microstructure", version: int = 1,
) -> FeatureArtifactContentIdentity:
    return FeatureArtifactContentIdentity(
        output_partitions=(output_partition(token=token, slug=slug, version=version),),
        declared_support=support,
    )


def support_shape(*, support: CoverageInterval = SUPPORT) -> SupportShape:
    return SupportShape(intervals=(support,))


def bound_input_evidence(
    *, token: str = "1", revision: int = 1, partition_key: str = "dt=2024-01-15",
    support: CoverageInterval = SUPPORT, dataset_identity: DatasetIdentity = DATASET_IDENTITY,
    provenance: str = "b04-coverage-reconstruction:1",
) -> BoundInputEvidence:
    return BoundInputEvidence(
        sources=(BoundSourceDataset(dataset_identity, (source_partition(
            token=token, revision=revision, partition_key=partition_key, dataset_identity=dataset_identity,
        ),)),),
        consumed_support=support_shape(support=support),
        provenance_identity=provenance,
    )


def feature_set_identity(slug: str = "trade_microstructure", version: int = 1) -> FeatureSetDefinitionIdentity:
    return FeatureSetDefinitionIdentity(slug, version)


def output_contract(equivalence: NumericalEquivalence | None = None) -> OutputContract:
    return OutputContract(
        OutputValueKind.NUMERIC, "scalar", OutputDimension.DIMENSIONLESS,
        equivalence or NumericalEquivalence.exact(),
    )


def feature_definition(
    *, feature_key: str = "order_flow.delta", version: str = "1",
    equivalence: NumericalEquivalence | None = None,
) -> FeatureDefinition:
    return FeatureDefinition(
        feature_key=feature_key,
        semantic_version=version,
        parameter_schema=(),
        input_contract=TRADES_CONTRACT,
        support=(SupportReference.current(),),
        input_maturity=InputMaturity.FINAL_ONLY,
        availability=FeatureAvailabilitySemantics(),
        finality=FeatureFinalitySemantics(),
        output_contract=output_contract(equivalence),
    )


def support_identity(*, contract: InputContractV1 = TRADES_CONTRACT, obs: str = "obs-1") -> SupportIdentity:
    return SupportIdentity(contract.identity, obs, SupportReference.current())


def feature_observation(
    *, definition: FeatureDefinition, obs: str = "obs-1", value: str = "1.0",
    lifecycle: ObservationLifecycle = ObservationLifecycle.FINAL,
    causal: Instant | str = "2024-01-15T00:00:00Z",
) -> FeatureObservation:
    return FeatureObservation(
        definition_id=definition.definition_id,
        support_identity=support_identity(contract=definition.input_contract, obs=obs),
        value=value,
        lifecycle=lifecycle,
        causal_available_at=causal,
    )


def constituent(definition: FeatureDefinition) -> ConstituentFeatureOutput:
    return ConstituentFeatureOutput(definition.definition_id, definition.output_contract)


def artifact(
    *, definition: FeatureDefinition | None = None,
    fsd_identity: FeatureSetDefinitionIdentity | None = None,
    evidence: BoundInputEvidence | None = None,
    support: CoverageInterval = SUPPORT,
    code: str = "commit-1",
    content: FeatureArtifactContentIdentity | None = None,
    observations: tuple[FeatureObservation, ...] | None = None,
    constituents: tuple[ConstituentFeatureOutput, ...] | None = None,
    expected_observation_identities: tuple[str, ...] | None = None,
) -> tuple[FeatureArtifact, FeatureDefinition]:
    definition = definition or feature_definition()
    fsd_identity = fsd_identity or feature_set_identity()
    constituents = constituents if constituents is not None else (constituent(definition),)
    observations = observations if observations is not None else (
        feature_observation(definition=definition, causal=support.start),
    )
    # By default the expected universe is exactly what the fixture itself
    # evaluated (the happy path); adversarial tests override this
    # independently of `observations` to prove omission/inflation is caught.
    expected_observation_identities = (
        expected_observation_identities
        if expected_observation_identities is not None
        else tuple(item.identity for item in observations)
    )
    result = seal_feature_artifact(
        feature_set_definition_identity=fsd_identity,
        bound_input_evidence=evidence or bound_input_evidence(support=support),
        declared_materialized_support=support,
        implementation_code_identity=code,
        content_identity=content or content_identity(slug=fsd_identity.slug, version=fsd_identity.version, support=support),
        constituent_output_contracts=constituents,
        observations=observations,
        expected_observation_identities=expected_observation_identities,
    )
    return result, definition


class FeatureSetDefinitionIdentityTests(unittest.TestCase):
    """Incorporated finding: portable (slug, version) runtime identity."""

    def test_portable_representation_and_stable_dict(self):
        identity = feature_set_identity("trade_microstructure", 3)
        self.assertEqual("trade_microstructure@v3", identity.portable)
        self.assertEqual({"slug": "trade_microstructure", "version": 3}, identity.stable_dict())

    def test_no_uuid_field_exists_to_carry_a_surrogate(self):
        identity = feature_set_identity("trade_microstructure", 1)
        self.assertEqual({"slug", "version"}, set(identity.stable_dict()))

    def test_equal_slug_version_is_equal_identity_regardless_of_surrogate_context(self):
        first = feature_set_identity("trade_microstructure", 1)
        second = feature_set_identity("trade_microstructure", 1)
        self.assertEqual(first, second)
        self.assertEqual(first.portable, second.portable)

    def test_empty_slug_fails_closed(self):
        with self.assertRaises(FeatureArtifactError):
            FeatureSetDefinitionIdentity("", 1)

    def test_ungoverned_slug_spelling_fails_closed(self):
        with self.assertRaises(FeatureArtifactError):
            FeatureSetDefinitionIdentity("Trade-Microstructure!", 1)

    def test_non_positive_version_fails_closed(self):
        with self.assertRaises(FeatureArtifactError):
            FeatureSetDefinitionIdentity("trade_microstructure", 0)
        with self.assertRaises(FeatureArtifactError):
            FeatureSetDefinitionIdentity("trade_microstructure", -1)

    def test_non_integer_version_fails_closed(self):
        with self.assertRaises(FeatureArtifactError):
            FeatureSetDefinitionIdentity("trade_microstructure", "1")  # type: ignore[arg-type]

    def test_frozen(self):
        identity = feature_set_identity()
        with self.assertRaises(FrozenInstanceError):
            identity.slug = "other"  # type: ignore[misc]


class SupportShapeTests(unittest.TestCase):
    def test_requires_at_least_one_interval(self):
        with self.assertRaises(FeatureArtifactError):
            SupportShape(intervals=())

    def test_non_contiguous_intervals_are_accepted_and_order_independent(self):
        gap_day = CoverageInterval(Instant.parse("2024-01-17T00:00:00Z"), Instant.parse("2024-01-18T00:00:00Z"))
        first = SupportShape(intervals=(SUPPORT, gap_day))
        second = SupportShape(intervals=(gap_day, SUPPORT))
        self.assertEqual(first.stable_dict(), second.stable_dict())
        self.assertEqual(2, len(first.intervals))

    def test_overlapping_intervals_are_rejected(self):
        overlapping = CoverageInterval(Instant.parse("2024-01-15T12:00:00Z"), Instant.parse("2024-01-16T12:00:00Z"))
        with self.assertRaises(FeatureArtifactError):
            SupportShape(intervals=(SUPPORT, overlapping))

    def test_adjacent_intervals_coalesce_to_the_same_identity_as_one_merged_interval(self):
        # Fresh-review finding: [a,c) and [a,b)+[b,c) are the exact same
        # real coverage and must produce the same identity -- the split
        # representation must not survive as an accidental identity input.
        midpoint = Instant.parse("2024-01-15T12:00:00Z")
        split = SupportShape(intervals=(
            CoverageInterval(SUPPORT.start, midpoint),
            CoverageInterval(midpoint, SUPPORT.end),
        ))
        merged = SupportShape(intervals=(SUPPORT,))
        self.assertEqual(merged.stable_dict(), split.stable_dict())
        self.assertEqual(1, len(split.intervals))


class BoundInputEvidenceTests(unittest.TestCase):
    def test_missing_sources_fails_closed(self):
        with self.assertRaises(FeatureArtifactError):
            BoundInputEvidence(sources=(), consumed_support=support_shape(), provenance_identity="prov-1")

    def test_source_dataset_requires_at_least_one_partition(self):
        with self.assertRaises(FeatureArtifactError):
            BoundSourceDataset(DATASET_IDENTITY, ())

    def test_invalid_content_sha_fails_closed(self):
        with self.assertRaises(FeatureArtifactError):
            BoundSourcePartition(natural_identity=natural(), content_sha256="not-hex", manifest_sha256="b" * 64)

    def test_duplicate_partitions_are_rejected(self):
        part = source_partition()
        with self.assertRaises(FeatureArtifactError):
            BoundSourceDataset(DATASET_IDENTITY, (part, part))

    def test_duplicate_source_datasets_are_rejected(self):
        dataset = BoundSourceDataset(DATASET_IDENTITY, (source_partition(),))
        with self.assertRaises(FeatureArtifactError):
            BoundInputEvidence(sources=(dataset, dataset), consumed_support=support_shape(), provenance_identity="prov-1")

    def test_partition_embedded_dataset_identity_must_match_enclosing_dataset(self):
        mismatched = source_partition(dataset_identity=OTHER_DATASET_IDENTITY)
        with self.assertRaises(FeatureArtifactError):
            BoundSourceDataset(DATASET_IDENTITY, (mismatched,))

    def test_missing_provenance_identity_fails_closed(self):
        with self.assertRaises(FeatureArtifactError):
            BoundInputEvidence(
                sources=(BoundSourceDataset(DATASET_IDENTITY, (source_partition(),)),),
                consumed_support=support_shape(),
                provenance_identity="",
            )
        with self.assertRaises(TypeError):
            BoundInputEvidence(  # type: ignore[call-arg]
                sources=(BoundSourceDataset(DATASET_IDENTITY, (source_partition(),)),),
                consumed_support=support_shape(),
            )

    def test_identity_is_order_independent_across_sources_and_partitions(self):
        first_partitions = (source_partition(token="1"), source_partition(token="2", partition_key="dt=2024-01-16"))
        second_partitions = tuple(reversed(first_partitions))
        first = BoundInputEvidence(
            sources=(BoundSourceDataset(DATASET_IDENTITY, first_partitions),),
            consumed_support=support_shape(), provenance_identity="prov-1",
        )
        second = BoundInputEvidence(
            sources=(BoundSourceDataset(DATASET_IDENTITY, second_partitions),),
            consumed_support=support_shape(), provenance_identity="prov-1",
        )
        self.assertEqual(first.identity, second.identity)

    def test_different_revision_changes_identity(self):
        first = bound_input_evidence(revision=1)
        second = bound_input_evidence(revision=2)
        self.assertNotEqual(first.identity, second.identity)

    def test_different_content_sha_changes_identity(self):
        first = bound_input_evidence(token="1")
        second = bound_input_evidence(token="2")
        self.assertNotEqual(first.identity, second.identity)

    def test_non_contiguous_support_shape_changes_identity(self):
        gap_day = CoverageInterval(Instant.parse("2024-01-17T00:00:00Z"), Instant.parse("2024-01-18T00:00:00Z"))
        contiguous = bound_input_evidence()
        non_contiguous = BoundInputEvidence(
            sources=(BoundSourceDataset(DATASET_IDENTITY, (source_partition(),)),),
            consumed_support=SupportShape(intervals=(SUPPORT, gap_day)),
            provenance_identity="b04-coverage-reconstruction:1",
        )
        self.assertNotEqual(contiguous.identity, non_contiguous.identity)

    def test_different_provenance_identity_changes_identity(self):
        first = bound_input_evidence(provenance="b04-coverage-reconstruction:1")
        second = bound_input_evidence(provenance="b04-coverage-reconstruction:2")
        self.assertNotEqual(first.identity, second.identity)


class FeatureArtifactIdentityTests(unittest.TestCase):
    def test_same_everything_different_path_same_identity_and_content_identity(self):
        sealed, _ = artifact()
        relocated_a = sealed.relocated(physical_locators=("s3://bucket/a.parquet",))
        relocated_b = sealed.relocated(physical_locators=("s3://bucket/b.parquet",))
        self.assertEqual(sealed.identity, relocated_a.identity)
        self.assertEqual(sealed.identity, relocated_b.identity)
        self.assertEqual(sealed.content_identity, relocated_a.content_identity)
        self.assertNotEqual(relocated_a.physical_locators, relocated_b.physical_locators)

    def test_malformed_non_hex_artifact_identity_fails_closed(self):
        with self.assertRaises(FeatureArtifactError):
            FeatureArtifactIdentity("feature-artifact-v1:sha256:" + "z" * 64)

    def test_different_source_revision_is_distinct_artifact_identity(self):
        first, definition = artifact(evidence=bound_input_evidence(revision=1))
        second, _ = artifact(definition=definition, evidence=bound_input_evidence(revision=2))
        self.assertNotEqual(first.identity, second.identity)

    def test_different_declared_support_is_distinct_identity(self):
        first, definition = artifact(support=SUPPORT)
        second, _ = artifact(definition=definition, support=OTHER_SUPPORT, evidence=bound_input_evidence(support=OTHER_SUPPORT))
        self.assertNotEqual(first.identity, second.identity)

    def test_different_implementation_code_identity_is_distinct_identity(self):
        first, definition = artifact(code="commit-1")
        second, _ = artifact(definition=definition, code="commit-2")
        self.assertNotEqual(first.identity, second.identity)
        self.assertEqual(first.constituent_output_contracts, second.constituent_output_contracts)

    def test_byte_identical_content_from_different_source_revision_is_distinct_identity(self):
        shared_content = content_identity("c")
        first, definition = artifact(evidence=bound_input_evidence(revision=1), content=shared_content)
        second, _ = artifact(definition=definition, evidence=bound_input_evidence(revision=2), content=shared_content)
        self.assertEqual(first.content_identity, second.content_identity)
        self.assertNotEqual(first.identity, second.identity)

    def test_different_feature_set_definition_identity_is_distinct_identity(self):
        first, definition = artifact(fsd_identity=feature_set_identity("trade_microstructure", 1))
        second, _ = artifact(definition=definition, fsd_identity=feature_set_identity("trade_microstructure", 2))
        third, _ = artifact(definition=definition, fsd_identity=feature_set_identity("other_bundle", 1))
        self.assertNotEqual(first.identity, second.identity)
        self.assertNotEqual(first.identity, third.identity)

    def test_corrected_source_revision_leaves_old_artifact_immutable(self):
        original, definition = artifact(evidence=bound_input_evidence(revision=1))
        snapshot = original.stable_dict()
        corrected, _ = artifact(definition=definition, evidence=bound_input_evidence(revision=2))
        self.assertEqual(snapshot, original.stable_dict())
        self.assertNotEqual(original.identity, corrected.identity)
        with self.assertRaises(FrozenInstanceError):
            original.implementation_code_identity = "tampered"  # type: ignore[misc]

    def test_singleton_feature_set_uses_the_same_artifact_type(self):
        sealed, definition = artifact()
        self.assertEqual(1, len(sealed.constituent_output_contracts))
        self.assertIsInstance(sealed, FeatureArtifact)
        self.assertEqual(definition.output_contract, sealed.output_contract_for(definition.definition_id))

    def test_excluded_fields_do_not_appear_in_the_identity_payload(self):
        sealed, _ = artifact()
        payload_text = str(sealed.identity_payload())
        for excluded in ("physical_locators", "content_sha256", "lifecycle"):
            self.assertNotIn(excluded, payload_text)


class OutputPartitionBindingTests(unittest.TestCase):
    def test_output_partition_must_belong_to_the_features_layer(self):
        non_feature_layer = NaturalPartitionIdentity(DATASET_IDENTITY, "dt=2024-01-15", 1)
        with self.assertRaises(FeatureArtifactError):
            BoundOutputPartition(natural_identity=non_feature_layer, content_sha256="a" * 64, manifest_sha256="b" * 64)

    def test_content_identity_requires_at_least_one_output_partition(self):
        with self.assertRaises(FeatureArtifactError):
            FeatureArtifactContentIdentity(output_partitions=(), declared_support=SUPPORT)

    def test_artifact_rejects_content_identity_from_a_different_feature_set_natural_key(self):
        with self.assertRaises(FeatureArtifactError):
            artifact(
                fsd_identity=feature_set_identity("trade_microstructure", 1),
                content=content_identity(slug="trade_microstructure", version=2),
            )

    def test_artifact_rejects_content_identity_from_a_different_slug(self):
        with self.assertRaises(FeatureArtifactError):
            artifact(
                fsd_identity=feature_set_identity("trade_microstructure", 1),
                content=content_identity(slug="other_bundle", version=1),
            )

    def test_artifact_rejects_content_identity_declared_support_mismatch(self):
        with self.assertRaises(FeatureArtifactError):
            artifact(support=SUPPORT, content=content_identity(support=OTHER_SUPPORT))

    def test_matching_output_partition_and_declared_support_succeeds(self):
        sealed, _ = artifact(fsd_identity=feature_set_identity("trade_microstructure", 1), support=SUPPORT)
        self.assertEqual(SUPPORT, sealed.content_identity.declared_support)
        output_dataset = sealed.content_identity.output_partitions[0].natural_identity.dataset_identity
        self.assertEqual("features", output_dataset.layer)
        self.assertEqual("trade_microstructure", output_dataset.feature_set_slug)
        self.assertEqual(1, output_dataset.feature_set_version)


class FinalityGateTests(unittest.TestCase):
    def test_provisional_observation_refuses_materialization(self):
        definition = feature_definition()
        with self.assertRaises(FeatureArtifactError):
            artifact(definition=definition, observations=(
                feature_observation(definition=definition, lifecycle=ObservationLifecycle.PROVISIONAL),
            ))

    def test_mixed_final_and_provisional_refuses_materialization(self):
        definition = feature_definition()
        with self.assertRaises(FeatureArtifactError):
            artifact(definition=definition, observations=(
                feature_observation(definition=definition, obs="obs-1", lifecycle=ObservationLifecycle.FINAL),
                feature_observation(definition=definition, obs="obs-2", lifecycle=ObservationLifecycle.PROVISIONAL),
            ))

    def test_empty_observations_refuses_materialization(self):
        definition = feature_definition()
        with self.assertRaises(FeatureArtifactError):
            require_final_observations(
                (), expected_observation_identities=("dummy-expected-id",),
                constituent_output_contracts=(constituent(definition),), declared_materialized_support=SUPPORT,
            )

    def test_all_final_observations_satisfy_the_gate(self):
        definition = feature_definition()
        obs = feature_observation(definition=definition, causal=SUPPORT.start)
        require_final_observations(
            (obs,),
            expected_observation_identities=(obs.identity,),
            constituent_output_contracts=(constituent(definition),),
            declared_materialized_support=SUPPORT,
        )
        sealed, _ = artifact(definition=definition)
        self.assertEqual(FeatureArtifactLifecycle.FINAL, sealed.lifecycle)

    def test_unrelated_final_observation_cannot_stand_in_for_a_declared_constituent(self):
        feature_a = feature_definition(feature_key="order_flow.delta")
        feature_b = feature_definition(feature_key="order_flow.other")
        obs_b = feature_observation(definition=feature_b, causal=SUPPORT.start)
        with self.assertRaises(FeatureArtifactError):
            require_final_observations(
                (obs_b,),
                expected_observation_identities=(obs_b.identity,),
                constituent_output_contracts=(constituent(feature_a), constituent(feature_b)),
                declared_materialized_support=SUPPORT,
            )

    def test_causal_availability_outside_the_interval_does_not_by_itself_refuse(self):
        # Fresh-review finding: causal_available_at ("earliest legal
        # consumption time", ADR-0026) is NOT a proxy for support
        # membership. A named, FINAL, correctly-declared observation must
        # not be refused merely because it became available strictly after
        # the materialized-support interval closes -- that is a legitimate
        # delayed-finality case, not evidence of a support mismatch.
        definition = feature_definition()
        delayed = feature_observation(definition=definition, causal=OTHER_SUPPORT.start)
        require_final_observations(
            (delayed,),
            expected_observation_identities=(delayed.identity,),
            constituent_output_contracts=(constituent(definition),), declared_materialized_support=SUPPORT,
        )

    def test_observation_for_an_undeclared_feature_is_refused(self):
        declared = feature_definition(feature_key="order_flow.delta")
        undeclared = feature_definition(feature_key="order_flow.other")
        undeclared_obs = feature_observation(definition=undeclared, causal=SUPPORT.start)
        with self.assertRaises(FeatureArtifactError):
            require_final_observations(
                (undeclared_obs,),
                expected_observation_identities=(undeclared_obs.identity,),
                constituent_output_contracts=(constituent(declared),),
                declared_materialized_support=SUPPORT,
            )

    def test_omitted_provisional_observation_is_caught_by_expected_universe(self):
        # round-2 REQUEST_CHANGES finding 1: a caller with 2 evaluated points
        # (1 FINAL, 1 still PROVISIONAL) cannot cherry-pick just the FINAL
        # one and claim the whole declared support -- the expected universe
        # named by E06 must include the still-provisional point, so its
        # absence from `observations` fails closed.
        definition = feature_definition()
        final_obs = feature_observation(definition=definition, obs="obs-1", causal=SUPPORT.start)
        provisional_obs = feature_observation(
            definition=definition, obs="obs-2", lifecycle=ObservationLifecycle.PROVISIONAL, causal=SUPPORT.start,
        )
        with self.assertRaises(FeatureArtifactError):
            require_final_observations(
                (final_obs,),
                expected_observation_identities=(final_obs.identity, provisional_obs.identity),
                constituent_output_contracts=(constituent(definition),),
                declared_materialized_support=SUPPORT,
            )

    def test_observation_not_in_expected_universe_is_refused(self):
        # The reverse direction: a FINAL observation that was never named in
        # the expected universe cannot be smuggled in either.
        definition = feature_definition()
        named = feature_observation(definition=definition, obs="obs-1", causal=SUPPORT.start)
        unnamed = feature_observation(definition=definition, obs="obs-2", causal=SUPPORT.start)
        with self.assertRaises(FeatureArtifactError):
            require_final_observations(
                (named, unnamed),
                expected_observation_identities=(named.identity,),
                constituent_output_contracts=(constituent(definition),),
                declared_materialized_support=SUPPORT,
            )

    def test_direct_construction_is_always_rejected(self):
        # REQUEST_CHANGES finding 4: FeatureArtifact cannot be constructed
        # directly under any circumstances, bypassing seal_feature_artifact()
        # entirely -- not even with an otherwise-plausible-looking payload.
        definition = feature_definition()
        with self.assertRaises(FeatureArtifactError):
            FeatureArtifact(
                feature_set_definition_identity=feature_set_identity(),
                bound_input_evidence=bound_input_evidence(),
                declared_materialized_support=SUPPORT,
                implementation_code_identity="commit-1",
                content_identity=content_identity(),
                constituent_output_contracts=(constituent(definition),),
            )

    def test_unsupported_materialization_contract_version_is_refused(self):
        # Fresh-review finding: this runtime implements ONLY
        # FEATURE_ARTIFACT_MODEL_VERSION semantics; a caller must not be
        # able to seal (or rehydrate) an artifact claiming a version this
        # code never actually established.
        self.assertEqual("1", FEATURE_ARTIFACT_MODEL_VERSION)
        definition = feature_definition()
        obs = feature_observation(definition=definition, causal=SUPPORT.start)
        with self.assertRaises(FeatureArtifactError):
            seal_feature_artifact(
                feature_set_definition_identity=feature_set_identity(),
                bound_input_evidence=bound_input_evidence(),
                declared_materialized_support=SUPPORT,
                implementation_code_identity="commit-1",
                content_identity=content_identity(),
                constituent_output_contracts=(constituent(definition),),
                observations=(obs,),
                expected_observation_identities=(obs.identity,),
                materialization_contract_version="2",
            )

    def test_rehydrate_reconstructs_an_already_sealed_artifact_without_observations(self):
        # rehydrate_feature_artifact() is the ONLY other legitimate
        # construction path: reconstructing an already-sealed catalog
        # record's metadata, without re-running the finality proof.
        sealed, _ = artifact()
        rehydrated = rehydrate_feature_artifact(
            expected_identity=sealed.identity,
            feature_set_definition_identity=sealed.feature_set_definition_identity,
            bound_input_evidence=sealed.bound_input_evidence,
            declared_materialized_support=sealed.declared_materialized_support,
            implementation_code_identity=sealed.implementation_code_identity,
            content_identity=sealed.content_identity,
            constituent_output_contracts=sealed.constituent_output_contracts,
            materialization_contract_version=sealed.materialization_contract_version,
            physical_locators=sealed.physical_locators,
        )
        self.assertEqual(sealed.identity, rehydrated.identity)
        self.assertEqual(sealed.stable_dict(), rehydrated.stable_dict())

    def test_rehydrate_refuses_metadata_that_does_not_reproduce_the_expected_identity(self):
        # round-3 finding: rehydrate_feature_artifact() must not be a bare
        # metadata constructor -- a caller supplying plausible-but-wrong
        # metadata (here, a different implementation_code_identity than the
        # one the real sealed record actually has) cannot obtain a
        # FeatureArtifact whose identity was never actually proven by a real
        # seal_feature_artifact() call.
        sealed, _ = artifact()
        with self.assertRaises(FeatureArtifactError):
            rehydrate_feature_artifact(
                expected_identity=sealed.identity,
                feature_set_definition_identity=sealed.feature_set_definition_identity,
                bound_input_evidence=sealed.bound_input_evidence,
                declared_materialized_support=sealed.declared_materialized_support,
                implementation_code_identity="a-different-commit-never-actually-sealed",
                content_identity=sealed.content_identity,
                constituent_output_contracts=sealed.constituent_output_contracts,
                materialization_contract_version=sealed.materialization_contract_version,
                physical_locators=sealed.physical_locators,
            )


class DuplicateVsConflictTests(unittest.TestCase):
    def test_same_identity_same_content_is_idempotent_duplicate(self):
        shared_content = content_identity("d")
        first, definition = artifact(content=shared_content, code="commit-1")
        second, _ = artifact(definition=definition, content=shared_content, code="commit-1")
        self.assertEqual(first.identity, second.identity)
        self.assertEqual(ArtifactRegistrationOutcome.IDEMPOTENT_DUPLICATE, classify_registration(existing=first, candidate=second))

    def test_same_identity_different_content_is_conflict(self):
        first, definition = artifact(content=content_identity("d"), code="commit-1")
        second, _ = artifact(definition=definition, content=content_identity("e"), code="commit-1")
        self.assertEqual(first.identity, second.identity)
        self.assertEqual(ArtifactRegistrationOutcome.CONFLICT, classify_registration(existing=first, candidate=second))

    def test_classify_registration_requires_matching_identity(self):
        first, definition = artifact(code="commit-1")
        second, _ = artifact(definition=definition, code="commit-2")
        with self.assertRaises(FeatureArtifactError):
            classify_registration(existing=first, candidate=second)


class RelocationAndVerificationTests(unittest.TestCase):
    def test_relocation_changes_only_physical_locators(self):
        sealed, _ = artifact()
        relocated = sealed.relocated(physical_locators=("new-path.parquet",))
        self.assertEqual(("new-path.parquet",), relocated.physical_locators)
        self.assertEqual(sealed.stable_dict()["identity"], relocated.stable_dict()["identity"])

    def _verify_kwargs(self, **overrides):
        definition = feature_definition()
        evidence = bound_input_evidence()
        base = dict(
            feature_set_definition_identity=feature_set_identity(),
            bound_input_evidence=evidence,
            declared_materialized_support=SUPPORT,
            implementation_code_identity="commit-1",
            content_identity=content_identity(),
            constituent_output_contracts=(constituent(definition),),
        )
        base.update(overrides)
        return base

    def test_matching_metadata_verifies_before_payload_use(self):
        evidence = bound_input_evidence()
        sealed, _ = artifact(evidence=evidence, code="commit-1")
        verify_matches_request(sealed, **self._verify_kwargs(bound_input_evidence=evidence))

    def test_missing_trusted_content_identity_is_not_a_valid_call(self):
        sealed, _ = artifact()
        kwargs = self._verify_kwargs()
        kwargs.pop("content_identity")
        with self.assertRaises(TypeError):
            verify_matches_request(sealed, **kwargs)  # type: ignore[call-arg]

    def test_wrong_feature_set_binding_is_refused_before_payload_use(self):
        sealed, _ = artifact(fsd_identity=feature_set_identity("trade_microstructure", 1))
        with self.assertRaises(FeatureArtifactError):
            verify_matches_request(
                sealed,
                **self._verify_kwargs(feature_set_definition_identity=feature_set_identity("trade_microstructure", 2)),
            )

    def test_wrong_support_binding_is_refused_before_payload_use(self):
        sealed, _ = artifact(support=SUPPORT)
        with self.assertRaises(FeatureArtifactError):
            verify_matches_request(sealed, **self._verify_kwargs(declared_materialized_support=OTHER_SUPPORT))

    def test_wrong_source_evidence_is_refused_before_payload_use(self):
        sealed, _ = artifact(evidence=bound_input_evidence(revision=1))
        with self.assertRaises(FeatureArtifactError):
            verify_matches_request(sealed, **self._verify_kwargs(bound_input_evidence=bound_input_evidence(revision=2)))

    def test_wrong_implementation_code_identity_is_refused_before_payload_use(self):
        sealed, _ = artifact(code="commit-1")
        with self.assertRaises(FeatureArtifactError):
            verify_matches_request(sealed, **self._verify_kwargs(implementation_code_identity="commit-2"))

    def test_wrong_materialization_contract_version_is_refused_before_payload_use(self):
        sealed, _ = artifact()
        with self.assertRaises(FeatureArtifactError):
            verify_matches_request(sealed, **self._verify_kwargs(materialization_contract_version="2"))

    def test_wrong_trusted_content_identity_is_refused_before_payload_use(self):
        sealed, _ = artifact(content=content_identity("d"))
        with self.assertRaises(FeatureArtifactError):
            verify_matches_request(sealed, **self._verify_kwargs(content_identity=content_identity("e")))

    def test_matching_trusted_content_identity_passes(self):
        trusted_content = content_identity("d")
        sealed, _ = artifact(content=trusted_content)
        verify_matches_request(sealed, **self._verify_kwargs(content_identity=trusted_content))

    def test_wrong_trusted_constituent_evidence_is_refused_before_payload_use(self):
        sealed, _ = artifact()
        different = feature_definition(feature_key="order_flow.other")
        with self.assertRaises(FeatureArtifactError):
            verify_matches_request(
                sealed,
                **self._verify_kwargs(constituent_output_contracts=(constituent(different),)),
            )


class RecomputationEquivalenceTests(unittest.TestCase):
    def test_exact_equivalence_ignores_decimal_string_formatting(self):
        contract = output_contract(NumericalEquivalence.exact())
        self.assertTrue(values_semantically_equivalent(contract, "1.0", "1.00"))
        self.assertTrue(values_semantically_equivalent(contract, 1, "1.0"))
        self.assertFalse(values_semantically_equivalent(contract, "1.0", "1.01"))

    def test_quantized_equivalence_rounds_to_a_multiple_of_the_quantum(self):
        contract = output_contract(NumericalEquivalence(NumericalEquivalenceKind.QUANTIZED, "1", {"quantum": "0.05"}))
        self.assertTrue(values_semantically_equivalent(contract, "1.024", "0.999"))
        self.assertFalse(values_semantically_equivalent(contract, "1.024", "0.90"))

    def test_quantized_equivalence_within_quantum_is_equivalent(self):
        contract = output_contract(NumericalEquivalence(NumericalEquivalenceKind.QUANTIZED, "1", {"quantum": "0.01"}))
        self.assertTrue(values_semantically_equivalent(contract, "1.001", "1.004"))
        self.assertFalse(values_semantically_equivalent(contract, "1.001", "1.02"))

    def test_quantized_equivalence_requires_quantum_parameter(self):
        contract = output_contract(NumericalEquivalence(NumericalEquivalenceKind.QUANTIZED, "1", {}))
        with self.assertRaises(FeatureArtifactError):
            values_semantically_equivalent(contract, "1.0", "1.0")

    def test_tolerant_equivalence_within_absolute_tolerance_is_equivalent(self):
        contract = output_contract(NumericalEquivalence(NumericalEquivalenceKind.TOLERANT, "1", {"absolute": "0.001"}))
        self.assertTrue(values_semantically_equivalent(contract, "1.0000", "1.0009"))
        self.assertFalse(values_semantically_equivalent(contract, "1.0000", "1.01"))

    def test_tolerant_equivalence_without_any_parameter_fails_closed(self):
        contract = output_contract(NumericalEquivalence(NumericalEquivalenceKind.TOLERANT, "1", {}))
        with self.assertRaises(FeatureArtifactError):
            values_semantically_equivalent(contract, "1.0", "1.0")

    def test_unsupported_equivalence_version_fails_closed(self):
        contract = output_contract(NumericalEquivalence(NumericalEquivalenceKind.EXACT, "2"))
        with self.assertRaises(FeatureArtifactError):
            values_semantically_equivalent(contract, "1.0", "1.0")

    def test_categorical_output_compares_by_exact_equality_only(self):
        contract = OutputContract(OutputValueKind.CATEGORICAL, "scalar", OutputDimension.DIMENSIONLESS)
        self.assertTrue(values_semantically_equivalent(contract, "buy", "buy"))
        self.assertFalse(values_semantically_equivalent(contract, "buy", "sell"))

    def test_recomputation_equivalent_true_despite_distinct_artifact_and_content_identity(self):
        definition = feature_definition()
        first, _ = artifact(definition=definition, code="commit-1", content=content_identity("1"))
        second, _ = artifact(definition=definition, code="commit-2", content=content_identity("2"))
        self.assertNotEqual(first.identity, second.identity)
        self.assertNotEqual(first.content_identity, second.content_identity)
        left = (feature_observation(definition=definition, value="1.0"),)
        right = (feature_observation(definition=definition, value="1.00"),)
        self.assertTrue(recomputation_equivalent(output_contracts={definition.definition_id: definition.output_contract}, left=left, right=right))

    def test_recomputation_equivalent_false_on_differing_values(self):
        definition = feature_definition()
        left = (feature_observation(definition=definition, value="1.0"),)
        right = (feature_observation(definition=definition, value="2.0"),)
        self.assertFalse(recomputation_equivalent(output_contracts={definition.definition_id: definition.output_contract}, left=left, right=right))

    def test_recomputation_equivalent_false_on_observation_identity_mismatch(self):
        definition = feature_definition()
        left = (feature_observation(definition=definition, obs="obs-1"),)
        right = (feature_observation(definition=definition, obs="obs-2"),)
        self.assertFalse(recomputation_equivalent(output_contracts={definition.definition_id: definition.output_contract}, left=left, right=right))

    def test_recomputation_equivalent_false_on_empty_sequences(self):
        self.assertFalse(recomputation_equivalent(output_contracts={}, left=(), right=()))

    def test_recomputation_equivalent_raises_when_output_contract_is_missing(self):
        definition = feature_definition()
        left = (feature_observation(definition=definition),)
        right = (feature_observation(definition=definition),)
        with self.assertRaises(FeatureArtifactError):
            recomputation_equivalent(output_contracts={}, left=left, right=right)

    def test_recomputation_equivalent_rejects_duplicate_observation_identity(self):
        definition = feature_definition()
        duplicate = feature_observation(definition=definition, obs="obs-1")
        with self.assertRaises(FeatureArtifactError):
            recomputation_equivalent(
                output_contracts={definition.definition_id: definition.output_contract},
                left=(duplicate, duplicate),
                right=(duplicate,),
            )


if __name__ == "__main__":
    unittest.main()
