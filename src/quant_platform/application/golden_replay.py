"""Application-owned composition for the Wave 4 Golden E2E replay proof (issue #146).

Owns the wiring between a real DataGateway catalog, the minimal breakout
strategy exercised by this proof, and ``quant_platform.replay``'s runtime --
so ``tools/golden_replay_e2e.py`` stays a thin CLI that only calls through
this seam, per ADR-0024 (executable orchestration must not reach directly
into domain packages).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..access.catalog import Catalog
from ..access.gateway import DataGateway
from ..data.models import DatasetIdentity, Instant
from ..execution import FeeSchedule
from ..replay import HistoricalReplayRuntime, ReplayContext, ReplayResult, ReplaySpec
from ..source_adapters.bybit import BYBIT_ORDERING_PROVIDER, BYBIT_TRADE_V1_ORDERING_POLICY
from ..strategy import (
    CapitalRiskPolicy,
    CooldownPolicyDefinition,
    Direction,
    EntryPolicy,
    ExitPolicy,
    FixedFractionSizingPolicy,
    PositionPolicy,
    SessionDecision,
    SessionPolicyDefinition,
    SessionReferenceMarket,
    SessionState,
    SignalCombinationMode,
    SignalCombinationPolicy,
    StrategyInput,
    StrategySpec,
)


CANONICAL_BTCUSDT_DATASET = DatasetIdentity("canonical", "trades", "bybit", "BTCUSDT", "trade-v1")


class AlwaysOpenSessionPolicy(SessionPolicyDefinition):
    """Permissive 24/7 session policy for continuous crypto and replay proofs.

    Evaluates every instant as SessionState.OPEN without calendar or weekend
    filtering, so that proof and continuous crypto assets trade freely 24/7.
    """

    def evaluate(self, as_of: Instant) -> tuple[SessionDecision, ...]:
        evaluation_time = Instant.parse(as_of)
        return (
            SessionDecision(
                policy_identity=self.identity,
                as_of=evaluation_time,
                reference_market=self.reference_markets[0],
                state=SessionState.OPEN,
                phase="continuous_24_7",
                trading_date=evaluation_time.isoformat()[:10],
                reason="always_open",
            ),
        )


class NoOpExecutionPolicy:
    """Minimal identity-backed stub for ``StrategySpec.execution_policy``.

    #141 left this slot as the generic identity-backed protocol pending a
    concrete ``ExecutionPolicy`` type; this proof does not need one.
    """

    @property
    def identity(self) -> str:
        return "golden-replay-e2e-execution-policy-v1:sha256:" + ("0" * 64)

    def stable_dict(self) -> dict[str, Any]:
        return {"policy_type": "golden-replay-e2e-execution-policy-v1"}


def minimal_breakout_strategy() -> StrategySpec:
    """The leanest possible non-trivial strategy for this proof's purpose.

    A single-lot Donchian-channel breakout with permissive risk/sizing/
    cooldown settings: this proof is about the replay ENGINE's determinism,
    not strategy tuning -- richer strategies belong to later, more complete
    proofs.
    """

    return StrategySpec(
        strategy_key="golden.e2e.breakout",
        semantic_version="1",
        entry_policy=EntryPolicy(
            policy_key="golden.entry",
            direction=Direction.LONG,
            signal_key="signal.entry",
            confidence="1",
        ),
        exit_policy=ExitPolicy(
            policy_key="golden.exit",
            signal_key="signal.exit",
        ),
        position_policy=PositionPolicy(
            policy_key="golden.position",
            long_target_position="0.01",
            short_target_position="0.01",
        ),
        sizing_policy=FixedFractionSizingPolicy(
            policy_key="golden.sizing",
            lot_size="0.001",
            min_size="0.001",
        ),
        risk_policy=CapitalRiskPolicy(
            policy_key="golden.risk",
            max_drawdown_fraction="1",
            max_position_notional_fraction="1",
            max_total_exposure_fraction="1",
            risk_per_trade_fraction="1",
            max_evidence_age_seconds=3600,
        ),
        session_policy=AlwaysOpenSessionPolicy(
            policy_key="golden.session",
            reference_markets=(SessionReferenceMarket.NEW_YORK,),
        ),
        cooldown_policy=CooldownPolicyDefinition(
            policy_key="golden.cooldown",
            post_loss_cooldown_seconds=0,
            consecutive_loss_count=1_000_000,
            consecutive_loss_cooldown_seconds=0,
            max_entries_per_utc_day=1_000_000,
            max_trade_history_age_seconds=3600,
        ),
        signal_combination_policy=SignalCombinationPolicy(
            policy_key="golden.signals",
            mode=SignalCombinationMode.ALL,
            signal_keys=("signal.entry",),
        ),
        execution_policy=NoOpExecutionPolicy(),
    )


class MinimalBreakoutFeatureProvider:
    """Pure, deterministic Donchian-channel breakout over the trade stream
    seen so far in this run -- no lookahead, no state beyond the ordered
    sequence of records this same run has already processed.
    """

    def __init__(self, lookback: int = 20) -> None:
        if lookback < 1:
            raise ValueError("lookback must be a positive integer")
        self._lookback = lookback
        self._recent_prices: list[Decimal] = []

    def __call__(self, record: Any, context: ReplayContext) -> tuple[StrategyInput, ...]:
        price = Decimal(record.price)
        window = self._recent_prices[-self._lookback :]
        entry_signal = bool(window) and price > max(window)
        exit_signal = bool(window) and price < min(window)
        self._recent_prices.append(price)
        return (
            StrategyInput(
                key="signal.entry",
                value=entry_signal,
                available_at=record.exchange_ts,
                provenance="golden-replay-e2e:breakout",
            ),
            StrategyInput(
                key="signal.exit",
                value=exit_signal,
                available_at=record.exchange_ts,
                provenance="golden-replay-e2e:breakout",
            ),
        )


def build_golden_replay_gateway(dsn: str) -> DataGateway:
    return DataGateway(Catalog(dsn=dsn), ordering_providers=(BYBIT_ORDERING_PROVIDER,))


def build_golden_replay_spec(
    *,
    start: str,
    end: str,
    initial_capital: str = "10000",
    batch_size: int = 65_536,
) -> ReplaySpec:
    return ReplaySpec(
        dataset=CANONICAL_BTCUSDT_DATASET,
        start=start,
        end=end,
        strategy=minimal_breakout_strategy(),
        initial_capital=initial_capital,
        fee_schedule=FeeSchedule(policy_key="golden.fees"),
        ordering_policy=BYBIT_TRADE_V1_ORDERING_POLICY,
        batch_size=batch_size,
    )


@dataclass(frozen=True, slots=True)
class GoldenReplayProof:
    """Result of running the Golden E2E proof twice and comparing traces."""

    spec_identity: str
    first: ReplayResult
    second: ReplayResult

    @property
    def deterministic(self) -> bool:
        return self.first.trace_fingerprint == self.second.trace_fingerprint


def run_golden_replay_proof(
    *,
    dsn: str,
    start: str,
    end: str,
    initial_capital: str = "10000",
    lookback: int = 20,
    batch_size: int = 65_536,
) -> GoldenReplayProof:
    """Execute the replay twice, independently, and report both results.

    Requires a real, already-ingested canonical Bybit BTCUSDT ``trade-v1``
    dataset reachable through ``dsn``; this cannot run against synthetic
    fixtures.
    """

    spec = build_golden_replay_spec(
        start=start, end=end, initial_capital=initial_capital, batch_size=batch_size
    )
    first = HistoricalReplayRuntime(
        build_golden_replay_gateway(dsn), MinimalBreakoutFeatureProvider(lookback)
    ).run(spec)
    second = HistoricalReplayRuntime(
        build_golden_replay_gateway(dsn), MinimalBreakoutFeatureProvider(lookback)
    ).run(spec)
    return GoldenReplayProof(spec_identity=spec.identity, first=first, second=second)


__all__ = [
    "CANONICAL_BTCUSDT_DATASET",
    "GoldenReplayProof",
    "MinimalBreakoutFeatureProvider",
    "NoOpExecutionPolicy",
    "build_golden_replay_gateway",
    "build_golden_replay_spec",
    "minimal_breakout_strategy",
    "run_golden_replay_proof",
]
