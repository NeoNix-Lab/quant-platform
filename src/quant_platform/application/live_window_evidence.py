"""Evidence of a continuous live run against the Bybit archive (ADR-0075 slice 9.3, DG-O ``O03``).

Read-only: it reads the catalog and the files through the ``DataGateway``, the archive files and a captured server log, and writes
nothing. The question it answers is the one ADR-0075 asks of the real-provider run: of the trades the provider's archive holds for
the window the run claimed, how many does a ``strict`` gateway read return, and is every one that does not explained by an
interval the run itself declared unclaimed?

* The **windows the run claimed** are the valid live slices of the instrument inside the requested window (a slice is a half-open
  window, ``complete`` over exactly that window). Consecutive slices that chain exactly form a **segment**; a hole between two
  segments is an interval nobody claimed (an interruption with an explicit gap, a midnight with no trade).
* An archive trade **inside a claimed window** that is not read back is a loss the run did not declare: a stop condition. One inside
  a hole is the declared cost of that interruption: reported with the hole, not a failure. A trade read back that the archive does
  not hold is reported (the archive is the provider's later view); one the archive holds with a different payload, or read twice,
  is a stop condition.
* The signals are taken from the captured stdout of ``tools/live_ingest_server.py`` (``signal kind=... k=v,k=v`` lines): the
  counters the session reports (ordering violations, reconnects, buffer high-water mark, slice lag) and the interruptions.

The result states what was measured; it never turns a shortfall into a pass, and it makes no claim about the provider beyond the run.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import re
from statistics import median
from typing import Any

from quant_platform.data import Instant
from quant_platform.data.models import TradeRecord

_NS_PER_DAY = 86_400_000_000_000
_NS_PER_MS = 1_000_000
_EXAMPLES = 5
_READ_CHUNK = 12  # slices per strict read (a K12 window carries at most 16 members)


@dataclass(frozen=True, slots=True)
class SliceWindow:
    """One published live partition: its key, revision, lifecycle state, half-open window and row count."""

    key: str
    revision: int
    state: str
    start: Instant
    end: Instant
    rows: int


def _ms(instant: Instant) -> int:
    return instant.epoch_ns // _NS_PER_MS * _NS_PER_MS


def chain_segments(slices: Sequence[SliceWindow]) -> list[tuple[Instant, Instant, list[SliceWindow]]]:
    """The maximal runs of slices that chain exactly (each starts where the previous ended), by start."""

    segments: list[tuple[Instant, Instant, list[SliceWindow]]] = []
    for item in sorted(slices, key=lambda s: (s.start.epoch_ns, s.end.epoch_ns)):
        if segments and segments[-1][1].epoch_ns == item.start.epoch_ns:
            start, _end, members = segments[-1]
            segments[-1] = (start, item.end, [*members, item])
        else:
            segments.append((item.start, item.end, [item]))
    return segments


def _holes(segments: Sequence[tuple[Instant, Instant, list[SliceWindow]]]) -> list[tuple[Instant, Instant]]:
    return [(a[1], b[0]) for a, b in zip(segments, segments[1:])]


def _field_differences(archive: TradeRecord, read: TradeRecord) -> list[str]:
    """What differs between the archive's and the gateway's view of one trade (the fields the archive carries)."""

    differences = []
    for name in ("venue", "instrument", "aggressor_side"):
        if getattr(archive, name) != getattr(read, name):
            differences.append(name)
    if _ms(archive.exchange_ts) != _ms(read.exchange_ts):
        differences.append("exchange_ts_ms")
    for name in ("price", "size"):
        if Decimal(getattr(archive, name)) != Decimal(getattr(read, name)):
            differences.append(f"{name}_value")
    return differences


_SIGNAL_LINE = re.compile(r"^signal kind=(?P<kind>\S+) capability=\S+ signal_id=\S+(?: (?P<payload>.*))?$")


def _field(payload: str, name: str) -> str | None:
    match = re.search(rf"(?:^|[ ,]){re.escape(name)}=([^,\s]*)", payload)
    return None if match is None else match.group(1)


def summarize_signals(text: str) -> dict[str, Any]:
    """The counters and interruptions of a captured ``tools/live_ingest_server.py`` stdout.

    The session's counters are cumulative, so the largest value seen is the final one; the lag is reported as a maximum."""

    kinds: Counter[str] = Counter()
    phases: Counter[str] = Counter()
    health: Counter[str] = Counter()
    interruptions: Counter[str] = Counter()
    maxima = {"ordering_violations": 0, "reconnects": 0, "buffer_high_water": 0, "buffer_capacity": 0, "slice_lag_ms": 0}
    lags: list[int] = []
    failures: list[str] = []
    final_state: str | None = None
    for line in text.splitlines():
        match = _SIGNAL_LINE.match(line.strip())
        if match is None:
            continue
        kind, payload = match.group("kind"), match.group("payload") or ""
        kinds[kind] += 1
        phase = _field(payload, "phase")
        if phase:
            phases[phase] += 1
        if kind == "HEALTH_SNAPSHOT":
            state = _field(payload, "health")
            if state:
                health[state] += 1
        interruption = _field(payload, "interruption")
        if interruption:
            interruptions[interruption] += 1
        for name in maxima:
            value = _field(payload, name)
            if value is not None and value.lstrip("-").isdigit():
                maxima[name] = max(maxima[name], int(value))
                if name == "slice_lag_ms":
                    lags.append(int(value))
        if kind == "FAILURE":
            failures.append(_field(payload, "failure_code") or "unknown")
        resulting = _field(payload, "resulting_state")
        if resulting:
            final_state = resulting
    return {
        "signals": dict(sorted(kinds.items())),
        "health_snapshots": dict(sorted(health.items())),
        "phases": dict(sorted(phases.items())),
        "interruptions": dict(sorted(interruptions.items())),
        "ordering_violations": maxima["ordering_violations"],
        "reconnects": maxima["reconnects"],
        "buffer_high_water": maxima["buffer_high_water"],
        "buffer_capacity": maxima["buffer_capacity"],
        "slice_lag_ms_max": maxima["slice_lag_ms"],
        "slice_lag_ms_median": int(median(lags)) if lags else 0,
        "failures": failures,
        "final_lifecycle_state": final_state,
    }


