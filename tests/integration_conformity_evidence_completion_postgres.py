#!/usr/bin/env python3
"""Complete the four remaining conformity acceptance propositions.

M1 proves read-time overlap refusal against two real bridge-published rows.
M2 rebuilds the same durable evidence into two fresh PostgreSQL catalogs and
compares the RB1 semantic classes while requiring generated UUID differences.
M3 publishes two byte-different layouts of the same canonical records and
proves that DataGateway result identity remains physical-evidence-sensitive.
M4 composes the production DataGateway ordering output with the frozen
CandleDefinition first/last source-order OPEN/CLOSE rule, without introducing
a Candle runtime.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import psycopg  # noqa: E402

from adversarial_support import (  # noqa: E402
    CANONICAL_IDENTITY,
    PublicationFixture,
    build_publication_fixture,
    certification_runtime,
    eligibility_bridge,
    expect_refusal,
    gateway,
    register_catalog_prerequisites,
    request,
    trade,
)
from quant_platform.data import CatalogConflict, Instant  # noqa: E402
from quant_platform.ordering import (  # noqa: E402
    TRADES_CANONICAL_TOTAL_ORDER_V1,
    ordering_policy_satisfies,
)
from quant_platform.source_adapters.bybit import (  # noqa: E402
    BYBIT_ORDERING_PROVIDER,
    BYBIT_TRADE_V1_ORDERING_POLICY,
    bybit_trade_v1_ordering_key,
)


M2_KEY = "dt=2024-01-24"
M2_START = "2024-01-24T00:00:00Z"
M2_END = "2024-01-25T00:00:00Z"

M3_KEY = "dt=2024-01-26"
M3_START = "2024-01-26T00:00:00Z"
M3_END = "2024-01-27T00:00:00Z"

M1_LEFT_KEY = "window=overlap-left"
M1_RIGHT_KEY = "window=overlap-right"
M1_START = "2024-01-27T00:00:00Z"
M1_OVERLAP_START = "2024-01-27T01:00:00Z"
M1_LEFT_END = "2024-01-27T02:00:00Z"
M1_END = "2024-01-27T03:00:00Z"

M4_KEY = "dt=2024-01-28"
M4_START = "2024-01-28T00:00:00Z"
M4_END = "2024-01-29T00:00:00Z"


def require(condition: bool, what: str) -> None:
    if not condition:
        raise AssertionError(what)


def publish(connection: Any, fixture: PublicationFixture):
    runtime, profile = certification_runtime(connection)
    run = runtime.run(fixture.sealed_evidence())
    require(run.certification.status == "pass", "S13 must pass")
    require(
        run.quality_report is not None and run.quality_report.status == "pass",
        "S13 must persist pass evidence",
    )
    result = eligibility_bridge(connection).publish(
        fixture.eligibility_evidence(profile)
    )
    require(result.state == "valid", "S14 must publish state=valid")
    return run, result


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _instant(value: Any) -> str | None:
    return None if value is None else Instant.parse(value).isoformat()


def rb1_snapshot(connection: Any, partition_key: str) -> dict[str, Any]:
    """Project exactly the durable semantic classes listed by RB1."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT d.layer, d.kind, d.venue, d.instrument, d.schema_id,
                   d.feature_set_def_id::text, d.rel_root, d.manifest_sha256
              FROM catalog.datasets AS d
             WHERE d.layer = 'canonical' AND d.kind = 'trades'
               AND d.venue = 'bybit' AND d.instrument = 'BTCUSDT'
               AND d.schema_id = 'trade-v1'
            """
        )
        dataset = tuple(cursor.fetchone())

        cursor.execute(
            """
            SELECT p.partition_key, p.revision, p.ts_start, p.ts_end, p.state,
                   p.storage_root_id, p.rel_path, p.row_count, p.byte_size,
                   p.content_sha256, p.manifest_sha256, p.producer, p.code_ref
              FROM catalog.partitions AS p
              JOIN catalog.datasets AS d ON d.dataset_id = p.dataset_id
             WHERE d.layer = 'canonical' AND d.kind = 'trades'
               AND d.venue = 'bybit' AND d.instrument = 'BTCUSDT'
               AND d.schema_id = 'trade-v1' AND p.partition_key = %s
            """,
            (partition_key,),
        )
        partition_row = cursor.fetchone()
        partition = (
            *partition_row[:2],
            _instant(partition_row[2]),
            _instant(partition_row[3]),
            *partition_row[4:],
        )

        cursor.execute(
            """
            SELECT parent.layer, parent.kind, parent.venue, parent.instrument,
                   parent.schema_id, child.layer, child.kind, child.venue,
                   child.instrument, child.schema_id, l.transform
              FROM catalog.dataset_lineage AS l
              JOIN catalog.datasets AS parent ON parent.dataset_id = l.parent_id
              JOIN catalog.datasets AS child ON child.dataset_id = l.child_id
             WHERE child.layer = 'canonical' AND child.kind = 'trades'
               AND child.venue = 'bybit' AND child.instrument = 'BTCUSDT'
               AND child.schema_id = 'trade-v1'
             ORDER BY 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11
            """
        )
        lineage = tuple(tuple(row) for row in cursor.fetchall())

        cursor.execute(
            """
            SELECT s.schema_id, s.name, s.version, s.json_sha256, s.body
              FROM catalog.schema_registry AS s
             WHERE s.schema_id = 'trade-v1'
            """
        )
        schema_row = cursor.fetchone()
        schema = (*schema_row[:4], _json(schema_row[4]))

        cursor.execute(
            """
            SELECT q.check_suite, q.status, q.metrics, q.violations, q.code_ref
              FROM catalog.quality_reports AS q
              JOIN catalog.partitions AS p ON p.partition_id = q.partition_id
              JOIN catalog.datasets AS d ON d.dataset_id = p.dataset_id
             WHERE d.layer = 'canonical' AND d.kind = 'trades'
               AND d.venue = 'bybit' AND d.instrument = 'BTCUSDT'
               AND d.schema_id = 'trade-v1' AND p.partition_key = %s
             ORDER BY q.check_suite, q.status, q.code_ref
            """,
            (partition_key,),
        )
        quality = tuple(
            (row[0], row[1], _json(row[2]), _json(row[3]), row[4])
            for row in cursor.fetchall()
        )

    return {
        "dataset_identity_and_manifest": dataset,
        "natural_partition_coverage_lifecycle_physical_manifest_provenance": partition,
        "lineage": lineage,
        "schema_identity": schema,
        "certification_quality_outcome": quality,
    }


def generated_ids(connection: Any, partition_key: str) -> dict[str, Any]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT d.dataset_id::text, p.partition_id::text
              FROM catalog.partitions AS p
              JOIN catalog.datasets AS d ON d.dataset_id = p.dataset_id
             WHERE d.layer = 'canonical' AND d.kind = 'trades'
               AND d.venue = 'bybit' AND d.instrument = 'BTCUSDT'
               AND d.schema_id = 'trade-v1' AND p.partition_key = %s
            """,
            (partition_key,),
        )
        dataset_id, partition_id = cursor.fetchone()
        cursor.execute(
            """
            SELECT q.report_id::text
              FROM catalog.quality_reports AS q
              JOIN catalog.partitions AS p ON p.partition_id = q.partition_id
             WHERE p.partition_id = %s::uuid
             ORDER BY q.report_id::text
            """,
            (partition_id,),
        )
        report_ids = tuple(row[0] for row in cursor.fetchall())
    return {
        "dataset_id": dataset_id,
        "partition_id": partition_id,
        "report_ids": report_ids,
    }


