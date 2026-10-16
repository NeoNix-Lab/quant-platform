#!/usr/bin/env python3
"""Real PostgreSQL proof for the L2 DataGateway read path (A14e).

Two runs of one source day (cut at a ``u`` discontinuity, ADR-0070 Amendment 1) are
sealed, certified and published ``valid`` through the unchanged S13/S14 path with
the A14c certifier, then read through ``L2DataGateway`` over the real catalog and
through the ``l2_book_events@1`` consumer read.  Run it on a fresh disposable
database::

    DATA_GATEWAY_TEST_DSN="host=127.0.0.1 port=55441 dbname=<fresh> user=postgres" \\
        python tests/integration_l2_gateway_postgres.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import psycopg  # noqa: E402

from bootstrap_schema_registry import (  # noqa: E402
    bootstrap_schema_registry,
    read_schema_registration,
)
from quant_platform.access import Catalog, CoveragePolicy, DataRequest  # noqa: E402
from quant_platform.access.l2_gateway import L2DataGateway  # noqa: E402
from quant_platform.application.api_transport_server import encode_consumer_result  # noqa: E402
from quant_platform.application.l2_market_data import (  # noqa: E402
    L2_BOOK_EVENTS_KIND,
    execute_l2_book_events_query,
    l2_events_anchored,
)
from quant_platform.application.market_data import (  # noqa: E402
    ConsumerApiError,
    ConsumerMarketDataQuery,
    RepresentationRef,
)
from quant_platform.data import DatasetIdentity, Instant  # noqa: E402
from quant_platform.data.l2_materializer import materialize_l2_book_event_v1  # noqa: E402
from quant_platform.data.l2_publication import L2PublicationCertification  # noqa: E402
from quant_platform.data.manifests import (  # noqa: E402
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
)
from quant_platform.data.models import L2BookEvent, L2LevelChange, NoCoverage  # noqa: E402
from quant_platform.data.publication import SealedPartitionEvidence  # noqa: E402
from quant_platform.data.publication_catalog import CatalogPublicationWriter  # noqa: E402
from quant_platform.data.publication_eligibility import (  # noqa: E402
    PublicationEligibilityBridge,
    PublicationEligibilityEvidence,
)
from quant_platform.data.publication_eligibility_catalog import (  # noqa: E402
    PublicationEligibilityCatalog,
)
from quant_platform.source_adapters.bybit_l2 import (  # noqa: E402
    BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
    BYBIT_L2_COVERAGE_BASIS_ARCHIVE,
    BYBIT_L2_COVERAGE_MAPPING_ARCHIVE_DAY_V1,
    BYBIT_L2_COVERAGE_SOURCE_SEMANTICS,
    BYBIT_L2_ORDERING_PROVIDER,
    BybitL2BookEventV1CertificationProfile,
    bybit_l2_continuity_evidence_detail,
)

IDENTITY = DatasetIdentity("canonical", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
RAW = DatasetIdentity("raw", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
DAY, NEXT = "2024-01-15", "2024-01-16"
BASE = Instant.parse(f"{DAY}T00:00:01Z").epoch_ns


def up(side, price, size="1"):
    return L2LevelChange(side, price, size, "upsert")


def event(seq, u, kind="delta", bids=(), asks=()):
    t = BASE + seq * 1_000_000  # millisecond resolution, like Bybit cts
    return L2BookEvent(
        venue="bybit", market_type="linear_perp", instrument="BTCUSDT", native_symbol="BTCUSDT",
        source_channel="orderbook.200.BTCUSDT", acquisition_mode="historical_archive",
        event_type=kind, exchange_ts=Instant(t), provider_ts=Instant(t + 2_000_000),
        source_depth_limit=200, native_sequence=str(seq), native_update_id=str(u),
        bids=bids or (up("bid", "100.0", str(seq)),), asks=asks,
    )


def snap(seq, u):
    return event(seq, u, "snapshot", bids=(up("bid", "100.0", "5"),), asks=(up("ask", "101.0", "4"),))


RUN1 = [snap(1, 10), event(2, 11), event(3, 12)]
RUN2 = [snap(6, 1), event(7, 2), event(8, 3)]  # events 4-5 (u 50, 51) are the excluded interval


def ceil_us(ns):
    return (ns // 1000 + 1) * 1000


def floor_us(ns):
    return ns // 1000 * 1000


def iso(ns):
    return Instant(ns).isoformat()


def main() -> int:
    dsn = os.environ.get("DATA_GATEWAY_TEST_DSN")
    if not dsn:
        print("SKIP PostgreSQL L2 gateway integration: DATA_GATEWAY_TEST_DSN is unset")
        return 0
    profile = BybitL2BookEventV1CertificationProfile("integration-l2-gateway-certifier")
    gap_start = ceil_us(RUN1[-1].exchange_ts.epoch_ns)
    gap_end = floor_us(RUN2[0].exchange_ts.epoch_ns)
    intervals = [(f"{DAY}T00:00:00Z", iso(gap_start)), (iso(gap_end), f"{NEXT}T00:00:00Z")]
    with tempfile.TemporaryDirectory() as holder:
        root = Path(holder)
        dataset_path = root / "dataset.json"
        emit_dataset_manifest(
            dataset_path, dataset_identity=IDENTITY, created_at="2026-10-16T10:00:00Z",
            derived_from=[RAW], transform="canonicalize-l2-book-events-v1",
        )
        acquisition = {
            "basis": BYBIT_L2_COVERAGE_BASIS_ARCHIVE,
            "intent_start": f"{DAY}T00:00:00Z", "intent_end": f"{NEXT}T00:00:00Z",
            "source_semantics": BYBIT_L2_COVERAGE_SOURCE_SEMANTICS,
            "mapping": BYBIT_L2_COVERAGE_MAPPING_ARCHIVE_DAY_V1,
        }
        evidences = []
        for n, (run, (start, end)) in enumerate(zip((RUN1, RUN2), intervals), 1):
            key = f"dt={DAY}/seg={n:03d}"
            artifact = root / key / "part-001.parquet"
            report = profile.certify(IDENTITY, run)
            assert report.outcome == "eligible"
            materialization = materialize_l2_book_event_v1(
                artifact, run, dataset_identity=IDENTITY, ordering_provider=BYBIT_L2_ORDERING_PROVIDER
            )
            partition_path = root / f"partition-{n}.json"
            partition = emit_partition_manifest(
                partition_path, materialization, dataset_identity=IDENTITY, dataset_root=root,
                partition_key=key, revision=1, rel_path=f"{key}/part-001.parquet",
                created_at="2026-10-16T10:00:00Z", closed_at="2026-10-16T10:00:01Z",
                producer="integration-l2-producer", code_ref="integration-l2-producer-commit",
            ).document
            coverage_path = root / f"coverage-{n}.json"
            emit_coverage_manifest(
                coverage_path, dataset_identity=IDENTITY, source_dataset_identity=IDENTITY,
                coverage_id=f"integration-l2-gateway-cov-{n}", supersedes=None,
                created_at="2026-10-16T10:00:02Z", acquisition=acquisition,
                assertions=[{
                    "assertion_id": f"integration-l2-gateway-seg-{n}", "start": start, "end": end,
                    "status": "complete", "partitions": [{"partition_key": key, "revision": 1}],
                    "evidence": [
                        {"kind": "archive_completeness", "detail": "integration archive day"},
                        {"kind": "sequence_continuity", "detail": bybit_l2_continuity_evidence_detail(report)},
                    ],
                }],
                producer="integration-l2-source", code_ref="integration-l2-source-commit",
                partition_manifests=[partition],
            )
            evidences.append((
                SealedPartitionEvidence(dataset_path, partition_path, (coverage_path,), artifact, "l2-hot"),
                partition_path, coverage_path,
            ))

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
                    "VALUES ('l2-hot','/srv/l2-integration-hot','hot') ON CONFLICT DO NOTHING"
                )
                cursor.execute(
                    "INSERT INTO catalog.datasets (layer,kind,venue,instrument,rel_root,schema_id,manifest_sha256) "
                    "VALUES ('raw','l2','bybit','BTCUSDT','raw/l2/bybit/BTCUSDT/l2-book-event-v1',"
                    "'l2-book-event-v1',%s) ON CONFLICT DO NOTHING", ("1" * 64,),
                )
            connection.commit()

            writer = CatalogPublicationWriter(connection)
            bridge = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection))
            for evidence, partition_path, coverage_path in evidences:
                run = L2PublicationCertification(writer, profile).run(evidence)
                assert run.certification.status == "pass", run.certification.violations
                result = bridge.publish(
                    PublicationEligibilityEvidence(
                        dataset_path, partition_path, (coverage_path,), "l2-hot",
                        profile.profile_id, profile.check_suite,
                    )
                )
                assert result.state == "valid", result

        # The catalog stores a POSIX placeholder storage root; the artifacts live in a
        # temporary directory, so the physical path is resolved by an injected resolver
        # (the read path itself is the real gateway, catalog and Parquet reader).
        def resolver(storage_root, dataset_rel_root, rel_path):
            return root / Path(*rel_path.split("/"))

        gateway = L2DataGateway(
            Catalog(dsn=dsn), path_resolver=resolver, ordering_providers=(BYBIT_L2_ORDERING_PROVIDER,)
        )
        window = (Instant.parse(f"{DAY}T00:00:00Z"), Instant.parse(f"{NEXT}T00:00:00Z"))
        request = DataRequest(
            IDENTITY, *window, schema_requirement="l2-book-event-v1",
            coverage_policy=CoveragePolicy.ALLOW_PARTIAL,
            ordering_policy=BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
        )
        sliced = gateway.read(request)
        assert [e.native_sequence for e in sliced.records] == ["1", "2", "3", "6", "7", "8"]
        assert [e.stable_dict() for e in sliced.records] == [e.stable_dict() for e in RUN1 + RUN2]
        assert not sliced.metadata.coverage_complete
        assert [(g.start.epoch_ns, g.end.epoch_ns) for g in sliced.metadata.coverage_gaps] == [
            (gap_start, gap_end)
        ]
        assert [p.partition_key for p in sliced.metadata.natural_partitions] == [
            f"dt={DAY}/seg=001", f"dt={DAY}/seg=002"
        ]

        # strict coverage refuses the read across the gap; a read inside the gap has no coverage
        try:
            gateway.read(DataRequest(
                IDENTITY, *window, schema_requirement="l2-book-event-v1",
                coverage_policy=CoveragePolicy.STRICT,
                ordering_policy=BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
            ))
        except NoCoverage:
            pass
        else:
            raise AssertionError("strict coverage accepted a read across the gap")
        try:
            gateway.read(DataRequest(
                IDENTITY, Instant(gap_start), Instant(gap_end), schema_requirement="l2-book-event-v1",
                ordering_policy=BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
            ))
        except NoCoverage:
            pass
        else:
            raise AssertionError("a read wholly inside the gap had coverage")

        # the consumer read, end to end, with the J02 encoding
        query = ConsumerMarketDataQuery(
            "bybit", "BTCUSDT", window[0], window[1], RepresentationRef(L2_BOOK_EVENTS_KIND, 1)
        )
        result = execute_l2_book_events_query(query, gateway=gateway)
        assert result.row_count == 6 and not result.coverage.complete
        assert len(result.coverage.gaps) == 1
        assert l2_events_anchored(result.data)
        wire = json.loads(json.dumps(encode_consumer_result(result)))
        assert wire["data"][3] == RUN2[0].stable_dict()
        assert wire["representation"] == {"kind": L2_BOOK_EVENTS_KIND, "version": 1}
        mid = execute_l2_book_events_query(
            ConsumerMarketDataQuery(
                "bybit", "BTCUSDT", Instant(BASE + 2_000_000), Instant(gap_start),
                RepresentationRef(L2_BOOK_EVENTS_KIND, 1),
            ),
            gateway=gateway,
        )
        assert [e.native_sequence for e in mid.data] == ["2", "3"]
        assert not l2_events_anchored(mid.data) and mid.coverage.complete
        try:
            execute_l2_book_events_query(query, gateway=gateway, max_result_bytes=10)
        except ConsumerApiError as error:
            assert error.code.value == "result_too_large"
        else:
            raise AssertionError("the byte bound did not refuse")

    print("POSTGRESQL L2 GATEWAY GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
