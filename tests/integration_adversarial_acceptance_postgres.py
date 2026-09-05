#!/usr/bin/env python3
"""Real PostgreSQL acceptance for adversarial publication scenarios A1 and A4.

A1 proves that a zero-event partition can carry complete declared coverage,
pass S13/S14, and complete a real DataGateway scan with zero batches and final
zero-row metadata.  A4 proves wholesale A -> B coverage supersession through
the same publication chain and a current-state DataGateway read, then proves
that one cyclic supersession is refused without catalog mutation.

Set ``DATA_GATEWAY_TEST_DSN`` to a freshly initialized disposable PostgreSQL 17
catalog.  The real DataGateway leg also requires a POSIX filesystem because the
frozen catalog storage-root contract is POSIX-absolute.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import psycopg  # noqa: E402

from adversarial_support import (  # noqa: E402
    CANONICAL_IDENTITY,
    build_publication_fixture,
    certification_runtime,
    diff_catalog_state,
    eligibility_bridge,
    expect_refusal,
    gateway,
    register_catalog_prerequisites,
    request,
    restate_coverage,
    scoped_catalog_state,
    trade,
)
from quant_platform.data import Instant, NoCoverage, ScanState  # noqa: E402
from quant_platform.data.coverage import reconstruct_catalog_coverage  # noqa: E402
from quant_platform.data.publication import PublicationCertificationError  # noqa: E402
from quant_platform.data.publication_eligibility import (  # noqa: E402
    PublicationEligibilityRefusal,
)


A1_KEY = "dt=2024-01-18"
A1_START = "2024-01-18T00:00:00Z"
A1_END = "2024-01-19T00:00:00Z"

A4_KEY = "dt=2024-01-19"
A4_START = "2024-01-19T00:00:00Z"
A4_CURRENT_END = "2024-01-19T23:00:00Z"
A4_END = "2024-01-20T00:00:00Z"
A4_A_ID = "adversarial-a4-coverage-a"
A4_B_ID = "adversarial-a4-coverage-b"

A4_INVALID_KEY = "dt=2024-01-20"
A4_INVALID_START = "2024-01-20T00:00:00Z"
A4_INVALID_END = "2024-01-21T00:00:00Z"
A4_INVALID_CODE = "COVERAGE_SUPERSESSION_CYCLE"


def require(condition: bool, what: str) -> None:
    if not condition:
        raise AssertionError(what)


def partition_rows(connection, partition_key: str) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.partition_id::text, p.state, p.ts_start, p.ts_end, p.row_count
              FROM catalog.partitions AS p
              JOIN catalog.datasets AS d ON d.dataset_id = p.dataset_id
             WHERE d.layer = 'canonical' AND d.kind = 'trades'
               AND d.venue = 'bybit' AND d.instrument = 'BTCUSDT'
               AND d.schema_id = 'trade-v1' AND p.partition_key = %s
             ORDER BY p.revision
            """,
            (partition_key,),
        )
        return cursor.fetchall()


