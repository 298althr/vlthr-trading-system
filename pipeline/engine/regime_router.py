"""
VLTHR Regime Router — HMM + Rule-Based Regime Gate
===================================================
Phase 2 module: uses per-symbol Gaussian HMM for TRENDING/RANGING
classification, with rule-based fallback (ADX + efficiency ratio + ATR rank)
when no HMM model is loaded or confidence is below threshold.

VOLATILE detection remains rule-based (ATR spike is orthogonal to HMM state).

Inputs:
  - enriched DataFrame (must have adx, atr, close, high, low columns)
  - strategy: "trend_following" or "mean_reversion"
  - session: one of ACTIVE_SESSIONS or "asian"/"off"
  - symbol: symbol name for HMM model lookup

Outputs:
  - RegimeGateResult dataclass with:
    - regime: "TRENDING" | "RANGING" | "VOLATILE" | "MIXED"
    - efficiency_ratio: float 0-1
    - adx: float
    - session_quality: float 0-1
    - allowed: bool
    - reason: str

Gate Logic:
  TRENDING  (HMM state or adx >= 25, ER >= 0.30, atr_rank < 0.7):
    → trend_following: ALLOWED
    → mean_reversion:  VETOED (fighting the trend)

  RANGING   (HMM state or adx < 20, ER < 0.20, atr_rank < 0.7):
    → mean_reversion:  ALLOWED
    → trend_following: VETOED (no trend to follow)

  VOLATILE  (atr_rank >= 0.8):
    → Both strategies VETOED unless DQS >= REGIME_MIN_DQS["VOLATILE"]

  MIXED     (everything else):
    → Both strategies ALLOWED (let DQS and other gates decide)
"""

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class RegimeGateResult:
    regime: str
    efficiency_ratio: float
    adx: float
    atr_rank: float
    session_quality: float
    allowed: bool
    reason: str


# ── THRESHOLDS ──────────────────────────────────────────────────────────────

_ADX_TREND = 25.0
_ADX_RANGE = 20.0
_ER_TREND = 0.30
_ER_RANGE = 0.20
_ATR_VOLATILE_RANK = 0.80
_ATR_TREND_MAX_RANK = 0.70
_ER_LOOKBACK = 20  # bars (20 * 15m = 5h window)

_SESSION_QUALITY = {
    "london": 0.90,
    "ny_open": 0.80,
    "ny_late": 0.70,
    "asian": 0.45,
    "off": 0.30,
}


def compute_efficiency_ratio(enriched: pd.DataFrame, lookback: int = _ER_LOOKBACK) -> float:
    """Kaufman's Efficiency Ratio: |price_change| / sum(|bar_to_bar_changes|).

    ER near 1.0 = strong directional move (trending).
    ER near 0.0 = choppy/noise (ranging).
    """
    if len(enriched) < lookback + 1 or "close" not in enriched.columns:
        return 0.5  # neutral default

    closes = enriched["close"].tail(lookback + 1).dropna()
    if len(closes) < lookback:
        return 0.5

    direction = abs(float(closes.iloc[-1]) - float(closes.iloc[0]))
    volatility = float(closes.diff().abs().sum())

    if volatility <= 0:
        return 0.0

    er = direction / volatility
    return round(max(0.0, min(1.0, er)), 4)


def _compute_atr_rank(enriched: pd.DataFrame) -> float:
    """ATR percentile rank from last 50 bars (same logic as v2_filters)."""
    if len(enriched) < 50 or "atr" not in enriched.columns or "close" not in enriched.columns:
        return 0.5
    atr_pct = (enriched["atr"] / enriched["close"] * 100).tail(50)
    rank = atr_pct.rank(pct=True)
    val = float(rank.iloc[-1]) if len(rank) > 0 else 0.5
    return val if not pd.isna(val) else 0.5


