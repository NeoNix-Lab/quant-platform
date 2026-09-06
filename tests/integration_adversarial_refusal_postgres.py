#!/usr/bin/env python3
"""Adversarial publication refusal acceptance against real PostgreSQL 17.

Two scenarios from PRODUCER_CONSUMER_CONFORMITY.md §15.3, proved at the
producer/consumer seam rather than at the component level:

  A2  Bybit first-vertical eligibility refusal (§10 EP4, §13.2 Canonical,
      §13.4).  A partition carrying one ``trade_id = null`` record stays
      generically ``trade-v1``-representable, fails S13 certification, is
      refused by the S14 eligibility bridge, never leaves ``closed``, and is
      invisible to a default ``VALID_ONLY`` DataGateway read.

  A3  Gap / non-contiguous declared coverage refusal (DECLARED_COVERAGE I9,
      §14.3 BC8).  A partition whose attributed ``complete`` coverage folds to
      two intervals is refused by the publication chain and leaves the scoped
      catalog state semantically unchanged -- no partial row, no lineage edge,
      no quality evidence.

The existing fold-level, materializer-level and mock-catalog proofs are not
re-created here; this file adds only the seam evidence those cannot give.
Every publication step runs through the real production owners.

Set ``DATA_GATEWAY_TEST_DSN`` to a disposable PostgreSQL 17 catalog freshly
initialized from ``db/init/001_catalog.sql``.  Like the sibling publication
integration scripts, the assertions are exact row and evidence counts, so the
script is single-shot against one fresh catalog rather than re-runnable in
place.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import psycopg  # noqa: E402
from jsonschema import Draft202012Validator, FormatChecker  # noqa: E402

from adversarial_support import (  # noqa: E402
    CANONICAL_IDENTITY,
    build_publication_fixture,
    certification_runtime,
    diff_catalog_state,
    eligibility_bridge,
    expect_refusal,
    gateway,
    nullable_trade_id_ordering_provider,
    register_catalog_prerequisites,
    request,
    scoped_catalog_state,
    trade,
)
from quant_platform.access.catalog import Catalog  # noqa: E402
from quant_platform.data import NoCoverage  # noqa: E402
from quant_platform.data.parquet import read_trade_v1  # noqa: E402
from quant_platform.data.coverage import reconstruct_catalog_coverage  # noqa: E402
from quant_platform.data.publication import PublicationCertificationError  # noqa: E402
from quant_platform.data.publication_eligibility import (  # noqa: E402
    PublicationEligibilityRefusal,
)


TRADE_V1_SCHEMA = ROOT / "schemas" / "trade-v1.json"

A2_KEY = "dt=2024-01-15"
A2_START = "2024-01-15T00:00:00Z"
A2_END = "2024-01-16T00:00:00Z"

A3_KEY = "dt=2024-01-17"
A3_START = "2024-01-17T00:00:00Z"
A3_GAP_START = "2024-01-17T10:00:00Z"
A3_GAP_END = "2024-01-17T11:00:00Z"
A3_END = "2024-01-18T00:00:00Z"

EXPECTED_COVERAGE_VIOLATION = "COVERAGE_NOT_CONTIGUOUS"
EVIDENCE_CATEGORIES = ("source", "canonical", "physical", "manifests", "coverage")


def require(condition: bool, what: str) -> None:
    if not condition:
        raise AssertionError(what)


def record_document(record) -> dict:
    """Project a canonical record back to its frozen ``trade-v1`` document."""

    return {
        "venue": record.venue,
        "instrument": record.instrument,
        "exchange_ts": record.exchange_ts.isoformat(),
        "price": record.price,
        "size": record.size,
        "aggressor_side": record.aggressor_side,
        "receive_ts": None if record.receive_ts is None else record.receive_ts.isoformat(),
        "trade_id": record.trade_id,
        "sequence": record.sequence,
    }


def partition_rows(connection, partition_key: str) -> list[tuple]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT p.state, p.row_count
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


def report_statuses(connection, partition_key: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT q.status
              FROM catalog.quality_reports AS q
              JOIN catalog.partitions AS p ON p.partition_id = q.partition_id
              JOIN catalog.datasets AS d ON d.dataset_id = p.dataset_id
             WHERE d.layer = 'canonical' AND d.kind = 'trades'
               AND d.venue = 'bybit' AND d.instrument = 'BTCUSDT'
               AND d.schema_id = 'trade-v1' AND p.partition_key = %s
             ORDER BY q.status
            """,
            (partition_key,),
        )
        return [row[0] for row in cursor.fetchall()]