def copy_catalog_artifact(fixture: PublicationFixture, destination_root: Path) -> Path:
    dataset = json.loads(fixture.dataset_manifest_path.read_text(encoding="utf-8"))
    partition = fixture.partition_document
    destination = destination_root / dataset["rel_root"] / partition["rel_path"]
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(fixture.artifact_path, destination)
    return destination


def scenario_m2_rebuild(
    connection_a: Any,
    connection_b: Any,
    root_a: Path,
    root_b: Path,
) -> None:
    fixture = build_publication_fixture(
        root_a,
        partition_key=M2_KEY,
        records=[
            trade("2024-01-24T00:00:01Z", "m2-1"),
            trade("2024-01-24T23:59:59Z", "m2-2", aggressor_side="sell"),
        ],
        coverage_assertions=[(M2_START, M2_END)],
        intent_start=M2_START,
        intent_end=M2_END,
        coverage_id="conformity-m2-rebuild",
        source_extract_detail="durable rebuild evidence",
    )
    copied = copy_catalog_artifact(fixture, root_b)
    require(
        copied.read_bytes() == fixture.artifact_path.read_bytes(),
        "M2 rebuild artifact bytes must be identical",
    )

    publish(connection_a, fixture)
    publish(connection_b, fixture)
    semantic_a = rb1_snapshot(connection_a, M2_KEY)
    semantic_b = rb1_snapshot(connection_b, M2_KEY)
    require(semantic_a == semantic_b, "M2 rebuilt catalog violates RB1 equality")
    require(semantic_a["lineage"] == (), "M2 source-acquired lineage must rebuild to zero edges")

    ids_a = generated_ids(connection_a, M2_KEY)
    ids_b = generated_ids(connection_b, M2_KEY)
    require(ids_a["dataset_id"] != ids_b["dataset_id"], "M2 dataset UUIDs must differ")
    require(ids_a["partition_id"] != ids_b["partition_id"], "M2 partition UUIDs must differ")
    require(
        set(ids_a["report_ids"]).isdisjoint(ids_b["report_ids"]),
        "M2 quality-report UUIDs must differ",
    )

    slice_a = gateway(connection_a).read(request(fixture))
    slice_b = gateway(connection_b).read(request(fixture))
    require(
        slice_a.metadata.request_identity == slice_b.metadata.request_identity,
        "M2 request identity must survive rebuild",
    )
    require(
        slice_a.metadata.result_identity == slice_b.metadata.result_identity,
        "M2 result identity must survive RB1 rebuild",
    )
    print(
        "M2 PASS  catalogs=fresh RB1=equal lineage=zero "
        f"dataset_uuid_a={ids_a['dataset_id']} dataset_uuid_b={ids_b['dataset_id']} "
        f"partition_uuid_a={ids_a['partition_id']} partition_uuid_b={ids_b['partition_id']} "
        f"request_identity={slice_a.metadata.request_identity} "
        f"result_identity={slice_a.metadata.result_identity}"
    )