def classify_regime(enriched: pd.DataFrame, symbol: str = None) -> tuple:
    """Classify regime from enriched data.

    Returns (regime_str, adx, er, atr_rank).
    Uses HMM prediction when available, falls back to rule-based.

    HMM handles TRENDING vs RANGING. ATR rank still handles VOLATILE
    detection (spike detection is orthogonal to HMM state).
    """
    if len(enriched) < 55 or "adx" not in enriched.columns or "atr" not in enriched.columns:
        return "MIXED", 0.0, 0.5, 0.5

    last = enriched.iloc[-1]
    adx = float(last.get("adx", 0))
    if pd.isna(adx):
        adx = 0.0

    er = compute_efficiency_ratio(enriched)
    atr_rank = _compute_atr_rank(enriched)

    # VOLATILE detection stays rule-based (ATR spike is orthogonal to HMM state)
    if atr_rank >= _ATR_VOLATILE_RANK:
        return "VOLATILE", adx, er, atr_rank

    # Try HMM prediction for TRENDING vs RANGING
    if symbol is not None:
        try:
            from regime_hmm import predict_regime, _REGIME_CACHE
            # Check pre-computed cache first (by timestamp)
            if symbol in _REGIME_CACHE and len(_REGIME_CACHE[symbol]) > 0:
                ts = last.get("timestamp") if "timestamp" in enriched.columns else None
                if ts is not None and ts in _REGIME_CACHE[symbol]:
                    hmm_regime, confidence = _REGIME_CACHE[symbol][ts]
                    if confidence >= 0.55 and hmm_regime in ("TRENDING", "RANGING"):
                        return hmm_regime, adx, er, atr_rank
            # Fall back to live prediction
            hmm_regime, confidence = predict_regime(symbol, enriched)
            if confidence >= 0.55 and hmm_regime in ("TRENDING", "RANGING"):
                return hmm_regime, adx, er, atr_rank
        except Exception:
            pass  # fall through to rule-based

    # Rule-based fallback
    if adx >= _ADX_TREND and er >= _ER_TREND and atr_rank < _ATR_TREND_MAX_RANK:
        return "TRENDING", adx, er, atr_rank

    if adx < _ADX_RANGE and er < _ER_RANGE and atr_rank < _ATR_TREND_MAX_RANK:
        return "RANGING", adx, er, atr_rank

    return "MIXED", adx, er, atr_rank


def evaluate_regime_gate(
    enriched: pd.DataFrame,
    strategy: str,
    session: str = "",
    dqs: int = 0,
    regime_min_dqs: Optional[dict] = None,
    symbol: str = None,
) -> RegimeGateResult:
    """Evaluate whether a signal's strategy is aligned with the current regime.

    This is a PASS/FAIL gate. It does not modify the signal — it only
    returns allowed=True/False with a human-readable reason.

    Parameters:
        enriched: DataFrame from load_enriched()
        strategy: "trend_following" or "mean_reversion"
        session: session string from enriched data
        dqs: signal DQS score (used for VOLATILE override)
        regime_min_dqs: optional override for REGIME_MIN_DQS dict
        symbol: symbol name for HMM regime lookup
    """
    regime, adx, er, atr_rank = classify_regime(enriched, symbol=symbol)
    session_quality = _SESSION_QUALITY.get(session, 0.30)

    if regime_min_dqs is None:
        from portfolio_config import REGIME_MIN_DQS
        regime_min_dqs = REGIME_MIN_DQS

    strategy = (strategy or "trend_following").lower().strip()

    # VOLATILE: veto both strategies unless DQS clears the elevated threshold
    if regime == "VOLATILE":
        min_dqs = regime_min_dqs.get("VOLATILE", 60)
        if dqs >= min_dqs:
            return RegimeGateResult(regime, er, adx, atr_rank, session_quality,
                                    True, f"VOLATILE but DQS {dqs} >= {min_dqs}")
        return RegimeGateResult(regime, er, adx, atr_rank, session_quality,
                                False, f"VOLATILE regime veto (DQS {dqs} < {min_dqs})")

    # TRENDING: allow trend_following, veto mean_reversion
    if regime == "TRENDING":
        if strategy == "trend_following":
            return RegimeGateResult(regime, er, adx, atr_rank, session_quality,
                                    True, "TRENDING regime, trend_following aligned")
        return RegimeGateResult(regime, er, adx, atr_rank, session_quality,
                                False, "TRENDING regime, mean_reversion misaligned")

    # RANGING: allow mean_reversion, veto trend_following
    if regime == "RANGING":
        if strategy == "mean_reversion":
            return RegimeGateResult(regime, er, adx, atr_rank, session_quality,
                                    True, "RANGING regime, mean_reversion aligned")
        return RegimeGateResult(regime, er, adx, atr_rank, session_quality,
                                False, "RANGING regime, trend_following misaligned")

    # MIXED: allow both — let downstream gates (DQS, Kelly, calibration) decide
    return RegimeGateResult(regime, er, adx, atr_rank, session_quality,
                            True, "MIXED regime, no strategy veto")
