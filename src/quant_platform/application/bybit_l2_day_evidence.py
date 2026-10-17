"""Read-only measurement of one Bybit ``ob200`` archive day (A14 closeout evidence).

It closes the open evidence items of ADR-0070 on a *real* day: E2 (does ``seq``
survive a provider restart: sequence regressions and snapshots whose ``u`` goes
backwards), E3 (the delete-of-absent-level rate) and E4 (mid-day re-snapshots and
discontinuities).  One streaming pass through the A14a certifier; nothing is
written.  Wall-clock figures are isolated under ``timing``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import time
from typing import Any

from ..data.models import DatasetIdentity, Instant, L2BookEvent
from ..source_adapters.bybit_l2 import BybitL2BookEventV1CertificationProfile
from ..source_adapters.bybit_l2_archive import (
    BybitL2ArchiveError,
    archive_sha256,
    check_archive_name,
    iter_bybit_l2_archive_events,
)

_IDENTITY = DatasetIdentity("canonical", "l2", "bybit", "BTCUSDT", "l2-book-event-v1")
_DAY_NS = 86_400 * 1_000_000_000
_SAMPLES = 10


def measure_bybit_l2_day(
    archive_path: str | Path,
    day: str,
    *,
    profile: BybitL2BookEventV1CertificationProfile | None = None,
) -> dict[str, Any]:
    """Measure one archive day.  Raises :class:`BybitL2ArchiveError` on a bad archive."""

    profile = profile or BybitL2BookEventV1CertificationProfile("l2-day-evidence")
    path = Path(archive_path)
    check_archive_name(path, day)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise BybitL2ArchiveError("archive_unreadable", "cannot stat the archive file") from exc
    sha = archive_sha256(path)
    day_start = Instant.parse(f"{day}T00:00:00Z").epoch_ns
    day_end = day_start + _DAY_NS

    stats = {"snapshots": 0, "snapshots_with_u_1": 0, "u_backwards_snapshots": 0, "outside_day": 0}
    previous_u: list[int] = []

    def tap() -> Iterator[L2BookEvent]:
        for event in iter_bybit_l2_archive_events(path, day):
            if not (day_start <= event.exchange_ts.epoch_ns < day_end):
                stats["outside_day"] += 1
            if event.event_type == "snapshot":
                stats["snapshots"] += 1
                u = int(event.native_update_id)
                if u == 1:
                    stats["snapshots_with_u_1"] += 1
                if previous_u and u <= previous_u[0] and stats["snapshots"] > 1:
                    stats["u_backwards_snapshots"] += 1
            previous_u[:] = [int(event.native_update_id)]
            yield event

    started = time.monotonic()
    report = profile.certify(_IDENTITY, tap())
    elapsed = time.monotonic() - started
    document = report.stable_dict()
    return {
        "archive": {"name": path.name, "bytes": size, "sha256": sha},
        "day": day,
        "events": report.event_count,
        "outcome": report.outcome,
        "violation_counts": document["violation_counts"],
        "violation_samples": document["violations"][:5],
        "first_event": document["first_event"],
        "last_event": document["last_event"],
        "snapshots": stats["snapshots"],
        "snapshots_with_u_1": stats["snapshots_with_u_1"],
        "u_backwards_snapshots": stats["u_backwards_snapshots"],
        "events_outside_day": stats["outside_day"],
        "segments": len(document["segments"]),
        "unanchored_head": document["unanchored_head"],
        "unanchored_events": document["unanchored_event_count"],
        "discontinuities": {
            "count": len(document["discontinuities"]),
            "samples": document["discontinuities"][:_SAMPLES],
        },
        "deletes_of_absent": document["deletes_of_absent"],
        "crossed_states": document["crossed_states"],
        "parameters": document["parameters"],
        "timing": {"elapsed_seconds": round(elapsed, 3)},
    }


__all__ = ["measure_bybit_l2_day"]