def scenario_m3_layout(
    connection_a: Any,
    connection_b: Any,
    root_a: Path,
    root_b: Path,
) -> None:
    records = [
        trade("2024-01-26T00:00:01Z", "m3-1", price="101"),
        trade("2024-01-26T12:00:00Z", "m3-2", price="102"),
        trade("2024-01-26T23:59:59Z", "m3-3", price="103"),
    ]
    common = {
        "partition_key": M3_KEY,
        "records": records,
        "coverage_assertions": [(M3_START, M3_END)],
        "intent_start": M3_START,
        "intent_end": M3_END,
        "coverage_id": "conformity-m3-layout",
        "source_extract_detail": "same semantic records under varied layout",
    }
    fixture_a = build_publication_fixture(
        root_a, **common, compression="zstd", row_group_size=1
    )
    fixture_b = build_publication_fixture(
        root_b, **common, compression="snappy", row_group_size=3
    )
    run_a, _ = publish(connection_a, fixture_a)
    run_b, _ = publish(connection_b, fixture_b)

    physical_a = fixture_a.partition_document["sha256"]
    physical_b = fixture_b.partition_document["sha256"]
    canonical_a = run_a.quality_report.metrics["canonical_content_hash_v1"]
    canonical_b = run_b.quality_report.metrics["canonical_content_hash_v1"]
    require(fixture_a.artifact_path.read_bytes() != fixture_b.artifact_path.read_bytes(),
            "M3 Parquet bytes must differ")
    require(physical_a != physical_b, "M3 physical hashes must differ")
    require(canonical_a == canonical_b, "M3 canonical hashes must match")

    slice_a = gateway(connection_a).read(request(fixture_a))
    slice_b = gateway(connection_b).read(request(fixture_b))
    require(tuple(slice_a.records) == tuple(slice_b.records), "M3 records must match")
    require(
        slice_a.metadata.request_identity == slice_b.metadata.request_identity,
        "M3 request identity must match",
    )
    require(
        slice_a.metadata.result_identity != slice_b.metadata.result_identity,
        "M3 result identity must remain physical-evidence-sensitive",
    )
    print(
        "M3 PASS  bytes=different "
        f"physical_a={physical_a} physical_b={physical_b} "
        f"canonical_a={canonical_a} canonical_b={canonical_b} "
        f"result_a={slice_a.metadata.result_identity} "
        f"result_b={slice_b.metadata.result_identity}"
    )


