"""Canonical DSR-L / CSCV-PBO robust comparison foundation v1 (F08).

This module implements the canonical statistical robustness estimators governed by
ADR-0037 under the Validation owner (``quant_platform.validation``).

It preserves package ownership boundaries:
- ``validation`` depends strictly on ``{validation, shared}``;
- It does NOT import ``quant_platform.research``, ``experiment``, or ``strategy`` types;
- Comparable trial panels and cross-owner identities are consumed as immutable projections;
- Deterministic IEEE-754 binary64 policy for statistical operations;
- Zero tolerance for NaN/Inf/silent dropping.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
import hashlib
import itertools
import json
import math
from statistics import NormalDist
from types import MappingProxyType
from typing import Any

from quant_platform.data.models import CoverageInterval, Instant, InvalidRequest


PANEL_IDENTITY_DOMAIN = "comparable-trial-panel-v1"
DSR_RESULT_IDENTITY_DOMAIN = "dsr-result-v1"
PBO_RESULT_IDENTITY_DOMAIN = "pbo-result-v1"

EULER_MASCHERONI = 0.5772156649015329
_NORMAL_DIST = NormalDist()


class RobustnessError(Exception):
    """Base error for all F08 robustness validation operations."""


class ComparablePanelError(RobustnessError):
    """Raised when comparable trial panel invariants are violated."""


class NonEvaluableError(RobustnessError):
    """Raised when mathematical conditions render a statistic non-evaluable."""


class RobustnessStatus(StrEnum):
    """Evaluation status of a robustness statistic."""

    EVALUATED = "evaluated"
    NON_EVALUABLE = "non_evaluable"


class DsrSamplingModel(StrEnum):
    """Sampling model assumption for DSR."""

    IID_V1 = "IID_V1"


def _non_empty_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise RobustnessError(f"{name} must be a string, got {type(value).__name__}")
    cleaned = value.strip()
    if not cleaned:
        raise RobustnessError(f"{name} cannot be empty or whitespace")
    return cleaned


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    """Encode payload to canonical JSON and compute sha256 digest."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


# ---------------------------------------------------------------------------
# ComparableTrialPanel v1
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComparableTrialPanel:
    """Canonical immutable fail-closed comparable trial panel governed by ADR-0037."""

    population_id: str
    trial_ids: tuple[str, ...]
    observations: tuple[Any, ...]
    returns: Mapping[str, tuple[float, ...]]
    return_semantics: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "population_id", _non_empty_text(self.population_id, "population_id"))
        object.__setattr__(self, "return_semantics", _non_empty_text(self.return_semantics, "return_semantics"))

        if not isinstance(self.trial_ids, (list, tuple)) or not self.trial_ids:
            raise ComparablePanelError("trial_ids must be a non-empty sequence of trial IDs")

        cleaned_trial_ids: list[str] = []
        seen_trial_ids: set[str] = set()
        for idx, tid in enumerate(self.trial_ids):
            tid_clean = _non_empty_text(tid, f"trial_ids[{idx}]")
            if tid_clean in seen_trial_ids:
                raise ComparablePanelError(f"duplicate trial identity rejected: {tid_clean!r}")
            seen_trial_ids.add(tid_clean)
            cleaned_trial_ids.append(tid_clean)
        object.__setattr__(self, "trial_ids", tuple(cleaned_trial_ids))

        if not isinstance(self.observations, (list, tuple)) or not self.observations:
            raise ComparablePanelError("observations must be a non-empty sequence of observation coordinates")
        object.__setattr__(self, "observations", tuple(self.observations))

        if not isinstance(self.returns, Mapping):
            raise ComparablePanelError("returns must be a mapping from trial_id to excess-return series")

        expected_t = len(self.observations)
        normalized_returns: dict[str, tuple[float, ...]] = {}

        for tid in cleaned_trial_ids:
            if tid not in self.returns:
                raise ComparablePanelError(f"missing return series for trial: {tid!r}")
            series = self.returns[tid]
            if not isinstance(series, (list, tuple)):
                raise ComparablePanelError(f"returns for trial {tid!r} must be a sequence of floats")
            if len(series) != expected_t:
                raise ComparablePanelError(
                    f"unequal return series length for trial {tid!r}: expected {expected_t}, got {len(series)}"
                )

            cleaned_series: list[float] = []
            for r_idx, val in enumerate(series):
                if not isinstance(val, (int, float)) or isinstance(val, bool):
                    raise ComparablePanelError(
                        f"non-numeric return value at trial {tid!r} index {r_idx}: {val!r}"
                    )
                val_float = float(val)
                if not math.isfinite(val_float):
                    raise ComparablePanelError(
                        f"non-finite return value (NaN/Inf) at trial {tid!r} index {r_idx}: {val!r}"
                    )
                cleaned_series.append(val_float)
            normalized_returns[tid] = tuple(cleaned_series)

        if len(self.returns) != len(cleaned_trial_ids):
            extra_keys = set(self.returns.keys()) - seen_trial_ids
            raise ComparablePanelError(f"returns contains undeclared trial keys: {sorted(extra_keys)!r}")

        object.__setattr__(self, "returns", MappingProxyType(normalized_returns))

    @property
    def n_trials(self) -> int:
        return len(self.trial_ids)

    @property
    def t_observations(self) -> int:
        return len(self.observations)

    def canonical_payload(self) -> dict[str, Any]:
        obs_payload: list[Any] = []
        for obs in self.observations:
            if isinstance(obs, (Instant, CoverageInterval)):
                obs_payload.append(obs.canonical_payload())
            else:
                obs_payload.append(str(obs))

        return {
            "identity_domain": PANEL_IDENTITY_DOMAIN,
            "population_id": self.population_id,
            "return_semantics": self.return_semantics,
            "trial_ids": list(self.trial_ids),
            "observations": obs_payload,
            "returns": {tid: list(self.returns[tid]) for tid in self.trial_ids},
        }

    @property
    def panel_id(self) -> str:
        digest = _canonical_fingerprint(self.canonical_payload())
        return f"{PANEL_IDENTITY_DOMAIN}:sha256:{digest}"


