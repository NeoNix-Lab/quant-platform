#!/usr/bin/env python3
"""Test-only fixture support for adversarial publication acceptance.

This module is orchestration and inspection only.  Materialization, manifest
and coverage emission, S13 certification, S14 eligibility, catalog access and
DataGateway reads remain owned by the existing production seams; nothing here
re-implements a publication path, a certification rule or a coverage
semantics.  It exists so several adversarial scenarios can share one fixture
topology and one *semantic* catalog snapshot instead of copying the shape of
the existing PostgreSQL integration scripts scenario by scenario.

Not a production harness: it must never be imported from ``src/`` or from a
production entry point in ``tools/``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from bootstrap_schema_registry import (  # noqa: E402
    bootstrap_schema_registry,
    read_schema_registration,
)

from quant_platform.data import (  # noqa: E402
    Catalog,
    CatalogPublicationWriter,
    DataGateway,
    DataRequest,
    DatasetIdentity,
    Instant,
    LifecyclePolicy,
    PublicationCertification,
    PublicationEligibilityBridge,
    PublicationEligibilityCatalog,
    PublicationEligibilityEvidence,
    SealedPartitionEvidence,
    TradeRecord,
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
    materialize_trade_v1,
)
from quant_platform.ordering import (  # noqa: E402
    TRADES_CANONICAL_TOTAL_ORDER_V1,
    OrderingProvider,
)
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ORDERING_PROVIDER,
    BYBIT_TRADE_V1_ORDERING_POLICY,
    BybitTradeV1CertificationProfile,
    materialize_bybit_trade_v1,
)


CANONICAL_IDENTITY = DatasetIdentity(
    "canonical", "trades", "bybit", "BTCUSDT", "trade-v1"
)

# dataset-manifest-v2 / ADR-0025: the first vertical is source-acquired, so the
# expected catalog lineage for this dataset is exactly zero rows.  That makes
# "no unexpected lineage" an exact assertion rather than a comparison against a
# synthetic parent.
DATASET_SCHEMA_VERSION = "dataset-manifest-v2"
DATASET_ORIGIN = "source_acquired"
DATASET_TRANSFORM = "canonicalize-trades-v1"

SOURCE_SEMANTICS = "bybit-public-trades-sqlite-v1"
SOURCE_MAPPING = "bybit-sqlite-day-extract-v1"

STORAGE_ROOT_ID = "adversarial-refusal-root"
PRODUCER_ID = "adversarial-refusal-producer"
PRODUCER_CODE_REF = "adversarial-refusal-producer-commit"
SOURCE_ID = "adversarial-refusal-source"
SOURCE_CODE_REF = "adversarial-refusal-source-commit"
CERTIFIER_CODE_REF = "adversarial-refusal-certifier"

CREATED_AT = "2026-09-01T10:00:00Z"
CLOSED_AT = "2026-09-01T10:00:01Z"
COVERAGE_CREATED_AT = "2026-09-01T10:00:02Z"

# catalog.storage_roots.abs_path is constrained by db/init/001_catalog.sql to a
# POSIX absolute path.  Refusal-only developer runs on Windows may use a
# non-openable placeholder because they fail before path resolution.  Positive
# DataGateway acceptance runs on POSIX and therefore registers the real root.
_ABS_PATH = re.compile(r"^(/[A-Za-z0-9._-]+)+$")
_PLACEHOLDER_ABS_PATH = "/tmp/adversarial-refusal-fixture-root"

# Coverage/assertion identities are canonical identifiers in the frozen
# coverage-manifest-v1 grammar; a partition_key carries '=' and cannot be
# spliced into one verbatim.
_NON_IDENTIFIER = re.compile(r"[^a-z0-9]+")


def _slug(value: str) -> str:
    return _NON_IDENTIFIER.sub("-", value.lower()).strip("-")


def trade(
    timestamp: str,
    trade_id: str | None,
    *,
    identity: DatasetIdentity = CANONICAL_IDENTITY,
    aggressor_side: str = "buy",
    price: str = "100.00",
    size: str = "0.5000",
) -> TradeRecord:
    """One canonical ``trade-v1`` record; ``trade_id=None`` stays schema-valid."""

    return TradeRecord(
        identity.venue,
        identity.instrument,
        Instant.parse(timestamp),
        price,
        size,
        aggressor_side,
        None,
        trade_id,
        None,
    )


def nullable_trade_id_ordering_provider(
    identity: DatasetIdentity = CANONICAL_IDENTITY,
) -> OrderingProvider:
    """A provider that tolerates a null ``trade_id``.

    The Bybit first-vertical profile forbids a null ``trade_id`` (§10 EP1) and
    ``materialize_bybit_trade_v1`` therefore refuses to write such a partition
    at all.  The generic ``trade-v1`` schema does permit it (§9.1), and EP4
    requires a null-``trade_id`` artifact to remain generically representable
    while being certification-ineligible.  This provider is what lets the
    fixture materialize exactly that artifact through the real generic
    materializer, instead of hand-writing Parquet outside the production
    writer.  It is fixture construction, never an alternate producer.
    """

    return OrderingProvider(
        "adversarial-nullable-trade-id-ordering-v1",
        frozenset({TRADES_CANONICAL_TOTAL_ORDER_V1}),
        lambda record: (record.exchange_ts, record.trade_id or ""),
        lambda candidate: candidate == identity,
    )


@dataclass(frozen=True, slots=True)
class PublicationFixture:
    """Durable evidence for one natural partition, ready for S13 and S14."""

    identity: DatasetIdentity
    dataset_manifest_path: Path
    partition_manifest_path: Path
    coverage_manifest_paths: tuple[Path, ...]
    artifact_path: Path
    partition_key: str
    revision: int
    interval_start: str
    interval_end: str

    @property
    def coverage_documents(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            json.loads(path.read_text(encoding="utf-8"))
            for path in self.coverage_manifest_paths
        )

    @property
    def partition_document(self) -> dict[str, Any]:
        return json.loads(self.partition_manifest_path.read_text(encoding="utf-8"))

    def sealed_evidence(self) -> SealedPartitionEvidence:
        return SealedPartitionEvidence(
            self.dataset_manifest_path,
            self.partition_manifest_path,
            self.coverage_manifest_paths,
            self.artifact_path,
            STORAGE_ROOT_ID,
        )

    def eligibility_evidence(
        self, profile: BybitTradeV1CertificationProfile
    ) -> PublicationEligibilityEvidence:
        return PublicationEligibilityEvidence(
            self.dataset_manifest_path,
            self.partition_manifest_path,
            self.coverage_manifest_paths,
            STORAGE_ROOT_ID,
            profile.profile_id,
            profile.check_suite,
        )


def emit_dataset(root: Path, identity: DatasetIdentity = CANONICAL_IDENTITY) -> Path:
    """Emit the single dataset manifest every fixture partition must share.

    ``CatalogPublicationWriter._resolve_dataset`` refuses a second dataset
    manifest for one natural identity whose ``manifest_sha256`` differs, so all
    scenarios against one dataset reuse this exact document.
    """

    path = root / "dataset.json"
    if not path.exists():
        emit_dataset_manifest(
            path,
            dataset_identity=identity,
            created_at=CREATED_AT,
            transform=DATASET_TRANSFORM,
            schema_version=DATASET_SCHEMA_VERSION,
            origin=DATASET_ORIGIN,
        )
    return path


def build_publication_fixture(
    root: Path,
    *,
    partition_key: str,
    records: Sequence[TradeRecord],
    coverage_assertions: Sequence[tuple[str, str]],
    intent_start: str,
    intent_end: str,
    identity: DatasetIdentity = CANONICAL_IDENTITY,
    revision: int = 1,
    ordering_provider: OrderingProvider | None = None,
    coverage_id: str | None = None,
    supersedes: str | None = None,
    source_extract_detail: str = "deterministic source extract",
) -> PublicationFixture:
    """Materialize one partition and emit its durable dataset/partition/coverage evidence.

    ``ordering_provider`` selects the materializer: ``None`` uses the real
    Bybit first-vertical materializer (with its eligibility pre-check), and an
    explicit provider uses the generic source-neutral one.  ``coverage_assertions``
    is a sequence of half-open ``(start, end)`` pairs, all ``complete`` and all
    attributed to this one natural partition, so a caller can express a single
    contiguous interval or a deliberately non-contiguous one.
    """

    dataset_manifest_path = emit_dataset(root, identity)
    dataset_root = root / json.loads(
        dataset_manifest_path.read_text(encoding="utf-8")
    )["rel_root"]
    rel_path = f"{partition_key}/part-000.parquet"
    artifact_path = dataset_root / partition_key / "part-000.parquet"

    if ordering_provider is None:
        materialization = materialize_bybit_trade_v1(
            artifact_path, list(records), dataset_identity=identity
        )
    else:
        materialization = materialize_trade_v1(
            artifact_path,
            list(records),
            dataset_identity=identity,
            ordering_provider=ordering_provider,
        )

    partition_manifest_path = root / f"partition-{partition_key}.json"
    partition_emission = emit_partition_manifest(
        partition_manifest_path,
        materialization,
        dataset_identity=identity,
        dataset_root=dataset_root,
        partition_key=partition_key,
        revision=revision,
        rel_path=rel_path,
        created_at=CREATED_AT,
        closed_at=CLOSED_AT,
        producer=PRODUCER_ID,
        code_ref=PRODUCER_CODE_REF,
    )

    resolved_coverage_id = coverage_id or f"adversarial-coverage-{_slug(partition_key)}"
    coverage_manifest_path = root / f"coverage-{_slug(resolved_coverage_id)}.json"
    emit_coverage_manifest(
        coverage_manifest_path,
        dataset_identity=identity,
        source_dataset_identity=identity,
        coverage_id=resolved_coverage_id,
        supersedes=supersedes,
        created_at=COVERAGE_CREATED_AT,
        acquisition={
            "basis": "source_extract",
            "intent_start": intent_start,
            "intent_end": intent_end,
            "source_semantics": SOURCE_SEMANTICS,
            "mapping": SOURCE_MAPPING,
        },
        assertions=[
            {
                "assertion_id": f"{resolved_coverage_id}-assertion-{index}",
                "start": start,
                "end": end,
                "status": "complete",
                "partitions": [
                    {"partition_key": partition_key, "revision": revision}
                ],
                "evidence": [
                    {
                        "kind": "deterministic_source_extract",
                        "detail": source_extract_detail,
                    }
                ],
            }
            for index, (start, end) in enumerate(coverage_assertions, start=1)
        ],
        producer=SOURCE_ID,
        code_ref=SOURCE_CODE_REF,
        partition_manifests=[partition_emission.document],
    )

    return PublicationFixture(
        identity=identity,
        dataset_manifest_path=dataset_manifest_path,
        partition_manifest_path=partition_manifest_path,
        coverage_manifest_paths=(coverage_manifest_path,),
        artifact_path=artifact_path,
        partition_key=partition_key,
        revision=revision,
        interval_start=intent_start,
        interval_end=intent_end,
    )


def restate_coverage(
    fixture: PublicationFixture,
    *,
    coverage_id: str,
    supersedes: str,
    coverage_assertions: Sequence[tuple[str, str]],
    intent_start: str,
    intent_end: str,
    created_at: str = "2026-09-01T10:00:03Z",
    source_extract_detail: str = "deterministic source restatement",
) -> PublicationFixture:
    """Emit a successor coverage document over an existing durable partition.

    The returned evidence tuple places the successor first because the frozen
    S13 singular evidence identity denotes the current document.  The complete
    tuple still carries every predecessor required by the production coverage
    fold; no supersession decision is made by this helper.
    """

    coverage_manifest_path = fixture.dataset_manifest_path.parent / (
        f"coverage-{_slug(coverage_id)}.json"
    )
    emit_coverage_manifest(
        coverage_manifest_path,
        dataset_identity=fixture.identity,
        source_dataset_identity=fixture.identity,
        coverage_id=coverage_id,
        supersedes=supersedes,
        created_at=created_at,
        acquisition={
            "basis": "source_extract",
            "intent_start": intent_start,
            "intent_end": intent_end,
            "source_semantics": SOURCE_SEMANTICS,
            "mapping": SOURCE_MAPPING,
        },
        assertions=[
            {
                "assertion_id": f"{coverage_id}-assertion-{index}",
                "start": start,
                "end": end,
                "status": "complete",
                "partitions": [
                    {
                        "partition_key": fixture.partition_key,
                        "revision": fixture.revision,
                    }
                ],
                "evidence": [
                    {
                        "kind": "deterministic_source_extract",
                        "detail": source_extract_detail,
                    }
                ],
            }
            for index, (start, end) in enumerate(coverage_assertions, start=1)
        ],
        producer=SOURCE_ID,
        code_ref=SOURCE_CODE_REF,
        partition_manifests=[fixture.partition_document],
    )
    return replace(
        fixture,
        coverage_manifest_paths=(
            coverage_manifest_path,
            *fixture.coverage_manifest_paths,
        ),
        interval_start=coverage_assertions[0][0],
        interval_end=coverage_assertions[-1][1],
    )


def register_catalog_prerequisites(connection: Any, root: Path) -> None:
    """Register the schema identity and storage root the fixtures reference."""

    abs_path = root.as_posix()
    if not _ABS_PATH.fullmatch(abs_path):
        abs_path = _PLACEHOLDER_ABS_PATH
    # The authoritative repository schema bytes, through the real bootstrap
    # owner, rather than a placeholder registry row.
    bootstrap_schema_registry(connection, read_schema_registration())
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO catalog.storage_roots (storage_root_id, abs_path, tier)
            VALUES (%s, %s, 'hot')
            ON CONFLICT (storage_root_id) DO NOTHING
            """,
            (STORAGE_ROOT_ID, abs_path),
        )
    connection.commit()


