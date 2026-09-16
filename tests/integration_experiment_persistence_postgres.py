#!/usr/bin/env python3
"""Disposable PostgreSQL proof for I02 Experiment persistence.

Set EXPERIMENT_TEST_DSN to a disposable database where db/init/003_experiment.sql
has been applied. This script deliberately does not create production roles or
touch a production database.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main() -> int:
    dsn = os.environ.get("EXPERIMENT_TEST_DSN")
    if not dsn:
        print("POSTGRESQL I02 PERSISTENCE GATE NOT EXECUTED LOCALLY")
        return 0
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for PostgreSQL I02 integration proof") from exc

    from quant_platform.experiments import (
        ArtifactContentIdentity,
        ArtifactIdentity,
        ArtifactRegistration,
        ComparisonProtocolIdentity,
        ExperimentPersistenceConflict,
        ExperimentRepository,
        IdentityReference,
        RunIdentity,
        RunState,
        RunSpecIdentity,
        StudyIdentity,
        TrialIdentity,
    )

    def ref(kind: str, identity: str) -> IdentityReference:
        return IdentityReference(kind, identity)

    study = StudyIdentity(
        "study-definition-v1",
        ref("hypothesis", "i02-integration"),
        ref("metric-set", "metrics-v1"),
        ComparisonProtocolIdentity(ref("protocol", "protocol-v1"), ("lookback",)),
    )
    trial = TrialIdentity(study, {"lookback": 20})
    spec = RunSpecIdentity(
        trial,
        code_identity=ref("code", "git:i02"),
        data_identities=(ref("dataset-snapshot", "snapshot-v1"),),
        run_configuration={"seed": 1},
    )
    run = RunIdentity(spec, "postgres-execution-1")
    content = ArtifactContentIdentity("metrics", "metrics-v1", "sha256:" + "e" * 64)
    artifact = ArtifactRegistration(ArtifactIdentity(run, "metrics", content), {"uri": "file:///tmp/metrics.json"})

    with psycopg.connect(dsn) as connection:
        repository = ExperimentRepository(connection)
        repository.register_run(run)
        repository.transition_run(run, RunState.RUNNING)
        repository.succeed_run_with_artifacts(run, [artifact])
        with psycopg.connect(dsn) as restarted_connection:
            restarted = ExperimentRepository(restarted_connection)
            durable = restarted.get_run(run)
            assert durable.state == RunState.SUCCEEDED
            assert restarted.list_artifacts(run)[0].artifact_content_fingerprint == content.fingerprint
            try:
                restarted.transition_run(run, RunState.RUNNING)
            except ExperimentPersistenceConflict:
                pass
            else:
                raise AssertionError("terminal run reopened")
        with psycopg.connect(dsn) as check:
            with check.cursor() as cursor:
                cursor.execute("SELECT count(*) FROM experiment.studies WHERE study_fingerprint = %s", (study.fingerprint,))
                assert cursor.fetchone()[0] == 1
    print("I02 PostgreSQL Experiment persistence integration PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