# ---------------------------------------------------------------------------
# Canonical Moment Primitives v1
# ---------------------------------------------------------------------------


def compute_canonical_sharpe(returns: Sequence[float]) -> float:
    """Compute per-observation non-annualized Sharpe ratio using sample ddof=1.

    Formula:
        mean = (1/T) * sum(r_i)
        s^2  = sum((r_i - mean)^2) / (T - 1)
        SR   = mean / sqrt(s^2)

    Fails closed (raises NonEvaluableError) if T < 2 or sample variance <= 0.
    """
    t = len(returns)
    if t < 2:
        raise NonEvaluableError(f"Sharpe requires at least 2 observations, got {t}")

    r_mean = sum(returns) / t
    s2 = sum((x - r_mean) ** 2 for x in returns) / (t - 1)
    if s2 <= 0.0 or not math.isfinite(s2):
        raise NonEvaluableError(f"Sharpe non-evaluable due to zero or non-finite sample variance: {s2}")

    return r_mean / math.sqrt(s2)


def compute_standardized_moments(returns: Sequence[float]) -> tuple[float, float]:
    """Compute standardized skewness (gamma3) and raw/Pearson kurtosis (gamma4).

    Formula (T >= 4):
        d_i = r_i - mean
        m_k = (1/T) * sum(d_i^k)
        gamma3 = m_3 / m_2^(3/2)
        gamma4 = m_4 / m_2^2

    Note: gamma4 is raw/Pearson kurtosis (3.0 for standard Normal), not excess kurtosis.
    """
    t = len(returns)
    if t < 4:
        raise NonEvaluableError(f"Higher moments require at least 4 observations, got {t}")

    r_mean = sum(returns) / t
    m2 = sum((x - r_mean) ** 2 for x in returns) / t
    if m2 <= 0.0 or not math.isfinite(m2):
        raise NonEvaluableError(f"Central moment m2 must be strictly positive and finite, got {m2}")

    m3 = sum((x - r_mean) ** 3 for x in returns) / t
    m4 = sum((x - r_mean) ** 4 for x in returns) / t

    gamma3 = m3 / (m2 ** 1.5)
    gamma4 = m4 / (m2 ** 2.0)
    return gamma3, gamma4


