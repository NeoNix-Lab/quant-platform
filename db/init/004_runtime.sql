-- DG-K P02: runtime evidence only; canonical catalog/experiment unchanged.
-- Job reason_code/diagnostic_reference and attempt failure_code/evidence_reference
-- store ASCII JSON string values (or SQL NULL), decoded by DurableJobStore.
-- This preserves free-text NUL and literal escapes without changing identity bytes.
BEGIN;
CREATE ROLE runtime_owner NOLOGIN;
CREATE ROLE runtime_reader NOLOGIN;
CREATE ROLE runtime_writer NOLOGIN;
CREATE ROLE runtime_job_writer NOLOGIN;
CREATE SCHEMA runtime AUTHORIZATION runtime_owner;
REVOKE ALL ON SCHEMA runtime FROM PUBLIC;
SET ROLE runtime_owner;
SET search_path TO runtime;

CREATE TABLE durable_jobs (
    job_id text PRIMARY KEY,
    payload text NOT NULL,
    state text NOT NULL CHECK (state IN ('ADMITTED','QUEUED','RUNNING',
        'CANCELLATION_REQUESTED','RECOVERY_REQUIRED','SUCCEEDED','FAILED','CANCELLED')),
    reason_code text,
    result_references text NOT NULL DEFAULT '[]',
    diagnostic_reference text
);
CREATE TABLE durable_job_attempts (
    job_id text NOT NULL REFERENCES durable_jobs(job_id),
    attempt_no integer NOT NULL CHECK (attempt_no > 0),
    state text NOT NULL CHECK (state IN ('RUNNING','CANCELLATION_REQUESTED',
        'RECOVERY_REQUIRED','SUCCEEDED','FAILED','CANCELLED')),
    failure_code text,
    evidence_reference text,
    PRIMARY KEY (job_id, attempt_no)
);
CREATE TABLE admitted_inputs (
    admission_id text PRIMARY KEY,
    manifest_payload text NOT NULL,
    state text NOT NULL CHECK (state IN ('ADMITTED','SEALED','DELIVERY_PENDING',
        'DELIVERED','DELIVERY_FAILED','EXPIRED')),
    delivery_failure_reason text
);
CREATE TABLE governed_result_bundles (
    bundle_digest text PRIMARY KEY,
    payload text NOT NULL,
    run_spec_fingerprint text NOT NULL,
    execution_id text NOT NULL
);
CREATE TABLE governed_result_outputs (
    run_spec_fingerprint text NOT NULL,
    execution_id text NOT NULL,
    artifact_role text NOT NULL,
    content_checksum text NOT NULL,
    bundle_digest text NOT NULL REFERENCES governed_result_bundles(bundle_digest),
    PRIMARY KEY (run_spec_fingerprint, execution_id, artifact_role)
);
RESET ROLE;
GRANT USAGE ON SCHEMA runtime TO runtime_reader, runtime_writer, runtime_job_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA runtime TO runtime_reader;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA runtime TO runtime_writer;
GRANT SELECT, INSERT, UPDATE ON runtime.durable_jobs, runtime.durable_job_attempts
    TO runtime_job_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE runtime_owner IN SCHEMA runtime
    GRANT SELECT ON TABLES TO runtime_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE runtime_owner IN SCHEMA runtime
    GRANT SELECT, INSERT, UPDATE ON TABLES TO runtime_writer;
COMMIT;
