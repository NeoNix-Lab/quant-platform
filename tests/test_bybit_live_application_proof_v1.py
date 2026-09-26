"""Hermetic proof for the A11 bounded live-proof application seam's
connection-loss handling (finding 5 of the PR #112 adversarial review).

No real network access: `websockets` is substituted with a minimal fake
module via sys.modules, matching the target function's own `import
websockets` (done inside the function body, so it re-resolves against
whatever is currently in sys.modules at call time).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import quant_platform.application.bybit_live as app_bybit_live  # noqa: E402
from quant_platform.application.bybit_live import (  # noqa: E402
    LiveProviderProofPending,
    run_bounded_live_provider_proof,
)


class _FakeConnectionClosed(Exception):
    """Stand-in for websockets.exceptions.ConnectionClosed (Exception, not OSError)."""


class _FakeSocket:
    def __init__(self, raise_on_recv: Exception) -> None:
        self._raise_on_recv = raise_on_recv
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> str:
        raise self._raise_on_recv

    async def __aenter__(self) -> "_FakeSocket":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _ScriptedSocket:
    def __init__(self, responses: tuple[str | Exception, ...]) -> None:
        self._responses = list(responses)
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> str:
        if not self._responses:
            raise TimeoutError("script exhausted")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def __aenter__(self) -> "_ScriptedSocket":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FakeConnect:
    """Stands in for `websockets.connect(url, **kwargs)`, itself an async CM."""

    def __init__(self, socket: _FakeSocket) -> None:
        self._socket = socket

    def __call__(self, *args: object, **kwargs: object) -> "_FakeConnect":
        return self

    async def __aenter__(self) -> _FakeSocket:
        return self._socket

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


def _install_fake_websockets(*, raise_on_recv: Exception) -> None:
    module = types.ModuleType("websockets")
    module.ConnectionClosed = _FakeConnectionClosed  # type: ignore[attr-defined]
    module.connect = _FakeConnect(_FakeSocket(raise_on_recv))  # type: ignore[attr-defined]
    sys.modules["websockets"] = module


def _install_scripted_websockets(*responses: str | Exception) -> None:
    module = types.ModuleType("websockets")
    module.ConnectionClosed = _FakeConnectionClosed  # type: ignore[attr-defined]
    module.connect = _FakeConnect(_ScriptedSocket(tuple(responses)))  # type: ignore[attr-defined]
    sys.modules["websockets"] = module


def _subscribe_ack() -> str:
    return json.dumps({"op": "subscribe", "success": True, "conn_id": "c1"})


def _live_trade_message(*, trade_id: str = "tid-1") -> str:
    return json.dumps({
        "topic": "publicTrade.BTCUSDT",
        "type": "snapshot",
        "ts": 1705276800495,
        "data": [{
            "T": 1705276800490,
            "s": "BTCUSDT",
            "S": "Buy",
            "v": "0.00400",
            "p": "41731.10",
            "i": trade_id,
            "seq": 1783284617,
        }],
    })


class _SpyTracker(app_bybit_live.LiveSessionTracker):
    """Records every disconnected() call; captures itself for later assertions."""

    created: list["_SpyTracker"] = []

    def __init__(self) -> None:
        super().__init__()
        self.disconnect_reasons: list[str] = []
        _SpyTracker.created.append(self)

    def disconnected(self, reason: str) -> None:
        self.disconnect_reasons.append(reason)
        super().disconnected(reason)


class BybitLiveApplicationProofTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_tracker_cls = app_bybit_live.LiveSessionTracker
        app_bybit_live.LiveSessionTracker = _SpyTracker  # type: ignore[assignment]
        _SpyTracker.created = []

    def tearDown(self) -> None:
        app_bybit_live.LiveSessionTracker = self._original_tracker_cls  # type: ignore[assignment]
        sys.modules.pop("websockets", None)

    def test_connection_closed_maps_to_pending_and_records_disconnect(self) -> None:
        _install_fake_websockets(raise_on_recv=_FakeConnectionClosed("peer closed connection"))
        with self.assertRaises(LiveProviderProofPending) as caught:
            asyncio.run(run_bounded_live_provider_proof(max_messages=1, max_seconds=1))
        self.assertIn("provider connection closed", str(caught.exception))
        self.assertEqual(len(_SpyTracker.created), 1)
        self.assertEqual(_SpyTracker.created[0].disconnect_reasons, ["peer closed connection"])

    def test_os_error_maps_to_pending_and_records_disconnect(self) -> None:
        _install_fake_websockets(raise_on_recv=ConnectionResetError("connection reset"))
        with self.assertRaises(LiveProviderProofPending) as caught:
            asyncio.run(run_bounded_live_provider_proof(max_messages=1, max_seconds=1))
        self.assertIn("provider network unavailable", str(caught.exception))
        self.assertEqual(len(_SpyTracker.created), 1)
        self.assertEqual(len(_SpyTracker.created[0].disconnect_reasons), 1)

    def test_timeout_after_valid_trade_message_publishes_partial_bounded_window(self) -> None:
        _install_scripted_websockets(
            _subscribe_ack(),
            _live_trade_message(),
            TimeoutError("bounded window elapsed"),
        )

        report = asyncio.run(run_bounded_live_provider_proof(max_messages=2, max_seconds=1))

        self.assertEqual(report.status, "PASS")
        self.assertEqual(report.messages, 1)
        self.assertEqual(report.records, 1)
        self.assertEqual(report.errors, ())

    def test_timeout_with_no_trade_messages_remains_pending(self) -> None:
        _install_scripted_websockets(
            _subscribe_ack(),
            TimeoutError("bounded window elapsed"),
        )

        report = asyncio.run(run_bounded_live_provider_proof(max_messages=1, max_seconds=1))

        self.assertEqual(report.status, "LIVE_PROVIDER_PROOF_PENDING")
        self.assertEqual(report.messages, 0)
        self.assertEqual(report.records, 0)
        self.assertEqual(report.errors, ("bounded proof timed out before any trade messages arrived",))


if __name__ == "__main__":
    unittest.main(verbosity=2)