# ---------------------------------------------------------------------------
# DSR-L Estimator v1
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DsrResult:
    """Canonical immutable result evidence for Deflated Sharpe Ratio (DSR-L)."""

    dsr_id: str
    status: RobustnessStatus
    panel_id: str
    selected_trial_id: str | None
    sr_hat: float | None
    sr0: float | None
    dsr: float | None
    sigma_sr: float | None
    k_eff: float
    k_eff_method_id: str
    gamma3: float | None = None
    gamma4: float | None = None
    sampling_model: str = DsrSamplingModel.IID_V1.value
    non_evaluable_reason: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": DSR_RESULT_IDENTITY_DOMAIN,
            "status": self.status.value,
            "panel_id": self.panel_id,
            "selected_trial_id": self.selected_trial_id,
            "sr_hat": self.sr_hat,
            "sr0": self.sr0,
            "dsr": self.dsr,
            "sigma_sr": self.sigma_sr,
            "k_eff": self.k_eff,
            "k_eff_method_id": self.k_eff_method_id,
            "gamma3": self.gamma3,
            "gamma4": self.gamma4,
            "sampling_model": self.sampling_model,
            "non_evaluable_reason": self.non_evaluable_reason,
        }


def evaluate_dsr_l(
    panel: ComparableTrialPanel,
    k_eff: float,
    k_eff_method_id: str,
) -> DsrResult:
    """Evaluate canonical Deflated Sharpe Ratio (DSR-L) governed by ADR-0037."""
    if not isinstance(k_eff, (int, float)) or isinstance(k_eff, bool) or not math.isfinite(k_eff):
        raise RobustnessError(f"k_eff must be a finite float, got {k_eff!r}")
    k_eff_float = float(k_eff)
    method_id = _non_empty_text(k_eff_method_id, "k_eff_method_id")

    n = panel.n_trials
    if k_eff_float < 1.0 or k_eff_float > float(n):
        raise RobustnessError(f"k_eff must satisfy 1 <= k_eff <= N ({n}), got {k_eff_float}")

    # Check for non-evaluable k_eff regime (1 < k_eff < 2)
    if 1.0 < k_eff_float < 2.0:
        base_payload = {
            "identity_domain": DSR_RESULT_IDENTITY_DOMAIN,
            "status": RobustnessStatus.NON_EVALUABLE.value,
            "panel_id": panel.panel_id,
            "selected_trial_id": None,
            "sr_hat": None,
            "sr0": None,
            "dsr": None,
            "sigma_sr": None,
            "k_eff": k_eff_float,
            "k_eff_method_id": method_id,
            "gamma3": None,
            "gamma4": None,
            "sampling_model": DsrSamplingModel.IID_V1.value,
            "non_evaluable_reason": "k_eff in (1, 2) is non-evaluable under ADR-0037",
        }
        return DsrResult(
            dsr_id=f"{DSR_RESULT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(base_payload)}",
            status=RobustnessStatus.NON_EVALUABLE,
            panel_id=panel.panel_id,
            selected_trial_id=None,
            sr_hat=None,
            sr0=None,
            dsr=None,
            sigma_sr=None,
            k_eff=k_eff_float,
            k_eff_method_id=method_id,
            gamma3=None,
            gamma4=None,
            sampling_model=DsrSamplingModel.IID_V1.value,
            non_evaluable_reason=base_payload["non_evaluable_reason"],
        )

    # Compute Sharpe for all trials
    trial_sharpes: dict[str, float] = {}
    for tid in panel.trial_ids:
        try:
            sr = compute_canonical_sharpe(panel.returns[tid])
        except NonEvaluableError as exc:
            reason = f"Trial {tid!r} Sharpe non-evaluable: {exc}"
            payload = {
                "identity_domain": DSR_RESULT_IDENTITY_DOMAIN,
                "status": RobustnessStatus.NON_EVALUABLE.value,
                "panel_id": panel.panel_id,
                "selected_trial_id": None,
                "sr_hat": None,
                "sr0": None,
                "dsr": None,
                "sigma_sr": None,
                "k_eff": k_eff_float,
                "k_eff_method_id": method_id,
                "gamma3": None,
                "gamma4": None,
                "sampling_model": DsrSamplingModel.IID_V1.value,
                "non_evaluable_reason": reason,
            }
            return DsrResult(
                dsr_id=f"{DSR_RESULT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}",
                status=RobustnessStatus.NON_EVALUABLE,
                panel_id=panel.panel_id,
                selected_trial_id=None,
                sr_hat=None,
                sr0=None,
                dsr=None,
                sigma_sr=None,
                k_eff=k_eff_float,
                k_eff_method_id=method_id,
                gamma3=None,
                gamma4=None,
                sampling_model=DsrSamplingModel.IID_V1.value,
                non_evaluable_reason=reason,
            )
        trial_sharpes[tid] = sr

    # Select winner: maximum Sharpe, tie-breaker: lexicographically smallest trial identity
    selected_tid = min(panel.trial_ids, key=lambda tid: (-trial_sharpes[tid], tid))
    sr_hat = trial_sharpes[selected_tid]

    # Cross-trial Sharpe dispersion
    if n == 1:
        sigma_sr = 0.0
    else:
        sr_mean = sum(trial_sharpes.values()) / n
        var_sr = sum((s - sr_mean) ** 2 for s in trial_sharpes.values()) / (n - 1)
        sigma_sr = math.sqrt(max(0.0, var_sr))

    # Search-adjusted benchmark SR0
    if k_eff_float == 1.0:
        sr0 = 0.0
    else:
        p1 = 1.0 - 1.0 / k_eff_float
        p2 = 1.0 - 1.0 / (k_eff_float * math.e)
        z1 = _NORMAL_DIST.inv_cdf(p1)
        z2 = _NORMAL_DIST.inv_cdf(p2)
        z_max = (1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2
        sr0 = sigma_sr * z_max

    # Higher moments of selected trial
    selected_series = panel.returns[selected_tid]
    try:
        gamma3, gamma4 = compute_standardized_moments(selected_series)
    except NonEvaluableError as exc:
        reason = f"Selected trial {selected_tid!r} higher moments non-evaluable: {exc}"
        payload = {
            "identity_domain": DSR_RESULT_IDENTITY_DOMAIN,
            "status": RobustnessStatus.NON_EVALUABLE.value,
            "panel_id": panel.panel_id,
            "selected_trial_id": selected_tid,
            "sr_hat": sr_hat,
            "sr0": sr0,
            "dsr": None,
            "sigma_sr": sigma_sr,
            "k_eff": k_eff_float,
            "k_eff_method_id": method_id,
            "gamma3": None,
            "gamma4": None,
            "sampling_model": DsrSamplingModel.IID_V1.value,
            "non_evaluable_reason": reason,
        }
        return DsrResult(
            dsr_id=f"{DSR_RESULT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}",
            status=RobustnessStatus.NON_EVALUABLE,
            panel_id=panel.panel_id,
            selected_trial_id=selected_tid,
            sr_hat=sr_hat,
            sr0=sr0,
            dsr=None,
            sigma_sr=sigma_sr,
            k_eff=k_eff_float,
            k_eff_method_id=method_id,
            gamma3=None,
            gamma4=None,
            sampling_model=DsrSamplingModel.IID_V1.value,
            non_evaluable_reason=reason,
        )

    # Variance adjustment term A
    term_a = 1.0 - gamma3 * sr_hat + ((gamma4 - 1.0) / 4.0) * (sr_hat ** 2)
    if term_a <= 0.0 or not math.isfinite(term_a):
        reason = f"Variance adjustment factor A must be strictly positive and finite, got {term_a}"
        payload = {
            "identity_domain": DSR_RESULT_IDENTITY_DOMAIN,
            "status": RobustnessStatus.NON_EVALUABLE.value,
            "panel_id": panel.panel_id,
            "selected_trial_id": selected_tid,
            "sr_hat": sr_hat,
            "sr0": sr0,
            "dsr": None,
            "sigma_sr": sigma_sr,
            "k_eff": k_eff_float,
            "k_eff_method_id": method_id,
            "gamma3": gamma3,
            "gamma4": gamma4,
            "sampling_model": DsrSamplingModel.IID_V1.value,
            "non_evaluable_reason": reason,
        }
        return DsrResult(
            dsr_id=f"{DSR_RESULT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}",
            status=RobustnessStatus.NON_EVALUABLE,
            panel_id=panel.panel_id,
            selected_trial_id=selected_tid,
            sr_hat=sr_hat,
            sr0=sr0,
            dsr=None,
            sigma_sr=sigma_sr,
            k_eff=k_eff_float,
            k_eff_method_id=method_id,
            gamma3=gamma3,
            gamma4=gamma4,
            sampling_model=DsrSamplingModel.IID_V1.value,
            non_evaluable_reason=reason,
        )

    t = panel.t_observations
    z_dsr = ((sr_hat - sr0) * math.sqrt(t - 1)) / math.sqrt(term_a)
    dsr = _NORMAL_DIST.cdf(z_dsr)

    payload = {
        "identity_domain": DSR_RESULT_IDENTITY_DOMAIN,
        "status": RobustnessStatus.EVALUATED.value,
        "panel_id": panel.panel_id,
        "selected_trial_id": selected_tid,
        "sr_hat": sr_hat,
        "sr0": sr0,
        "dsr": dsr,
        "sigma_sr": sigma_sr,
        "k_eff": k_eff_float,
        "k_eff_method_id": method_id,
        "gamma3": gamma3,
        "gamma4": gamma4,
        "sampling_model": DsrSamplingModel.IID_V1.value,
        "non_evaluable_reason": None,
    }

    return DsrResult(
        dsr_id=f"{DSR_RESULT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}",
        status=RobustnessStatus.EVALUATED,
        panel_id=panel.panel_id,
        selected_trial_id=selected_tid,
        sr_hat=sr_hat,
        sr0=sr0,
        dsr=dsr,
        sigma_sr=sigma_sr,
        k_eff=k_eff_float,
        k_eff_method_id=method_id,
        gamma3=gamma3,
        gamma4=gamma4,
        sampling_model=DsrSamplingModel.IID_V1.value,
        non_evaluable_reason=None,
    )


# ---------------------------------------------------------------------------
# CSCV / PBO Estimator v1
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PboSplitEvidence:
    """Canonical evidence for a single CSCV in-sample / out-of-sample partition."""

    split_index: int
    is_blocks: tuple[int, ...]
    oos_blocks: tuple[int, ...]
    winner_trial_id: str
    oos_rank: float
    omega: float
    lambda_logit: float

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "split_index": self.split_index,
            "is_blocks": list(self.is_blocks),
            "oos_blocks": list(self.oos_blocks),
            "winner_trial_id": self.winner_trial_id,
            "oos_rank": self.oos_rank,
            "omega": self.omega,
            "lambda_logit": self.lambda_logit,
        }


