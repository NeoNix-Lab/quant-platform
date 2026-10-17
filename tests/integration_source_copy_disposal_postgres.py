#!/usr/bin/env python3
"""Real PostgreSQL proof for governed source-copy disposal (ADR-0049 Amendment 1, #425).

Publishes a synthetic ``ob200`` day with one ``u`` discontinuity through the unchanged
S13/S14 path (two ``valid`` partitions), keeps the archive in a *hot* and a *cold* root and
proves, against the real catalog and the real ``catalog.deletion_audit_records`` table:

* a tampered installed canonical artifact refuses the disposal and leaves the archive;
* the same disposal, once the bytes are restored, removes exactly the hot copy through the
  operator tool (exit codes included) and leaves the cold copy byte-identical;
* the audit rows hold the decision and the application result, and the decision was
  persisted before the bytes were removed.

Run it on a fresh disposable database::

    DATA_GATEWAY_TEST_DSN="host=127.0.0.1 port=55441 dbname=<fresh> user=postgres" \
        python tests/integration_source_copy_disposal_postgres.py
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tests"))

import psycopg  # noqa: E402

import test_bybit_l2_day_ingest_v1 as fixtures  # noqa: E402
from bootstrap_schema_registry import (  # noqa: E402
    bootstrap_schema_registry,
    read_schema_registration,
)
from quant_platform.application.l2_golden import run_l2_golden  # noqa: E402
from quant_platform.application.retention_deletion import PostgresRetentionDeletionAuditStore  # noqa: E402
from quant_platform.application.source_copy_disposal import (  # noqa: E402
    SourceCopyDisposalOutcome,
    SourceCopyLocation,
    dispose_source_copy,
    l2_canonical_dataset_identity,
    l2_raw_dataset_identity,
    read_catalog_canonical_partitions,
)

NAME = fixtures.archive_file_name(fixtures.DAY)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("SKIP PostgreSQL source-copy disposal integration: DATA_GATEWAY_TEST_DSN is unset")
        return 0
    with tempfile.TemporaryDirectory() as holder:
        root = Path(holder)
        hot, cold = root / "hot", root / "cold"
        (hot / "archive").mkdir(parents=True)
        (cold / "archive").mkdir(parents=True)
        made = fixtures.make_archive(root, fixtures.DAY, fixtures.ONE_GAP)
        shutil.move(str(made), hot / "archive" / NAME)
        archive = hot / "archive" / NAME
        shutil.copyfile(archive, cold / "archive" / NAME)
        sha = hashlib.sha256(archive.read_bytes()).hexdigest()
        size = archive.stat().st_size
        dataset_root = root / "canonical" / "l2" / "bybit" / "BTCUSDT" / "l2-book-event-v1"

        with psycopg.connect(dsn) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT 1 FROM catalog.datasets WHERE layer='canonical' AND kind='l2' "
                    "AND venue='bybit' AND instrument='BTCUSDT' AND schema_id='l2-book-event-v1'"
                )
                if cursor.fetchone() is not None:
                    raise RuntimeError("integration database already contains the fixed L2 dataset")
            bootstrap_schema_registry(
                connection, read_schema_registration(ROOT / "schemas" / "l2-book-event-v1.json")
            )
            connection.commit()
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO catalog.storage_roots (storage_root_id,abs_path,tier) "
                    "VALUES ('l2-hot','/srv/l2-disposal-hot','hot') ON CONFLICT DO NOTHING"
                )
                cursor.execute(
                    "INSERT INTO catalog.datasets (layer,kind,venue,instrument,rel_root,schema_id,manifest_sha256) "
                    "VALUES ('raw','l2','bybit','BTCUSDT','raw/l2/bybit/BTCUSDT/l2-book-event-v1',"
                    "'l2-book-event-v1',%s) ON CONFLICT DO NOTHING", ("1" * 64,),
                )
            connection.commit()

        def resolver(storage_root, dataset_rel_root, rel_path):
            return dataset_root / Path(*rel_path.split("/"))

        evidence = run_l2_golden(
            archive, day=fixtures.DAY, dataset_root=dataset_root, created_at="2026-10-17T10:00:00Z",
            closed_at="2026-10-17T10:00:01Z", producer="integration-source-copy-disposal",
            code_ref="integration-source-copy-disposal-commit", dsn=dsn, storage_root_id="l2-hot",
            path_resolver=resolver,
        )
        assert evidence["ok"], [c for c in evidence["claims"] if not c["ok"]]
        keys = [f"dt={fixtures.DAY}/seg=001", f"dt={fixtures.DAY}/seg=002"]

        autocommit = psycopg.connect(dsn, autocommit=True)  # every audit write is durable at once
        try:
            store = PostgresRetentionDeletionAuditStore(autocommit)
            partitions = read_catalog_canonical_partitions(
                autocommit, l2_canonical_dataset_identity("BTCUSDT"), keys, path_resolver=resolver
            )
            assert [p.catalog_state for p in partitions] == ["valid", "valid"], partitions

            def location(root_id, base):
                return SourceCopyLocation(root_id, base, f"archive/{NAME}")

            common = dict(
                dataset_identity=l2_raw_dataset_identity("BTCUSDT"), candidate=location("hot", hot),
                surviving=location("cold", cold), candidate_sha256=sha, candidate_byte_size=size,
                canonical_partitions=partitions, authority="integration-maintainer", decision_time=now_iso(),
                producer="integration-source-copy-disposal", code_ref="integration-commit",
                finalized_at="2026-10-17T10:00:01Z", audit_store=store,
            )

            # 1. a tampered installed artifact refuses; the archive stays
            artifact = partitions[0].artifact_path
            original = artifact.read_bytes()
            damaged = bytearray(original)
            damaged[len(damaged) // 2] ^= 0xFF
            artifact.write_bytes(bytes(damaged))
            refused = dispose_source_copy(**common)
            assert refused.outcome == SourceCopyDisposalOutcome.REFUSED, refused
            assert "canonical_bytes_mismatch" in refused.refusal_reasons, refused.refusal_reasons
            assert archive.exists() and (cold / "archive" / NAME).exists()
            with autocommit.cursor() as cursor:
                cursor.execute(
                    "SELECT decision, application_result->>'outcome', deleted_at FROM catalog.deletion_audit_records "
                    "WHERE deletion_decision_id=%s", (refused.decision_identity,)
                )
                row = cursor.fetchone()
            assert row == ("REFUSED", "refused", None), row

            # 2. bytes restored: the operator tool removes exactly the hot copy
            artifact.write_bytes(original)
            argv = [
                sys.executable, str(ROOT / "tools" / "dispose_source_copy.py"), "--dsn", dsn,
                "--candidate-root-id", "hot", "--candidate-root", str(hot), "--candidate-rel-path", f"archive/{NAME}",
                "--surviving-root-id", "cold", "--surviving-root", str(cold), "--surviving-rel-path", f"archive/{NAME}",
                "--source-sha256", sha, "--source-bytes", str(size), "--canonical-dataset-root", str(dataset_root),
                "--authority", "integration-maintainer", "--decision-time", now_iso(),
                "--finalized-at", "2026-10-17T10:00:01Z", "--producer", "integration-source-copy-disposal",
                "--code-ref", "integration-commit",
            ]
            for key in keys:
                argv += ["--partition", key]
            run = subprocess.run(argv, capture_output=True, text=True)
            assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
            payload = json.loads(run.stdout)
            assert payload["outcome"] == "removed" and payload["deleted"], payload
            assert not archive.exists(), "the hot copy must be removed"
            assert hashlib.sha256((cold / "archive" / NAME).read_bytes()).hexdigest() == sha, "cold copy must survive"
            with autocommit.cursor() as cursor:
                cursor.execute(
                    "SELECT decision, application_result->>'outcome', deleted_at IS NOT NULL, "
                    "decision_document->>'decision_kind', decision_document->'protection_evidence'->>'protection_state' "
                    "FROM catalog.deletion_audit_records WHERE deletion_decision_id=%s", (payload["decision_id"],)
                )
                row = cursor.fetchone()
            assert row == ("PERMITTED", "removed", True, "source_copy_disposal", "PROTECTED"), row

            # 3. the same request again: nothing left to remove, nothing touched
            again = subprocess.run(argv, capture_output=True, text=True)
            assert again.returncode == 0, (again.stdout, again.stderr)
            assert json.loads(again.stdout)["outcome"] == "already_absent"

            # 4. the last copy is never removed: with the hot copy gone, naming the cold copy as the
            #    candidate and any other path as its "copy" is refused
            lone = dispose_source_copy(**{**common, "candidate": location("cold", cold), "surviving": location("hot", hot),
                                          "decision_time": now_iso()})
            assert lone.outcome == SourceCopyDisposalOutcome.REFUSED, lone
            assert "missing_surviving_copy" in lone.refusal_reasons, lone.refusal_reasons
            assert (cold / "archive" / NAME).exists(), "the last copy must never be removed"
        finally:
            autocommit.close()

    print("POSTGRESQL SOURCE COPY DISPOSAL GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
