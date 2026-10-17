"""Bybit historical L2 day ingest (A14d, ADR-0070 Amendments 1-2).

One pinned ``ob200`` archive day becomes canonical L2 partitions, cut at the ``u``
discontinuities the A14a certifier reports:

* **Pass 1** routes contiguous source-file head/tail blocks outside the UTC day
  to retained-source evidence, then streams the in-day events through A14a.
  Its report fixes the cut points; nothing else defines them.
* **Pass 2** streams the archive once more and demultiplexes it into one
  ``materialize_l2_book_event_v1`` call per run (``dt=YYYY-MM-DD/seg=NNN``).  Events
  in a ``book_state_unknown`` interval, and before the first snapshot, are excluded
  from the canonical layer; they stay in the retained source archive and are
  counted, with its SHA-256, in the gap evidence.
* **Coverage** is one document per run (``complete``) and one per gap
  (``known_gap``), at a 1 microsecond unit (``coverage-manifest-v1`` carries at most
  six fractional digits).

The day is atomic: everything is staged and the day directory is moved into place
only when the whole day succeeded.  The caller publishes with the unchanged S13/S14
path using the ready ``SealedPartitionEvidence`` of each run.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterator
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil

from ..canonical import canonical_bytes
from ..data.l2_materializer import materialize_l2_book_event_v1
from ..data.l2_parquet import scan_l2_book_event_v1_all
from ..data.manifests import (
    emit_coverage_manifest,
    emit_dataset_manifest,
    emit_partition_manifest,
)
from ..data.materializer import ParquetMaterialization
from ..data.models import DatasetIdentity, Instant, L2BookEvent
from ..data.publication import SealedPartitionEvidence
from ..source_adapters.bybit_l2 import (
    BYBIT_L2_COVERAGE_BASIS_ARCHIVE,
    BYBIT_L2_COVERAGE_MAPPING_ARCHIVE_DAY_V1,
    BYBIT_L2_COVERAGE_SOURCE_SEMANTICS,
    BYBIT_L2_ORDERING_PROVIDER,
    BybitL2BookEventV1CertificationProfile,
    L2CertificationReport,
    OUTCOME_ELIGIBLE,
    OUTCOME_INELIGIBLE,
    bybit_l2_continuity_evidence_detail,
)
from ..source_adapters.bybit_l2_archive import (
    BybitL2ArchiveError,
    archive_file_name,
    archive_sha256,
    check_archive_name,
    iter_bybit_l2_archive_events,
)

L2_IDENTITY = DatasetIdentity("canonical", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
L2_RAW_IDENTITY = DatasetIdentity("raw", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
L2_DATASET_TRANSFORM = "canonicalize-l2-book-events-v1"
_DAY_NS = 86_400 * 1_000_000_000


class L2DayRefusal(Exception):
    """A typed refusal of a whole day; nothing is published and nothing is left behind."""

    def __init__(self, code: str, message: str, *, context: dict | None = None):
        self.code = code
        self.context = context or {}
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True, slots=True)
class L2RunPartition:
    partition_key: str
    artifact_path: Path
    partition_manifest_path: Path
    coverage_manifest_path: Path
    dataset_manifest_path: Path
    storage_root_id: str
    materialization: ParquetMaterialization
    report: L2CertificationReport
    complete_start: str
    complete_end: str

    @property
    def evidence(self) -> SealedPartitionEvidence:
        """Ready evidence for the unchanged S13/S14 path."""

        return SealedPartitionEvidence(
            self.dataset_manifest_path,
            self.partition_manifest_path,
            (self.coverage_manifest_path,),
            self.artifact_path,
            self.storage_root_id,
        )


@dataclass(frozen=True, slots=True)
class L2GapDocument:
    gap_key: str
    coverage_manifest_path: Path
    start: str
    end: str
    excluded_events: int
    detail: str


@dataclass(frozen=True, slots=True)
class L2DayIngestResult:
    day: str
    source_sha256: str
    source_events: int
    excluded_events: int
    partitions: tuple[L2RunPartition, ...]
    gaps: tuple[L2GapDocument, ...]
    dataset_manifest_path: Path
    already_published: bool


@dataclass(frozen=True, slots=True)
class _Cut:
    """A closed-open sequence range of excluded events."""

    low: int
    high: int | None  # exclusive; None = to the end of the day
    head: bool
    expected_u: int | None
    observed_u: int | None
    certified_events: int  # the certifier's own count, cross-checked in pass 2


@dataclass(slots=True)
class _Run:
    ordinal: int
    key: str
    materialization: ParquetMaterialization
    report: L2CertificationReport
    partition_manifest: Path


@dataclass(slots=True)
class _SourceBlock:
    """Bounded evidence for an excluded source edge, not a retained event list."""

    count: int = 0
    first: dict | None = None
    last: dict | None = None
    last_snapshot_repeats_previous: bool = False

    def record(self, event: L2BookEvent, previous: L2BookEvent | None) -> None:
        point = {
            "native_sequence": event.native_sequence,
            "native_update_id": event.native_update_id,
            "exchange_ts": event.exchange_ts.isoformat(),
        }
        if not self.count:
            self.first = point
        self.last = point
        self.count += 1
        self.last_snapshot_repeats_previous = (
            event.event_type == "snapshot" and previous is not None
            and event.native_sequence == previous.native_sequence
            and event.native_update_id == previous.native_update_id
        )

    def detail(self) -> dict:
        return {
            "count": self.count, "first": self.first, "last": self.last,
            "last_snapshot_repeats_previous": self.last_snapshot_repeats_previous,
        }


@dataclass(slots=True)
class _DayRouting:
    start: int
    end: int
    source_events: int = 0
    head: _SourceBlock = field(default_factory=_SourceBlock)
    tail: _SourceBlock = field(default_factory=_SourceBlock)

    def events(self, source: Iterator[L2BookEvent]) -> Iterator[L2BookEvent]:
        phase = 0  # head -> in-day -> tail; source order is never sorted/repaired
        previous = None
        for event in source:
            ts = event.exchange_ts.epoch_ns
            region = 0 if ts < self.start else (2 if ts >= self.end else 1)
            edge_regression = (
                previous is not None and ts < previous.exchange_ts.epoch_ns
                and (region != 1 or phase != 1)
            )
            if region < phase or edge_regression:
                raise L2DayRefusal(
                    "event_outside_day", "out-of-day events must be monotone source-edge blocks",
                    context={"native_sequence": event.native_sequence, "exchange_ts": _iso(ts)},
                )
            phase = region
            self.source_events += 1
            if region == 0:
                self.head.record(event, previous)
            elif region == 2:
                self.tail.record(event, previous)
            else:
                yield event  # A14a still owns in-day seq/u/time/replay certification.
            previous = event

    def detail(self) -> str:
        edges = {name: block.detail() for name, block in (("head", self.head), ("tail", self.tail))
                 if block.count}
        if not edges:
            return ""
        encoded = canonical_bytes(edges, profile="sorted-compact-ascii-v1", allow_nan=False).decode("utf-8")
        return f"|source_file_edges:{encoded}"


def _ceil_us(ns: int) -> int:
    """Least microsecond-aligned instant strictly greater than ``ns``."""

    return (ns // 1000 + 1) * 1000


def _floor_us(ns: int) -> int:
    return ns // 1000 * 1000


def _iso(ns: int) -> str:
    return Instant(ns).isoformat()


class _Peekable:
    def __init__(self, iterator: Iterator[L2BookEvent]) -> None:
        self._iterator = iterator
        self._head: L2BookEvent | None = None
        self._has_head = False

    def peek(self) -> L2BookEvent | None:
        if not self._has_head:
            self._head = next(self._iterator, None)
            self._has_head = True
        return self._head

    def take(self) -> L2BookEvent:
        event = self.peek()
        if event is None:
            raise StopIteration
        self._has_head = False
        self._head = None
        return event


def ingest_bybit_l2_day(
    archive_path: str | Path,
    *,
    day: str,
    dataset_root: str | Path,
    created_at: str,
    closed_at: str,
    producer: str,
    code_ref: str,
    storage_root_id: str = "hot",
    profile: BybitL2BookEventV1CertificationProfile | None = None,
    cleanup_source: bool = False,
) -> L2DayIngestResult:
    """Ingest one pinned ``ob200`` archive day.  See the module docstring."""

    profile = profile or BybitL2BookEventV1CertificationProfile(code_ref)
    archive_path = Path(archive_path)
    dataset_root = Path(dataset_root)
    check_archive_name(archive_path, day)
    source_sha = archive_sha256(archive_path)
    intent_start = Instant.parse(f"{day}T00:00:00Z").epoch_ns
    intent_end = intent_start + _DAY_NS

    # ---- pass 1: route file edges, certify only the owning UTC day's input ---
    routing = _DayRouting(intent_start, intent_end)
    report = profile.certify(
        L2_IDENTITY, routing.events(iter_bybit_l2_archive_events(archive_path, day))
    )
    if report.outcome == OUTCOME_INELIGIBLE:
        raise L2DayRefusal(
            "source_day_ineligible",
            "the source day violates the L2 certification rules",
            context={"violation_counts": dict(report.violation_counts)},
        )
    if report.event_count == 0 or not any(segment.anchored for segment in report.segments):
        raise L2DayRefusal("no_anchored_run", "the day has no snapshot-rooted run")

    cuts: list[_Cut] = []
    if report.unanchored_head:
        head = report.segments[0]
        cuts.append(_Cut(head.first_sequence, head.last_sequence + 1, True, None, None, head.event_count))
    for item in report.discontinuities:
        cuts.append(
            _Cut(
                item.observed_sequence, item.resumes_at_sequence, False,
                item.expected_update_id, item.observed_update_id, item.unanchored_event_count,
            )
        )
    lows = [cut.low for cut in cuts]
    discontinuities = [cut for cut in cuts if not cut.head]
    open_end = bool(discontinuities) and discontinuities[-1].high is None
    run_count = len(discontinuities) if open_end else len(discontinuities) + 1

    def cut_of(event: L2BookEvent) -> int | None:
        seq = int(event.native_sequence)
        index = bisect_right(lows, seq) - 1
        if index >= 0 and (cuts[index].high is None or seq < cuts[index].high):
            return index
        return None

    staging = dataset_root / f".ingest-{day}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        second = _DayRouting(intent_start, intent_end)
        runs, counts = _pass_two(
            second.events(iter_bybit_l2_archive_events(archive_path, day)),
            day, staging, profile, cut_of, cuts, run_count,
            created_at, closed_at, producer, code_ref,
        )
        if second.source_events != routing.source_events or second.detail() != routing.detail():
            raise L2DayRefusal("plan_mismatch", "source-edge evidence changed between passes")
        complete, gaps = _plan_intervals(
            runs, cuts, intent_start, intent_end, has_tail=bool(routing.tail.count)
        )
        gap_documents = _emit_coverage(
            staging, day, runs, complete, gaps, counts, cuts, source_sha,
            created_at, producer, code_ref, intent_start, intent_end, routing,
        )
        result = _install(
            staging, dataset_root, day, runs, complete, gap_documents, source_sha,
            routing, counts, storage_root_id, created_at,
        )
        if cleanup_source:
            Path(archive_path).unlink(missing_ok=True)
        return result
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _pass_two(events, day, staging, profile, cut_of, cuts, run_count,
              created_at, closed_at, producer, code_ref):
    source = _Peekable(events)
    counts = [0] * len(cuts)
    runs: list[_Run] = []

    def skip_excluded() -> None:
        while True:
            event = source.peek()
            if event is None:
                return
            index = cut_of(event)
            if index is None:
                return
            counts[index] += 1
            source.take()

    def take_run() -> Iterator[L2BookEvent]:
        while True:
            event = source.peek()
            if event is None or cut_of(event) is not None:
                return
            yield source.take()

    for ordinal in range(1, run_count + 1):
        skip_excluded()
        if source.peek() is None:
            break
        key = f"dt={day}/seg={ordinal:03d}"
        rel_path = f"{key}/part-001.parquet"
        artifact = staging / Path(*rel_path.split("/"))
        materialization = materialize_l2_book_event_v1(
            artifact, take_run(), dataset_identity=L2_IDENTITY,
            ordering_provider=BYBIT_L2_ORDERING_PROVIDER,
        )
        stored = profile.certify(
            L2_IDENTITY, (e for batch in scan_l2_book_event_v1_all(artifact) for e in batch)
        )
        if stored.outcome != OUTCOME_ELIGIBLE:
            raise L2DayRefusal(
                "plan_mismatch", f"run {key} is not one clean continuity segment once stored",
                context={"outcome": stored.outcome},
            )
        partition_manifest = staging / key / "partition-manifest.json"
        emit_partition_manifest(
            partition_manifest, materialization, dataset_identity=L2_IDENTITY, dataset_root=staging,
            partition_key=key, revision=1, rel_path=rel_path, created_at=created_at,
            closed_at=closed_at, producer=producer, code_ref=code_ref,
        )
        runs.append(_Run(ordinal, key, materialization, stored, partition_manifest))
    skip_excluded()  # the open gap at the end of the day
    if source.peek() is not None or len(runs) != run_count:
        raise L2DayRefusal(
            "plan_mismatch", "the second pass disagrees with the certifier's cut plan",
            context={"runs": len(runs), "expected_runs": run_count},
        )
    for cut, counted in zip(cuts, counts):
        if cut.certified_events != counted:
            raise L2DayRefusal(
                "plan_mismatch", "an excluded interval differs from the certifier's count",
                context={"certified": cut.certified_events, "counted": counted},
            )
    return runs, counts


def _plan_intervals(runs, cuts, intent_start, intent_end, *, has_tail=False):
    """Per-run coverage with explicit source-file head/tail gaps (Amendment 2)."""

    head = bool(cuts) and cuts[0].head
    discontinuity_cuts = [(i, cut) for i, cut in enumerate(cuts) if not cut.head]
    first = [run.materialization.first_exchange_ts.epoch_ns for run in runs]
    last = [run.materialization.last_exchange_ts.epoch_ns for run in runs]
    if first[0] % 1000:
        raise L2DayRefusal("discontinuity_not_representable", "source head is not microsecond-aligned")
    complete: list[tuple[int, int]] = []
    for n in range(len(runs)):
        start = first[n] if n == 0 else _floor_us(first[n])
        end = _ceil_us(last[n]) if n < len(discontinuity_cuts) or not has_tail else intent_end
        complete.append((start, end))
    gaps: list[tuple[int, int, int | str]] = []  # cut index, or source-file edge cause
    if head or first[0] > intent_start:
        gaps.append((intent_start, first[0], 0 if head else "source_file_head"))
    for n, (index, cut) in enumerate(discontinuity_cuts):
        end = intent_end if cut.high is None else _floor_us(first[n + 1])
        gaps.append((_ceil_us(last[n]), end, index))
    open_gap = bool(discontinuity_cuts) and discontinuity_cuts[-1][1].high is None
    if not has_tail and not open_gap and complete[-1][1] < intent_end:
        gaps.append((complete[-1][1], intent_end, "source_file_tail"))
    for start, end, _ in gaps:
        if start >= end:
            raise L2DayRefusal(
                "discontinuity_not_representable",
                "a gap interval is empty at the 1 microsecond coverage resolution",
                context={"start": _iso(start), "end": _iso(end)},
            )
    return complete, gaps


def _gap_detail(cut: _Cut, excluded: int, source_sha: str) -> str:
    if cut.head:
        return f"unanchored_head|excluded_events:{excluded}|source_sha256:{source_sha}"
    return (
        f"expected_u:{cut.expected_u}|observed_u:{cut.observed_u}"
        f"|excluded_events:{excluded}|source_sha256:{source_sha}"
    )


def _emit_coverage(staging, day, runs, complete, gaps, counts, cuts, source_sha,
                   created_at, producer, code_ref, intent_start, intent_end, routing):
    acquisition = {
        "basis": BYBIT_L2_COVERAGE_BASIS_ARCHIVE,
        "intent_start": _iso(intent_start),
        "intent_end": _iso(intent_end),
        "source_semantics": BYBIT_L2_COVERAGE_SOURCE_SEMANTICS,
        "mapping": BYBIT_L2_COVERAGE_MAPPING_ARCHIVE_DAY_V1,
    }
    for run, (start, end) in zip(runs, complete):
        partition = json.loads(run.partition_manifest.read_text(encoding="utf-8"))
        emit_coverage_manifest(
            staging / run.key / "coverage-manifest.json",
            dataset_identity=L2_IDENTITY, source_dataset_identity=L2_IDENTITY,
            coverage_id=f"l2-{day}-seg-{run.ordinal:03d}", supersedes=None, created_at=created_at,
            acquisition=acquisition,
            assertions=[{
                "assertion_id": f"seg-{run.ordinal:03d}",
                "start": _iso(start), "end": _iso(end), "status": "complete",
                "partitions": [{"partition_key": run.key, "revision": 1}],
                "evidence": [
                    {"kind": "archive_completeness",
                     "detail": f"source_archive:{archive_file_name(day)}|sha256:{source_sha}" + routing.detail()},
                    {"kind": "sequence_continuity",
                     "detail": bybit_l2_continuity_evidence_detail(run.report)},
                ],
            }],
            producer=producer, code_ref=code_ref, partition_manifests=[partition],
        )
    documents = []
    for number, (start, end, index) in enumerate(gaps, 1):
        excluded = counts[index] if isinstance(index, int) else 0
        if index == "source_file_head":
            detail = f"source_file_head|first_event:{_iso(end)}|source_sha256:{source_sha}"
        elif index == "source_file_tail":
            last = runs[-1].materialization.last_exchange_ts.isoformat()
            detail = f"source_file_tail|last_event:{last}|source_sha256:{source_sha}"
        else:
            detail = _gap_detail(cuts[index], excluded, source_sha)
            if cuts[index].head:
                detail += f"|source_file_head|first_event:{_iso(end)}"
        path = staging / f"dt={day}" / f"gap={number:03d}" / "coverage-manifest.json"
        emit_coverage_manifest(
            path, dataset_identity=L2_IDENTITY, source_dataset_identity=L2_IDENTITY,
            coverage_id=f"l2-{day}-gap-{number:03d}", supersedes=None, created_at=created_at,
            acquisition=acquisition,
            assertions=[{
                "assertion_id": f"gap-{number:03d}", "start": _iso(start), "end": _iso(end),
                "status": "known_gap", "partitions": [],
                "evidence": [{"kind": "sequence_discontinuity", "detail": detail}],
            }],
            producer=producer, code_ref=code_ref,
        )
        documents.append((f"gap={number:03d}", _iso(start), _iso(end), excluded, detail))
    return documents


def _install(staging, dataset_root, day, runs, complete, gap_documents, source_sha,
             routing, counts, storage_root_id, created_at):
    final_day = dataset_root / f"dt={day}"
    dataset_manifest = dataset_root / "dataset-manifest.json"
    already = False
    if final_day.exists():
        existing = {}
        for manifest in sorted(final_day.glob("seg=*/partition-manifest.json")):
            document = json.loads(manifest.read_text(encoding="utf-8"))
            existing[document["partition_key"]] = (document["sha256"], document["row_count"])
        produced = {
            run.key: (run.materialization.sha256, run.materialization.row_count) for run in runs
        }
        if existing != produced:
            raise L2DayRefusal(
                "day_already_published_differently",
                f"dt={day} exists with different partitions",
                context={"existing": sorted(existing), "produced": sorted(produced)},
            )
        # Equal rows cannot silently widen coverage or replace source-edge evidence.
        old_coverage = {p.relative_to(final_day): json.loads(p.read_text(encoding="utf-8"))
                        for p in final_day.glob("*/coverage-manifest.json")}
        new_day = staging / f"dt={day}"
        new_coverage = {p.relative_to(new_day): json.loads(p.read_text(encoding="utf-8"))
                        for p in new_day.glob("*/coverage-manifest.json")}
        if ({k: (d["acquisition"], d["assertions"]) for k, d in old_coverage.items()}
                != {k: (d["acquisition"], d["assertions"]) for k, d in new_coverage.items()}):
            raise L2DayRefusal("day_already_published_differently", "declared coverage or source evidence differs")
        already = True
        shutil.rmtree(staging, ignore_errors=True)
    else:
        dataset_root.mkdir(parents=True, exist_ok=True)
        os.replace(staging / f"dt={day}", final_day)
        shutil.rmtree(staging, ignore_errors=True)
    if not dataset_manifest.exists():
        emit_dataset_manifest(
            dataset_manifest, dataset_identity=L2_IDENTITY, created_at=created_at,
            derived_from=[L2_RAW_IDENTITY], transform=L2_DATASET_TRANSFORM,
        )
    partitions = tuple(
        L2RunPartition(
            partition_key=run.key,
            artifact_path=dataset_root / Path(*run.key.split("/")) / "part-001.parquet",
            partition_manifest_path=dataset_root / Path(*run.key.split("/")) / "partition-manifest.json",
            coverage_manifest_path=dataset_root / Path(*run.key.split("/")) / "coverage-manifest.json",
            dataset_manifest_path=dataset_manifest,
            storage_root_id=storage_root_id,
            materialization=run.materialization,
            report=run.report,
            complete_start=_iso(start),
            complete_end=_iso(end),
        )
        for run, (start, end) in zip(runs, complete)
    )
    gaps = tuple(
        L2GapDocument(
            gap_key=key, coverage_manifest_path=final_day / key / "coverage-manifest.json",
            start=start, end=end, excluded_events=excluded, detail=detail,
        )
        for key, start, end, excluded, detail in gap_documents
    )
    return L2DayIngestResult(
        day=day, source_sha256=source_sha, source_events=routing.source_events,
        excluded_events=sum(counts) + routing.head.count + routing.tail.count,
        partitions=partitions, gaps=gaps,
        dataset_manifest_path=dataset_manifest, already_published=already,
    )


__all__ = [
    "BybitL2ArchiveError",
    "L2DayIngestResult",
    "L2DayRefusal",
    "L2GapDocument",
    "L2RunPartition",
    "ingest_bybit_l2_day",
]
