#!/usr/bin/env python3
"""Real PostgreSQL proof for A10 candidate staging isolation and atomic cutover.

This script CREDITS the existing S13/A16/S14 PostgreSQL integration proof
(``integration_publication_certification_postgres.py``,
``integration_publication_eligibility_postgres.py``) for sealing,
certification and eligibility semantics, and the existing
``partitions_one_live`` real-database race/lock proof those scripts already
establish. It proves only what is new in A10:

1. two distinct candidate attempts for the same repair intent stage under
   isolated ``staging_partition_key`` values and coexist in the real catalog
   without clobbering each other (frozen contract item 5 / vector 22);
2. a semantically identical retry reuses the same staged candidate row
   (idempotent retry, vector 10);
3. a proven candidate converges through ``RepairCutoverCatalog.cutover`` in
   one atomic transaction: the predecessor is superseded and the candidate
   becomes the sole live revision, with ``partitions_one_live`` intact
   (vector 8);
4. a losing concurrent candidate, evaluated after the winner already
   converged, resolves deterministically to ``ALREADY_SATISFIED`` rather
   than creating a gratuitous new revision (vector 12/13) -- the
   ``STALE_CONFLICT``/no-half-switch/no-reactivation branches are proven
   against a real transactional rollback here and exhaustively against every
   topology shape in ``tests/test_data_repair_cutover_v1.py``.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import psycopg  # noqa: E402
from quant_platform.data import DatasetIdentity, Instant, TradeRecord  # noqa: E402
from quant_platform.data.manifests import (  # noqa: E402
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
)
from quant_platform.data.publication import (  # noqa: E402
    PublicationCertification,
    SealedPartitionEvidence,
    _load_manifest,
)
from quant_platform.data.publication_catalog import CatalogPublicationWriter  # noqa: E402
from quant_platform.data.publication_eligibility import (  # noqa: E402
    PublicationEligibilityBridge,
    PublicationEligibilityEvidence,
)
from quant_platform.data.publication_eligibility_catalog import PublicationEligibilityCatalog  # noqa: E402
from quant_platform.data.quality_lifecycle import QualityLifecycleCatalog  # noqa: E402
from quant_platform.data.repair import (  # noqa: E402
    CandidateAttempt,
    InvalidLiveRevisionTrigger,
    PredecessorReference,
    RepairCutoverCatalog,
    RepairIntent,
    RepairOutcome,
)
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BybitTradeV1CertificationProfile,
    materialize_bybit_trade_v1,
)


class FaultAfterPromoteUpdate:
    def __init__(self, connection):
        self.connection = connection

    def cursor(self):
        return _FaultCursor(self.connection.cursor())

    def commit(self):
        return self.connection.commit()

    def rollback(self):
        return self.connection.rollback()


class _FaultCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def __enter__(self):
        self.cursor.__enter__()
        return self

    def __exit__(self, *args):
        return self.cursor.__exit__(*args)

    def execute(self, statement, params=None):
        self.cursor.execute(statement, params)
        if "SET partition_key = %s, revision = %s" in statement:
            self.cursor.execute("SELECT 1 / 0")

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()


def _seal_and_certify(
    root: Path, writer: CatalogPublicationWriter, profile, identity: DatasetIdentity,
    *, partition_key: str, day: str, trade_id: str, price: str, created_at: str,
):
    """Seal + certify one closed partition under an explicit partition_key.

    Mirrors the credited S13 flow from the existing integration scripts; the
    only A10-specific choice is that ``partition_key`` may be a staging key.
    """

    dataset_path = root / f"dataset-{partition_key.replace('/', '_')}.json"
    partition_path = root / f"partition-{partition_key.replace('/', '_')}.json"
    coverage_path = root / f"coverage-{partition_key.replace('/', '_')}.json"
    dataset = emit_dataset_manifest(
        dataset_path, dataset_identity=identity, created_at=created_at,
        derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
        transform="canonicalize-trades-v1",
    )
    artifact = root / partition_key / "part-001.parquet"
    materialization = materialize_bybit_trade_v1(
        artifact, [TradeRecord("bybit", "BTCUSDT", Instant.parse(f"{day}T00:00:01Z"), price, "0.5000", "buy", None, trade_id, None)],
        dataset_identity=identity,
    )
    partition = emit_partition_manifest(
        partition_path, materialization, dataset_identity=identity, dataset_root=root,
        partition_key=partition_key, revision=1, rel_path=f"{partition_key}/part-001.parquet",
        created_at=created_at, closed_at=created_at, producer="a10-integration-producer",
        code_ref="a10-integration-producer-commit",
    ).document
    emit_coverage_manifest(
        coverage_path, dataset_identity=identity, source_dataset_identity=identity,
        coverage_id=f"coverage-{partition_key.replace('/', '_')}", supersedes=None, created_at=created_at,
        acquisition={
            "basis": "reconciliation", "intent_start": f"{day}T00:00:00Z", "intent_end": f"{day[:8]}{int(day[8:10]) + 1:02d}T00:00:00Z",
            "source_semantics": "bybit-public-trades-sqlite-v1", "mapping": "bybit-sqlite-day-extract-v1",
        },
        assertions=[{
            "assertion_id": f"assertion-{partition_key.replace('/', '_')}", "start": f"{day}T00:00:00Z",
            "end": f"{day[:8]}{int(day[8:10]) + 1:02d}T00:00:00Z", "status": "complete",
            "partitions": [{"partition_key": partition_key, "revision": 1}],
            "evidence": [{"kind": "reconciliation", "detail": "a10 integration candidate"}],
        }],
        producer="a10-integration-source", code_ref="a10-integration-source-commit", partition_manifests=[partition],
    )
    evidence = SealedPartitionEvidence(dataset_path, partition_path, (coverage_path,), artifact, "a10-hot")
    run = PublicationCertification(writer, profile).run(evidence)
    return dataset_path, partition_path, coverage_path, run


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("POSTGRESQL A10 REPAIR CUTOVER GATE NOT EXECUTED LOCALLY")
        return 0

    identity = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
    required = (Instant.parse("2024-01-15T00:00:00Z"), Instant.parse("2024-01-16T00:00:00Z"))
    natural_key = "dt=2024-01-15"

    with tempfile.TemporaryDirectory() as holder:
        root = Path(holder)
        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO catalog.schema_registry (schema_id,name,version,json_sha256,body) "
                    "VALUES ('trade-v1','trade',1,%s,%s::jsonb) ON CONFLICT DO NOTHING",
                    ("0" * 64, "{}"),
                )
                cursor.execute(
                    "INSERT INTO catalog.storage_roots (storage_root_id,abs_path,tier) "
                    "VALUES ('a10-hot',%s,'hot') ON CONFLICT DO NOTHING",
                    (str(root),),
                )
                cursor.execute(
                    "INSERT INTO catalog.datasets (layer,kind,venue,instrument,rel_root,schema_id,manifest_sha256) "
                    "VALUES ('raw','trades','bybit','BTCUSDT','raw/trades/bybit/BTCUSDT/trade-v1','trade-v1',%s) "
                    "ON CONFLICT DO NOTHING",
                    ("1" * 64,),
                )
            connection.commit()

            writer = CatalogPublicationWriter(connection)
            profile = BybitTradeV1CertificationProfile("a10-integration-certifier")

            # --- Establish the invalid live predecessor at the NATURAL key ---
            _, pred_partition_path, _, pred_run = _seal_and_certify(
                root, writer, profile, identity, partition_key=natural_key, day="2024-01-15",
                trade_id="pred-1", price="90.00", created_at="2026-09-17T09:00:00Z",
            )
            pred_sealed = pred_run.sealed_partition
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO catalog.quality_reports (partition_id, check_suite, status, metrics, violations, code_ref) "
                    "VALUES (%s,%s,'fail','{}'::jsonb,%s::jsonb,%s)",
                    (pred_sealed.partition_id, "predecessor-fail-suite", '[{"category":"source","message":"controlled"}]', "predecessor-fail-code"),
                )
            connection.commit()
            QualityLifecycleCatalog(connection).apply_partition_lifecycle(
                dataset=_load_manifest(root / f"dataset-{natural_key.replace('/', '_')}.json", "dataset")[0],
                dataset_sha256=_load_manifest(root / f"dataset-{natural_key.replace('/', '_')}.json", "dataset")[1],
                partition=_load_manifest(pred_partition_path, "partition")[0],
                partition_sha256=_load_manifest(pred_partition_path, "partition")[1],
                coverage_start=pred_sealed.ts_start, coverage_end=pred_sealed.ts_end,
                coverage_ids=[], assertion_ids=[], coverage_sha256=[],
                storage_root_id="a10-hot", expected_profile="predecessor-fail-profile",
                expected_check_suite="predecessor-fail-suite",
            )
            with connection.cursor() as cursor:
                cursor.execute("SELECT state FROM catalog.partitions WHERE partition_id=%s", (pred_sealed.partition_id,))
                assert cursor.fetchone()[0] == "invalid", "setup: predecessor must be invalid before repair"

            predecessor = PredecessorReference(pred_sealed.partition_id, 1, "invalid")
            intent = RepairIntent(
                dataset_identity=identity, partition_key=natural_key,
                required_support=_coverage_interval(required),
                predecessor=predecessor,
                trigger=InvalidLiveRevisionTrigger(
                    predecessor=predecessor, assessment_signature="predecessor-fail-signature",
                    assessment_status="fail",
                ),
            )
            assert intent.outcome == RepairOutcome.REPAIR_REQUIRED

            # --- Stage two DISTINCT candidates: isolated staging (vector 22) ---
            candidate_a = CandidateAttempt(
                intent_identity=intent.intent_identity, dataset_identity=identity,
                natural_partition_key=natural_key, source_semantics_id="bybit-public-trades-sqlite-v1",
                mapping_id="bybit-sqlite-day-extract-v1", dataset_sha256="a" * 64,
                partition_sha256="b" * 64, content_sha256="c" * 64, code_ref="repair-attempt-a",
            )
            candidate_b = CandidateAttempt(
                intent_identity=intent.intent_identity, dataset_identity=identity,
                natural_partition_key=natural_key, source_semantics_id="bybit-public-trades-sqlite-v1",
                mapping_id="bybit-sqlite-day-extract-v1", dataset_sha256="a" * 64,
                partition_sha256="b" * 64, content_sha256="d" * 64, code_ref="repair-attempt-b",
            )
            assert candidate_a.staging_partition_key != candidate_b.staging_partition_key

            dataset_a, partition_a, coverage_a, run_a = _seal_and_certify(
                root, writer, profile, identity, partition_key=candidate_a.staging_partition_key,
                day="2024-01-15", trade_id="cand-a-1", price="100.00", created_at="2026-09-17T10:00:00Z",
            )
            dataset_b, partition_b, coverage_b, run_b = _seal_and_certify(
                root, writer, profile, identity, partition_key=candidate_b.staging_partition_key,
                day="2024-01-15", trade_id="cand-b-1", price="101.00", created_at="2026-09-17T10:05:00Z",
            )
            # Both staged rows coexist right now: neither clobbered the other,
            # and neither touched the still-invalid natural predecessor.
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT partition_key, state, rel_path FROM catalog.partitions WHERE partition_id IN (%s,%s)",
                    (run_a.sealed_partition.partition_id, run_b.sealed_partition.partition_id),
                )
                rows = {row[0]: row for row in cursor.fetchall()}
                assert rows[candidate_a.staging_partition_key][2].startswith(candidate_a.staging_partition_key + "/")
                assert rows[candidate_b.staging_partition_key][2].startswith(candidate_b.staging_partition_key + "/")
                cursor.execute("SELECT state FROM catalog.partitions WHERE partition_id=%s", (pred_sealed.partition_id,))
                assert cursor.fetchone()[0] == "invalid"

            # --- Idempotent retry (vector 10): identical evidence, same row ---
            candidate_a_retry = CandidateAttempt(
                intent_identity=intent.intent_identity, dataset_identity=identity,
                natural_partition_key=natural_key, source_semantics_id="bybit-public-trades-sqlite-v1",
                mapping_id="bybit-sqlite-day-extract-v1", dataset_sha256="a" * 64,
                partition_sha256="b" * 64, content_sha256="c" * 64, code_ref="repair-attempt-a",
            )
            assert candidate_a_retry.candidate_identity == candidate_a.candidate_identity
            retry_run = PublicationCertification(writer, profile).run(
                SealedPartitionEvidence(dataset_a, partition_a, (coverage_a,), root / candidate_a.staging_partition_key / "part-001.parquet", "a10-hot")
            )
            assert retry_run.sealed_partition.partition_id == run_a.sealed_partition.partition_id

            # --- Certify candidate A to 'valid' through the credited S14 bridge ---
            eligibility_a = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection)).publish(
                PublicationEligibilityEvidence(dataset_a, partition_a, (coverage_a,), "a10-hot", profile.profile_id, profile.check_suite)
            )
            assert eligibility_a.state == "valid"
            eligibility_b = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection)).publish(
                PublicationEligibilityEvidence(dataset_b, partition_b, (coverage_b,), "a10-hot", profile.profile_id, profile.check_suite)
            )
            assert eligibility_b.state == "valid"

            # --- Winner converges: one atomic transactional compare-and-cutover ---
            result_a = RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate_a)
            assert result_a.status == RepairOutcome.CONVERGED
            assert result_a.live_partition_id == run_a.sealed_partition.partition_id
            assert result_a.live_revision == 2
            with connection.cursor() as cursor:
                cursor.execute("SELECT state FROM catalog.partitions WHERE partition_id=%s", (pred_sealed.partition_id,))
                assert cursor.fetchone()[0] == "superseded"
                cursor.execute(
                    "SELECT count(*) FROM catalog.partitions WHERE dataset_id=(SELECT dataset_id FROM catalog.partitions WHERE partition_id=%s) AND partition_key=%s AND state <> 'superseded'",
                    (run_a.sealed_partition.partition_id, natural_key),
                )
                assert cursor.fetchone()[0] == 1
                cursor.execute("SELECT indexdef FROM pg_indexes WHERE schemaname='catalog' AND indexname='partitions_one_live'")
                assert "UNIQUE INDEX partitions_one_live" in cursor.fetchone()[0]

            # --- Loser re-evaluates current authority: ALREADY_SATISFIED, no
            # gratuitous new revision (vector 12/13); B's own staged row is
            # untouched -- it remains isolated, non-authoritative evidence. ---
            result_b = RepairCutoverCatalog(connection).cutover(intent=intent, candidate=candidate_b)
            assert result_b.status == RepairOutcome.ALREADY_SATISFIED
            assert result_b.candidate_identity is None
            with connection.cursor() as cursor:
                cursor.execute("SELECT partition_key, state FROM catalog.partitions WHERE partition_id=%s", (run_b.sealed_partition.partition_id,))
                row = cursor.fetchone()
                assert row[0] == candidate_b.staging_partition_key and row[1] == "valid"

            # --- Transactional failure leaves predecessor exactly authoritative
            # (vector 9), proven against real PostgreSQL rollback this time. ---
            _, pred2_partition_path, _, pred2_run = _seal_and_certify(
                root, writer, profile, identity, partition_key="dt=2024-01-16", day="2024-01-16",
                trade_id="pred-2", price="90.00", created_at="2026-09-17T11:00:00Z",
            )
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO catalog.quality_reports (partition_id, check_suite, status, metrics, violations, code_ref) "
                    "VALUES (%s,%s,'fail','{}'::jsonb,%s::jsonb,%s)",
                    (pred2_run.sealed_partition.partition_id, "predecessor2-fail-suite", '[{"category":"source","message":"controlled"}]', "predecessor2-fail-code"),
                )
            connection.commit()
            QualityLifecycleCatalog(connection).apply_partition_lifecycle(
                dataset=_load_manifest(root / "dataset-dt=2024-01-16.json", "dataset")[0],
                dataset_sha256=_load_manifest(root / "dataset-dt=2024-01-16.json", "dataset")[1],
                partition=_load_manifest(pred2_partition_path, "partition")[0],
                partition_sha256=_load_manifest(pred2_partition_path, "partition")[1],
                coverage_start=pred2_run.sealed_partition.ts_start, coverage_end=pred2_run.sealed_partition.ts_end,
                coverage_ids=[], assertion_ids=[], coverage_sha256=[],
                storage_root_id="a10-hot", expected_profile="predecessor2-fail-profile",
                expected_check_suite="predecessor2-fail-suite",
            )
            predecessor2 = PredecessorReference(pred2_run.sealed_partition.partition_id, 1, "invalid")
            intent2 = RepairIntent(
                dataset_identity=identity, partition_key="dt=2024-01-16",
                required_support=_coverage_interval((Instant.parse("2024-01-16T00:00:00Z"), Instant.parse("2024-01-17T00:00:00Z"))),
                predecessor=predecessor2,
                trigger=InvalidLiveRevisionTrigger(predecessor2, "predecessor2-fail-signature", "fail"),
            )
            candidate_c = CandidateAttempt(
                intent_identity=intent2.intent_identity, dataset_identity=identity,
                natural_partition_key="dt=2024-01-16", source_semantics_id="bybit-public-trades-sqlite-v1",
                mapping_id="bybit-sqlite-day-extract-v1", dataset_sha256="a" * 64,
                partition_sha256="b" * 64, content_sha256="e" * 64, code_ref="repair-attempt-c",
            )
            dataset_c, partition_c, coverage_c, run_c = _seal_and_certify(
                root, writer, profile, identity, partition_key=candidate_c.staging_partition_key,
                day="2024-01-16", trade_id="cand-c-1", price="100.00", created_at="2026-09-17T11:10:00Z",
            )
            PublicationEligibilityBridge(PublicationEligibilityCatalog(connection)).publish(
                PublicationEligibilityEvidence(dataset_c, partition_c, (coverage_c,), "a10-hot", profile.profile_id, profile.check_suite)
            )
            faulty = RepairCutoverCatalog(FaultAfterPromoteUpdate(connection))
            try:
                faulty.cutover(intent=intent2, candidate=candidate_c)
            except Exception:
                pass
            else:
                raise AssertionError("injected post-promote fault did not propagate")
            with psycopg.connect(dsn) as fresh:
                with fresh.cursor() as cursor:
                    cursor.execute("SELECT partition_key, state FROM catalog.partitions WHERE partition_id=%s", (predecessor2.partition_id,))
                    row = cursor.fetchone()
                    assert row == ("dt=2024-01-16", "invalid"), "predecessor must remain exactly authoritative after a failed cutover"
                    cursor.execute("SELECT partition_key, state FROM catalog.partitions WHERE partition_id=%s", (run_c.sealed_partition.partition_id,))
                    row = cursor.fetchone()
                    assert row == (candidate_c.staging_partition_key, "valid"), "no half-switch: candidate stays isolated, not authoritative"

    print("A10 REPAIR CUTOVER POSTGRESQL PROOF OK")
    return 0


def _coverage_interval(bounds):
    from quant_platform.data import CoverageInterval

    return CoverageInterval(bounds[0], bounds[1])


if __name__ == "__main__":
    raise SystemExit(main())