def build_window_evidence(
    *,
    instrument: str,
    archive: Iterable[TradeRecord],
    slices: Sequence[SliceWindow],
    read_segment: Callable[[Instant, Instant], Sequence[TradeRecord]],
    window: tuple[Instant, Instant] | None = None,
    signals_text: str | None = None,
    configuration: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare the claimed windows of a run with the archive; ``read_segment(start, end)`` is the ``strict`` gateway read."""

    valid = _claimed(slices, window)
    stops: list[str] = []
    if not valid:
        return {"instrument": instrument, "passed": False, "stop_conditions": ["no valid live slice in the window"],
                "window": None}
    ordered_slices = sorted(valid, key=lambda s: (s.start.epoch_ns, s.end.epoch_ns))
    overlapping = sum(1 for a, b in zip(ordered_slices, ordered_slices[1:]) if b.start.epoch_ns < a.end.epoch_ns)
    if overlapping:  # the catalog admits no two valid partitions of one dataset over the same time (partitions_one_live)
        stops.append(f"{overlapping} valid slices overlap the one before them")
    segments = chain_segments(valid)
    holes = _holes(segments)
    start, end = segments[0][0], segments[-1][1]

    # what the gateway returns for the claimed windows, one strict read per exact chain
    read: list[TradeRecord] = []
    refused: list[str] = []
    for _seg_start, _seg_end, members in segments:
        for first in range(0, len(members), _READ_CHUNK):  # a read is bounded in members (K12): chunks end on slice boundaries
            chunk = members[first:first + _READ_CHUNK]
            a, b = chunk[0].start, chunk[-1].end
            try:
                read.extend(read_segment(a, b))
            except Exception as exc:  # noqa: BLE001 - a refused strict read is a finding of the evidence, never a crash
                refused.append(f"{a.isoformat()}..{b.isoformat()}: {type(exc).__name__}: {exc}")
    if refused:
        stops.append(f"a strict read of a claimed window was refused ({len(refused)})")
    read_ids = Counter(record.trade_id for record in read)
    duplicated = sorted(trade_id for trade_id, count in read_ids.items() if count > 1)
    if duplicated:
        stops.append(f"{len(duplicated)} trades are read more than once")
    read_by_id = {record.trade_id: record for record in read}
    ordered = [(r.exchange_ts.epoch_ns, r.trade_id) for r in read]
    out_of_order = sum(1 for a, b in zip(ordered, ordered[1:]) if b[0] < a[0])
    if out_of_order:
        stops.append(f"the gateway returned {out_of_order} trades out of exchange_ts order")

    low, high = start.epoch_ns, end.epoch_ns
    in_window = {r.trade_id: r for r in archive if low <= r.exchange_ts.epoch_ns < high}
    inside_claimed: list[str] = []
    inside_hole: dict[int, list[str]] = {i: [] for i in range(len(holes))}
    for trade_id, record in in_window.items():
        if trade_id in read_by_id:
            continue
        ts = record.exchange_ts.epoch_ns
        for index, (hole_start, hole_end) in enumerate(holes):
            if hole_start.epoch_ns <= ts < hole_end.epoch_ns:
                inside_hole[index].append(trade_id)
                break
        else:
            inside_claimed.append(trade_id)
    if inside_claimed:
        stops.append(f"{len(inside_claimed)} archive trades inside a claimed window are not read back (an undeclared loss)")
    extra = sorted(trade_id for trade_id in read_by_id if trade_id not in in_window)
    conflicts: dict[str, list[str]] = {}
    for trade_id, record in read_by_id.items():
        if trade_id in in_window:
            differences = _field_differences(in_window[trade_id], record)
            if differences:
                conflicts[trade_id] = differences
    if conflicts:
        stops.append(f"{len(conflicts)} trades differ from the archive")
    captured = len(set(read_by_id) & set(in_window))

    window_ns = high - low
    listened_ns = sum(b - a for a, b in ((s[0].epoch_ns, s[1].epoch_ns) for s in segments))
    row_counts = [s.rows for s in valid]
    lengths = [(s.end.epoch_ns - s.start.epoch_ns) / 1e9 for s in valid]
    report: dict[str, Any] = {
        "instrument": instrument,
        "window": {"start": start.isoformat(), "end": end.isoformat(), "seconds": window_ns / 1e9},
        "configuration": dict(configuration or {}),
        "slices": {
            "valid": len(valid),
            "not_valid_in_catalog": len([s for s in slices if s.state != "valid"]),
            "revisions_above_one": len([s for s in valid if s.revision != 1]),
            "rows_total": sum(row_counts),
            "rows_min": min(row_counts), "rows_median": int(median(row_counts)), "rows_max": max(row_counts),
            "seconds_min": min(lengths), "seconds_median": median(lengths), "seconds_max": max(lengths),
            "chain_segments": len(segments),
        },
        "holes": [
            {"start": a.isoformat(), "end": b.isoformat(), "seconds": (b.epoch_ns - a.epoch_ns) / 1e9,
             "archive_trades_inside": len(inside_hole[i]), "examples": sorted(inside_hole[i])[:_EXAMPLES]}
            for i, (a, b) in enumerate(holes)
        ],
        "claimed_share": listened_ns / window_ns if window_ns else 1.0,
        "archive_trades_in_window": len(in_window),
        "read_through_gateway": len(read),
        "captured_of_archive": captured,
        "captured_ratio": captured / len(in_window) if in_window else 1.0,
        "missing_inside_claimed_windows": len(inside_claimed),
        "missing_inside_unclaimed_spans": sum(len(v) for v in inside_hole.values()),
        "read_not_in_archive": len(extra),
        "examples_missing_inside_claimed_windows": sorted(inside_claimed)[:_EXAMPLES],
        "examples_read_not_in_archive": extra[:_EXAMPLES],
        "conflicts": {k: conflicts[k] for k in sorted(conflicts)[:_EXAMPLES]},
        "strict_reads_refused": refused,
        "duplicated_reads": duplicated[:_EXAMPLES],
        "stop_conditions": stops,
        "passed": not stops,
    }
    if signals_text is not None:
        report["signals"] = summarize_signals(signals_text)
    return report


_ARCHIVE_NAME = re.compile(r"^(?P<instrument>[A-Z0-9]+)(?P<day>\d{4}-\d{2}-\d{2})\.csv\.gz$")


def archive_days_needed(window_start: Instant, window_end: Instant) -> list[str]:
    """The UTC days (``YYYY-MM-DD``) a half-open window touches; each needs its archive file."""

    first = window_start.epoch_ns // _NS_PER_DAY
    last = (window_end.epoch_ns - 1) // _NS_PER_DAY
    return [Instant(day * _NS_PER_DAY).isoformat()[:10] for day in range(first, last + 1)]


def parse_window(start: str | None, end: str | None) -> tuple[Instant, Instant] | None:
    """The requested window from two ISO instants, or ``None`` when neither is given."""

    if start is None and end is None:
        return None
    if start is None or end is None:
        raise ValueError("a window needs both a start and an end")
    low, high = Instant.parse(start), Instant.parse(end)
    if not low.epoch_ns < high.epoch_ns:
        raise ValueError("the window start must be before its end")
    return low, high


def archive_days_missing(
    slices: Sequence[SliceWindow], window: tuple[Instant, Instant] | None, available: Iterable[str],
) -> tuple[list[str], list[str]]:
    """``(days the claimed windows touch, those without an archive file)``; both empty when no valid slice is in the window."""

    valid = _claimed(slices, window)
    if not valid:
        return [], []
    needed = archive_days_needed(min(s.start for s in valid), max(s.end for s in valid))
    have = set(available)
    return needed, [day for day in needed if day not in have]


def _claimed(slices: Sequence[SliceWindow], window: tuple[Instant, Instant] | None) -> list[SliceWindow]:
    valid = [s for s in slices if s.state == "valid"]
    if window is not None:
        valid = [s for s in valid if s.start.epoch_ns >= window[0].epoch_ns and s.end.epoch_ns <= window[1].epoch_ns]
    return valid


def load_archive_days(paths: Iterable[str | Path], *, instrument: str) -> dict[str, list[TradeRecord]]:
    """``day -> records`` of local ``<INSTRUMENT><YYYY-MM-DD>.csv.gz`` files (the day is read from the file name)."""

    from quant_platform.source_adapters.bybit_archive import read_bybit_archive_day

    days: dict[str, list[TradeRecord]] = {}
    for path in paths:
        match = _ARCHIVE_NAME.match(Path(path).name)
        if match is None or match.group("instrument") != instrument:
            raise ValueError(f"{Path(path).name!r} is not a {instrument}<YYYY-MM-DD>.csv.gz archive file")
        days[match.group("day")] = list(read_bybit_archive_day(path, instrument=instrument, day=match.group("day")).records)
    return days


# ---------------------------------------------------------------------------------------------------- the real catalog


def read_live_slices(dsn: str | None, instrument: str) -> list[SliceWindow]:
    """Every live-slice partition (``dt=<day>/seg=<time>``) of the instrument, by window start, whatever its state."""

    import psycopg

    with (psycopg.connect(dsn) if dsn else psycopg.connect()) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT p.partition_key, p.revision, p.state, p.ts_start, p.ts_end, p.row_count FROM catalog.partitions p "
            "JOIN catalog.datasets d ON d.dataset_id = p.dataset_id WHERE d.instrument = %s AND p.partition_key LIKE %s "
            "ORDER BY p.ts_start, p.revision",
            (instrument, "dt=%/seg=%"),
        )
        rows = cursor.fetchall()
        connection.rollback()
    return [SliceWindow(key, int(revision), state, Instant.parse(a), Instant.parse(b), int(count))
            for key, revision, state, a, b, count in rows]


def gateway_strict_reader(dsn: str | None, instrument: str):
    """A ``read_segment`` that reads through the real ``DataGateway`` (``VALID_ONLY``, ``strict`` coverage) and a connection to close."""

    import psycopg

    from quant_platform.access.catalog import Catalog
    from quant_platform.access.gateway import DataGateway
    from quant_platform.access.models import DataRequest, LifecyclePolicy
    from quant_platform.source_adapters.bybit import BYBIT_ORDERING_PROVIDER
    from quant_platform.source_adapters.bybit_live import bybit_live_dataset_identity

    connection = psycopg.connect(dsn) if dsn else psycopg.connect()
    gateway = DataGateway(Catalog(connection=connection), ordering_providers=(BYBIT_ORDERING_PROVIDER,))
    identity = bybit_live_dataset_identity(instrument)

    def read_segment(start: Instant, end: Instant) -> Sequence[TradeRecord]:
        try:
            return gateway.read(DataRequest(
                identity, start, end, schema_requirement="trade-v1", lifecycle_policy=LifecyclePolicy.VALID_ONLY,
                coverage_policy="strict", ordering_policy=BYBIT_ORDERING_PROVIDER.identity)).records
        finally:
            connection.rollback()

    return read_segment, connection


__all__ = ["SliceWindow", "archive_days_missing", "archive_days_needed", "parse_window", "build_window_evidence", "chain_segments", "gateway_strict_reader",
           "load_archive_days", "read_live_slices", "summarize_signals"]
