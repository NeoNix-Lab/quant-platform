#!/usr/bin/env python3
"""Rebuild explicitly selected, closed legacy BTCUSDT live days from the Bybit public archive (DG-R R02, ADR-0074 Amendment 2).

Operator-run, one command per step, nothing automatic:

* ``plan``    read-only: writes a plan file (and prints its digest) for the named days. Refuses to overwrite a file.
* ``execute`` runs a reviewed plan, one day at a time: retire the damaged live revision, import the whole archive day as revision 1 of
  ``dt=<day>/seg=000000.000``, verify. It must be given the digest of the plan and recomputes the plan first: a plan that is not
  current is refused and nothing is done.
* ``verify``  read-only: compares a rebuilt day with the archive file and with a strict DataGateway read.
* ``restore`` the documented way back for a retired day that was not rebuilt: a superseding pass returns it to valid.

The connection string is read from an environment variable (``--dsn-env``, default ``CATALOG_DSN``), never from the command line.
``--code-ref`` defaults to the commit of this checkout and is refused when the tree has uncommitted changes. Exit status 0 when every
day asked for is done (or verified), 1 when a day is refused or fails, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quant_platform.application.bybit_legacy_day_rebuild import (  # noqa: E402
    RebuildRefused,
    execute_rebuild,
    plan_rebuild,
    restore_retired_day,
    verify_rebuilt_day,
)


def _clean_head() -> str | None:
    try:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, check=True, capture_output=True, text=True)
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return None if status.stdout.strip() else head.stdout.strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--storage-root-id", default="hot")
    parser.add_argument("--dsn-env", default="CATALOG_DSN", help="environment variable that holds the PostgreSQL DSN")
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="read-only plan for the named days")
    plan.add_argument("--days", required=True, help="comma-separated UTC days, ascending, e.g. 2026-10-20")
    plan.add_argument("--archive-dir", required=True, type=Path, help="directory with BTCUSDT<YYYY-MM-DD>.csv.gz (the days and their neighbours)")
    plan.add_argument("--checkpoint-path", required=True, type=Path, help="the live-ingest checkpoint file")
    plan.add_argument("--out", required=True, type=Path, help="plan file to write (never overwritten)")
    plan.add_argument("--code-ref", help="producing commit; default: the clean HEAD of this checkout")
    execute = sub.add_parser("execute", help="run a reviewed plan")
    execute.add_argument("--plan", required=True, type=Path)
    execute.add_argument("--digest", required=True, help="the digest of the plan, as printed by `plan`")
    execute.add_argument("--archive-dir", required=True, type=Path)
    execute.add_argument("--checkpoint-path", required=True, type=Path)
    verify = sub.add_parser("verify", help="read-only verification of rebuilt days")
    verify.add_argument("--days", required=True)
    verify.add_argument("--archive-dir", required=True, type=Path)
    restore = sub.add_parser("restore", help="return a retired, not rebuilt day to valid")
    restore.add_argument("--day", required=True)
    restore.add_argument("--code-ref", help="producing commit; default: the clean HEAD of this checkout")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", arguments.dsn_env):
        parser.error("--dsn-env names an environment variable, not a connection string")
    dsn = os.environ.get(arguments.dsn_env)
    if not dsn:
        parser.error(f"the environment variable {arguments.dsn_env} is not set")
    needs_commit = arguments.command in ("plan", "restore")
    code_ref = getattr(arguments, "code_ref", None) or (_clean_head() if needs_commit else None)
    if needs_commit and not code_ref:
        parser.error("the checkout has uncommitted changes (or no git); commit them or pass --code-ref")
    if arguments.command == "plan" and arguments.out.exists():
        parser.error(f"{arguments.out} exists; a plan file is never overwritten")
    import psycopg

    with psycopg.connect(dsn) as connection:
        try:
            if arguments.command == "plan":
                plan = plan_rebuild(
                    connection, days=[d.strip() for d in arguments.days.split(",") if d.strip()], archive_dir=arguments.archive_dir,
                    storage_root_id=arguments.storage_root_id, checkpoint_path=arguments.checkpoint_path, code_ref=code_ref,
                )
                arguments.out.write_text(json.dumps(plan, sort_keys=True, indent=2) + "\n", encoding="utf-8")
                refused = [d["day"] for d in plan["days"] if d["action"] == "refused"]
                print(json.dumps({"digest": plan["digest"], "plan_file": str(arguments.out),
                                  "days": {d["day"]: d["action"] for d in plan["days"]},
                                  "refusals": {d["day"]: d["refusals"] for d in plan["days"] if d["refusals"]}}, sort_keys=True))
                return 1 if refused else 0
            if arguments.command == "execute":
                plan = json.loads(arguments.plan.read_text(encoding="utf-8"))
                result = execute_rebuild(connection, plan=plan, digest=arguments.digest, archive_dir=arguments.archive_dir,
                                         checkpoint_path=arguments.checkpoint_path)
                print(json.dumps(result, sort_keys=True))
                return 0 if all(d["outcome"] in ("rebuilt", "verified") for d in result["days"]) else 1
            if arguments.command == "verify":
                reports = [verify_rebuilt_day(connection, day=d.strip(), archive_dir=arguments.archive_dir,
                                              storage_root_id=arguments.storage_root_id)
                           for d in arguments.days.split(",") if d.strip()]
                print(json.dumps(reports, sort_keys=True))
                return 0 if all(r["passed"] for r in reports) else 1
            restored = restore_retired_day(connection, day=arguments.day, storage_root_id=arguments.storage_root_id, code_ref=code_ref)
            print(json.dumps({"day": arguments.day, "restored": restored.lifecycle.resulting_state, "inserted": restored.inserted}))
            return 0 if restored.lifecycle.resulting_state == "valid" else 1
        except RebuildRefused as exc:
            print(json.dumps({"refused": exc.phase, "detail": str(exc)}, sort_keys=True))
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