def scenario_a2(connection, root: Path) -> None:
    """A2 — a null ``trade_id`` partition never becomes eligible data."""

    fixture = build_publication_fixture(
        root,
        partition_key=A2_KEY,
        records=[
            trade("2024-01-15T00:00:01Z", "1"),
            trade("2024-01-15T00:00:02Z", None, aggressor_side="sell"),
            trade("2024-01-15T00:00:03Z", "3"),
        ],
        coverage_assertions=[(A2_START, A2_END)],
        intent_start=A2_START,
        intent_end=A2_END,
        ordering_provider=nullable_trade_id_ordering_provider(),
        source_extract_detail="adversarial null trade_id extract",
    )

    # EP4 clause 1: the artifact remains representable by generic trade-v1.
    # The violation being proved is the Bybit eligibility profile, not the
    # frozen schema's nullability.
    validator = Draft202012Validator(
        json.loads(TRADE_V1_SCHEMA.read_text(encoding="utf-8")),
        format_checker=FormatChecker(),
    )
    materialized = read_trade_v1(fixture.artifact_path, None, None)
    require(len(materialized) == 3, "A2 fixture must materialize three records")
    require(
        sum(1 for item in materialized if item.trade_id is None) == 1,
        "A2 fixture must carry exactly one null trade_id record",
    )
    for record in materialized:
        errors = sorted(validator.iter_errors(record_document(record)), key=str)
        require(
            not errors,
            f"A2 fixture record is not generic trade-v1 valid: {errors[:1]}",
        )

    before = scoped_catalog_state(connection, CANONICAL_IDENTITY, [A2_KEY])

    runtime, profile = certification_runtime(connection)
    run = runtime.run(fixture.sealed_evidence())

    statuses = {item.category: item.status for item in run.certification.categories}
    require(
        set(statuses) == set(EVIDENCE_CATEGORIES) | {"publication"},
        f"A2 certification must evaluate all five categories, saw {sorted(statuses)}",
    )
    require(
        statuses["publication"] == "deferred",
        "A2 S13 must defer eligibility to S14, never decide it",
    )
    # The eligibility profile is enforced while the certifier reads the
    # artifact under the profile's own ordering key, so EP1 surfaces in the
    # physical category and canonical cannot then run.  §13.4 groups both as
    # the same refusal-to-publish class.  The fixture must break nothing else:
    # a source, manifest or coverage failure would prove the wrong rule.
    for name in ("source", "manifests", "coverage"):
        require(
            statuses[name] == "pass",
            f"A2 {name} evidence must pass, observed {statuses[name]!r}",
        )
    failed = {name for name in EVIDENCE_CATEGORIES if statuses[name] == "fail"}
    require(
        failed == {"physical", "canonical"},
        f"A2 must fail only on the eligibility-profile axis, failed {sorted(failed)}",
    )
    require(
        run.certification.status == "fail",
        f"A2 S13 status must be fail, observed {run.certification.status!r}",
    )
    require(
        any(
            "trade_id" in str(item.get("message", ""))
            for item in run.certification.violations
        ),
        f"A2 refusal must be attributable to trade_id, observed {run.certification.violations}",
    )
    require(
        run.quality_report is not None and run.quality_report.status == "fail",
        "A2 S13 must record durable fail evidence",
    )
    require(
        run.sealed_partition.state == "closed",
        f"A2 sealed partition must stay closed, observed {run.sealed_partition.state!r}",
    )

    rows = partition_rows(connection, A2_KEY)
    require(rows == [("closed", 3)], f"A2 catalog row must be closed/3, observed {rows}")
    require(
        report_statuses(connection, A2_KEY) == ["fail"],
        "A2 must leave exactly one durable fail report and no pass report",
    )

    # S13 Phases 1-3 legitimately write the dataset row, the closed partition
    # row and the fail evidence.  S14 must then add nothing at all.
    after_s13 = scoped_catalog_state(connection, CANONICAL_IDENTITY, [A2_KEY])
    changed = diff_catalog_state(before, after_s13)
    require(
        changed == ("datasets", "partitions", "quality_reports"),
        f"A2 S13 footprint must be exactly seal plus evidence, changed {changed}",
    )

    refusal = expect_refusal(
        lambda: eligibility_bridge(connection).publish(
            fixture.eligibility_evidence(profile)
        ),
        PublicationEligibilityRefusal,
        what="A2 S14 publication eligibility",
    )

    after = scoped_catalog_state(connection, CANONICAL_IDENTITY, [A2_KEY])
    changed = diff_catalog_state(after_s13, after)
    require(
        changed == (),
        f"A2 S14 refusal must not mutate scoped catalog state, changed {changed}",
    )
    rows = partition_rows(connection, A2_KEY)
    require(
        rows == [("closed", 3)],
        f"A2 partition must remain closed after S14 refusal, observed {rows}",
    )
    require(
        report_statuses(connection, A2_KEY) == ["fail"],
        "A2 must have no pass report authorizing promotion after S14",
    )
    require(
        after["dataset_lineage"] == [],
        f"A2 source-acquired dataset must carry zero lineage, observed {after['dataset_lineage']}",
    )

    # The row exists and is selectable under an explicit opt-in; the default
    # VALID_ONLY policy is what excludes it.  Both halves are needed, or the
    # refusal could be an accident of an absent row.
    catalog = Catalog(connection=connection)
    dataset = catalog.resolve_dataset(CANONICAL_IDENTITY)
    valid_only = request(fixture)
    start, end = valid_only.start, valid_only.end
    require(
        catalog.select_partitions(dataset, start, end, ("valid",)) == [],
        "A2 partition must not be selectable under VALID_ONLY",
    )
    opted_in = catalog.select_partitions(dataset, start, end, ("valid", "closed"))
    require(
        len(opted_in) == 1 and opted_in[0].state == "closed",
        "A2 partition must be selectable only under an explicit closed opt-in",
    )

    gateway_refusal = expect_refusal(
        lambda: gateway(connection).read(valid_only),
        NoCoverage,
        what="A2 DataGateway VALID_ONLY read",
    )

    print(
        f"A2 PASS  S13=fail{sorted(failed)} state=closed reports=['fail'] lineage=0 "
        f"S14={type(refusal).__name__} gateway={type(gateway_refusal).__name__}"
    )


