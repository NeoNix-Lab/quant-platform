-- experiment (rev 1) -- canonical Experiment persistence boundary.
--
-- This schema persists I01 Study/Trial/RunSpec/Run/Artifact identities and
-- runtime lifecycle facts. It is deliberately separate from catalog: market
-- data remains catalog-owned, while experiment restart/query state is owned by
-- the Experiment System.

BEGIN;

CREATE ROLE experiment_owner  NOLOGIN;
CREATE ROLE experiment_reader NOLOGIN;
CREATE ROLE experiment_writer NOLOGIN;

CREATE SCHEMA IF NOT EXISTS experiment AUTHORIZATION experiment_owner;

SET ROLE experiment_owner;
SET search_path TO experiment, public;

CREATE DOMAIN fingerprint_sha256 AS char(64)
    CONSTRAINT fingerprint_sha256_hex CHECK (VALUE ~ '^[0-9a-f]{64}$');

CREATE TABLE studies (
    study_fingerprint fingerprint_sha256 PRIMARY KEY,
    payload           jsonb       NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE trials (
    trial_fingerprint fingerprint_sha256 PRIMARY KEY,
    study_fingerprint fingerprint_sha256 NOT NULL
        REFERENCES studies(study_fingerprint) ON DELETE RESTRICT,
    payload           jsonb       NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE run_specs (
    run_spec_fingerprint fingerprint_sha256 PRIMARY KEY,
    trial_fingerprint    fingerprint_sha256 NOT NULL
        REFERENCES trials(trial_fingerprint) ON DELETE RESTRICT,
    payload              jsonb       NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE artifact_contents (
    artifact_content_fingerprint fingerprint_sha256 PRIMARY KEY,
    artifact_kind                text  NOT NULL CHECK (length(trim(artifact_kind)) > 0),
    artifact_schema_version      text  NOT NULL CHECK (length(trim(artifact_schema_version)) > 0),
    content_identity             text  NOT NULL CHECK (length(trim(content_identity)) > 0),
    payload                      jsonb NOT NULL,
    created_at                   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE runs (
    run_spec_fingerprint fingerprint_sha256 NOT NULL
        REFERENCES run_specs(run_spec_fingerprint) ON DELETE RESTRICT,
    execution_id         text NOT NULL CHECK (length(trim(execution_id)) > 0),
    state                text NOT NULL CHECK (state IN ('REGISTERED','RUNNING','SUCCEEDED','FAILED')),
    registered_at        timestamptz NOT NULL DEFAULT now(),
    started_at           timestamptz,
    completed_at         timestamptz,
    failure_details      jsonb,
    updated_at           timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (run_spec_fingerprint, execution_id),
    CONSTRAINT running_has_started
        CHECK (state <> 'RUNNING' OR started_at IS NOT NULL),
    CONSTRAINT terminal_has_completed
        CHECK (state NOT IN ('SUCCEEDED','FAILED') OR completed_at IS NOT NULL),
    CONSTRAINT succeeded_has_no_failure
        CHECK (state <> 'SUCCEEDED' OR failure_details IS NULL),
    CONSTRAINT failed_has_failure
        CHECK (state <> 'FAILED' OR failure_details IS NOT NULL)
);

CREATE INDEX runs_by_spec_state
    ON runs (run_spec_fingerprint, state, execution_id);

CREATE TABLE artifacts (
    run_spec_fingerprint          fingerprint_sha256 NOT NULL,
    execution_id                  text NOT NULL,
    artifact_role                 text NOT NULL CHECK (length(trim(artifact_role)) > 0),
    artifact_content_fingerprint fingerprint_sha256 NOT NULL
        REFERENCES artifact_contents(artifact_content_fingerprint) ON DELETE RESTRICT,
    locator                       jsonb,
    registered_at                 timestamptz NOT NULL DEFAULT now(),

    PRIMARY KEY (
        run_spec_fingerprint,
        execution_id,
        artifact_role,
        artifact_content_fingerprint
    ),
    FOREIGN KEY (run_spec_fingerprint, execution_id)
        REFERENCES runs(run_spec_fingerprint, execution_id) ON DELETE RESTRICT,

    -- I02 v1 treats a run output role as one semantic claim. A second content
    -- identity for the same run+role is contradictory rather than a tie.
    UNIQUE (run_spec_fingerprint, execution_id, artifact_role)
);

CREATE INDEX artifacts_by_content
    ON artifacts (artifact_content_fingerprint);

RESET ROLE;

GRANT USAGE ON SCHEMA experiment TO experiment_reader, experiment_writer;
GRANT SELECT ON ALL TABLES IN SCHEMA experiment TO experiment_reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA experiment TO experiment_writer;

ALTER DEFAULT PRIVILEGES FOR ROLE experiment_owner IN SCHEMA experiment
    GRANT SELECT ON TABLES TO experiment_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE experiment_owner IN SCHEMA experiment
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO experiment_writer;
ALTER DEFAULT PRIVILEGES FOR ROLE experiment_owner IN SCHEMA experiment
    GRANT USAGE, SELECT ON SEQUENCES TO experiment_writer;

COMMIT;