def certification_runtime(
    connection: Any, *, code_ref: str = CERTIFIER_CODE_REF
) -> tuple[PublicationCertification, BybitTradeV1CertificationProfile]:
    """Compose the real S13 runtime over a real catalog write boundary."""

    profile = BybitTradeV1CertificationProfile(code_ref)
    return PublicationCertification(CatalogPublicationWriter(connection), profile), profile


def eligibility_bridge(connection: Any) -> PublicationEligibilityBridge:
    """Compose the real S14 bridge over a real catalog transaction boundary."""

    return PublicationEligibilityBridge(PublicationEligibilityCatalog(connection))


def gateway(connection: Any) -> DataGateway:
    """The real DataGateway over the real PostgreSQL catalog resolver."""

    return DataGateway(
        Catalog(connection=connection),
        ordering_providers=(BYBIT_ORDERING_PROVIDER,),
    )


def request(
    fixture: PublicationFixture,
    *,
    start: str | None = None,
    end: str | None = None,
    lifecycle_policy: LifecyclePolicy = LifecyclePolicy.VALID_ONLY,
) -> DataRequest:
    return DataRequest(
        fixture.identity,
        start or fixture.interval_start,
        end or fixture.interval_end,
        lifecycle_policy=lifecycle_policy,
        ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
    )


