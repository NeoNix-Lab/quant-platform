"""Bybit public L2 order-book archive reader (A14d, ADR-0070 Amendment 1).

One archive day is ``<day>_BTCUSDT_ob200.data.zip``: a zip (written as a stream,
so the local header carries sizes of ``0`` and a data descriptor follows the
data; the central directory is authoritative) with exactly one member
``<day>_BTCUSDT_ob200.data``, JSON lines in the live WebSocket message shape.

The reader streams: the zip member is decompressed incrementally and one line at
a time is normalized with the A14a adapter (``acquisition_mode =
historical_archive``, ``exchange_ts = cts``).  A day is never materialized.
Failures are typed with a stable ``code``.
"""

from __future__ import annotations

from collections.abc import Iterator
import hashlib
import json
from pathlib import Path
import re
import zipfile

from ..data.models import InvalidRequest, L2BookEvent
from .l2 import L2SourceError, normalize_bybit_l2_message

BYBIT_L2_ARCHIVE_DEPTH = 200
BYBIT_L2_ARCHIVE_SYMBOL = "BTCUSDT"
BYBIT_L2_ARCHIVE_TOPIC = f"orderbook.{BYBIT_L2_ARCHIVE_DEPTH}.{BYBIT_L2_ARCHIVE_SYMBOL}"

_NAME = re.compile(r"^(?P<day>\d{4}-\d{2}-\d{2})_(?P<symbol>[A-Z0-9]+)_ob(?P<depth>\d+)\.data\.zip$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_READ_CHUNK = 1024 * 1024


class BybitL2ArchiveError(ValueError):
    """A typed archive refusal; ``code`` is stable, ``line`` is 1-based when known."""

    def __init__(self, code: str, message: str, *, line: int | None = None):
        self.code = code
        self.line = line
        suffix = f" [line {line}]" if line is not None else ""
        super().__init__(f"{code}: {message}{suffix}")


def archive_file_name(day: str) -> str:
    return f"{day}_{BYBIT_L2_ARCHIVE_SYMBOL}_ob{BYBIT_L2_ARCHIVE_DEPTH}.data.zip"


def archive_member_name(day: str) -> str:
    return f"{day}_{BYBIT_L2_ARCHIVE_SYMBOL}_ob{BYBIT_L2_ARCHIVE_DEPTH}.data"


def check_archive_name(path: str | Path, day: str) -> None:
    """The file name must be exactly the pinned ``ob200`` day of ``day``."""

    if not isinstance(day, str) or not _DAY.fullmatch(day):
        raise BybitL2ArchiveError("archive_name_mismatch", "day must be YYYY-MM-DD")
    name = Path(path).name
    match = _NAME.fullmatch(name)
    if match is None:
        raise BybitL2ArchiveError("archive_name_mismatch", f"not a Bybit L2 archive name: {name!r}")
    if int(match["depth"]) != BYBIT_L2_ARCHIVE_DEPTH:
        raise BybitL2ArchiveError(
            "source_depth_unsupported",
            f"only depth {BYBIT_L2_ARCHIVE_DEPTH} is supported, the archive is ob{match['depth']}",
        )
    if match["symbol"] != BYBIT_L2_ARCHIVE_SYMBOL or match["day"] != day:
        raise BybitL2ArchiveError(
            "archive_name_mismatch", f"archive {name!r} is not the {BYBIT_L2_ARCHIVE_SYMBOL} day {day}"
        )


def archive_sha256(path: str | Path) -> str:
    """SHA-256 of the exact archive bytes, streamed."""

    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(_READ_CHUNK), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BybitL2ArchiveError("archive_unreadable", "cannot read the archive file") from exc
    return digest.hexdigest()


def iter_bybit_l2_archive_events(path: str | Path, day: str) -> Iterator[L2BookEvent]:
    """Stream the normalized events of one archive day, in source order."""

    check_archive_name(path, day)
    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise BybitL2ArchiveError("archive_unreadable", "not a readable zip archive") from exc
    with archive:
        names = archive.namelist()
        if names != [archive_member_name(day)]:
            raise BybitL2ArchiveError(
                "archive_unreadable",
                f"the archive must hold exactly the member {archive_member_name(day)!r}",
            )
        try:
            member = archive.open(names[0])
        except (OSError, zipfile.BadZipFile, NotImplementedError, RuntimeError) as exc:
            raise BybitL2ArchiveError("archive_unreadable", "cannot open the archive member") from exc
        with member:
            line_number = 0
            pending = b""
            try:
                while True:
                    chunk = member.read(_READ_CHUNK)
                    if not chunk:
                        break
                    pending += chunk
                    *lines, pending = pending.split(b"\n")
                    for raw in lines:
                        line_number += 1
                        event = _event(raw, line_number)
                        if event is not None:
                            yield event
                if pending.strip():
                    line_number += 1
                    event = _event(pending, line_number)
                    if event is not None:
                        yield event
            except (zipfile.BadZipFile, OSError, EOFError) as exc:
                raise BybitL2ArchiveError(
                    "archive_unreadable", "the archive member is corrupt or truncated"
                ) from exc


def _event(raw: bytes, line_number: int) -> L2BookEvent | None:
    text = raw.strip()
    if not text:
        return None
    try:
        message = json.loads(text.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BybitL2ArchiveError("archive_record_invalid", "not a JSON line", line=line_number) from exc
    if not isinstance(message, dict):
        raise BybitL2ArchiveError("archive_record_invalid", "a line is not a JSON object", line=line_number)
    topic = message.get("topic")
    if isinstance(topic, str) and topic != BYBIT_L2_ARCHIVE_TOPIC:
        parts = topic.split(".")
        if len(parts) == 3 and parts[0] == "orderbook" and parts[1] != str(BYBIT_L2_ARCHIVE_DEPTH):
            raise BybitL2ArchiveError(
                "source_depth_unsupported",
                f"only {BYBIT_L2_ARCHIVE_TOPIC} is supported, the line is {topic}",
                line=line_number,
            )
        raise BybitL2ArchiveError(
            "archive_record_invalid", f"unexpected topic {topic!r}", line=line_number
        )
    try:
        return normalize_bybit_l2_message(message, acquisition_mode="historical_archive")
    except (L2SourceError, InvalidRequest, ValueError) as exc:
        reason = getattr(exc, "reason", None) or "invalid"
        raise BybitL2ArchiveError(
            "archive_record_invalid", f"the message is not a valid L2 event ({reason})", line=line_number
        ) from exc


__all__ = [
    "BYBIT_L2_ARCHIVE_DEPTH",
    "BYBIT_L2_ARCHIVE_TOPIC",
    "BybitL2ArchiveError",
    "archive_file_name",
    "archive_member_name",
    "archive_sha256",
    "check_archive_name",
    "iter_bybit_l2_archive_events",
]
