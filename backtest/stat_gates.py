#!/usr/bin/env python3
"""Statistical Gate Infrastructure for Overfitting Control
============================================================
Four automated gates that prevent promoting undersampled or overfit
parameter cells to production. Built before any parameter fitting begins.

Gates:
  1. Mann-Whitney U test: non-parametric test for session/regime differentiation
  2. Sample-size validator: 30 x N_free_params rule (Bailey et al., 2014)
  3. Deflated Sharpe Ratio (DSR): corrects for multiple testing (Bailey & Lopez de Prado, 2014)
  4. Walk-Forward Efficiency (WFE): OOS return / IS return, annualized

Usage:
  from stat_gates import mann_whitney_test, validate_sample_size, deflated_sharpe_ratio, walk_forward_efficiency
"""
import math
from dataclasses import dataclass
from typing import List, Tuple, Optional

import numpy as np
from scipy import stats


# ── 1. MANN-WHITNEY U TEST ──────────────────────────────────────────────────

@dataclass
class MannWhitneyResult:
    u_statistic: float
    p_value: float
    effect_size_r: float
    significant: bool
    alpha: float


def mann_whitney_test(
    group_a: np.ndarray,
    group_b: np.ndarray,
    alpha: float = 0.05,
    alternative: str = "two-sided",
) -> MannWhitneyResult:
    """Two-sample Mann-Whitney U test on trade R-multiples.

    Non-parametric: no normality assumption on trade P&L.
    Returns (U, p, effect_size_r, significant).

    effect_size_r = |Z| / sqrt(N)  (Rosenthal, 1991)
    N = len(a) + len(b)

    Gate: p < alpha required to justify a session/regime split.
    """
    a = np.asarray(group_a, dtype=float)
    b = np.asarray(group_b, dtype=float)
    a = a[~np.isnan(a)]
    b = b[~np.isnan(b)]

    if len(a) < 5 or len(b) < 5:
        return MannWhitneyResult(
            u_statistic=float("nan"),
            p_value=1.0,
            effect_size_r=0.0,
            significant=False,
            alpha=alpha,
        )

    u_stat, p_value = stats.mannwhitneyu(a, b, alternative=alternative)

    n = len(a) + len(b)
    z = (u_stat - len(a) * len(b) / 2) / math.sqrt(len(a) * len(b) * (n + 1) / 12)
    effect_size_r = abs(z) / math.sqrt(n)

    return MannWhitneyResult(
        u_statistic=float(u_stat),
        p_value=float(p_value),
        effect_size_r=float(effect_size_r),
        significant=bool(p_value < alpha),
        alpha=alpha,
    )


# ── 2. SAMPLE-SIZE VALIDATOR ────────────────────────────────────────────────

@dataclass
class SampleSizeResult:
    cell_trades: int
    free_params: int
    required_trades: int
    passed: bool
    ratio: float


def validate_sample_size(
    cell_trades: int,
    free_params: int,
    min_per_param: int = 30,
) -> SampleSizeResult:
    """Validate cell trade count against the 30 x N_free_params rule.

    Bailey et al. (2014): minimum 30 trades per free optimized parameter
    per validation cell. With 3 free params (sl_mult, tp_mult, adx_min),
    that is 90 trades per cell minimum.

    Returns PASS/FAIL and the ratio of actual to required.
    """
    if free_params <= 0:
        raise ValueError("free_params must be >= 1")
    if cell_trades < 0:
        raise ValueError("cell_trades must be >= 0")

    required = min_per_param * free_params
    passed = cell_trades >= required
    ratio = cell_trades / required if required > 0 else 0.0

    return SampleSizeResult(
        cell_trades=cell_trades,
        free_params=free_params,
        required_trades=required,
        passed=bool(passed),
        ratio=float(ratio),
    )


# ── 3. DEFLATED SHARPE RATIO ────────────────────────────────────────────────

@dataclass
class DSRResult:
    sharpe: float
    n_trials: int
    dsr: float
    passed: bool
    threshold: float
    variance: float
    skew: float
    kurtosis: float


