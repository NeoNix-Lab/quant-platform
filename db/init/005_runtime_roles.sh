#!/usr/bin/env bash
# Separate unit logins, using the existing 002_roles.sh environment pattern.
# No production credentials are versioned. No ingest grants are changed.
set -euo pipefail
for v in PLATFORM_API_PASSWORD PLATFORM_WORKER_PASSWORD POSTGRES_USER POSTGRES_DB; do
    if [[ -z "${!v:-}" ]]; then
        echo "005_runtime_roles.sh: FATAL: variable $v not set" >&2
        exit 1
    fi
done
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v apw="$PLATFORM_API_PASSWORD" -v wpw="$PLATFORM_WORKER_PASSWORD" \
    -v db="$POSTGRES_DB" <<'EOSQL'
BEGIN;
CREATE ROLE platform_api LOGIN PASSWORD :'apw'
    IN ROLE catalog_reader, experiment_writer, runtime_writer;
CREATE ROLE platform_worker LOGIN PASSWORD :'wpw' IN ROLE runtime_job_writer;
GRANT CONNECT ON DATABASE :"db" TO platform_api, platform_worker;
COMMIT;
EOSQL
