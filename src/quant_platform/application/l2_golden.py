"""The ``v1`` golden harness for canonical L2 (A14 closeout evidence).

``run_l2_golden`` proves, on one pinned ``ob200`` archive day, the chain
historical ingest -> materialization -> certification -> catalog publication ->
read, and returns one JSON-able evidence document made of named *claims*:

* measurement (E2-E4) of the real day, read-only;
* ingest (A14d) and event accounting (source events = stored events + excluded);
* reproducibility: a second ingest into a second root is byte-identical;
* (with a catalog DSN) every run is sealed, certified and published ``valid``
  through the unchanged S13/S14 path, then the whole day is read back through
  ``L2DataGateway``: the read events, grouped by partition, hash to each
  partition's ``canonical_content_hash_v1``; the read gaps equal the gap
  documents; the read starts anchored; the count matches; and a bounded
  ``l2_book_events@1`` consumer read of the first minute equals the scan.

Wall-clock fields are isolated under ``timing``; everything else is deterministic.
The catalog must already hold the ``l2-book-event-v1`` schema registration, a
storage root and the raw L2 parent dataset (as for any first publication).
"""

from __future__ import annotations

from bisect import bisect_right
from pathlib import Path
import shutil
import time
from typing import Any

from ..access import Catalog, CoveragePolicy, DataRequest
from ..access.l2_gateway import L2DataGateway
from ..data.l2_parquet import L2ContentHasher
from ..data.l2_publication import L2PublicationCertification
from ..data.models import Instant
from ..data.parquet import resolve_partition_path
from ..data.publication_catalog import CatalogPublicationWriter
from ..data.publication_eligibility import PublicationEligibilityBridge, PublicationEligibilityEvidence
from ..data.publication_eligibility_catalog import PublicationEligibilityCatalog
from ..source_adapters.bybit_l2 import (
    BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
    BYBIT_L2_ORDERING_PROVIDER,
    BybitL2BookEventV1CertificationProfile,
)
from ..source_adapters.bybit_l2_archive import BybitL2ArchiveError
from .bybit_l2_day_evidence import measure_bybit_l2_day
from .bybit_l2_day_ingest import L2_IDENTITY, L2DayIngestResult, L2DayRefusal, ingest_bybit_l2_day
from .l2_market_data import L2_BOOK_EVENTS_KIND, execute_l2_book_events_query, l2_events_anchored
from .market_data import ConsumerMarketDataQuery, RepresentationRef

_DAY_NS = 86_400 * 1_000_000_000
_WINDOW_NS = 60 * 1_000_000_000


def _claim(claims: list[dict[str, Any]], name: str, ok: bool, detail: Any = None) -> bool:
    claims.append({"claim": name, "ok": bool(ok), "detail": detail})
    return bool(ok)


def _tree_bytes(result: L2DayIngestResult) -> list[bytes]:
    files: list[Path] = []
    for partition in result.partitions:
        files += [
            partition.artifact_path,
            partition.partition_manifest_path,
            partition.coverage_manifest_path,
        ]
    files += [gap.coverage_manifest_path for gap in result.gaps]
    files.append(result.dataset_manifest_path)
    return [path.read_bytes() for path in files]


def _ingest_summary(result: L2DayIngestResult) -> dict[str, Any]:
    return {
        "source_sha256": result.source_sha256,
        "source_events": result.source_events,
        "excluded_events": result.excluded_events,
        "already_published": result.already_published,
        "partitions": [
            {
                "partition_key": p.partition_key,
                "rows": p.materialization.row_count,
                "artifact_sha256": p.materialization.sha256,
                "canonical_content_hash_v1": p.materialization.canonical_content_hash_v1,
                "complete_start": p.complete_start,
                "complete_end": p.complete_end,
                "report_outcome": p.report.outcome,
            }
            for p in result.partitions
        ],
        "gaps": [
            {
                "gap_key": g.gap_key, "start": g.start, "end": g.end,
                "excluded_events": g.excluded_events, "detail": g.detail,
            }
            for g in result.gaps
        ],
    }