def scenario_a1(connection, root: Path) -> None:
    fixture = build_publication_fixture(
        root,
        partition_key=A1_KEY,
        records=[],
        coverage_assertions=[(A1_START, A1_END)],
        intent_start=A1_START,
        intent_end=A1_END,
        coverage_id="adversarial-a1-zero-event",
        source_extract_detail="exhaustive source extract with zero events",
    )

    partition = fixture.partition_document
    require(partition["row_count"] == 0, "A1 manifest row_count must be zero")
    require(
        partition["first_exchange_ts"] is None
        and partition["last_exchange_ts"] is None,
        "A1 zero-event manifest must have null observed record bounds",
    )
    folded, violations = reconstruct_catalog_coverage(
        fixture.coverage_documents, (partition,)
    )
    require(not violations, f"A1 coverage must fold without violations: {violations}")
    require(
        (A1_KEY, 1) in folded,
        "A1 complete coverage must remain attributable to its zero-row partition",
    )

    runtime, profile = certification_runtime(connection)
    run = runtime.run(fixture.sealed_evidence())
    require(run.certification.status == "pass", "A1 S13 must pass")
    require(
        run.quality_report is not None and run.quality_report.status == "pass",
        "A1 S13 must persist pass evidence",
    )
    result = eligibility_bridge(connection).publish(
        fixture.eligibility_evidence(profile)
    )
    require(result.state == "valid", "A1 S14 must make the partition valid")

    rows = partition_rows(connection, A1_KEY)
    require(len(rows) == 1, f"A1 must have exactly one catalog row, observed {rows}")
    require(rows[0][1] == "valid" and rows[0][4] == 0, f"A1 row mismatch: {rows}")

    scan = gateway(connection).scan(request(fixture))
    require(scan.state is ScanState.OPEN, "A1 scan must open in OPEN state")
    require(scan.completed_metadata is None, "A1 final metadata must start unavailable")
    require(list(scan) == [], "A1 zero-event scan must emit no batches")
    require(scan.state is ScanState.COMPLETED, "A1 exhausted scan must complete")
    metadata = scan.completed_metadata
    require(metadata is not None, "A1 exhausted scan must expose final metadata")
    require(metadata.row_count == 0, "A1 completed metadata row_count must be zero")
    require(
        metadata.returned_record_bounds is None,
        "A1 zero-event completed metadata must have no returned record bounds",
    )
    require(metadata.coverage_complete, "A1 requested coverage must be complete")
    require(metadata.coverage_gaps == (), "A1 complete request must have no gaps")

    uncovered = expect_refusal(
        lambda: gateway(connection).scan(
            request(
                fixture,
                start="2024-01-17T23:00:00Z",
                end=A1_START,
            )
        ),
        NoCoverage,
        what="A1 adjacent uncovered DataGateway request",
    )
    print(
        "A1 PASS  rows=0 bounds=null coverage=complete "
        f"S13=pass S14=valid scan=COMPLETED control={type(uncovered).__name__}"
    )


def scenario_a4_positive(connection, root: Path) -> None:
    fixture_a = build_publication_fixture(
        root,
        partition_key=A4_KEY,
        records=[
            trade("2024-01-19T01:00:00Z", "a4-1"),
            trade("2024-01-19T22:00:00Z", "a4-2", aggressor_side="sell"),
        ],
        coverage_assertions=[(A4_START, A4_END)],
        intent_start=A4_START,
        intent_end=A4_END,
        coverage_id=A4_A_ID,
        source_extract_detail="original exhaustive source extract",
    )
    runtime, profile = certification_runtime(connection)
    run_a = runtime.run(fixture_a.sealed_evidence())
    require(run_a.certification.status == "pass", "A4 predecessor S13 must pass")
    original_id = run_a.sealed_partition.partition_id

    fixture_b = restate_coverage(
        fixture_a,
        coverage_id=A4_B_ID,
        supersedes=A4_A_ID,
        coverage_assertions=[(A4_START, A4_CURRENT_END)],
        intent_start=A4_START,
        intent_end=A4_END,
        source_extract_detail="current wholesale coverage restatement",
    )
    folded, violations = reconstruct_catalog_coverage(
        fixture_b.coverage_documents, (fixture_b.partition_document,)
    )
    require(not violations, f"A4 valid A -> B fold failed: {violations}")
    interval = folded[(A4_KEY, 1)]
    require(
        interval[0].isoformat() == A4_START
        and interval[1].isoformat() == A4_CURRENT_END,
        f"A4 fold must expose only B current coverage, observed {interval}",
    )

    run_b = runtime.run(fixture_b.sealed_evidence())
    require(run_b.certification.status == "pass", "A4 successor S13 must pass")
    require(
        run_b.sealed_partition.partition_id == original_id,
        "A4 same-revision restatement must retain the catalog partition identity",
    )
    result = eligibility_bridge(connection).publish(
        fixture_b.eligibility_evidence(profile)
    )
    require(result.state == "valid", "A4 successor S14 must make current state valid")
    require(result.partition_id == original_id, "A4 S14 must promote the restated row")

    rows = partition_rows(connection, A4_KEY)
    require(len(rows) == 1, f"A4 must retain one live catalog row, observed {rows}")
    require(rows[0][1] == "valid" and rows[0][4] == 2, f"A4 current row mismatch: {rows}")
    require(
        Instant.parse(rows[0][3]) == Instant.parse(A4_CURRENT_END),
        f"A4 catalog must expose B end, observed {rows[0][3]}",
    )

    scan = gateway(connection).scan(request(fixture_b), batch_size=1)
    records = [record for batch in scan for record in batch]
    require(scan.state is ScanState.COMPLETED, "A4 current scan must complete")
    require([record.trade_id for record in records] == ["a4-1", "a4-2"],
            f"A4 current read returned wrong records: {records}")
    require(
        scan.completed_metadata is not None
        and scan.completed_metadata.row_count == 2
        and scan.completed_metadata.coverage_complete,
        "A4 current read must carry complete two-row metadata",
    )

    removed = expect_refusal(
        lambda: gateway(connection).scan(
            request(fixture_b, start=A4_CURRENT_END, end=A4_END)
        ),
        NoCoverage,
        what="A4 superseded predecessor-only interval",
    )
    print(
        "A4 POSITIVE PASS  A->B S13=pass S14=valid live_rows=1 "
        f"read_rows=2 removed_support={type(removed).__name__}"
    )


