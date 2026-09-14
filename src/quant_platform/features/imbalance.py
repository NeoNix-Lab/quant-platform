"""Pure diagonal and stacked imbalance kernel for E05.

The caller supplies already-formed price-level rows with explicit bar identity
and integer level indexes.  This module does not discover data, define a
footprint representation, resolve tick grids, or create feature artifacts.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass
import math
from typing import Any


class ImbalanceInputError(ValueError):
    """Supplied price-level inputs violate the pure kernel contract."""


@dataclass(frozen=True, slots=True)
class ImbalanceConfig:
    """Thresholds for the accepted H01 imbalance core."""

    imbalance_ratio: float = 3.0
    stacked_min_levels: int = 3

    def __post_init__(self) -> None:
        ratio = _positive_finite_number(self.imbalance_ratio, "imbalance_ratio")
        if ratio <= 1.0:
            raise ImbalanceInputError("imbalance_ratio must be greater than 1")
        if type(self.stacked_min_levels) is not int:
            raise ImbalanceInputError("stacked_min_levels must be an integer")
        if self.stacked_min_levels < 2:
            raise ImbalanceInputError("stacked_min_levels must be at least 2")
        object.__setattr__(self, "imbalance_ratio", ratio)


@dataclass(frozen=True, slots=True)
class PriceLevelInput:
    """One caller-supplied level inside one bar."""

    bar_id: Hashable
    level_index: int
    buy_volume: float
    sell_volume: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "bar_id", _hashable_bar_id(self.bar_id))
        object.__setattr__(self, "level_index", _level_index(self.level_index))
        object.__setattr__(self, "buy_volume", _volume(self.buy_volume, "buy_volume"))
        object.__setattr__(self, "sell_volume", _volume(self.sell_volume, "sell_volume"))


@dataclass(frozen=True, slots=True)
class DiagonalImbalanceLevel:
    """Per-level diagonal imbalance values."""

    bar_id: Hashable
    level_index: int
    buy_volume: float
    sell_volume: float
    total_volume: float
    delta: float
    ask_imbalance_ratio: float
    bid_imbalance_ratio: float
    imbalance_side: str | None


@dataclass(frozen=True, slots=True)
class StackedImbalanceLevel:
    """Per-level imbalance values with stacked-run annotations."""

    bar_id: Hashable
    level_index: int
    buy_volume: float
    sell_volume: float
    total_volume: float
    delta: float
    ask_imbalance_ratio: float
    bid_imbalance_ratio: float
    imbalance_side: str | None
    stacked_imbalance: bool
    stacked_run_length: int


def _ratio(numerator: float, denominator: float | None) -> float:
    """Accepted H01 ratio semantics for present, zero and absent counterparties."""

    num = _volume(numerator, "numerator")
    if denominator is None:
        return math.nan
    den = _optional_volume(denominator, "denominator")
    if math.isnan(den):
        return math.nan
    if den > 0.0:
        return num / den
    if num > 0.0:
        return math.inf
    return 0.0


def compute_diagonal_imbalance(
    levels: Iterable[PriceLevelInput],
    config: ImbalanceConfig | None = None,
) -> tuple[DiagonalImbalanceLevel, ...]:
    """Compute diagonal ask/bid imbalance for supplied price-level rows."""

    cfg = config or ImbalanceConfig()
    rows = _price_level_tuple(levels)
    by_key: dict[tuple[Hashable, int], PriceLevelInput] = {}
    for row in rows:
        key = (row.bar_id, row.level_index)
        if key in by_key:
            raise ImbalanceInputError(
                "price levels contain more than one row per (bar_id, level_index)"
            )
        by_key[key] = row

    out: list[DiagonalImbalanceLevel] = []
    for row in rows:
        below = by_key.get((row.bar_id, row.level_index - 1))
        above = by_key.get((row.bar_id, row.level_index + 1))
        ask_ratio = _ratio(row.buy_volume, None if below is None else below.sell_volume)
        bid_ratio = _ratio(row.sell_volume, None if above is None else above.buy_volume)
        ask_hit = (
            not math.isnan(ask_ratio)
            and ask_ratio >= cfg.imbalance_ratio
            and row.buy_volume > 0.0
        )
        bid_hit = (
            not math.isnan(bid_ratio)
            and bid_ratio >= cfg.imbalance_ratio
            and row.sell_volume > 0.0
        )
        side = None
        if ask_hit and (not bid_hit or ask_ratio >= bid_ratio):
            side = "ask"
        elif bid_hit:
            side = "bid"
        out.append(
            DiagonalImbalanceLevel(
                bar_id=row.bar_id,
                level_index=row.level_index,
                buy_volume=row.buy_volume,
                sell_volume=row.sell_volume,
                total_volume=row.buy_volume + row.sell_volume,
                delta=row.buy_volume - row.sell_volume,
                ask_imbalance_ratio=ask_ratio,
                bid_imbalance_ratio=bid_ratio,
                imbalance_side=side,
            )
        )
    return tuple(out)


def compute_stacked_imbalance(
    levels: Iterable[PriceLevelInput | DiagonalImbalanceLevel],
    config: ImbalanceConfig | None = None,
) -> tuple[StackedImbalanceLevel, ...]:
    """Annotate consecutive same-side imbalance runs inside each bar."""

    cfg = config or ImbalanceConfig()
    diagonal = _diagonal_tuple(levels, cfg)
    _assert_unique_diagonal_levels(diagonal)
    out = [_stacked_level(row) for row in diagonal]

    grouped: dict[Hashable, list[int]] = {}
    for index, row in enumerate(out):
        grouped.setdefault(row.bar_id, []).append(index)

    for row_indices in grouped.values():
        ordered = sorted(row_indices, key=lambda index: out[index].level_index)
        run_start = 0
        for position in range(1, len(ordered) + 1):
            current = out[ordered[position]] if position < len(ordered) else None
            previous = out[ordered[position - 1]]
            first = out[ordered[run_start]]
            broken = (
                current is None
                or current.imbalance_side is None
                or current.imbalance_side != first.imbalance_side
                or current.level_index != previous.level_index + 1
            )
            if not broken:
                continue
            run_length = position - run_start
            if first.imbalance_side is not None and run_length >= cfg.stacked_min_levels:
                for marked in ordered[run_start:position]:
                    out[marked] = _replace_stacked(out[marked], True, run_length)
            run_start = position

    return tuple(out)


def _price_level_tuple(levels: Iterable[PriceLevelInput]) -> tuple[PriceLevelInput, ...]:
    if isinstance(levels, (str, bytes, bytearray)) or levels is None:
        raise ImbalanceInputError("levels must be an iterable of PriceLevelInput values")
    rows = tuple(levels)
    for index, row in enumerate(rows):
        if not isinstance(row, PriceLevelInput):
            raise ImbalanceInputError(f"levels[{index}] must be a PriceLevelInput")
    return rows


def _diagonal_tuple(
    levels: Iterable[PriceLevelInput | DiagonalImbalanceLevel],
    config: ImbalanceConfig,
) -> tuple[DiagonalImbalanceLevel, ...]:
    if isinstance(levels, (str, bytes, bytearray)) or levels is None:
        raise ImbalanceInputError(
            "levels must be an iterable of PriceLevelInput or DiagonalImbalanceLevel values"
        )
    rows = tuple(levels)
    if all(isinstance(row, DiagonalImbalanceLevel) for row in rows):
        return rows  # type: ignore[return-value]
    if all(isinstance(row, PriceLevelInput) for row in rows):
        return compute_diagonal_imbalance(rows, config)  # type: ignore[arg-type]
    raise ImbalanceInputError(
        "levels must contain only PriceLevelInput values or only DiagonalImbalanceLevel values"
    )


def _assert_unique_diagonal_levels(rows: tuple[DiagonalImbalanceLevel, ...]) -> None:
    seen = set()
    for row in rows:
        key = (row.bar_id, row.level_index)
        if key in seen:
            raise ImbalanceInputError(
                "price levels contain more than one row per (bar_id, level_index)"
            )
        seen.add(key)


def _stacked_level(row: DiagonalImbalanceLevel) -> StackedImbalanceLevel:
    return StackedImbalanceLevel(
        bar_id=row.bar_id,
        level_index=row.level_index,
        buy_volume=row.buy_volume,
        sell_volume=row.sell_volume,
        total_volume=row.total_volume,
        delta=row.delta,
        ask_imbalance_ratio=row.ask_imbalance_ratio,
        bid_imbalance_ratio=row.bid_imbalance_ratio,
        imbalance_side=row.imbalance_side,
        stacked_imbalance=False,
        stacked_run_length=0,
    )


def _replace_stacked(
    row: StackedImbalanceLevel,
    stacked: bool,
    run_length: int,
) -> StackedImbalanceLevel:
    return StackedImbalanceLevel(
        bar_id=row.bar_id,
        level_index=row.level_index,
        buy_volume=row.buy_volume,
        sell_volume=row.sell_volume,
        total_volume=row.total_volume,
        delta=row.delta,
        ask_imbalance_ratio=row.ask_imbalance_ratio,
        bid_imbalance_ratio=row.bid_imbalance_ratio,
        imbalance_side=row.imbalance_side,
        stacked_imbalance=stacked,
        stacked_run_length=run_length,
    )


def _hashable_bar_id(value: Any) -> Hashable:
    if not isinstance(value, Hashable):
        raise ImbalanceInputError("bar_id must be hashable")
    return value


def _level_index(value: int) -> int:
    if type(value) is not int:
        raise ImbalanceInputError("level_index must be an integer")
    return value


def _positive_finite_number(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ImbalanceInputError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ImbalanceInputError(f"{field} must be a positive finite number")
    return result


def _volume(value: float, field: str) -> float:
    result = _positive_or_zero_finite_number(value, field)
    return result


def _optional_volume(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ImbalanceInputError(f"{field} must be numeric")
    result = float(value)
    if math.isnan(result):
        return result
    if not math.isfinite(result) or result < 0.0:
        raise ImbalanceInputError(f"{field} must be a non-negative finite number")
    return result


def _positive_or_zero_finite_number(value: float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ImbalanceInputError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ImbalanceInputError(f"{field} must be a non-negative finite number")
    return result


__all__ = [
    "DiagonalImbalanceLevel",
    "ImbalanceConfig",
    "ImbalanceInputError",
    "PriceLevelInput",
    "StackedImbalanceLevel",
    "_ratio",
    "compute_diagonal_imbalance",
    "compute_stacked_imbalance",
]
