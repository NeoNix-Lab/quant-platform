#!/usr/bin/env python3
"""CLI proof controls for the bounded live-ingest server."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stderr, redirect_stdout
import io
import sys
import unittest


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import live_ingest_server as cli  # noqa: E402


class LiveIngestServerCliTests(unittest.TestCase):
    def test_max_cycles_requests_clean_stop_after_health_snapshot(self):
        original = cli.run_live_ingest_server
        calls: list[bool] = []

        def fake_run(config, *, stop_requested, on_signal):
            calls.append(stop_requested())
            signal = SimpleNamespace(
                kind=SimpleNamespace(value="HEALTH_SNAPSHOT"),
                capability_id="live-ingest-server-v1",
                signal_id="signal-1",
                payload={"health": "HEALTHY"},
            )
            on_signal(signal)
            calls.append(stop_requested())
            return SimpleNamespace(status=cli.SERVER_STOPPED, cycles=1, signals=(signal,))

        cli.run_live_ingest_server = fake_run
        try:
            with redirect_stdout(io.StringIO()):
                rc = cli.main([
                    "--storage-root", "/srv/marketdata",
                    "--checkpoint-path", "/srv/marketdata/canonical/_k10_checkpoints/test.json",
                    "--max-cycles", "1",
                ])
        finally:
            cli.run_live_ingest_server = original

        self.assertEqual(rc, 0)
        self.assertEqual(calls, [False, True])

    def test_max_cycles_must_be_positive(self):
        with redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                cli.main([
                    "--storage-root", "/srv/marketdata",
                    "--checkpoint-path", "/srv/marketdata/canonical/_k10_checkpoints/test.json",
                    "--max-cycles", "0",
                ])

        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