def scenario_a4_invalid(connection, root: Path) -> None:
    fixture_a = build_publication_fixture(
        root,
        partition_key=A4_INVALID_KEY,
        records=[trade("2024-01-20T01:00:00Z", "a4-invalid-1")],
        coverage_assertions=[(A4_INVALID_START, A4_INVALID_END)],
        intent_start=A4_INVALID_START,
        intent_end=A4_INVALID_END,
        coverage_id="adversarial-a4-cycle-a",
        supersedes="adversarial-a4-cycle-b",
        source_extract_detail="intentionally cyclic coverage document A",
    )
    fixture = restate_coverage(
        fixture_a,
        coverage_id="adversarial-a4-cycle-b",
        supersedes="adversarial-a4-cycle-a",
        coverage_assertions=[(A4_INVALID_START, A4_INVALID_END)],
        intent_start=A4_INVALID_START,
        intent_end=A4_INVALID_END,
        source_extract_detail="intentionally cyclic coverage document B",
    )
    folded, violations = reconstruct_catalog_coverage(
        fixture.coverage_documents, (fixture.partition_document,)
    )
    codes = sorted({item.code for item in violations})
    require(
        codes == [A4_INVALID_CODE],
        f"A4 invalid fixture must raise exactly {A4_INVALID_CODE}, observed {codes}",
    )
    require(not folded, f"A4 invalid fixture must not fold coverage: {folded}")

    keys = [A1_KEY, A4_KEY, A4_INVALID_KEY]
    before = scoped_catalog_state(connection, CANONICAL_IDENTITY, keys)
    runtime, profile = certification_runtime(connection)
    s13 = expect_refusal(
        lambda: runtime.run(fixture.sealed_evidence()),
        PublicationCertificationError,
        what="A4 cyclic supersession S13",
    )
    require(A4_INVALID_CODE in str(s13), f"A4 S13 refusal lost code: {s13}")
    after_s13 = scoped_catalog_state(connection, CANONICAL_IDENTITY, keys)
    require(
        diff_catalog_state(before, after_s13) == (),
        "A4 invalid S13 must leave scoped catalog state unchanged",
    )

    s14 = expect_refusal(
        lambda: eligibility_bridge(connection).publish(
            fixture.eligibility_evidence(profile)
        ),
        PublicationEligibilityRefusal,
        what="A4 cyclic supersession S14",
    )
    require(A4_INVALID_CODE in str(s14), f"A4 S14 refusal lost code: {s14}")
    after_s14 = scoped_catalog_state(connection, CANONICAL_IDENTITY, keys)
    require(
        diff_catalog_state(before, after_s14) == (),
        "A4 invalid S14 must leave scoped catalog state unchanged",
    )
    require(
        partition_rows(connection, A4_INVALID_KEY) == [],
        "A4 cyclic supersession must create no catalog partition row",
    )
    print(
        f"A4 NEGATIVE PASS  fold={A4_INVALID_CODE} "
        f"S13={type(s13).__name__} S14={type(s14).__name__} mutation=none"
    )


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("POSTGRESQL ADVERSARIAL ACCEPTANCE NOT EXECUTED LOCALLY")
        return 0
    if os.name == "nt":
        print("POSTGRESQL ADVERSARIAL ACCEPTANCE NOT EXECUTED — POSIX FILESYSTEM REQUIRED")
        return 0
    with tempfile.TemporaryDirectory() as holder:
        root = Path(holder)
        with psycopg.connect(dsn) as connection:
            register_catalog_prerequisites(connection, root)
            scenario_a1(connection, root)
            scenario_a4_positive(connection, root)
            scenario_a4_invalid(connection, root)
    print(
        "PASS PostgreSQL 17 adversarial publication acceptance: "
        "A1 zero-event complete coverage, A4 supersession"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
