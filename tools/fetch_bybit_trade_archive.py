#!/usr/bin/env python3
"""Download Bybit public trade archive days into a staging directory (operator helper, ADR-0074 Amendment 1 A1.2 item 1).

Outside the producer path: it only fetches ``https://public.bybit.com/trading/<INSTRUMENT>/<INSTRUMENT><YYYY-MM-DD>.csv.gz`` and
writes ``<dest>/<INSTRUMENT><YYYY-MM-DD>.csv.gz`` atomically (``.part`` then rename). An existing file is kept, not re-fetched. A day
the archive does not have (HTTP 404) is reported as ``absent``: it is a gap, never invented. One JSON line per day.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.bybit_archive_import import (  # noqa: E402
    BYBIT_LINEAR_TRADE_INSTRUMENTS_V1,
    bybit_archive_file_name,
    bybit_archive_url,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_day(instrument: str, day: str, dest: Path, *, timeout: float = 120.0) -> dict[str, object]:
    target = dest / bybit_archive_file_name(instrument, day)
    if target.exists():
        return {"day": day, "status": "present", "file": str(target), "sha256": _sha256(target),
                "bytes": target.stat().st_size}
    partial = target.with_name(target.name + ".part")
    try:
        with urllib.request.urlopen(bybit_archive_url(instrument, day), timeout=timeout) as response, partial.open("wb") as out:
            for chunk in iter(lambda: response.read(1 << 20), b""):
                out.write(chunk)
    except urllib.error.HTTPError as exc:
        partial.unlink(missing_ok=True)
        if exc.code == 404:
            return {"day": day, "status": "absent"}
        return {"day": day, "status": "error", "detail": f"HTTP {exc.code}"}
    except (OSError, urllib.error.URLError) as exc:
        partial.unlink(missing_ok=True)
        return {"day": day, "status": "error", "detail": str(exc)}
    os.replace(partial, target)
    return {"day": day, "status": "fetched", "file": str(target), "sha256": _sha256(target), "bytes": target.stat().st_size}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--instrument", required=True, choices=BYBIT_LINEAR_TRADE_INSTRUMENTS_V1)
    parser.add_argument("--from", dest="first", required=True, type=date.fromisoformat, help="first UTC day, YYYY-MM-DD")
    parser.add_argument("--to", dest="last", required=True, type=date.fromisoformat, help="last UTC day, inclusive")
    parser.add_argument("--dest", required=True, type=Path, help="staging directory")
    arguments = parser.parse_args(argv)
    if arguments.last < arguments.first:
        print("error: --to is before --from", file=sys.stderr)
        return 2
    arguments.dest.mkdir(parents=True, exist_ok=True)
    failed = False
    day = arguments.first
    while day <= arguments.last:
        result = fetch_day(arguments.instrument, day.isoformat(), arguments.dest)
        failed = failed or result["status"] == "error"
        print(json.dumps({"instrument": arguments.instrument, **result}, sort_keys=True), flush=True)
        day += timedelta(days=1)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
