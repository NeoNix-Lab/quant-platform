"""Application-owned bounded Bybit live proof orchestration."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen

from quant_platform.source_adapters.bybit_live import (
    SUPPORTED_CATEGORY,
    SUPPORTED_SYMBOL,
    SUPPORTED_TOPIC,
    BybitLiveSourceError,
    LiveSessionTracker,
    canonicalize_bybit_live_message,
    canonicalize_bybit_recent_public_trade,
    deduplicate_live_records,
)


BYBIT_PUBLIC_LINEAR_WS_URL = "wss://stream.bybit.com/v5/public/linear"
BYBIT_RECENT_TRADES_URL = "https://api.bybit.com/v5/market/recent-trade"


class LiveProviderProofPending(RuntimeError):
    """Raised when a real-provider proof cannot be honestly executed."""


@dataclass(frozen=True, slots=True)
class LiveProviderProofReport:
    status: str
    topic: str
    messages: int
    records: int
    duplicates_removed: int
    final_state: str
    errors: tuple[str, ...]


def fetch_recent_public_trades(*, limit: int = 100) -> tuple[Any, ...]:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise BybitLiveSourceError("recent trade limit must be in [1, 1000]", field="limit")
    query = urlencode({"category": SUPPORTED_CATEGORY, "symbol": SUPPORTED_SYMBOL, "limit": str(limit)})
    with urlopen(f"{BYBIT_RECENT_TRADES_URL}?{query}", timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("retCode") != 0:
        raise LiveProviderProofPending(f"Bybit recent-trade request failed: {payload!r}")
    result = payload.get("result") or {}
    if result.get("category") != SUPPORTED_CATEGORY:
        raise BybitLiveSourceError("recent-trade response category does not match requested linear category")
    return tuple(canonicalize_bybit_recent_public_trade(item) for item in result.get("list") or ())


async def run_bounded_live_provider_proof(
    *,
    max_messages: int = 3,
    max_seconds: float = 20.0,
) -> LiveProviderProofReport:
    try:
        import websockets  # type: ignore[import-not-found]
    except ImportError as exc:
        raise LiveProviderProofPending(
            "LIVE_PROVIDER_PROOF_PENDING: optional dependency 'websockets' is not installed"
        ) from exc
    if max_messages < 1:
        raise BybitLiveSourceError("max_messages must be positive")
    if max_seconds <= 0:
        raise BybitLiveSourceError("max_seconds must be positive")

    tracker = LiveSessionTracker()
    accepted = []
    errors: list[str] = []
    try:
        async with websockets.connect(BYBIT_PUBLIC_LINEAR_WS_URL, ping_interval=None, close_timeout=5) as socket:
            tracker.connected()
            await socket.send(json.dumps({"op": "subscribe", "args": [SUPPORTED_TOPIC]}))
            deadline = asyncio.get_running_loop().time() + max_seconds
            messages = 0
            while messages < max_messages:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    break
                raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
                document = json.loads(raw)
                if document.get("op") == "subscribe":
                    if document.get("success") is not True:
                        raise LiveProviderProofPending(f"subscription refused: {document!r}")
                    tracker.subscribed(topic=SUPPORTED_TOPIC, conn_id=document.get("conn_id"))
                    continue
                if document.get("op") in {"pong", "ping"}:
                    tracker.heartbeat(conn_id=document.get("conn_id"))
                    continue
                if document.get("topic") != SUPPORTED_TOPIC:
                    continue
                batch = canonicalize_bybit_live_message(document)
                tracker.observed_message(batch)
                accepted.extend(batch.records)
                messages += 1
    except TimeoutError:
        errors.append("bounded proof timed out before enough trade messages arrived")
    except OSError as exc:
        raise LiveProviderProofPending(f"LIVE_PROVIDER_PROOF_PENDING: provider network unavailable: {exc}") from exc

    evidence = tracker.evidence()
    deduped = deduplicate_live_records(accepted)
    return LiveProviderProofReport(
        status="PASS" if evidence.validated_messages and not errors else "LIVE_PROVIDER_PROOF_PENDING",
        topic=SUPPORTED_TOPIC,
        messages=evidence.validated_messages,
        records=len(deduped),
        duplicates_removed=len(accepted) - len(deduped),
        final_state=evidence.final_state.value,
        errors=tuple(errors),
    )


def run_bounded_live_provider_proof_sync(*, max_messages: int = 3, max_seconds: float = 20.0) -> LiveProviderProofReport:
    return asyncio.run(run_bounded_live_provider_proof(max_messages=max_messages, max_seconds=max_seconds))


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded A11 Bybit public live trade proof")
    parser.add_argument("--max-messages", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=20.0)
    return parser


__all__ = [
    "BYBIT_PUBLIC_LINEAR_WS_URL",
    "BYBIT_RECENT_TRADES_URL",
    "LiveProviderProofPending",
    "LiveProviderProofReport",
    "build_arg_parser",
    "fetch_recent_public_trades",
    "run_bounded_live_provider_proof",
    "run_bounded_live_provider_proof_sync",
]