def run_l2_golden(
    archive_path: str | Path,
    *,
    day: str,
    dataset_root: str | Path,
    created_at: str,
    closed_at: str,
    producer: str,
    code_ref: str,
    dsn: str | None = None,
    storage_root_id: str = "hot",
    reproduce: bool = True,
    path_resolver: Any = None,
    profile: BybitL2BookEventV1CertificationProfile | None = None,
) -> dict[str, Any]:
    """Run the golden chain and return the evidence document (``ok`` is the verdict)."""

    profile = profile or BybitL2BookEventV1CertificationProfile(code_ref)
    dataset_root = Path(dataset_root)
    claims: list[dict[str, Any]] = []
    started = time.monotonic()

    measurement = measure_bybit_l2_day(archive_path, day, profile=profile)
    result = ingest_bybit_l2_day(
        archive_path, day=day, dataset_root=dataset_root, created_at=created_at,
        closed_at=closed_at, producer=producer, code_ref=code_ref,
        storage_root_id=storage_root_id, profile=profile,
    )
    stored = sum(p.materialization.row_count for p in result.partitions)
    _claim(
        claims, "event_accounting",
        measurement["source_events"] == result.source_events == stored + result.excluded_events,
        {"measured": measurement["source_events"], "stored": stored, "excluded": result.excluded_events},
    )
    _claim(
        claims, "partitions_eligible",
        all(p.report.outcome == "eligible" for p in result.partitions),
        [p.report.outcome for p in result.partitions],
    )

    if reproduce:
        repro_root = dataset_root.parent / (dataset_root.name + ".reproduce")
        shutil.rmtree(repro_root, ignore_errors=True)
        again = ingest_bybit_l2_day(
            archive_path, day=day, dataset_root=repro_root, created_at=created_at,
            closed_at=closed_at, producer=producer, code_ref=code_ref,
            storage_root_id=storage_root_id, profile=profile,
        )
        identical = _tree_bytes(result) == _tree_bytes(again)
        _claim(claims, "reproducible_byte_identical", identical, len(_tree_bytes(result)))
        if identical:
            shutil.rmtree(repro_root, ignore_errors=True)

    catalog_section: dict[str, Any] | None = None
    if dsn:
        catalog_section = _catalog_leg(
            claims, result, day, dsn, storage_root_id, path_resolver, profile
        )

    return {
        "ok": all(c["ok"] for c in claims),
        "day": day,
        "claims": claims,
        "measurement": measurement,
        "ingest": _ingest_summary(result),
        "catalog": catalog_section,
        "timing": {"elapsed_seconds": round(time.monotonic() - started, 3)},
    }


