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
3. TWO REAL, CONCURRENT transactions -- separate ``psycopg`` connections,
   separate OS threads, synchronized to enter ``cutover`` together -- race
   for the SAME repair intent. Real PostgreSQL row-level locking on
   ``catalog.partitions`` (the topology ``FOR UPDATE`` acquired inside
   ``cutover``) serializes them: exactly one becomes the sole live revision
   (``CONVERGED``), and the other -- having lost the race, not merely having
   an incomplete view -- resolves deterministically to ``STALE_CONFLICT``,
   never ``ALREADY_SATISFIED`` (vector 8/12; the specific defect an earlier
   review found in the sequential, single-connection version of this proof);
4. an idempotent retry of the ACTUAL race winner, evaluated after the race,
   resolves to ``ALREADY_SATISFIED`` -- durable convergence provenance, not
   mere coverage sufficiency, is what makes this determination (vector 13);
5. a transactional failure mid-cutover leaves the predecessor exactly
   authoritative and the candidate still isolated -- proven against real
   PostgreSQL rollback (vector 9).
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import psycopg  # noqa: E402
from quant_platform.data import CoverageInterval, DatasetIdentity, Instant, TradeRecord  # noqa: E402
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
    CandidateProof,
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
        if "SET partition_key = %s, revision = %s, manifest_sha256 = %s" in statement:
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
    partition_emission = emit_partition_manifest(
        partition_path, materialization, dataset_identity=identity, dataset_root=root,
        partition_key=partition_key, revision=1, rel_path=f"{partition_key}/part-001.parquet",
        created_at=created_at, closed_at=created_at, producer="a10-integration-producer",
        code_ref="a10-integration-producer-commit",
    )
    partition = partition_emission.document
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


def _content_sha256(root: Path, identity: DatasetIdentity, *, day: str, trade_id: str, price: str) -> str:
    """The deterministic physical artifact hash for one candidate's records,
    known before any staging path is chosen (materialization content does
    not depend on the path it happens to be written to).
    """

    scratch = root / f"_scratch_{trade_id}" / "part-001.parquet"
    materialization = materialize_bybit_trade_v1(
        scratch, [TradeRecord("bybit", "BTCUSDT", Instant.parse(f"{day}T00:00:01Z"), price, "0.5000", "buy", None, trade_id, None)],
        dataset_identity=identity,
    )
    return materialization.sha256