def deflated_sharpe_ratio(
    sharpe: float,
    n_trials: int,
    returns: np.ndarray,
    threshold: float = 0.80,
    periods_per_year: int = 252,
) -> DSRResult:
    """Compute the Deflated Sharpe Ratio (Bailey & Lopez de Prado, 2014).

    Corrects the observed Sharpe ratio for:
    - Multiple testing (n_trials)
    - Non-normality (skew, kurtosis)
    - Selection bias (variance across trial returns)

    Formula:
      SR_0 = expected maximum Sharpe under the null (all trials have zero edge)
           = sqrt(2 * ln(n_trials)) * sigma_SR  (approximation)
      where sigma_SR = sqrt((1 - skew*SR + (kurtosis-1)/4 * SR^2) / (n-1))

      DSR = Phi((SR_observed - SR_0) / sigma_SR)

    Practical thresholds:
      DSR > 0.95: strong evidence of genuine edge
      DSR 0.80-0.95: probable but borderline
      DSR < 0.80: likely noise

    Parameters:
        sharpe: observed annualized Sharpe ratio
        n_trials: total number of parameter configurations tested
        returns: array of period returns (used for skew, kurtosis, variance)
        threshold: minimum DSR to pass (default 0.80)
        periods_per_year: annualization factor (252 for daily, 12 for monthly)
    """
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]

    if len(r) < 5:
        return DSRResult(
            sharpe=sharpe,
            n_trials=n_trials,
            dsr=0.0,
            passed=False,
            threshold=threshold,
            variance=0.0,
            skew=0.0,
            kurtosis=0.0,
        )

    n = len(r)
    skew_val = float(stats.skew(r, bias=False))
    kurt_val = float(stats.kurtosis(r, fisher=False, bias=False))
    variance = float(np.var(r, ddof=1))

    if n_trials < 1:
        n_trials = 1

    # SR_0: expected max Sharpe under null hypothesis (zero edge across all trials)
    # Uses the expected maximum of n_trials draws from N(0, sigma_SR^2)
    # Approximation: E[max] = sigma_SR * sqrt(2 * ln(n_trials))
    # sigma_SR = standard error of the Sharpe ratio estimate
    sr_annual = sharpe / math.sqrt(periods_per_year) if periods_per_year > 0 else sharpe

    # Non-normality correction (Lo, 2002; Bailey & Lopez de Prado, 2014)
    # Var(SR) = (1 - skew*SR + (kurt-1)/4 * SR^2) / (n-1)
    if n > 1:
        sr_var = (1 - skew_val * sr_annual + (kurt_val - 1) / 4 * sr_annual ** 2) / (n - 1)
        sr_var = max(sr_var, 1e-10)
    else:
        sr_var = 1e-10

    sigma_sr = math.sqrt(sr_var)

    # Expected maximum Sharpe under null: E[max of n_trials N(0, sigma_sr^2)]
    # = sigma_sr * sqrt(2 * ln(n_trials))
    sr_0 = sigma_sr * math.sqrt(2 * math.log(n_trials)) if n_trials > 1 else 0.0

    # DSR = Phi((SR_observed - SR_0) / sigma_SR)
    z = (sr_annual - sr_0) / sigma_sr if sigma_sr > 0 else 0.0
    dsr = float(stats.norm.cdf(z))

    return DSRResult(
        sharpe=float(sharpe),
        n_trials=int(n_trials),
        dsr=dsr,
        passed=bool(dsr >= threshold),
        threshold=float(threshold),
        variance=variance,
        skew=skew_val,
        kurtosis=kurt_val,
    )


# ── 4. WALK-FORWARD EFFICIENCY ──────────────────────────────────────────────

@dataclass
class WFEResult:
    wfe: float
    is_return_annual: float
    oos_return_annual: float
    passed: bool
    threshold: float


def walk_forward_efficiency(
    is_returns: np.ndarray,
    oos_returns: np.ndarray,
    is_periods: int,
    oos_periods: int,
    periods_per_year: int = 252,
    threshold: float = 0.5,
) -> WFEResult:
    """Compute Walk-Forward Efficiency = OOS_annual_return / IS_annual_return.

    WFE >= 0.5 required (OOS captures at least half the IS annualized return).
    WFE >= 0.7 is the target.

    Parameters:
        is_returns: in-sample period returns (per-period, not cumulative)
        oos_returns: out-of-sample period returns
        is_periods: number of periods in the IS window
        oos_periods: number of periods in the OOS window
        periods_per_year: annualization factor
        threshold: minimum WFE to pass (default 0.5)
    """
    is_r = np.asarray(is_returns, dtype=float)
    is_r = is_r[~np.isnan(is_r)]
    oos_r = np.asarray(oos_returns, dtype=float)
    oos_r = oos_r[~np.isnan(oos_r)]

    if len(is_r) == 0 or len(oos_r) == 0:
        return WFEResult(
            wfe=0.0,
            is_return_annual=0.0,
            oos_return_annual=0.0,
            passed=False,
            threshold=threshold,
        )

    # Annualized return: (1 + total_return)^(periods_per_year / n_periods) - 1
    is_total = float(np.prod(1 + is_r) - 1)
    oos_total = float(np.prod(1 + oos_r) - 1)

    if is_periods > 0:
        is_annual = (1 + is_total) ** (periods_per_year / is_periods) - 1
    else:
        is_annual = 0.0

    if oos_periods > 0:
        oos_annual = (1 + oos_total) ** (periods_per_year / oos_periods) - 1
    else:
        oos_annual = 0.0

    if abs(is_annual) < 1e-10:
        wfe = 0.0 if oos_annual <= 0 else float("inf")
    else:
        wfe = oos_annual / is_annual

    passed = bool(wfe >= threshold) if not math.isinf(wfe) else False

    return WFEResult(
        wfe=float(wfe),
        is_return_annual=float(is_annual),
        oos_return_annual=float(oos_annual),
        passed=passed,
        threshold=float(threshold),
    )


