"""One-root filesystem capacity observation for K04.

This module is intentionally observational only.  It measures a caller-supplied
storage root and returns either one complete snapshot or one explicit
unavailable result; it does not discover roots, classify pressure, or mutate
storage.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import shutil
from typing import Any


@dataclass(frozen=True, slots=True)
class StorageRoot:
    """A caller-supplied storage resource descriptor."""

    storage_root_id: str
    root_path: Path

    def __post_init__(self) -> None:
        if not isinstance(self.storage_root_id, str) or not self.storage_root_id.strip():
            raise ValueError("storage_root_id must be a non-empty string")
        try:
            root_path = Path(self.root_path)
        except TypeError as exc:
            raise ValueError("root_path must be path-like") from exc
        object.__setattr__(self, "root_path", root_path)


@dataclass(frozen=True, slots=True)
class CapacityObservation:
    """Exact capacity state for the filesystem containing one storage root."""

    storage_root_id: str
    root_path: Path
    observed_at: datetime
    total_bytes: int
    used_bytes: int
    available_bytes: int


@dataclass(frozen=True, slots=True)
class CapacityUnavailable:
    """Explicit measurement failure for one storage root."""

    storage_root_id: str
    root_path: Path
    reason: str


def observe_capacity(
    storage_root: StorageRoot,
    *,
    _clock: Callable[[], datetime] | None = None,
    _disk_usage: Callable[[Path], Any] | None = None,
) -> CapacityObservation | CapacityUnavailable:
    """Measure the filesystem containing ``storage_root.root_path``.

    ``available_bytes`` is the OS-reported value available to this process.
    ``used_bytes`` is the OS-reported allocated/used value.  The values are
    returned as reported by the filesystem primitive and no
    ``total == used + available`` policy invariant is imposed.
    """

    clock = _clock or _utc_now
    disk_usage = _disk_usage or shutil.disk_usage

    try:
        usage = disk_usage(storage_root.root_path)
        total_bytes = _non_negative_int(usage.total, "total_bytes")
        used_bytes = _non_negative_int(usage.used, "used_bytes")
        available_bytes = _non_negative_int(usage.free, "available_bytes")
    except FileNotFoundError:
        return CapacityUnavailable(
            storage_root.storage_root_id,
            storage_root.root_path,
            "root missing",
        )
    except PermissionError:
        return CapacityUnavailable(
            storage_root.storage_root_id,
            storage_root.root_path,
            "root inaccessible",
        )
    except OSError as exc:
        return CapacityUnavailable(
            storage_root.storage_root_id,
            storage_root.root_path,
            _unavailable_reason(exc),
        )
    except (AttributeError, TypeError, ValueError) as exc:
        return CapacityUnavailable(
            storage_root.storage_root_id,
            storage_root.root_path,
            f"filesystem statistics unavailable: {exc}",
        )

    return CapacityObservation(
        storage_root_id=storage_root.storage_root_id,
        root_path=storage_root.root_path,
        observed_at=_utc_datetime(clock()),
        total_bytes=total_bytes,
        used_bytes=used_bytes,
        available_bytes=available_bytes,
    )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_datetime(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("observed_at clock must return a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _non_negative_int(value: Any, field: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{field} must be an integer")
    if value < 0:
        raise ValueError(f"{field} must be non-negative")
    return value


def _unavailable_reason(exc: OSError) -> str:
    detail = exc.strerror or str(exc)
    if detail:
        return f"filesystem statistics unavailable: {detail}"
    return "filesystem statistics unavailable"