def _catalog_leg(claims, result, day, dsn, storage_root_id, path_resolver, profile):
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise RuntimeError("psycopg is required for the catalog leg") from exc

    published = []
    with psycopg.connect(dsn) as connection:
        writer = CatalogPublicationWriter(connection)
        bridge = PublicationEligibilityBridge(PublicationEligibilityCatalog(connection))
        for partition in result.partitions:
            key = partition.partition_key
            try:
                run = L2PublicationCertification(writer, profile).run(partition.evidence)
            except Exception as exc:  # a catalog or evidence refusal is a failed claim, not a crash
                _claim(claims, f"certified_pass:{key}", False, f"{type(exc).__name__}: {exc}")
                return {"published": []}
            if not _claim(claims, f"certified_pass:{key}", run.certification.status == "pass",
                          [v["message"] for v in run.certification.violations]):
                return {"published": []}
            try:
                outcome = bridge.publish(
                    PublicationEligibilityEvidence(
                        partition.dataset_manifest_path, partition.partition_manifest_path,
                        (partition.coverage_manifest_path,), storage_root_id,
                        profile.profile_id, profile.check_suite,
                    )
                )
            except Exception as exc:
                _claim(claims, f"published_valid:{key}", False, f"{type(exc).__name__}: {exc}")
                return {"published": []}
            if not _claim(claims, f"published_valid:{key}", outcome.state == "valid", outcome.state):
                return {"published": []}
            published.append(outcome)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT partition_key, state, row_count, ts_start, ts_end "
                "FROM catalog.partitions WHERE dataset_id=%s ORDER BY partition_key",
                (published[0].dataset_id,),
            )
            rows = [
                {"partition_key": r[0], "state": r[1], "row_count": r[2],
                 "ts_start": Instant.parse(r[3]).isoformat(),
                 "ts_end": Instant.parse(r[4]).isoformat()}
                for r in cursor.fetchall()
            ]

    day_start = Instant.parse(f"{day}T00:00:00Z")
    day_end = Instant(day_start.epoch_ns + _DAY_NS)
    gateway = L2DataGateway(
        Catalog(dsn=dsn),
        path_resolver=path_resolver or resolve_partition_path,
        ordering_providers=(BYBIT_L2_ORDERING_PROVIDER,),
    )
    request = DataRequest(
        L2_IDENTITY, day_start, day_end, schema_requirement="l2-book-event-v1",
        coverage_policy=CoveragePolicy.ALLOW_PARTIAL,
        ordering_policy=BYBIT_L2_BOOK_EVENT_V1_ORDERING_POLICY,
    )
    ranges = sorted(
        (int(p.report.first_event["native_sequence"]), int(p.report.last_event["native_sequence"]),
         p.partition_key)
        for p in result.partitions
    )
    lows = [r[0] for r in ranges]
    hashers = {r[2]: L2ContentHasher() for r in ranges}
    window_start = result.partitions[0].materialization.first_exchange_ts
    window_end = Instant(window_start.epoch_ns + _WINDOW_NS)
    window_count = 0
    total = 0
    first_event = None
    scan = gateway.scan(request)
    misplaced = 0
    for batch in scan:
        for event in batch:
            total += 1
            if first_event is None:
                first_event = event
            index = bisect_right(lows, int(event.native_sequence)) - 1
            if index < 0 or int(event.native_sequence) > ranges[index][1]:
                misplaced += 1
                continue
            hashers[ranges[index][2]].update(event)
            if window_start <= event.exchange_ts < window_end:
                window_count += 1
    metadata = scan.completed_metadata
    _claim(claims, "read_events_in_partition_ranges", misplaced == 0, misplaced)
    _claim(
        claims, "read_hash_equals_partition_hash",
        all(
            hashers[p.partition_key].hexdigest_identity() == p.materialization.canonical_content_hash_v1
            for p in result.partitions
        ),
        {p.partition_key: p.materialization.canonical_content_hash_v1 for p in result.partitions},
    )
    stored_total = sum(p.materialization.row_count for p in result.partitions)
    _claim(claims, "read_event_count", total == stored_total, {"read": total, "stored": stored_total})
    read_gaps = [(g.start.isoformat(), g.end.isoformat()) for g in metadata.coverage_gaps]
    document_gaps = [(g.start, g.end) for g in result.gaps]
    _claim(claims, "read_gaps_equal_gap_documents", read_gaps == document_gaps,
           {"read": read_gaps, "documents": document_gaps})
    _claim(claims, "read_starts_anchored",
           first_event is not None and l2_events_anchored([first_event]))
    consumer = execute_l2_book_events_query(
        ConsumerMarketDataQuery(
            "bybit", "BTCUSDT", window_start, window_end, RepresentationRef(L2_BOOK_EVENTS_KIND, 1)
        ),
        gateway=gateway,
    )
    _claim(claims, "consumer_read_window_equals_scan", consumer.row_count == window_count,
           {"consumer": consumer.row_count, "scan": window_count})
    return {
        "partitions": rows,
        "read": {"events": total, "gaps": [list(g) for g in read_gaps],
                 "consumer_window_events": consumer.row_count},
    }


__all__ = ["BybitL2ArchiveError", "L2DayRefusal", "run_l2_golden"]