def scenario_m1_overlap(connection: Any, root: Path) -> None:
    left = build_publication_fixture(
        root,
        partition_key=M1_LEFT_KEY,
        records=[trade("2024-01-27T00:30:00Z", "m1-left")],
        coverage_assertions=[(M1_START, M1_LEFT_END)],
        intent_start=M1_START,
        intent_end=M1_LEFT_END,
        coverage_id="conformity-m1-overlap-left",
    )
    right = build_publication_fixture(
        root,
        partition_key=M1_RIGHT_KEY,
        records=[trade("2024-01-27T01:30:00Z", "m1-right")],
        coverage_assertions=[(M1_OVERLAP_START, M1_END)],
        intent_start=M1_OVERLAP_START,
        intent_end=M1_END,
        coverage_id="conformity-m1-overlap-right",
    )
    publish(connection, left)
    publish(connection, right)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.partition_key, p.state
              FROM catalog.partitions AS p
              JOIN catalog.datasets AS d ON d.dataset_id = p.dataset_id
             WHERE d.layer = 'canonical' AND d.kind = 'trades'
               AND d.venue = 'bybit' AND d.instrument = 'BTCUSDT'
               AND d.schema_id = 'trade-v1'
               AND p.partition_key = ANY(%s)
             ORDER BY p.partition_key
            """,
            ([M1_LEFT_KEY, M1_RIGHT_KEY],),
        )
        rows = cursor.fetchall()
    require(
        rows == [(M1_LEFT_KEY, "valid"), (M1_RIGHT_KEY, "valid")],
        f"M1 requires two differently-keyed bridge-published valid rows: {rows}",
    )
    conflict = expect_refusal(
        lambda: gateway(connection).scan(
            request(left, start=M1_START, end=M1_END)
        ),
        CatalogConflict,
        what="M1 real bridge-published overlap read",
    )
    require(
        "unexplained temporal overlap" in str(conflict),
        f"M1 raised the wrong CatalogConflict: {conflict}",
    )
    print(
        "M1 PASS  publication=S13/S14 rows=2 state=valid "
        "overlap=[01:00Z,02:00Z) DataGateway=CatalogConflict"
    )


def scenario_m4_ordering(connection: Any, root: Path) -> None:
    tied_timestamp = "2024-01-28T12:00:00Z"
    source_events = [
        trade(tied_timestamp, "9", price="109"),
        trade(tied_timestamp, "100", price="101"),
        trade(tied_timestamp, "20", price="102"),
    ]
    fixture = build_publication_fixture(
        root,
        partition_key=M4_KEY,
        records=source_events,
        coverage_assertions=[(M4_START, M4_END)],
        intent_start=M4_START,
        intent_end=M4_END,
        coverage_id="conformity-m4-candle-ordering",
    )
    publish(connection, fixture)
    result = gateway(connection).read(request(fixture))
    actual = tuple(result.records)
    expected = tuple(sorted(source_events, key=bybit_trade_v1_ordering_key))
    require(
        ordering_policy_satisfies(
            BYBIT_TRADE_V1_ORDERING_POLICY,
            TRADES_CANONICAL_TOTAL_ORDER_V1,
            (BYBIT_ORDERING_PROVIDER,),
        ),
        "M4 concrete ordering policy must satisfy CandleDefinition source order",
    )
    require(actual == expected, "M4 DataGateway output must equal explicit canonical order")
    require(
        [record.trade_id for record in actual] == ["100", "20", "9"],
        "M4 tied trade_id order must be opaque lexicographic",
    )

    # This is the frozen CandleDefinition proposition, not a Candle runtime:
    # open/close are a direct observation of first/last source-ordered prices.
    candle_open = actual[0].price
    candle_close = actual[-1].price
    expected_open = expected[0].price
    expected_close = expected[-1].price
    require((candle_open, candle_close) == (expected_open, expected_close),
            "M4 OPEN/CLOSE must match explicit canonical ordering")
    require((candle_open, candle_close) == ("101", "109"),
            "M4 pinned OPEN/CLOSE values drifted")
    print(
        "M4 PASS  tied_ids=['100','20','9'] DataGateway=canonical "
        "OPEN=101 CLOSE=109"
    )


def main() -> int:
    dsn_a = os.environ.get("CONFORMITY_EVIDENCE_DSN_A")
    dsn_b = os.environ.get("CONFORMITY_EVIDENCE_DSN_B")
    if not dsn_a or not dsn_b:
        print("POSTGRESQL CONFORMITY EVIDENCE COMPLETION NOT EXECUTED LOCALLY")
        return 0
    if os.name == "nt":
        print("POSTGRESQL CONFORMITY EVIDENCE COMPLETION NOT EXECUTED — POSIX REQUIRED")
        return 0

    with tempfile.TemporaryDirectory() as holder_a, tempfile.TemporaryDirectory() as holder_b:
        root_a = Path(holder_a)
        root_b = Path(holder_b)
        with psycopg.connect(dsn_a) as connection_a, psycopg.connect(dsn_b) as connection_b:
            register_catalog_prerequisites(connection_a, root_a)
            register_catalog_prerequisites(connection_b, root_b)
            scenario_m2_rebuild(connection_a, connection_b, root_a, root_b)
            scenario_m3_layout(connection_a, connection_b, root_a, root_b)
            scenario_m1_overlap(connection_a, root_a)
            scenario_m4_ordering(connection_a, root_a)

    print("PASS conformity evidence completion: M1, M2, M3, M4")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
