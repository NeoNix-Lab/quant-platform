"""Canonical DSR-L / full-CSCV PBO robust comparison v1 (F08).

This module implements the canonical F08 estimator foundation governed by
ADR-0037 under the Validation owner (``quant_platform.validation``).

It preserves package ownership boundaries:
- ``validation`` depends strictly on ``{validation, shared}``;
- it does NOT import ``quant_platform.research``/``experiments``/``strategy``
  runtime types;
- cross-owner identities (population, trial) are consumed as opaque evidence
  through Validation-owned immutable projections (``ComparableTrialPanel``).

F08 v1 freezes DSR-L (location-only) and the complete CSCV PBO procedure. It
does not estimate effective trial count, apply serial-correlation
corrections, or implement DSR-LS/full-search DSR variants.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import combinations
import hashlib
import json
import math
from statistics import NormalDist
from types import MappingProxyType
from typing import Any


DSR_SPEC_VERSION = "dsr-l-v1"
PBO_SPEC_VERSION = "cscv-pbo-v1"
PANEL_IDENTITY_DOMAIN = "comparable-trial-panel-v1"
SAMPLING_MODEL_IID_V1 = "IID_V1"
NUMERICAL_POLICY_ID = "numerical-policy-v1:ieee754-binary64"

# Bailey/Lopez de Prado expected-maximum location approximation constant.
EULER_MASCHERONI = 0.5772156649015329


class RobustnessError(ValueError):
    """A supplied F08 panel, evidence or split specification is invalid."""


class EvaluationStatus(StrEnum):
    """Explicit evaluable/non-evaluable outcome, never silently mapped to 0/inf."""

    EVALUABLE = "evaluable"
    NON_EVALUABLE = "non_evaluable"


def _non_empty_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RobustnessError(f"{field_name} must be a non-empty string")
    text = value.strip()
    if any(ord(c) < 32 for c in text):
        raise RobustnessError(f"{field_name} must not contain control characters")
    return text


def _finite_float(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RobustnessError(f"{field_name} must be numeric")
    parsed = float(value)
    if not math.isfinite(parsed):
        raise RobustnessError(f"{field_name} must be finite")
    return parsed


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _encode_observation_ids(observation_ids: Sequence[str | int]) -> list[list[str]]:
    """Type-tag each observation identity so ``1`` and ``"1"`` fingerprint differently."""

    return [
        ["int", str(obs_id)] if isinstance(obs_id, int) else ["str", obs_id]
        for obs_id in observation_ids
    ]


# ---------------------------------------------------------------------------
# ComparableTrialPanel v1
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ComparableTrialPanel:
    """Immutable fail-closed comparable-trial excess-return panel (ADR-0037 s.2).

    Carries one canonical population/evidence identity, an ordered set of
    unique opaque trial identities, one common ordered observation
    universe/support identity, and one same-length finite excess-return
    series per trial. F08 does not reconstruct prices, PnL, costs, fees,
    slippage, risk-free returns or annualization: the supplied series are
    already the dimensionless excess-return quantity to be evaluated.
    """

    population_id: str
    trial_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    returns: Mapping[str, tuple[float, ...]]
    return_semantics_id: str

    def __post_init__(self) -> None:
        population_id = _non_empty_text(self.population_id, "population_id")
        return_semantics_id = _non_empty_text(self.return_semantics_id, "return_semantics_id")

        if not isinstance(self.trial_ids, Sequence) or isinstance(self.trial_ids, (str, bytes)):
            raise RobustnessError("trial_ids must be an ordered sequence of trial identities")
        trial_ids = tuple(_non_empty_text(tid, "trial_id") for tid in self.trial_ids)
        if not trial_ids:
            raise RobustnessError("panel requires at least one trial")
        if len(set(trial_ids)) != len(trial_ids):
            raise RobustnessError("duplicate canonical trial identity in panel")

        if not isinstance(self.observation_ids, Sequence) or isinstance(self.observation_ids, (str, bytes)):
            raise RobustnessError("observation_ids must be an ordered sequence")
        observation_ids = tuple(self.observation_ids)
        if not observation_ids:
            raise RobustnessError("panel requires at least one observation")
        for obs_id in observation_ids:
            if isinstance(obs_id, bool) or not isinstance(obs_id, (str, int)):
                raise RobustnessError("observation identity must be a string or integer")
        if len(set(observation_ids)) != len(observation_ids):
            raise RobustnessError("duplicate observation identity in panel support")
        observation_count = len(observation_ids)

        if not isinstance(self.returns, Mapping):
            raise RobustnessError("returns must be a mapping of trial_id to a return series")
        if set(self.returns.keys()) != set(trial_ids):
            raise RobustnessError("returns must declare exactly one series per declared trial, no more, no less")

        normalized_returns: dict[str, tuple[float, ...]] = {}
        for trial_id in trial_ids:
            series_raw = self.returns[trial_id]
            if not isinstance(series_raw, Sequence) or isinstance(series_raw, (str, bytes)):
                raise RobustnessError(f"trial {trial_id!r} return series must be an ordered sequence")
            series = tuple(
                _finite_float(value, f"trial {trial_id!r} return[{i}]")
                for i, value in enumerate(series_raw)
            )
            if len(series) != observation_count:
                raise RobustnessError(
                    f"trial {trial_id!r} has {len(series)} observations, "
                    f"panel support requires exactly {observation_count}"
                )
            normalized_returns[trial_id] = series

        object.__setattr__(self, "population_id", population_id)
        object.__setattr__(self, "return_semantics_id", return_semantics_id)
        object.__setattr__(self, "trial_ids", trial_ids)
        object.__setattr__(self, "observation_ids", observation_ids)
        object.__setattr__(self, "returns", MappingProxyType(normalized_returns))

    @property
    def trial_count(self) -> int:
        return len(self.trial_ids)

    @property
    def observation_count(self) -> int:
        return len(self.observation_ids)

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": PANEL_IDENTITY_DOMAIN,
            "population_id": self.population_id,
            "return_semantics_id": self.return_semantics_id,
            "trial_ids": list(self.trial_ids),
            "observation_ids": _encode_observation_ids(self.observation_ids),
            "returns": {trial_id: list(series) for trial_id, series in self.returns.items()},
        }

    @property
    def content_digest(self) -> str:
        return _canonical_fingerprint(self.canonical_payload())


# ---------------------------------------------------------------------------
# Canonical Sharpe v1 and frozen higher moments (ADR-0037 s.3, s.4)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SharpeResult:
    """Canonical per-observation non-annualized Sharpe outcome (ddof=1)."""

    status: EvaluationStatus
    value: float | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status is EvaluationStatus.EVALUABLE:
            if self.value is None or not math.isfinite(self.value):
                raise RobustnessError("an evaluable SharpeResult requires a finite value")
            if self.reason is not None:
                raise RobustnessError("an evaluable SharpeResult must not carry a reason")
        else:
            if self.value is not None:
                raise RobustnessError("a non-evaluable SharpeResult must not carry a value")
            if not self.reason:
                raise RobustnessError("a non-evaluable SharpeResult requires an explicit reason")


def compute_sharpe_v1(values: Sequence[float]) -> SharpeResult:
    """SR = mean / sqrt(sample variance), ddof=1; T < 2 or zero variance is non-evaluable."""

    series = tuple(_finite_float(value, f"return[{i}]") for i, value in enumerate(values))
    observation_count = len(series)
    if observation_count < 2:
        return SharpeResult(EvaluationStatus.NON_EVALUABLE, reason="observation_count_below_2")

    mean = sum(series) / observation_count
    if not math.isfinite(mean):
        return SharpeResult(EvaluationStatus.NON_EVALUABLE, reason="non_finite_intermediate")

    # `*` saturates to +/-inf on overflow for finite binary64 inputs; `**`
    # raises OverflowError instead, which must not escape as an exception.
    sum_sq = sum((x - mean) * (x - mean) for x in series)
    if not math.isfinite(sum_sq):
        return SharpeResult(EvaluationStatus.NON_EVALUABLE, reason="non_finite_intermediate")

    variance = sum_sq / (observation_count - 1)
    if not math.isfinite(variance):
        return SharpeResult(EvaluationStatus.NON_EVALUABLE, reason="non_finite_intermediate")
    if variance == 0.0:
        return SharpeResult(EvaluationStatus.NON_EVALUABLE, reason="zero_variance")

    sharpe = mean / math.sqrt(variance)
    if not math.isfinite(sharpe):
        return SharpeResult(EvaluationStatus.NON_EVALUABLE, reason="non_finite_sharpe")
    return SharpeResult(EvaluationStatus.EVALUABLE, value=sharpe)


@dataclass(frozen=True, slots=True)
class MomentsResult:
    """Frozen standardized third/fourth central moments (ADR-0037 s.4)."""

    status: EvaluationStatus
    gamma3: float | None = None
    gamma4: float | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.status is EvaluationStatus.EVALUABLE:
            if self.gamma3 is None or self.gamma4 is None:
                raise RobustnessError("an evaluable MomentsResult requires gamma3 and gamma4")
            if not (math.isfinite(self.gamma3) and math.isfinite(self.gamma4)):
                raise RobustnessError("an evaluable MomentsResult requires finite moments")
        else:
            if self.gamma3 is not None or self.gamma4 is not None:
                raise RobustnessError("a non-evaluable MomentsResult must not carry moments")
            if not self.reason:
                raise RobustnessError("a non-evaluable MomentsResult requires an explicit reason")


def compute_moments_v1(values: Sequence[float]) -> MomentsResult:
    """gamma3 = m3/m2^1.5 (skew), gamma4 = m4/m2^2 (raw/Pearson kurtosis).

    The formulas are evaluable for any series of length ``T >= 2`` with non-zero
    sample variance (``m2 > 0``). For DSR evaluation, ADR-0037 s.4 additionally
    requires ``T >= 4``.
    """

    series = tuple(_finite_float(value, f"return[{i}]") for i, value in enumerate(values))
    observation_count = len(series)
    if observation_count < 2:
        return MomentsResult(EvaluationStatus.NON_EVALUABLE, reason="observation_count_below_2")

    mean = sum(series) / observation_count
    if not math.isfinite(mean):
        return MomentsResult(EvaluationStatus.NON_EVALUABLE, reason="non_finite_intermediate")
    deviations = tuple(x - mean for x in series)

    # `*` saturates to +/-inf on overflow for finite binary64 inputs; `**`
    # raises OverflowError instead, which must not escape as an exception.
    sum_sq = sum(d * d for d in deviations)
    if not math.isfinite(sum_sq):
        return MomentsResult(EvaluationStatus.NON_EVALUABLE, reason="non_finite_intermediate")
    m2 = sum_sq / observation_count
    if not math.isfinite(m2):
        return MomentsResult(EvaluationStatus.NON_EVALUABLE, reason="non_finite_intermediate")
    if m2 == 0.0:
        return MomentsResult(EvaluationStatus.NON_EVALUABLE, reason="second_moment_zero")

    sum_cube = sum(d * d * d for d in deviations)
    sum_quad = sum((d * d) * (d * d) for d in deviations)
    if not (math.isfinite(sum_cube) and math.isfinite(sum_quad)):
        return MomentsResult(EvaluationStatus.NON_EVALUABLE, reason="non_finite_intermediate")
    m3 = sum_cube / observation_count
    m4 = sum_quad / observation_count
    if not (math.isfinite(m3) and math.isfinite(m4)):
        return MomentsResult(EvaluationStatus.NON_EVALUABLE, reason="non_finite_intermediate")

    m2_pow_1_5 = m2 * math.sqrt(m2)
    m2_pow_2 = m2 * m2
    if not (math.isfinite(m2_pow_1_5) and math.isfinite(m2_pow_2)):
        return MomentsResult(EvaluationStatus.NON_EVALUABLE, reason="non_finite_intermediate")

    gamma3 = m3 / m2_pow_1_5
    gamma4 = m4 / m2_pow_2
    if not (math.isfinite(gamma3) and math.isfinite(gamma4)):
        return MomentsResult(EvaluationStatus.NON_EVALUABLE, reason="non_finite_moment")
    return MomentsResult(EvaluationStatus.EVALUABLE, gamma3=gamma3, gamma4=gamma4)


# ---------------------------------------------------------------------------
# DSR-L v1 (ADR-0037 s.5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EffectiveTrialCountEvidence:
    """Caller-supplied explicit effective-trial-count evidence for DSR-L.

    F08 does not estimate trial independence; ``k_eff`` and the opaque
    ``evidence_id`` that justifies it are bound into the result identity.
    This is permanent, not a placeholder for a future estimator (ADR-0037
    Amendment 1, #253).

    Recommended conservative fallback with no principled independence
    estimate: ``k_eff = N`` (the nominal trial count). A higher ``k_eff``
    raises the search-adjusted benchmark ``SR0``, making significance
    *harder* to achieve, not easier -- understating ``k_eff`` is the
    dangerous direction to be wrong in.
    """

    k_eff: float
    evidence_id: str

    def __post_init__(self) -> None:
        k_eff = _finite_float(self.k_eff, "k_eff")
        if k_eff <= 0:
            raise RobustnessError("k_eff must be strictly positive")
        object.__setattr__(self, "k_eff", k_eff)
        object.__setattr__(self, "evidence_id", _non_empty_text(self.evidence_id, "evidence_id"))


@dataclass(frozen=True, slots=True)
class DSRResult:
    """Deterministic immutable DSR-L result evidence (ADR-0037 s.7)."""

    status: EvaluationStatus
    reason: str | None
    population_id: str
    return_semantics_id: str
    trial_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    content_digest: str
    numerical_policy_id: str
    sampling_model: str
    k_eff: float
    k_eff_evidence_id: str
    observation_count: int
    selected_trial_id: str | None = None
    selected_sharpe: float | None = None
    sigma_sr: float | None = None
    sr0: float | None = None
    gamma3: float | None = None
    gamma4: float | None = None
    dsr: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, EvaluationStatus):
            raise RobustnessError("status must be an EvaluationStatus")
        _non_empty_text(self.population_id, "population_id")
        _non_empty_text(self.return_semantics_id, "return_semantics_id")
        if type(self.trial_ids) is not tuple or not self.trial_ids:
            raise RobustnessError("trial_ids must be a non-empty tuple")
        if any(not isinstance(tid, str) or not tid for tid in self.trial_ids):
            raise RobustnessError("trial_ids must contain non-empty strings")
        if len(set(self.trial_ids)) != len(self.trial_ids):
            raise RobustnessError("trial_ids must not contain duplicates")
        if type(self.observation_ids) is not tuple or not self.observation_ids:
            raise RobustnessError("observation_ids must be a non-empty tuple")
        for obs_id in self.observation_ids:
            if isinstance(obs_id, bool) or not isinstance(obs_id, (str, int)):
                raise RobustnessError("observation identity must be a string or integer")
        _non_empty_text(self.content_digest, "content_digest")
        _non_empty_text(self.numerical_policy_id, "numerical_policy_id")
        _non_empty_text(self.sampling_model, "sampling_model")
        if not math.isfinite(self.k_eff) or self.k_eff <= 0:
            raise RobustnessError("k_eff must be a finite positive number")
        _non_empty_text(self.k_eff_evidence_id, "k_eff_evidence_id")
        if type(self.observation_count) is not int or self.observation_count < 1:
            raise RobustnessError("observation_count must be a positive integer")

        evaluable_fields = (
            self.selected_sharpe,
            self.sigma_sr,
            self.sr0,
            self.gamma3,
            self.gamma4,
            self.dsr,
        )
        if self.status is EvaluationStatus.EVALUABLE:
            if self.reason is not None:
                raise RobustnessError("an evaluable DSRResult must not carry a reason")
            if self.selected_trial_id not in self.trial_ids:
                raise RobustnessError("selected_trial_id must be a member of trial_ids")
            if not (1.0 <= self.k_eff <= len(self.trial_ids)):
                raise RobustnessError(
                    f"an evaluable DSRResult requires 1.0 <= k_eff <= trial_count ({len(self.trial_ids)})"
                )
            if 1.0 < self.k_eff < 2.0:
                raise RobustnessError(
                    f"an evaluable DSRResult requires k_eff == 1.0 or k_eff >= 2.0, got {self.k_eff}"
                )
            if self.observation_count < 4:
                raise RobustnessError("an evaluable DSRResult requires observation_count >= 4")
            if self.observation_count != len(self.observation_ids):
                raise RobustnessError(
                    f"an evaluable DSRResult requires observation_count ({self.observation_count}) == "
                    f"len(observation_ids) ({len(self.observation_ids)})"
                )
            if any(field is None for field in evaluable_fields):
                raise RobustnessError("an evaluable DSRResult requires all formula fields")
            if not all(math.isfinite(field) for field in evaluable_fields):  # type: ignore[arg-type]
                raise RobustnessError("an evaluable DSRResult requires finite formula fields")
            assert self.dsr is not None and self.sigma_sr is not None and self.sr0 is not None
            if not (0.0 <= self.dsr <= 1.0):
                raise RobustnessError("an evaluable DSRResult requires dsr in [0, 1]")
            if self.k_eff == 1.0 and self.sr0 != 0.0:
                raise RobustnessError(
                    f"an evaluable DSRResult with k_eff == 1.0 requires sr0 == 0.0, got {self.sr0}"
                )
            if len(self.trial_ids) == 1:
                if self.sigma_sr != 0.0 or self.sr0 != 0.0:
                    raise RobustnessError(
                        "an evaluable single-trial DSRResult (trial_count == 1) requires "
                        f"sigma_sr == 0.0 and sr0 == 0.0, got sigma_sr={self.sigma_sr}, sr0={self.sr0}"
                    )
            else:
                if self.sigma_sr <= 0.0:
                    raise RobustnessError(
                        f"an evaluable multi-trial DSRResult requires positive sigma_sr, got {self.sigma_sr}"
                    )
        else:
            if not self.reason:
                raise RobustnessError("a non-evaluable DSRResult requires an explicit reason")
            if self.selected_trial_id is not None or any(field is not None for field in evaluable_fields):
                raise RobustnessError("a non-evaluable DSRResult must not carry formula evidence")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": DSR_SPEC_VERSION,
            "status": self.status.value,
            "reason": self.reason,
            "population_id": self.population_id,
            "return_semantics_id": self.return_semantics_id,
            "trial_ids": list(self.trial_ids),
            "observation_ids": _encode_observation_ids(self.observation_ids),
            "content_digest": self.content_digest,
            "numerical_policy_id": self.numerical_policy_id,
            "sampling_model": self.sampling_model,
            "k_eff": self.k_eff,
            "k_eff_evidence_id": self.k_eff_evidence_id,
            "observation_count": self.observation_count,
            "selected_trial_id": self.selected_trial_id,
            "selected_sharpe": self.selected_sharpe,
            "sigma_sr": self.sigma_sr,
            "sr0": self.sr0,
            "gamma3": self.gamma3,
            "gamma4": self.gamma4,
            "dsr": self.dsr,
        }

    @property
    def result_id(self) -> str:
        return f"{DSR_SPEC_VERSION}:sha256:{_canonical_fingerprint(self.canonical_payload())}"


def evaluate_dsr_v1(
    panel: ComparableTrialPanel,
    k_eff_evidence: EffectiveTrialCountEvidence,
) -> DSRResult:
    """Evaluate canonical DSR-L over a comparable panel (ADR-0037 s.5)."""

    if not isinstance(panel, ComparableTrialPanel):
        raise RobustnessError("panel must be a ComparableTrialPanel")
    if not isinstance(k_eff_evidence, EffectiveTrialCountEvidence):
        raise RobustnessError("k_eff_evidence must be an EffectiveTrialCountEvidence")

    trial_count = panel.trial_count
    observation_count = panel.observation_count

    def non_evaluable(reason: str) -> DSRResult:
        return DSRResult(
            status=EvaluationStatus.NON_EVALUABLE,
            reason=reason,
            population_id=panel.population_id,
            return_semantics_id=panel.return_semantics_id,
            trial_ids=panel.trial_ids,
            observation_ids=panel.observation_ids,
            content_digest=panel.content_digest,
            numerical_policy_id=NUMERICAL_POLICY_ID,
            sampling_model=SAMPLING_MODEL_IID_V1,
            k_eff=k_eff_evidence.k_eff,
            k_eff_evidence_id=k_eff_evidence.evidence_id,
            observation_count=observation_count,
        )

    if observation_count < 4:
        # ADR-0037 s.4: If T < 4 or m_2 == 0, DSR is non-evaluable.
        return non_evaluable("observation_count_below_4")

    sharpe_by_trial: dict[str, float] = {}
    for trial_id in panel.trial_ids:
        result = compute_sharpe_v1(panel.returns[trial_id])
        if result.status is EvaluationStatus.NON_EVALUABLE:
            return non_evaluable(f"trial_sharpe_non_evaluable:{trial_id}:{result.reason}")
        sharpe_by_trial[trial_id] = result.value  # type: ignore[assignment]

    max_sharpe = max(sharpe_by_trial.values())
    selected_trial_id = min(tid for tid, value in sharpe_by_trial.items() if value == max_sharpe)
    selected_sharpe = sharpe_by_trial[selected_trial_id]

    if trial_count == 1:
        # ADR-0037 s.5.2 explicitly defines sigma_SR := 0 for N == 1; this is
        # a frozen exception, not a degenerate/undefined computation.
        sigma_sr = 0.0
    else:
        mean_sr = sum(sharpe_by_trial.values()) / trial_count
        if not math.isfinite(mean_sr):
            return non_evaluable("sharpe_dispersion_non_evaluable")
        # `*` saturates to +/-inf on overflow instead of raising OverflowError.
        sum_sq_sr = sum((v - mean_sr) * (v - mean_sr) for v in sharpe_by_trial.values())
        if not math.isfinite(sum_sq_sr):
            return non_evaluable("sharpe_dispersion_non_evaluable")
        var_sr = sum_sq_sr / (trial_count - 1)
        if not math.isfinite(var_sr) or var_sr < 0:
            return non_evaluable("sharpe_dispersion_non_evaluable")
        if var_sr == 0.0:
            # For N > 1, zero cross-trial dispersion is degenerate (unlike the
            # ADR-mandated N == 1 exception above) and must not be silently
            # accepted as a valid search-adjusted benchmark scale.
            return non_evaluable("sharpe_dispersion_degenerate")
        sigma_sr = math.sqrt(var_sr)

    k_eff = k_eff_evidence.k_eff
    if not (1.0 <= k_eff <= trial_count):
        return non_evaluable("k_eff_out_of_domain")

    if k_eff == 1.0:
        sr0 = 0.0
    elif k_eff < 2.0:
        return non_evaluable("k_eff_in_open_interval_one_two")
    else:
        quantile_max = 1.0 - 1.0 / k_eff
        quantile_em = 1.0 - 1.0 / (k_eff * math.e)
        if not (0.0 < quantile_max < 1.0 and 0.0 < quantile_em < 1.0):
            return non_evaluable("expected_maximum_quantile_out_of_domain")
        normal = NormalDist()
        try:
            inv_max = normal.inv_cdf(quantile_max)
            inv_em = normal.inv_cdf(quantile_em)
        except (ValueError, ZeroDivisionError):
            return non_evaluable("expected_maximum_inverse_cdf_failed")
        z_max = (1.0 - EULER_MASCHERONI) * inv_max + EULER_MASCHERONI * inv_em
        if not math.isfinite(z_max):
            return non_evaluable("expected_maximum_non_finite")
        sr0 = sigma_sr * z_max

    moments = compute_moments_v1(panel.returns[selected_trial_id])
    if moments.status is EvaluationStatus.NON_EVALUABLE:
        return non_evaluable(f"selected_trial_moments_non_evaluable:{moments.reason}")
    gamma3 = moments.gamma3
    gamma4 = moments.gamma4
    assert gamma3 is not None and gamma4 is not None

    # `*` saturates to +/-inf on overflow for finite binary64 inputs; `**`
    # raises OverflowError instead, which must not escape as an exception.
    selected_sharpe_sq = selected_sharpe * selected_sharpe
    if not math.isfinite(selected_sharpe_sq):
        return non_evaluable("dsr_denominator_domain_invalid")
    radicand = 1.0 - gamma3 * selected_sharpe + ((gamma4 - 1.0) / 4.0) * selected_sharpe_sq
    if not math.isfinite(radicand) or radicand <= 0.0:
        return non_evaluable("dsr_denominator_domain_invalid")

    z_dsr = ((selected_sharpe - sr0) * math.sqrt(observation_count - 1)) / math.sqrt(radicand)
    if not math.isfinite(z_dsr):
        return non_evaluable("dsr_z_non_finite")

    dsr = NormalDist().cdf(z_dsr)
    if not math.isfinite(dsr):
        return non_evaluable("dsr_probability_non_finite")

    return DSRResult(
        status=EvaluationStatus.EVALUABLE,
        reason=None,
        population_id=panel.population_id,
        return_semantics_id=panel.return_semantics_id,
        trial_ids=panel.trial_ids,
        observation_ids=panel.observation_ids,
        content_digest=panel.content_digest,
        numerical_policy_id=NUMERICAL_POLICY_ID,
        sampling_model=SAMPLING_MODEL_IID_V1,
        k_eff=k_eff,
        k_eff_evidence_id=k_eff_evidence.evidence_id,
        observation_count=observation_count,
        selected_trial_id=selected_trial_id,
        selected_sharpe=selected_sharpe,
        sigma_sr=sigma_sr,
        sr0=sr0,
        gamma3=gamma3,
        gamma4=gamma4,
        dsr=dsr,
    )


# ---------------------------------------------------------------------------
# Full CSCV / PBO v1 (ADR-0037 s.6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CSCVSplitEvidence:
    """One deterministic CSCV split's winner, OOS rank and logit evidence."""

    split_index: int
    in_sample_blocks: tuple[int, ...]
    winner_trial_id: str
    oos_rank: float
    omega: float
    logit: float

    def __post_init__(self) -> None:
        if type(self.split_index) is not int or self.split_index < 0:
            raise RobustnessError("split_index must be a non-negative integer")
        if type(self.in_sample_blocks) is not tuple:
            raise RobustnessError("in_sample_blocks must be a tuple")
        if not self.in_sample_blocks or any(
            type(b) is not int or b < 0 for b in self.in_sample_blocks
        ):
            raise RobustnessError("in_sample_blocks must be non-negative integers")
        if len(set(self.in_sample_blocks)) != len(self.in_sample_blocks):
            raise RobustnessError("in_sample_blocks must not contain duplicates")
        _non_empty_text(self.winner_trial_id, "winner_trial_id")
        if not math.isfinite(self.oos_rank) or self.oos_rank < 1.0:
            raise RobustnessError("oos_rank must be finite and >= 1")
        if not math.isfinite(self.omega) or not (0.0 < self.omega < 1.0):
            raise RobustnessError("omega must be finite and strictly within (0, 1)")
        if not math.isfinite(self.logit):
            raise RobustnessError("logit must be finite")
        expected_logit = math.log(self.omega / (1.0 - self.omega))
        if abs(self.logit - expected_logit) > 1e-9:
            raise RobustnessError("logit must equal ln(omega / (1 - omega))")

    def stable_dict(self) -> dict[str, Any]:
        return {
            "split_index": self.split_index,
            "in_sample_blocks": list(self.in_sample_blocks),
            "winner_trial_id": self.winner_trial_id,
            "oos_rank": self.oos_rank,
            "omega": self.omega,
            "logit": self.logit,
        }


@dataclass(frozen=True, slots=True)
class PBOResult:
    """Deterministic immutable full-CSCV PBO result evidence (ADR-0037 s.7)."""

    status: EvaluationStatus
    reason: str | None
    population_id: str
    return_semantics_id: str
    trial_ids: tuple[str, ...]
    observation_ids: tuple[str, ...]
    content_digest: str
    numerical_policy_id: str
    block_count: int
    split_count: int | None = None
    negative_logit_count: int | None = None
    pbo: float | None = None
    splits: tuple[CSCVSplitEvidence, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.status, EvaluationStatus):
            raise RobustnessError("status must be an EvaluationStatus")
        _non_empty_text(self.population_id, "population_id")
        _non_empty_text(self.return_semantics_id, "return_semantics_id")
        if type(self.trial_ids) is not tuple or not self.trial_ids:
            raise RobustnessError("trial_ids must be a non-empty tuple")
        if any(not isinstance(tid, str) or not tid for tid in self.trial_ids):
            raise RobustnessError("trial_ids must contain non-empty strings")
        if len(set(self.trial_ids)) != len(self.trial_ids):
            raise RobustnessError("trial_ids must not contain duplicates")
        if type(self.observation_ids) is not tuple or not self.observation_ids:
            raise RobustnessError("observation_ids must be a non-empty tuple")
        for obs_id in self.observation_ids:
            if isinstance(obs_id, bool) or not isinstance(obs_id, (str, int)):
                raise RobustnessError("observation identity must be a string or integer")
        _non_empty_text(self.content_digest, "content_digest")
        _non_empty_text(self.numerical_policy_id, "numerical_policy_id")
        if type(self.block_count) is not int or self.block_count < 4 or self.block_count % 2 != 0:
            raise RobustnessError("block_count must be an even integer >= 4")
        if type(self.splits) is not tuple:
            raise RobustnessError("splits must be a tuple")
        if not all(isinstance(split, CSCVSplitEvidence) for split in self.splits):
            raise RobustnessError("splits must contain only CSCVSplitEvidence")

        if self.status is EvaluationStatus.EVALUABLE:
            if self.reason is not None:
                raise RobustnessError("an evaluable PBOResult must not carry a reason")
            if len(self.trial_ids) < 2:
                raise RobustnessError("an evaluable PBOResult requires at least two trials (N >= 2)")
            observation_count = len(self.observation_ids)
            if observation_count % self.block_count != 0:
                raise RobustnessError(
                    f"an evaluable PBOResult requires observation_ids length ({observation_count}) to be "
                    f"divisible by block_count ({self.block_count})"
                )
            half_s = self.block_count // 2
            block_size = observation_count // self.block_count
            if block_size * half_s < 2:
                raise RobustnessError(
                    "an evaluable PBOResult requires each IS/OOS half to contain at least 2 observations"
                )
            expected_split_count = math.comb(self.block_count, half_s)
            if (
                type(self.split_count) is not int
                or self.split_count != expected_split_count
                or type(self.negative_logit_count) is not int
                or not (0 <= self.negative_logit_count <= self.split_count)
            ):
                raise RobustnessError(
                    f"an evaluable PBOResult requires split_count == C({self.block_count}, {half_s}) = {expected_split_count} "
                    "and 0 <= negative_logit_count <= split_count"
                )
            if self.pbo is None or not math.isfinite(self.pbo) or not (0.0 <= self.pbo <= 1.0):
                raise RobustnessError("an evaluable PBOResult requires a finite pbo in [0, 1]")
            if abs(self.pbo - self.negative_logit_count / self.split_count) > 1e-9:
                raise RobustnessError("pbo must equal negative_logit_count / split_count")
            if len(self.splits) != expected_split_count:
                raise RobustnessError("splits must contain exactly split_count entries")

            expected_combos = list(combinations(range(self.block_count), half_s))
            for i, split in enumerate(self.splits):
                if split.split_index != i:
                    raise RobustnessError(f"split at index {i} must have split_index {i}, got {split.split_index}")
                if split.in_sample_blocks != expected_combos[i]:
                    raise RobustnessError(
                        f"split at index {i} must have in_sample_blocks {expected_combos[i]}, got {split.in_sample_blocks}"
                    )

            actual_negative = sum(1 for split in self.splits if split.logit < 0.0)
            if self.negative_logit_count != actual_negative:
                raise RobustnessError("negative_logit_count must match count of splits with logit < 0")

            trial_set = set(self.trial_ids)
            n_trials = len(self.trial_ids)
            for split in self.splits:
                if split.winner_trial_id not in trial_set:
                    raise RobustnessError(f"winner_trial_id {split.winner_trial_id} must be a member of trial_ids")
                if not (1.0 <= split.oos_rank <= n_trials):
                    raise RobustnessError(f"split oos_rank {split.oos_rank} out of range [1, {n_trials}]")
                expected_omega = split.oos_rank / (n_trials + 1.0)
                if abs(split.omega - expected_omega) > 1e-9:
                    raise RobustnessError("split omega must equal oos_rank / (N + 1)")
        else:
            if not self.reason:
                raise RobustnessError("a non-evaluable PBOResult requires an explicit reason")
            if (
                self.split_count is not None
                or self.negative_logit_count is not None
                or self.pbo is not None
                or self.splits
            ):
                raise RobustnessError("a non-evaluable PBOResult must not carry split evidence")

    def canonical_payload(self) -> dict[str, Any]:
        return {
            "identity_domain": PBO_SPEC_VERSION,
            "status": self.status.value,
            "reason": self.reason,
            "population_id": self.population_id,
            "return_semantics_id": self.return_semantics_id,
            "trial_ids": list(self.trial_ids),
            "observation_ids": _encode_observation_ids(self.observation_ids),
            "content_digest": self.content_digest,
            "numerical_policy_id": self.numerical_policy_id,
            "block_count": self.block_count,
            "split_count": self.split_count,
            "negative_logit_count": self.negative_logit_count,
            "pbo": self.pbo,
            "splits": [split.stable_dict() for split in self.splits],
        }

    @property
    def result_id(self) -> str:
        return f"{PBO_SPEC_VERSION}:sha256:{_canonical_fingerprint(self.canonical_payload())}"


def _average_rank(oos_sharpe_by_trial: Mapping[str, float], target_trial_id: str) -> float:
    """Rank 1 = worst, rank N = best; exact ties receive the average rank."""

    target_value = oos_sharpe_by_trial[target_trial_id]
    ascending = sorted(oos_sharpe_by_trial.values())
    positions = [index + 1 for index, value in enumerate(ascending) if value == target_value]
    return sum(positions) / len(positions)


def evaluate_pbo_v1(panel: ComparableTrialPanel, block_count: int) -> PBOResult:
    """Evaluate the complete CSCV PBO procedure over a comparable panel (ADR-0037 s.6)."""

    if not isinstance(panel, ComparableTrialPanel):
        raise RobustnessError("panel must be a ComparableTrialPanel")
    if isinstance(block_count, bool) or not isinstance(block_count, int):
        raise RobustnessError("block_count must be an integer")

    trial_count = panel.trial_count
    observation_count = panel.observation_count

    if trial_count < 2:
        raise RobustnessError("PBO requires at least two comparable trials")
    if block_count < 4 or block_count % 2 != 0:
        raise RobustnessError("block_count must be even and at least 4")
    if observation_count % block_count != 0:
        raise RobustnessError("observation_count must be exactly divisible by block_count")

    block_size = observation_count // block_count
    half_block_count = block_count // 2
    if block_size * half_block_count < 2:
        raise RobustnessError("each in-sample/out-of-sample half must contain at least two observations")

    blocks = [
        tuple(range(index * block_size, (index + 1) * block_size))
        for index in range(block_count)
    ]

    def non_evaluable(reason: str) -> PBOResult:
        return PBOResult(
            status=EvaluationStatus.NON_EVALUABLE,
            reason=reason,
            population_id=panel.population_id,
            return_semantics_id=panel.return_semantics_id,
            trial_ids=panel.trial_ids,
            observation_ids=panel.observation_ids,
            content_digest=panel.content_digest,
            numerical_policy_id=NUMERICAL_POLICY_ID,
            block_count=block_count,
        )

    combos = list(combinations(range(block_count), half_block_count))
    split_count = len(combos)
    negative_logit_count = 0
    splits: list[CSCVSplitEvidence] = []

    for split_index, in_sample_blocks in enumerate(combos):
        in_sample_rows = [row for block in in_sample_blocks for row in blocks[block]]
        out_sample_blocks = tuple(b for b in range(block_count) if b not in in_sample_blocks)
        out_sample_rows = [row for block in out_sample_blocks for row in blocks[block]]

        in_sample_sharpe: dict[str, float] = {}
        for trial_id in panel.trial_ids:
            series = panel.returns[trial_id]
            result = compute_sharpe_v1(tuple(series[row] for row in in_sample_rows))
            if result.status is EvaluationStatus.NON_EVALUABLE:
                return non_evaluable(
                    f"in_sample_sharpe_non_evaluable:split_{split_index}:{trial_id}:{result.reason}"
                )
            in_sample_sharpe[trial_id] = result.value  # type: ignore[assignment]

        max_in_sample = max(in_sample_sharpe.values())
        winner_trial_id = min(
            tid for tid, value in in_sample_sharpe.items() if value == max_in_sample
        )

        out_sample_sharpe: dict[str, float] = {}
        for trial_id in panel.trial_ids:
            series = panel.returns[trial_id]
            result = compute_sharpe_v1(tuple(series[row] for row in out_sample_rows))
            if result.status is EvaluationStatus.NON_EVALUABLE:
                return non_evaluable(
                    f"out_of_sample_sharpe_non_evaluable:split_{split_index}:{trial_id}:{result.reason}"
                )
            out_sample_sharpe[trial_id] = result.value  # type: ignore[assignment]

        rank = _average_rank(out_sample_sharpe, winner_trial_id)
        omega = rank / (trial_count + 1)
        logit = math.log(omega / (1.0 - omega))
        if logit < 0.0:
            negative_logit_count += 1

        splits.append(
            CSCVSplitEvidence(
                split_index=split_index,
                in_sample_blocks=in_sample_blocks,
                winner_trial_id=winner_trial_id,
                oos_rank=rank,
                omega=omega,
                logit=logit,
            )
        )

    pbo = negative_logit_count / split_count

    return PBOResult(
        status=EvaluationStatus.EVALUABLE,
        reason=None,
        population_id=panel.population_id,
        return_semantics_id=panel.return_semantics_id,
        trial_ids=panel.trial_ids,
        observation_ids=panel.observation_ids,
        content_digest=panel.content_digest,
        numerical_policy_id=NUMERICAL_POLICY_ID,
        block_count=block_count,
        split_count=split_count,
        negative_logit_count=negative_logit_count,
        pbo=pbo,
        splits=tuple(splits),
    )


__all__ = [
    "DSR_SPEC_VERSION",
    "EULER_MASCHERONI",
    "NUMERICAL_POLICY_ID",
    "PANEL_IDENTITY_DOMAIN",
    "PBO_SPEC_VERSION",
    "SAMPLING_MODEL_IID_V1",
    "CSCVSplitEvidence",
    "ComparableTrialPanel",
    "DSRResult",
    "EffectiveTrialCountEvidence",
    "EvaluationStatus",
    "MomentsResult",
    "PBOResult",
    "RobustnessError",
    "SharpeResult",
    "compute_moments_v1",
    "compute_sharpe_v1",
    "evaluate_dsr_v1",
    "evaluate_pbo_v1",
]