# ── CONVENIENCE: RUN ALL GATES ON A CELL ────────────────────────────────────

@dataclass
class CellGateResult:
    sample_size: SampleSizeResult
    mann_whitney: Optional[MannWhitneyResult]
    dsr: Optional[DSRResult]
    wfe: Optional[WFEResult]
    overall_pass: bool


def evaluate_cell(
    cell_trades: int,
    free_params: int,
    cell_returns: Optional[np.ndarray] = None,
    parent_returns: Optional[np.ndarray] = None,
    n_trials: int = 1,
    is_returns: Optional[np.ndarray] = None,
    oos_returns: Optional[np.ndarray] = None,
    is_periods: int = 0,
    oos_periods: int = 0,
    dsr_threshold: float = 0.80,
    wfe_threshold: float = 0.5,
    mw_alpha: float = 0.05,
) -> CellGateResult:
    """Run all applicable gates on a parameter cell.

    sample_size is always checked.
    mann_whitney is checked when parent_returns are provided (tests cell vs parent differentiation).
    dsr is checked when cell_returns and n_trials are provided.
    wfe is checked when is_returns and oos_returns are provided.
    """
    ss = validate_sample_size(cell_trades, free_params)

    mw = None
    if parent_returns is not None and cell_returns is not None:
        mw = mann_whitney_test(cell_returns, parent_returns, alpha=mw_alpha)

    dsr = None
    if cell_returns is not None and n_trials > 0:
        sharpe = _compute_sharpe(cell_returns)
        dsr = deflated_sharpe_ratio(sharpe, n_trials, cell_returns, threshold=dsr_threshold)

    wfe = None
    if is_returns is not None and oos_returns is not None:
        wfe = walk_forward_efficiency(is_returns, oos_returns, is_periods, oos_periods, threshold=wfe_threshold)

    gates = [ss.passed]
    if mw is not None:
        gates.append(mw.significant)
    if dsr is not None:
        gates.append(dsr.passed)
    if wfe is not None:
        gates.append(wfe.passed)

    return CellGateResult(
        sample_size=ss,
        mann_whitney=mw,
        dsr=dsr,
        wfe=wfe,
        overall_pass=all(gates),
    )


def _compute_sharpe(returns: np.ndarray, periods_per_year: int = 252) -> float:
    """Annualized Sharpe ratio from period returns (risk-free = 0)."""
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if len(r) < 2:
        return 0.0
    mean_r = float(np.mean(r))
    std_r = float(np.std(r, ddof=1))
    if std_r < 1e-10:
        return 0.0
    return (mean_r / std_r) * math.sqrt(periods_per_year)


if __name__ == "__main__":
    print("stat_gates.py — self-test with synthetic data")
    print("=" * 50)

    # Mann-Whitney: two distinguishable groups
    np.random.seed(42)
    a = np.random.normal(0.5, 1.0, 100)
    b = np.random.normal(0.0, 1.0, 100)
    mw = mann_whitney_test(a, b)
    print(f"\n1. Mann-Whitney U:")
    print(f"   U={mw.u_statistic:.1f}, p={mw.p_value:.6f}, r={mw.effect_size_r:.3f}, significant={mw.significant}")

    # Sample size
    ss = validate_sample_size(45, 3)
    print(f"\n2. Sample Size:")
    print(f"   trades={ss.cell_trades}, required={ss.required_trades}, passed={ss.passed}, ratio={ss.ratio:.2f}")

    ss2 = validate_sample_size(120, 3)
    print(f"   trades={ss2.cell_trades}, required={ss2.required_trades}, passed={ss2.passed}, ratio={ss2.ratio:.2f}")

    # DSR
    returns = np.random.normal(0.001, 0.02, 252)
    dsr = deflated_sharpe_ratio(1.5, 20, returns)
    print(f"\n3. Deflated Sharpe Ratio:")
    print(f"   SR={dsr.sharpe:.2f}, trials={dsr.n_trials}, DSR={dsr.dsr:.4f}, passed={dsr.passed}")
    print(f"   skew={dsr.skew:.3f}, kurt={dsr.kurtosis:.3f}")

    # WFE
    is_ret = np.random.normal(0.002, 0.01, 180)
    oos_ret = np.random.normal(0.0015, 0.01, 60)
    wfe = walk_forward_efficiency(is_ret, oos_ret, 180, 60)
    print(f"\n4. Walk-Forward Efficiency:")
    print(f"   WFE={wfe.wfe:.3f}, IS_annual={wfe.is_return_annual:.4f}, OOS_annual={wfe.oos_return_annual:.4f}, passed={wfe.passed}")

    print("\n" + "=" * 50)
    print("All self-tests completed.")