def _build_candidate_and_proof(
    root: Path, writer: CatalogPublicationWriter, profile, connection, identity: DatasetIdentity,
    *, intent: RepairIntent, day: str, trade_id: str, price: str, created_at: str, code_ref: str,
) -> tuple[CandidateAttempt, "CandidateProof", object]:
    content_sha256 = _content_sha256(root, identity, day=day, trade_id=trade_id, price=price)
    # dataset_sha256 participates in staging_partition_key (isolation must
    # bind lineage, not only physical bytes), so it must be known BEFORE
    # the staging key -- and thus before _seal_and_certify -- can be
    # computed. emit_dataset_manifest is a pure, deterministic function of
    # these exact arguments, so probing it here and letting
    # _seal_and_certify emit the identical document again internally
    # produces the identical hash, without needing to thread a precomputed
    # document through the credited S13 flow.
    dataset_probe = emit_dataset_manifest(
        root / f"_probe-dataset-{trade_id}.json", dataset_identity=identity, created_at=created_at,
        derived_from=[DatasetIdentity("raw", "trades", "bybit", "BTCUSDT", "trade-v1")],
        transform="canonicalize-trades-v1",
    )
    dataset_sha256 = dataset_probe.manifest_sha256
    provisional = CandidateAttempt(
        intent_identity=intent.intent_identity, dataset_identity=identity,
        natural_partition_key=intent.partition_key, source_semantics_id="bybit-public-trades-sqlite-v1",
        mapping_id="bybit-sqlite-day-extract-v1", dataset_sha256=dataset_sha256, partition_sha256="b" * 64,
        content_sha256=content_sha256, code_ref=code_ref,
    )
    staging_key = provisional.staging_partition_key
    dataset_path, partition_path, coverage_path, run = _seal_and_certify(
        root, writer, profile, identity, partition_key=staging_key,
        day=day, trade_id=trade_id, price=price, created_at=created_at,
    )
    dataset_document, reloaded_dataset_sha256 = _load_manifest(dataset_path, "dataset")
    assert reloaded_dataset_sha256 == dataset_sha256, "dataset manifest hash must be reproducible from identical inputs"
    partition_document, partition_sha256 = _load_manifest(partition_path, "partition")
    coverage_document, _ = _load_manifest(coverage_path, "coverage")
    candidate = CandidateAttempt(
        intent_identity=intent.intent_identity, dataset_identity=identity,
        natural_partition_key=intent.partition_key, source_semantics_id="bybit-public-trades-sqlite-v1",
        mapping_id="bybit-sqlite-day-extract-v1", dataset_sha256=dataset_sha256, partition_sha256=partition_sha256,
        content_sha256=content_sha256, code_ref=code_ref,
    )
    assert candidate.staging_partition_key == staging_key

    eligibility = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection)).publish(
        PublicationEligibilityEvidence(dataset_path, partition_path, (coverage_path,), "a10-hot", profile.profile_id, profile.check_suite)
    )
    assert eligibility.state == "valid"

    # The REAL canonical_content_hash_v1 S13 certification actually computed
    # and durably recorded in catalog.quality_reports.metrics -- never an
    # arbitrary caller-chosen value (finding 1).
    canonical_content_hash_v1 = run.certification.metrics["canonical_content_hash_v1"]
    assert canonical_content_hash_v1 is not None

    proof = CandidateProof(
        dataset_document=dataset_document, dataset_sha256=dataset_sha256,
        partition_document=partition_document, partition_sha256=partition_sha256,
        coverage_documents=(coverage_document,), canonical_content_hash_v1=canonical_content_hash_v1,
        assessment_signature=eligibility.certification_signature,
        assessment_status=eligibility.certification_status,
        eligibility_state=eligibility.state, repair_code_ref=code_ref,
        expected_profile=profile.profile_id, expected_check_suite=profile.check_suite,
    )
    return candidate, proof, run


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("POSTGRESQL A10 REPAIR CUTOVER GATE NOT EXECUTED LOCALLY")
        return 0

    identity = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")
    required = CoverageInterval(Instant.parse("2024-01-15T00:00:00Z"), Instant.parse("2024-01-16T00:00:00Z"))
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
                required_support=required,
                predecessor=predecessor,
                trigger=InvalidLiveRevisionTrigger(
                    predecessor=predecessor, assessment_signature="predecessor-fail-signature",
                    assessment_status="fail",
                ),
            )
            assert intent.outcome == RepairOutcome.REPAIR_REQUIRED

            # --- Stage two DISTINCT candidates for the SAME intent: isolated
            # staging (vector 22), neither touches the still-invalid predecessor.
            candidate_a, proof_a, run_a = _build_candidate_and_proof(
                root, writer, profile, connection, identity, intent=intent,
                day="2024-01-15", trade_id="cand-a-1", price="100.00",
                created_at="2026-09-17T10:00:00Z", code_ref="repair-attempt-a",
            )
            candidate_b, proof_b, run_b = _build_candidate_and_proof(
                root, writer, profile, connection, identity, intent=intent,
                day="2024-01-15", trade_id="cand-b-1", price="101.00",
                created_at="2026-09-17T10:05:00Z", code_ref="repair-attempt-b",
            )
            assert candidate_a.staging_partition_key != candidate_b.staging_partition_key
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
            content_sha256_a_retry = _content_sha256(root, identity, day="2024-01-15", trade_id="cand-a-1", price="100.00")
            assert content_sha256_a_retry == candidate_a.content_sha256
            candidate_a_retry = CandidateAttempt(
                intent_identity=intent.intent_identity, dataset_identity=identity,
                natural_partition_key=natural_key, source_semantics_id="bybit-public-trades-sqlite-v1",
                mapping_id="bybit-sqlite-day-extract-v1", dataset_sha256=candidate_a.dataset_sha256,
                partition_sha256=candidate_a.partition_sha256, content_sha256=content_sha256_a_retry,
                code_ref="repair-attempt-a",
            )
            assert candidate_a_retry.candidate_identity == candidate_a.candidate_identity
            retry_run = PublicationCertification(writer, profile).run(
                SealedPartitionEvidence(
                    root / f"dataset-{candidate_a.staging_partition_key.replace('/', '_')}.json",
                    root / f"partition-{candidate_a.staging_partition_key.replace('/', '_')}.json",
                    (root / f"coverage-{candidate_a.staging_partition_key.replace('/', '_')}.json",),
                    root / candidate_a.staging_partition_key / "part-001.parquet", "a10-hot",
                )
            )
            assert retry_run.sealed_partition.partition_id == run_a.sealed_partition.partition_id

            # --- Genuine concurrent race (finding 6/7): two real transactions
            # on two separate connections, synchronized to enter the topology
            # lock together. Real PostgreSQL row locking -- not application
            # logic -- decides who commits first; the loser must see the
            # winner's already-promoted topology and resolve to
            # STALE_CONFLICT, never infer ALREADY_SATISFIED merely because
            # the winner's coverage happens to be sufficient too. ---
            promoted_path_a = root / "promoted-a.json"
            promoted_path_b = root / "promoted-b.json"
            barrier = threading.Barrier(2)
            outcomes: dict[str, object] = {}
            errors: dict[str, BaseException] = {}

            def race(label, candidate, proof, promoted_path):
                try:
                    with psycopg.connect(dsn) as own_connection:
                        barrier.wait(timeout=30)
                        result = RepairCutoverCatalog(own_connection).cutover(
                            intent=intent, candidate=candidate, proof=proof,
                            dataset_root=root, promoted_manifest_path=promoted_path,
                        )
                        outcomes[label] = result
                except BaseException as exc:  # noqa: BLE001 - surfaced to the main thread
                    errors[label] = exc

            thread_a = threading.Thread(target=race, args=("A", candidate_a, proof_a, promoted_path_a))
            thread_b = threading.Thread(target=race, args=("B", candidate_b, proof_b, promoted_path_b))
            thread_a.start()
            thread_b.start()
            thread_a.join(timeout=60)
            thread_b.join(timeout=60)

            if errors:
                raise AssertionError(f"race threads raised: {errors}")
            statuses = {label: result.status for label, result in outcomes.items()}
            assert set(statuses.values()) == {RepairOutcome.CONVERGED, RepairOutcome.STALE_CONFLICT}, statuses
            winner_label = next(label for label, status in statuses.items() if status == RepairOutcome.CONVERGED)
            loser_label = "B" if winner_label == "A" else "A"
            winner_candidate, winner_proof = (candidate_a, proof_a) if winner_label == "A" else (candidate_b, proof_b)
            loser_candidate = candidate_b if winner_label == "A" else candidate_a

            with connection.cursor() as cursor:
                cursor.execute("SELECT state FROM catalog.partitions WHERE partition_id=%s", (pred_sealed.partition_id,))
                assert cursor.fetchone()[0] == "superseded"
                cursor.execute(
                    "SELECT count(*) FROM catalog.partitions WHERE dataset_id=(SELECT dataset_id FROM catalog.partitions WHERE partition_id=%s) AND partition_key=%s AND state <> 'superseded'",
                    (pred_sealed.partition_id, natural_key),
                )
                assert cursor.fetchone()[0] == 1
                cursor.execute("SELECT indexdef FROM pg_indexes WHERE schemaname='catalog' AND indexname='partitions_one_live'")
                assert "UNIQUE INDEX partitions_one_live" in cursor.fetchone()[0]
                # the loser's own staged row is untouched: it remains
                # isolated, non-authoritative evidence, never promoted.
                loser_partition_id = run_b.sealed_partition.partition_id if loser_label == "B" else run_a.sealed_partition.partition_id
                cursor.execute("SELECT partition_key, state FROM catalog.partitions WHERE partition_id=%s", (loser_partition_id,))
                row = cursor.fetchone()
                assert row == (loser_candidate.staging_partition_key, "valid")

            # --- Idempotent retry of the ACTUAL winner (vector 13): resolved
            # by durable convergence provenance, never by coverage alone. ---
            retry_result = RepairCutoverCatalog(connection).cutover(
                intent=intent, candidate=winner_candidate, proof=winner_proof,
                dataset_root=root, promoted_manifest_path=root / "promoted-retry.json",
            )
            assert retry_result.status == RepairOutcome.ALREADY_SATISFIED
            assert retry_result.candidate_identity == winner_candidate.candidate_identity

            # --- Transactional failure leaves predecessor exactly authoritative
            # (vector 9), proven against real PostgreSQL rollback. ---
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
                required_support=CoverageInterval(Instant.parse("2024-01-16T00:00:00Z"), Instant.parse("2024-01-17T00:00:00Z")),
                predecessor=predecessor2,
                trigger=InvalidLiveRevisionTrigger(predecessor2, "predecessor2-fail-signature", "fail"),
            )
            candidate_c, proof_c, run_c = _build_candidate_and_proof(
                root, writer, profile, connection, identity, intent=intent2,
                day="2024-01-16", trade_id="cand-c-1", price="100.00",
                created_at="2026-09-17T11:10:00Z", code_ref="repair-attempt-c",
            )
            faulty = RepairCutoverCatalog(FaultAfterPromoteUpdate(connection))
            try:
                faulty.cutover(
                    intent=intent2, candidate=candidate_c, proof=proof_c,
                    dataset_root=root, promoted_manifest_path=root / "promoted-c.json",
                )
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


if __name__ == "__main__":
    raise SystemExit(main())
