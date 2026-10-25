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


BASE = ["--storage-root", "/srv/marketdata", "--checkpoint-path", "/srv/marketdata/canonical/_k10_checkpoints/test.json"]


class ModeAndBoundsTests(unittest.TestCase):
    """DG-O O03: the continuous session is a typed configuration the flags build, and nothing else."""

    def run_cli(self, *argv: str) -> tuple[int, list]:
        configs: list = []
        original = cli.run_live_ingest_server

        def fake_run(config, *, stop_requested, on_signal):
            configs.append(config)
            return SimpleNamespace(status=cli.SERVER_STOPPED, cycles=0, signals=())

        cli.run_live_ingest_server = fake_run
        try:
            with redirect_stdout(io.StringIO()):
                rc = cli.main([*BASE, *argv])
        finally:
            cli.run_live_ingest_server = original
        return rc, configs

    def refused(self, *argv: str) -> None:
        configs: list = []
        original = cli.run_live_ingest_server
        cli.run_live_ingest_server = lambda *a, **k: configs.append(a)
        try:
            with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    cli.main([*BASE, *argv])
        finally:
            cli.run_live_ingest_server = original
        self.assertEqual(2, raised.exception.code)
        self.assertEqual([], configs, "the server must not start with a refused configuration")

    def test_no_mode_and_the_cycles_mode_are_the_existing_behaviour(self) -> None:
        for argv in ((), ("--mode", "cycles"), ("--mode", "")):
            rc, (config,) = self.run_cli(*argv)
            self.assertEqual(0, rc)
            self.assertIsNone(config.continuous_session, argv)

    def test_the_continuous_mode_builds_the_session_with_the_defaults(self) -> None:
        rc, (config,) = self.run_cli("--mode", "continuous")
        session = config.continuous_session
        self.assertEqual((300, 50_000, 20_000), (session.slice_seconds, session.slice_max_trades, session.buffer_messages))
        self.assertEqual(1000, session.recent_limit)

    def test_the_bounds_reach_the_session(self) -> None:
        rc, (config,) = self.run_cli("--mode", "continuous", "--slice-seconds", "60", "--slice-max-trades", "7000",
                                     "--buffer-messages", "500", "--recent-limit", "200")
        session = config.continuous_session
        self.assertEqual((60, 7000, 500, 200), (session.slice_seconds, session.slice_max_trades, session.buffer_messages,
                                                session.recent_limit))

    def test_an_empty_bound_is_the_default_so_an_environment_file_without_the_option_still_works(self) -> None:
        rc, (config,) = self.run_cli("--mode=continuous", "--slice-seconds=", "--slice-max-trades=", "--buffer-messages=")
        session = config.continuous_session
        self.assertEqual((300, 50_000, 20_000), (session.slice_seconds, session.slice_max_trades, session.buffer_messages))
        rc, (config,) = self.run_cli("--mode=", "--slice-seconds=", "--slice-max-trades=", "--buffer-messages=")
        self.assertIsNone(config.continuous_session)

    def test_a_bound_without_the_continuous_mode_is_refused_not_ignored(self) -> None:
        for flag in ("--slice-seconds", "--slice-max-trades", "--buffer-messages"):
            self.refused(flag, "10")
            self.refused("--mode", "cycles", flag, "10")

    def test_invalid_bounds_and_modes_are_refused_before_any_connection(self) -> None:
        for bad in ("0", "-5", "abc", "1.5"):
            for flag in ("--slice-seconds", "--slice-max-trades", "--buffer-messages"):
                self.refused("--mode", "continuous", flag, bad)
        self.refused("--mode", "forever")
        self.refused("--mode", "continuous", "--recent-limit", "5000")

    def test_the_instrument_is_still_passed_with_the_session(self) -> None:
        rc, (config,) = self.run_cli("--mode", "continuous", "--instrument", "ETHUSDT")
        self.assertEqual("ETHUSDT", config.instrument)
        self.assertIsNotNone(config.continuous_session)


if __name__ == "__main__":
    unittest.main()