def scenario_a3(connection, root: Path) -> None:
    """A3 — non-contiguous declared coverage refuses without partial publication."""

    fixture = build_publication_fixture(
        root,
        partition_key=A3_KEY,
        records=[
            trade("2024-01-17T00:00:01Z", "1"),
            trade("2024-01-17T09:59:59Z", "2", aggressor_side="sell"),
        ],
        coverage_assertions=[(A3_START, A3_GAP_START), (A3_GAP_END, A3_END)],
        intent_start=A3_START,
        intent_end=A3_END,
        source_extract_detail="adversarial non-contiguous extract",
    )

    # Fixture precondition, not the proof: the document set must break exactly
    # I9 and nothing else, or the seam refusal below would prove the wrong rule.
    folded, violations = reconstruct_catalog_coverage(
        fixture.coverage_documents, (fixture.partition_document,)
    )
    codes = sorted({item.code for item in violations})
    require(
        codes == [EXPECTED_COVERAGE_VIOLATION],
        f"A3 fixture must raise exactly {EXPECTED_COVERAGE_VIOLATION}, observed {codes}",
    )
    require(
        (A3_KEY, fixture.revision) not in folded,
        "A3 fixture must not fold to a publishable interval",
    )

    before = scoped_catalog_state(connection, CANONICAL_IDENTITY, [A2_KEY, A3_KEY])
    require(
        partition_rows(connection, A3_KEY) == [],
        "A3 must start with no catalog row for its partition key",
    )

    runtime, profile = certification_runtime(connection)
    s13_refusal = expect_refusal(
        lambda: runtime.run(fixture.sealed_evidence()),
        PublicationCertificationError,
        what="A3 S13 certification",
    )
    require(
        EXPECTED_COVERAGE_VIOLATION in str(s13_refusal),
        f"A3 S13 refusal must name {EXPECTED_COVERAGE_VIOLATION}, observed {s13_refusal}",
    )

    after_s13 = scoped_catalog_state(connection, CANONICAL_IDENTITY, [A2_KEY, A3_KEY])
    changed = diff_catalog_state(before, after_s13)
    require(
        changed == (),
        f"A3 S13 refusal must not mutate scoped catalog state, changed {changed}",
    )

    s14_refusal = expect_refusal(
        lambda: eligibility_bridge(connection).publish(
            fixture.eligibility_evidence(profile)
        ),
        PublicationEligibilityRefusal,
        what="A3 S14 publication eligibility",
    )
    require(
        EXPECTED_COVERAGE_VIOLATION in str(s14_refusal),
        f"A3 S14 refusal must name {EXPECTED_COVERAGE_VIOLATION}, observed {s14_refusal}",
    )

    after_s14 = scoped_catalog_state(connection, CANONICAL_IDENTITY, [A2_KEY, A3_KEY])
    changed = diff_catalog_state(before, after_s14)
    require(
        changed == (),
        f"A3 S14 refusal must not mutate scoped catalog state, changed {changed}",
    )
    require(
        partition_rows(connection, A3_KEY) == [],
        "A3 must publish no partition row at all",
    )
    require(
        report_statuses(connection, A3_KEY) == [],
        "A3 must record no quality evidence for an unpublished partition",
    )
    require(
        after_s14["dataset_lineage"] == [],
        f"A3 must add no lineage edge, observed {after_s14['dataset_lineage']}",
    )

    gateway_refusal = expect_refusal(
        lambda: gateway(connection).read(request(fixture)),
        NoCoverage,
        what="A3 DataGateway VALID_ONLY read",
    )

    print(
        f"A3 PASS  fold={EXPECTED_COVERAGE_VIOLATION} "
        f"S13={type(s13_refusal).__name__} S14={type(s14_refusal).__name__} "
        f"rows=0 reports=0 lineage=0 gateway={type(gateway_refusal).__name__}"
    )


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("POSTGRESQL ADVERSARIAL REFUSAL ACCEPTANCE NOT EXECUTED LOCALLY")
        return 0
    with tempfile.TemporaryDirectory() as holder:
        root = Path(holder)
        with psycopg.connect(dsn) as connection:
            register_catalog_prerequisites(connection, root)
            scenario_a2(connection, root)
            scenario_a3(connection, root)
    print(
        "PASS PostgreSQL 17 adversarial publication refusal: "
        "A2 Bybit eligibility refusal, A3 gap/non-contiguous refusal"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