@dataclass(frozen=True, slots=True)
class PboResult:
    """Canonical immutable result evidence for Probability of Backtest Overfitting (PBO)."""

    pbo_id: str
    status: RobustnessStatus
    panel_id: str
    s: int
    split_count: int
    negative_logit_count: int
    pbo: float | None
    splits: tuple[PboSplitEvidence, ...] = field(default_factory=tuple)
    non_evaluable_reason: str | None = None

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": PBO_RESULT_IDENTITY_DOMAIN,
            "status": self.status.value,
            "panel_id": self.panel_id,
            "s": self.s,
            "split_count": self.split_count,
            "negative_logit_count": self.negative_logit_count,
            "pbo": self.pbo,
            "splits": [s.canonical_payload() for s in self.splits],
            "non_evaluable_reason": self.non_evaluable_reason,
        }


def evaluate_pbo_cscv(panel: ComparableTrialPanel, s: int) -> PboResult:
    """Evaluate canonical Probability of Backtest Overfitting (PBO via CSCV) under ADR-0037."""
    if not isinstance(s, int) or isinstance(s, bool):
        raise RobustnessError(f"s must be an integer, got {s!r}")
    if s < 4 or s % 2 != 0:
        raise RobustnessError(f"s must be an even integer >= 4, got {s}")

    n = panel.n_trials
    if n < 2:
        raise RobustnessError(f"PBO requires at least 2 trials, got {n}")

    t = panel.t_observations
    if t % s != 0:
        raise RobustnessError(f"Observation count T ({t}) must be divisible by s ({s})")

    block_size = t // s
    half_s = s // 2
    observations_per_half = half_s * block_size
    if observations_per_half < 2:
        raise RobustnessError(
            f"Each IS/OOS half must contain at least 2 observations, got {observations_per_half}"
        )

    # Build blocks: list of index ranges
    blocks = [list(range(i * block_size, (i + 1) * block_size)) for i in range(s)]

    # Enumerate all C(S, S/2) combinations in deterministic lexicographical order
    split_combinations = list(itertools.combinations(range(s), half_s))
    total_splits = len(split_combinations)

    splits_evidence: list[PboSplitEvidence] = []
    negative_logit_count = 0

    for split_idx, is_blocks in enumerate(split_combinations):
        oos_blocks = tuple(b for b in range(s) if b not in is_blocks)

        is_indices = [idx for b in is_blocks for idx in blocks[b]]
        oos_indices = [idx for b in oos_blocks for idx in blocks[b]]

        # Compute IS Sharpes
        is_sharpes: dict[str, float] = {}
        for tid in panel.trial_ids:
            try:
                is_sharpes[tid] = compute_canonical_sharpe([panel.returns[tid][i] for i in is_indices])
            except NonEvaluableError as exc:
                reason = f"Split {split_idx} IS Sharpe non-evaluable for trial {tid!r}: {exc}"
                payload = {
                    "identity_domain": PBO_RESULT_IDENTITY_DOMAIN,
                    "status": RobustnessStatus.NON_EVALUABLE.value,
                    "panel_id": panel.panel_id,
                    "s": s,
                    "split_count": total_splits,
                    "negative_logit_count": 0,
                    "pbo": None,
                    "splits": [],
                    "non_evaluable_reason": reason,
                }
                return PboResult(
                    pbo_id=f"{PBO_RESULT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}",
                    status=RobustnessStatus.NON_EVALUABLE,
                    panel_id=panel.panel_id,
                    s=s,
                    split_count=total_splits,
                    negative_logit_count=0,
                    pbo=None,
                    splits=(),
                    non_evaluable_reason=reason,
                )

        # Compute OOS Sharpes
        oos_sharpes: dict[str, float] = {}
        for tid in panel.trial_ids:
            try:
                oos_sharpes[tid] = compute_canonical_sharpe([panel.returns[tid][i] for i in oos_indices])
            except NonEvaluableError as exc:
                reason = f"Split {split_idx} OOS Sharpe non-evaluable for trial {tid!r}: {exc}"
                payload = {
                    "identity_domain": PBO_RESULT_IDENTITY_DOMAIN,
                    "status": RobustnessStatus.NON_EVALUABLE.value,
                    "panel_id": panel.panel_id,
                    "s": s,
                    "split_count": total_splits,
                    "negative_logit_count": 0,
                    "pbo": None,
                    "splits": [],
                    "non_evaluable_reason": reason,
                }
                return PboResult(
                    pbo_id=f"{PBO_RESULT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}",
                    status=RobustnessStatus.NON_EVALUABLE,
                    panel_id=panel.panel_id,
                    s=s,
                    split_count=total_splits,
                    negative_logit_count=0,
                    pbo=None,
                    splits=(),
                    non_evaluable_reason=reason,
                )

        # IS Winner: max IS Sharpe; tie-breaker: lexicographically smallest trial ID
        winner_tid = min(panel.trial_ids, key=lambda tid: (-is_sharpes[tid], tid))

        # OOS Rank: 1 = worst, N = best; exact ties receive arithmetic average rank
        winner_oos_sr = oos_sharpes[winner_tid]
        strictly_worse = sum(1 for tid in panel.trial_ids if oos_sharpes[tid] < winner_oos_sr)
        equal_count = sum(1 for tid in panel.trial_ids if oos_sharpes[tid] == winner_oos_sr)
        oos_rank = strictly_worse + (equal_count + 1) / 2.0

        omega = oos_rank / (n + 1)
        lambda_logit = math.log(omega / (1.0 - omega))

        # Strict below-median event: lambda < 0
        if lambda_logit < 0.0:
            negative_logit_count += 1

        splits_evidence.append(
            PboSplitEvidence(
                split_index=split_idx,
                is_blocks=is_blocks,
                oos_blocks=oos_blocks,
                winner_trial_id=winner_tid,
                oos_rank=oos_rank,
                omega=omega,
                lambda_logit=lambda_logit,
            )
        )

    pbo = negative_logit_count / total_splits

    payload = {
        "identity_domain": PBO_RESULT_IDENTITY_DOMAIN,
        "status": RobustnessStatus.EVALUATED.value,
        "panel_id": panel.panel_id,
        "s": s,
        "split_count": total_splits,
        "negative_logit_count": negative_logit_count,
        "pbo": pbo,
        "splits": [sp.canonical_payload() for sp in splits_evidence],
        "non_evaluable_reason": None,
    }

    return PboResult(
        pbo_id=f"{PBO_RESULT_IDENTITY_DOMAIN}:sha256:{_canonical_fingerprint(payload)}",
        status=RobustnessStatus.EVALUATED,
        panel_id=panel.panel_id,
        s=s,
        split_count=total_splits,
        negative_logit_count=negative_logit_count,
        pbo=pbo,
        splits=tuple(splits_evidence),
        non_evaluable_reason=None,
    )