def scoped_catalog_state(
    connection: Any,
    identity: DatasetIdentity,
    partition_keys: Iterable[str],
) -> dict[str, Any]:
    """Snapshot the semantic catalog state this slice is allowed to compare.

    Catalog-generated UUIDs and wall-clock columns are deliberately excluded:
    ``partition_id``/``dataset_id``/``report_id`` are runtime locators and
    ``ran_at``/``recorded_at`` are process metadata, so comparing them would
    make an unchanged catalog look changed.  Everything that carries publication
    meaning -- natural identity, lifecycle state, declared coverage, durable
    hashes, provenance, lineage edges and certification outcome -- is included.
    """

    keys = sorted(set(partition_keys))
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT layer, kind, venue, instrument, rel_root, schema_id, manifest_sha256
              FROM catalog.datasets
             WHERE layer = %s AND kind = %s AND venue = %s
               AND instrument = %s AND schema_id = %s
               AND feature_set_def_id IS NULL
             ORDER BY rel_root
            """,
            (
                identity.layer,
                identity.dataset_kind,
                identity.venue,
                identity.instrument,
                identity.record_schema_id,
            ),
        )
        datasets = [tuple(_scalar(value) for value in row) for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT p.partition_key, p.revision, p.state, p.ts_start, p.ts_end,
                   p.row_count, p.byte_size, p.content_sha256, p.manifest_sha256,
                   p.storage_root_id, p.rel_path, p.producer, p.code_ref,
                   p.tiered_at
              FROM catalog.partitions AS p
              JOIN catalog.datasets AS d ON d.dataset_id = p.dataset_id
             WHERE d.layer = %s AND d.kind = %s AND d.venue = %s
               AND d.instrument = %s AND d.schema_id = %s
               AND p.partition_key = ANY(%s)
             ORDER BY p.partition_key, p.revision
            """,
            (
                identity.layer,
                identity.dataset_kind,
                identity.venue,
                identity.instrument,
                identity.record_schema_id,
                keys,
            ),
        )
        partitions = [tuple(_scalar(value) for value in row) for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT parent.layer, parent.kind, parent.venue, parent.instrument,
                   parent.schema_id, l.transform
              FROM catalog.dataset_lineage AS l
              JOIN catalog.datasets AS child ON child.dataset_id = l.child_id
              JOIN catalog.datasets AS parent ON parent.dataset_id = l.parent_id
             WHERE child.layer = %s AND child.kind = %s AND child.venue = %s
               AND child.instrument = %s AND child.schema_id = %s
             ORDER BY 1, 2, 3, 4, 5, 6
            """,
            (
                identity.layer,
                identity.dataset_kind,
                identity.venue,
                identity.instrument,
                identity.record_schema_id,
            ),
        )
        lineage = [tuple(_scalar(value) for value in row) for row in cursor.fetchall()]

        cursor.execute(
            """
            SELECT p.partition_key, p.revision, q.check_suite, q.status, q.code_ref,
                   q.metrics #>> '{certification_profile}',
                   q.metrics #>> '{coverage_manifest_id}',
                   jsonb_array_length(coalesce(q.violations, '[]'::jsonb))
              FROM catalog.quality_reports AS q
              JOIN catalog.partitions AS p ON p.partition_id = q.partition_id
              JOIN catalog.datasets AS d ON d.dataset_id = p.dataset_id
             WHERE d.layer = %s AND d.kind = %s AND d.venue = %s
               AND d.instrument = %s AND d.schema_id = %s
               AND p.partition_key = ANY(%s)
             ORDER BY 1, 2, 3, 4, 5, 6, 7, 8
            """,
            (
                identity.layer,
                identity.dataset_kind,
                identity.venue,
                identity.instrument,
                identity.record_schema_id,
                keys,
            ),
        )
        reports = [tuple(_scalar(value) for value in row) for row in cursor.fetchall()]

    return {
        "datasets": datasets,
        "partitions": partitions,
        "dataset_lineage": lineage,
        "quality_reports": reports,
    }


def _scalar(value: Any) -> Any:
    """Normalize a catalog value so two snapshots compare on meaning."""

    if value is None or isinstance(value, (bool, int, str)):
        return value.strip() if isinstance(value, str) else value
    return str(value)


def diff_catalog_state(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> tuple[str, ...]:
    """Name every scoped catalog table whose semantic state changed."""

    return tuple(
        name for name in sorted(before) if before[name] != after.get(name)
    )


def expect_refusal(
    action: Callable[[], Any],
    expected: type[BaseException] | tuple[type[BaseException], ...],
    *,
    what: str,
) -> BaseException:
    """Run ``action`` and require the exact frozen refusal type."""

    try:
        action()
    except expected as exc:
        return exc
    except Exception as exc:  # noqa: BLE001 - the exact type is the assertion
        raise AssertionError(
            f"{what}: expected {expected} refusal, observed {type(exc).__name__}: {exc}"
        ) from exc
    raise AssertionError(f"{what}: expected {expected} refusal, observed success")


__all__ = [
    "CANONICAL_IDENTITY",
    "CERTIFIER_CODE_REF",
    "PublicationFixture",
    "STORAGE_ROOT_ID",
    "build_publication_fixture",
    "certification_runtime",
    "diff_catalog_state",
    "eligibility_bridge",
    "emit_dataset",
    "expect_refusal",
    "gateway",
    "nullable_trade_id_ordering_provider",
    "register_catalog_prerequisites",
    "request",
    "restate_coverage",
    "scoped_catalog_state",
    "trade",
]
