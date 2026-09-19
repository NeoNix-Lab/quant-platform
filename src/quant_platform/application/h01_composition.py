"""Application-owned D06 Footprint -> H01 Feature composition (ADR-0035).

This module owns the concrete cross-owner composition boundary only: it
adapts the accepted D06 FINAL Footprint result into the Feature-owned H01
evaluation seam and, when durability is explicitly requested, binds the
exact immutable D06 result identity into the existing E04 seam.  It
introduces no H01 quantitative semantics of its own.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Sequence

from ..features.artifacts import (
    BoundInputEvidence,
    BoundSourceDataset,
    ConstituentFeatureOutput,
    FeatureArtifact,
    FeatureArtifactContentIdentity,
    FeatureSetDefinitionIdentity,
    SupportShape,
    seal_feature_artifact,
)
from ..features.definitions import FeatureDefinition, FeatureObservation, FeatureObservationIdentity
from ..features.h01_imbalance import (
    H01_IMBALANCE_FEATURE_SET_IDENTITY,
    H01BucketInput,
    H01LevelInput,
    derive_h01_expected_observation_identities,
    diagonal_imbalance_definition,
    evaluate_diagonal_imbalance,
    evaluate_stacked_imbalance,
    stacked_imbalance_definition,
)
from ..representation.footprints import FootprintBucket, HistoricalFootprintResult


BUCKET_OBSERVATION_IDENTITY_DOMAIN = "h01-footprint-bucket-v1"


class H01CompositionError(ValueError):
    """A D06 Footprint -> H01 Feature composition input violates the seam."""


@dataclass(frozen=True, slots=True)
class H01Evaluation:
    """In-memory H01 evaluation result over one D06 FINAL Footprint result.

    Evaluation never requires materialization; use
    ``materialize_h01_feature_artifact`` only when durability is explicitly
    requested.
    """

    diagonal_definition: FeatureDefinition
    stacked_definition: FeatureDefinition
    observations: tuple[FeatureObservation, ...]
    expected_observation_identities: tuple[FeatureObservationIdentity, ...]
    footprint_result_identity: str


def bucket_observation_identity(footprint_result: HistoricalFootprintResult, bucket: FootprintBucket) -> str:
    """Deterministic per-bucket support coordinate derived from the exact D06
    dataset identity, FootprintDefinition identity and bucket boundaries.

    The dataset identity is included so that two distinct datasets (e.g.
    different venue/instrument) sharing the same FootprintDefinition and
    bucket boundaries never collide on the same support coordinate.  This is
    a reversible E06 implementation choice for naming one concrete support
    coordinate; it does not define new Footprint identity/provenance
    semantics and does not replace D06's own result identity.
    """

    payload = {
        "dataset_identity": footprint_result.source_evidence.dataset_identity.stable_dict(),
        "footprint_definition_identity": footprint_result.definition_identity,
        "bucket_start": bucket.bucket_start.isoformat(),
        "bucket_end": bucket.bucket_end.isoformat(),
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return f"{BUCKET_OBSERVATION_IDENTITY_DOMAIN}:sha256:{digest}"


def _bucket_input(footprint_result: HistoricalFootprintResult, bucket: FootprintBucket) -> H01BucketInput:
    return H01BucketInput(
        bucket_observation_identity=bucket_observation_identity(footprint_result, bucket),
        causal_available_at=bucket.causal_floor,
        observed_available_at=footprint_result.source_evidence.observed_available_at,
        levels=tuple(
            H01LevelInput(
                level_index=level.level_index,
                buy_volume=level.buy_volume,
                sell_volume=level.sell_volume,
            )
            for level in bucket.levels
        ),
    )


def evaluate_h01_imbalance(
    footprint_result: HistoricalFootprintResult,
    *,
    diagonal_imbalance_ratio: Any = "3",
    stacked_imbalance_ratio: Any = "3",
    stacked_min_levels: int = 3,
) -> H01Evaluation:
    """Evaluate the two independent H01 FeatureDefinitions over every eligible
    FINAL D06 Footprint bucket in ``footprint_result`` (ADR-0035).

    Every bucket in ``footprint_result.buckets`` is, by D06's own FINAL-only
    contract, an eligible FINAL bucket -- a covered-empty bucket simply has
    zero levels and yields two FINAL empty observations.  No fabricated
    observation is produced for support that was not actually evaluated:
    only buckets D06 actually returned participate.
    """

    if not isinstance(footprint_result, HistoricalFootprintResult):
        raise H01CompositionError("footprint_result must be a HistoricalFootprintResult")
    diagonal_definition = diagonal_imbalance_definition(imbalance_ratio=diagonal_imbalance_ratio)
    stacked_definition = stacked_imbalance_definition(
        imbalance_ratio=stacked_imbalance_ratio,
        stacked_min_levels=stacked_min_levels,
    )
    observations: list[FeatureObservation] = []
    bucket_ids: list[str] = []
    for bucket in footprint_result.buckets:
        bucket_input = _bucket_input(footprint_result, bucket)
        bucket_ids.append(bucket_input.bucket_observation_identity)
        observations.append(evaluate_diagonal_imbalance(diagonal_definition, bucket_input))
        observations.append(evaluate_stacked_imbalance(stacked_definition, bucket_input))
    expected = derive_h01_expected_observation_identities(
        diagonal_definition=diagonal_definition,
        stacked_definition=stacked_definition,
        bucket_observation_identities=bucket_ids,
    )
    return H01Evaluation(
        diagonal_definition=diagonal_definition,
        stacked_definition=stacked_definition,
        observations=tuple(observations),
        expected_observation_identities=expected,
        footprint_result_identity=footprint_result.result_identity,
    )


def materialize_h01_feature_artifact(
    evaluation: H01Evaluation,
    footprint_result: HistoricalFootprintResult,
    *,
    sources: Sequence[BoundSourceDataset],
    implementation_code_identity: str,
    content_identity: FeatureArtifactContentIdentity,
) -> FeatureArtifact:
    """Durable ``h01_imbalance@1`` materialization (ADR-0035 sections 11-12).

    Binds the exact immutable D06 ``result_identity`` as the E04
    ``BoundInputEvidence.provenance_identity`` -- that value already commits
    to every D06 result field (buckets, source evidence, definition,
    implementation identity), so this is the exact D06 binding rather than an
    independently reconstructed trade-lineage model or an opaque hash.

    ``evaluation`` must have been produced by ``evaluate_h01_imbalance`` over
    this exact ``footprint_result`` -- verified via ``result_identity``,
    which is a content commitment over the whole D06 result, not merely
    dataset/support identity.  This prevents sealing observations that were
    actually computed from a different (even same-dataset/same-support) D06
    result under this result's provenance.

    The expected observation universe is derived fresh from
    ``footprint_result.buckets`` and the evaluation's own canonical
    FeatureDefinitions rather than trusted from
    ``evaluation.expected_observation_identities``, so a caller cannot
    substitute a competing universe for the one E04 actually verifies
    against.

    ``sources``/``implementation_code_identity``/``content_identity`` remain
    caller-supplied: real per-partition content/manifest hash evidence and
    durable output-partition evidence are owned by the existing data-plane
    and E04 seams, not reconstructed here from D06's own summary evidence.
    """

    if evaluation.footprint_result_identity != footprint_result.result_identity:
        raise H01CompositionError(
            "evaluation was not produced from the exact supplied footprint_result "
            "(footprint_result_identity mismatch)"
        )
    source_dataset_identity = footprint_result.source_evidence.dataset_identity
    bound_sources = tuple(sources)
    if not any(item.dataset_identity == source_dataset_identity for item in bound_sources):
        raise H01CompositionError(
            "sources must include the exact D06 source_evidence.dataset_identity"
        )
    expected_observation_identities = derive_h01_expected_observation_identities(
        diagonal_definition=evaluation.diagonal_definition,
        stacked_definition=evaluation.stacked_definition,
        bucket_observation_identities=(
            bucket_observation_identity(footprint_result, bucket) for bucket in footprint_result.buckets
        ),
    )
    consumed_support = SupportShape(intervals=footprint_result.coverage.covered_intervals)
    bound_input_evidence = BoundInputEvidence(
        sources=bound_sources,
        consumed_support=consumed_support,
        provenance_identity=footprint_result.result_identity,
    )
    constituent_output_contracts = (
        ConstituentFeatureOutput(
            evaluation.diagonal_definition.definition_id,
            evaluation.diagonal_definition.output_contract,
        ),
        ConstituentFeatureOutput(
            evaluation.stacked_definition.definition_id,
            evaluation.stacked_definition.output_contract,
        ),
    )
    return seal_feature_artifact(
        feature_set_definition_identity=FeatureSetDefinitionIdentity(
            H01_IMBALANCE_FEATURE_SET_IDENTITY.slug,
            H01_IMBALANCE_FEATURE_SET_IDENTITY.version,
        ),
        bound_input_evidence=bound_input_evidence,
        declared_materialized_support=consumed_support,
        implementation_code_identity=implementation_code_identity,
        content_identity=content_identity,
        constituent_output_contracts=constituent_output_contracts,
        observations=evaluation.observations,
        expected_observation_identities=expected_observation_identities,
    )


__all__ = [
    "BUCKET_OBSERVATION_IDENTITY_DOMAIN",
    "H01CompositionError",
    "H01Evaluation",
    "bucket_observation_identity",
    "evaluate_h01_imbalance",
    "materialize_h01_feature_artifact",
]
