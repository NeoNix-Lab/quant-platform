#!/usr/bin/env python3
"""Real PostgreSQL parity/privilege proof on an explicitly disposable database.

Run after 001/002/003/004/005 bootstrap, with RUNTIME_TEST_ADMIN_DSN,
RUNTIME_TEST_API_DSN, RUNTIME_TEST_WORKER_DSN and RUNTIME_TEST_INGEST_DSN.
No silent skip: missing configuration fails. The database must be named
dgk331_disposable. Existing J03/K12/K13 test methods are reused unchanged.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest

import psycopg
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.admitted_input import (  # noqa: E402
    AdmittedInputConflict, AdmittedInputState, AdmittedInputStore,
)
from quant_platform.application.durable_jobs import (  # noqa: E402
    DurableJobConflict, DurableJobStore, JobState,
)
from quant_platform.application.governed_result_import import (  # noqa: E402
    GovernedResultEvidenceStore, GovernedResultImportService, GovernedResultRefused,
    ResultOutputContractV1,
)
from quant_platform.experiments import ExperimentRepository  # noqa: E402
import test_admitted_input_manifest_v1 as k12  # noqa: E402
import test_durable_job_runtime_v1 as j03  # noqa: E402
import test_governed_result_import_v1 as k13  # noqa: E402


def connect(role: str, *, autocommit: bool = True):
    dsn = os.environ[f"RUNTIME_TEST_{role}_DSN"]
    if conninfo_to_dict(dsn).get("dbname") != "dgk331_disposable":
        raise RuntimeError("runtime proof requires database dgk331_disposable")
    return psycopg.connect(dsn, autocommit=autocommit)


class DisposableCase:
    def setUp(self):
        with connect("ADMIN") as admin:
            admin.execute(
                "TRUNCATE runtime.durable_jobs, runtime.durable_job_attempts, "
                "runtime.admitted_inputs, runtime.governed_result_bundles, "
                "runtime.governed_result_outputs, experiment.studies CASCADE"
            )

    def connection(self, role="API", *, autocommit=True):
        connection = connect(role, autocommit=autocommit)
        self.addCleanup(connection.close)
        return connection


class PostgresJobs(DisposableCase, j03.DurableJobRuntimeV1Tests):
    def store(self):
        return DurableJobStore(self.connection("WORKER"))

    def test_restart_marks_running_job_recovery_required_without_redispatch(self):
        first = self.store()
        job = first.admit(j03.admission())
        first.transition(job.job_id, JobState.QUEUED)
        first.transition(job.job_id, JobState.RUNNING)
        first.connection.close()
        restarted = self.store()
        recovered = restarted.recover_after_restart()
        self.assertEqual((job.job_id,), tuple(item.job_id for item in recovered))
        self.assertEqual(JobState.RECOVERY_REQUIRED, restarted.get(job.job_id).state)
        self.assertEqual((JobState.RECOVERY_REQUIRED,), tuple(
            item.state for item in restarted.attempts(job.job_id)
        ))

    def test_free_text_evidence_and_terminal_append_survive_reconnect(self):
        first = self.store()
        job = first.admit(j03.admission())
        raw = first.connection.execute(
            "SELECT reason_code, diagnostic_reference FROM runtime.durable_jobs WHERE job_id = %s",
            (job.job_id,),
        ).fetchone()
        self.assertEqual((None, None), raw)
        first.transition(job.job_id, JobState.QUEUED)
        first.transition(job.job_id, JobState.RUNNING)
        reason = 'failed\x00detail' + r'\u0000 "quoted"'
        failed = first.transition(job.job_id, JobState.FAILED, reason_code=reason)
        attempt = first.attempts(job.job_id)[0]
        first.connection.close()
        reopened = self.store()
        self.assertEqual(failed, reopened.get(job.job_id))
        self.assertEqual((attempt,), reopened.attempts(job.job_id))
        diagnostic = 'evidence\x00ref' + r'\x00\u0000'
        appended = reopened.transition(
            job.job_id, JobState.FAILED, reason_code=reason, diagnostic_reference=diagnostic,
        )
        reopened.connection.close()
        final = self.store()
        self.assertEqual(appended, final.get(job.job_id))
        self.assertEqual(appended, final.transition(
            job.job_id, JobState.FAILED, reason_code=reason, diagnostic_reference=diagnostic,
        ))
        self.assertEqual((attempt,), final.attempts(job.job_id))
        raw = final.connection.execute(
            "SELECT reason_code, diagnostic_reference FROM runtime.durable_jobs WHERE job_id = %s",
            (job.job_id,),
        ).fetchone()
        self.assertEqual((reason, diagnostic), tuple(json.loads(value) for value in raw))
        raw_attempt = final.connection.execute(
            "SELECT failure_code, evidence_reference FROM runtime.durable_job_attempts WHERE job_id = %s",
            (job.job_id,),
        ).fetchone()
        self.assertEqual(reason, json.loads(raw_attempt[0]))
        self.assertIsNone(raw_attempt[1])

        second = final.admit(replace(j03.admission(), request_identity="reconnect-attempt"))
        final.transition(second.job_id, JobState.QUEUED)
        final.transition(second.job_id, JobState.RUNNING)
        failed = final.transition(second.job_id, JobState.FAILED,
                                  reason_code=reason, diagnostic_reference=diagnostic)
        attempt = final.attempts(second.job_id)[0]
        final.connection.close()
        after_failure = self.store()
        self.assertEqual(failed, after_failure.get(second.job_id))
        self.assertEqual((attempt,), after_failure.attempts(second.job_id))
        self.assertEqual((reason, diagnostic), (attempt.failure_code, attempt.evidence_reference))


class PostgresInputs(DisposableCase, k12.AdmittedInputManifestV1Tests):
    def store(self):
        return AdmittedInputStore(self.connection())


class PostgresResults(DisposableCase, k13.GovernedResultImportV1Tests):
    def setUp(self):
        DisposableCase.setUp(self)
        self.admissions = AdmittedInputStore(self.connection())
        self.evidence = GovernedResultEvidenceStore(self.connection())
        self.experiments = ExperimentRepository(self.connection(autocommit=False))
        self.service = GovernedResultImportService(
            self.admissions, self.experiments, self.evidence,
            lambda evidence: evidence.content_checksum,
            (ResultOutputContractV1("metrics", "metrics", "metrics-v1"),),
        )

    def test_payload_bytes_and_identities_match_sqlite_and_survive_reconnect(self):
        bundle = self.bundle()
        native = k13.GovernedResultImportV1Tests("test_exact_resubmission_is_idempotent_and_conflicting_role_is_refused")
        native.setUp()
        self.addCleanup(native.admissions.connection.close)
        self.addCleanup(native.evidence.connection.close)
        local_bundle = native.bundle()
        self.assertEqual(local_bundle.canonical_payload_v1, bundle.canonical_payload_v1)
        self.assertEqual(local_bundle.bundle_digest, bundle.bundle_digest)
        self.assertEqual(local_bundle.admission_id, bundle.admission_id)
        native.service.import_result(local_bundle)
        self.service.import_result(bundle)
        with connect("API") as api:
            payload = api.execute(
                "SELECT payload FROM runtime.governed_result_bundles WHERE bundle_digest = %s",
                (bundle.bundle_digest,),
            ).fetchone()[0]
            self.assertEqual(local_bundle.canonical_payload_v1.encode(), payload.encode())
            manifest_payload = api.execute(
                "SELECT manifest_payload FROM runtime.admitted_inputs WHERE admission_id = %s",
                (bundle.admission_id,),
            ).fetchone()[0]
            self.assertEqual(local_bundle.consumed_manifest.canonical_payload_v1.encode(), manifest_payload.encode())
            GovernedResultEvidenceStore(api).record(bundle)
            self.assertEqual(1, api.execute("SELECT count(*) FROM runtime.governed_result_bundles").fetchone()[0])

    def test_concurrent_evidence_resubmission_and_conflicting_output(self):
        bundle = self.bundle()

        def record(candidate):
            with connect("API") as connection:
                GovernedResultEvidenceStore(connection).record(candidate)

        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(record, [bundle, bundle]))
        conflicting = replace(bundle, configuration={"different": True})
        with self.assertRaises(GovernedResultRefused):
            record(conflicting)
        with connect("API") as connection:
            self.assertEqual(1, connection.execute("SELECT count(*) FROM runtime.governed_result_bundles").fetchone()[0])

    def test_corrupt_bundle_payload_is_refused(self):
        bundle = self.bundle()
        self.evidence.record(bundle)
        with connect("ADMIN") as admin:
            admin.execute("UPDATE runtime.governed_result_bundles SET payload = '{}' WHERE bundle_digest = %s", (bundle.bundle_digest,))
        with self.assertRaisesRegex(GovernedResultRefused, "payload conflicts"):
            self.evidence.record(bundle)


class PostgresSafety(DisposableCase, unittest.TestCase):
    def test_job_bytes_match_sqlite_and_conflicting_admission_is_refused(self):
        sqlite = sqlite3.connect(":memory:")
        self.addCleanup(sqlite.close)
        local = DurableJobStore(sqlite).admit(j03.admission())
        store = DurableJobStore(self.connection())
        remote = store.admit(j03.admission())
        self.assertEqual(local, remote)
        payload = store.connection.execute("SELECT payload FROM runtime.durable_jobs").fetchone()[0]
        self.assertEqual(sqlite.execute("SELECT payload FROM durable_jobs").fetchone()[0].encode(), payload.encode())
        with connect("ADMIN") as admin:
            admin.execute("UPDATE runtime.durable_jobs SET payload = '{}' WHERE job_id = %s", (remote.job_id,))
        with self.assertRaises(DurableJobConflict):
            store.admit(j03.admission())

    def test_conflicting_admission_manifest_is_refused(self):
        store = AdmittedInputStore(self.connection())
        record = store.admit(k12.manifest())
        with connect("ADMIN") as admin:
            admin.execute("UPDATE runtime.admitted_inputs SET manifest_payload = '{}' WHERE admission_id = %s", (record.admission_id,))
        with self.assertRaises(AdmittedInputConflict):
            store.admit(k12.manifest())

    def test_concurrent_job_admission_and_claim_have_one_attempt(self):
        def admit(_):
            with connect("API") as connection:
                return DurableJobStore(connection).admit(j03.admission())
        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(admit, range(2))
        self.assertEqual(first, second)
        store = DurableJobStore(self.connection("WORKER"))
        store.transition(first.job_id, JobState.QUEUED)

        def claim(_):
            with connect("WORKER") as connection:
                return DurableJobStore(connection).transition(first.job_id, JobState.RUNNING)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claimed = list(pool.map(claim, range(2)))
        self.assertEqual(claimed[0], claimed[1])
        self.assertEqual((1,), tuple(item.attempt_no for item in store.attempts(first.job_id)))

    def test_delivery_cannot_revive_expired_input(self):
        store = AdmittedInputStore(self.connection())
        record = store.admit(k12.manifest())
        store.seal(record.admission_id)
        store.begin_delivery(record.admission_id)
        store.expire(record.admission_id)
        with self.assertRaises(AdmittedInputConflict):
            store.record_delivery(record.admission_id, record.manifest.manifest_digest)
        self.assertEqual(AdmittedInputState.EXPIRED, store.get(record.admission_id).state)

    def test_admission_read_check_write_obeys_transaction_lock(self):
        def admit():
            with connect("API") as connection:
                connection.execute("SET statement_timeout = '5s'")
                return DurableJobStore(connection).admit(j03.admission())

        with ThreadPoolExecutor(max_workers=1) as pool:
            with connect("ADMIN") as admin:
                with admin.transaction():
                    admin.execute("SELECT pg_advisory_xact_lock(%s)", (33101,))
                    pending = pool.submit(admit)
                    try:
                        with self.assertRaises(TimeoutError):
                            pending.result(timeout=0.5)
                    finally:
                        # The pool must never wait for a task behind a held
                        # lock when the assertion detects the regression.
                        admin.execute("SELECT 1")
            self.assertEqual(JobState.ADMITTED, pending.result(timeout=6).state)

    def test_ingest_is_actually_refused_on_every_runtime_table(self):
        ingest = self.connection("INGEST")
        for table in ("durable_jobs", "durable_job_attempts", "admitted_inputs",
                      "governed_result_bundles", "governed_result_outputs"):
            with self.subTest(table=table), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                ingest.execute(f"SELECT * FROM runtime.{table}")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            ingest.execute("SELECT * FROM experiment.studies")

    def test_worker_cannot_access_k12_k13_experiment_or_ddl(self):
        worker = self.connection("WORKER")
        for statement in (
            "SELECT * FROM runtime.admitted_inputs",
            "SELECT * FROM runtime.governed_result_bundles",
            "SELECT * FROM runtime.governed_result_outputs",
            "SELECT * FROM experiment.studies",
            "CREATE TABLE runtime.unauthorized (id integer)",
            "DELETE FROM runtime.durable_jobs",
        ):
            with self.subTest(statement=statement), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                worker.execute(statement)

    def test_api_is_reader_in_catalog_and_not_runtime_ddl_or_delete(self):
        api = self.connection()
        api.execute("SELECT * FROM catalog.datasets LIMIT 0")
        for statement in (
            "CREATE TABLE runtime.unauthorized (id integer)",
            "DELETE FROM runtime.admitted_inputs",
            "UPDATE catalog.datasets SET dataset_id = dataset_id",
            "SET ROLE runtime_owner",
        ):
            with self.subTest(statement=statement), self.assertRaises(psycopg.errors.InsufficientPrivilege):
                api.execute(statement)

    def test_runtime_owner_and_reader_roles(self):
        admin = self.connection("ADMIN")
        rows = admin.execute(
            "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole "
            "FROM pg_roles WHERE rolname IN ('runtime_owner','platform_api','platform_worker') ORDER BY rolname"
        ).fetchall()
        self.assertEqual([
            ("platform_api", True, False, False, False),
            ("platform_worker", True, False, False, False),
            ("runtime_owner", False, False, False, False),
        ], rows)
        self.assertEqual([("runtime_owner",)], admin.execute(
            "SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class "
            "WHERE relnamespace = 'runtime'::regnamespace AND relkind = 'r'"
        ).fetchall())
        admin.execute("SET ROLE runtime_reader")
        admin.execute("SELECT * FROM runtime.admitted_inputs")
        with self.assertRaises(psycopg.errors.InsufficientPrivilege):
            admin.execute("UPDATE runtime.admitted_inputs SET state = state")
        admin.execute("RESET ROLE")

    def test_constructor_does_not_close_or_commit_caller_connection(self):
        connection = self.connection(autocommit=False)
        with self.assertRaisesRegex(ValueError, "autocommit"):
            DurableJobStore(connection)
        self.assertFalse(connection.closed)
        auto = self.connection()
        DurableJobStore(auto).admit(j03.admission())
        self.assertFalse(auto.closed)
        self.assertEqual(psycopg.pq.TransactionStatus.IDLE, auto.info.transaction_status)


if __name__ == "__main__":
    for role in ("ADMIN", "API", "WORKER", "INGEST"):
        if not os.environ.get(f"RUNTIME_TEST_{role}_DSN"):
            raise SystemExit(f"RUNTIME_TEST_{role}_DSN is required (no PostgreSQL proof executed)")
    unittest.main(verbosity=2)
