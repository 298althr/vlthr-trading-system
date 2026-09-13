"""
Adaptive Scorer — Weighted-Max DQS with Regime Awareness
=========================================================
Replaces the averaging-based DQS with a weighted-max architecture
that learns from trade outcomes and adapts to market regime.

Key differences from old scorer:
  - Uses weighted average of domains (40/30/30), not max
  - ADX acts as a trend-quality multiplier (not a raw score input)
  - RSI gives pullback-depth bonus (not a raw score input)
  - Liquidity alignment gives composite bonus
  - Session and symbol have learned multipliers
  - Contradictions penalize heavily instead of mild deduction

Architecture:
  DQS = (weighted_avg + pullback_bonus + liquidity_bonus) * trend_mult * session_mult * symbol_mult - contra_penalty

where:
  weighted_avg = technical_raw * 0.40 + structure_raw * 0.30 + context_raw * 0.30
  trend_mult = f(ADX) — higher ADX = higher multiplier
  pullback_bonus = f(RSI) — deeper pullback = higher bonus
  liquidity_bonus = f(funding, OI, orderbook) — aligned = bonus
  session_mult = learned from trade history per session
  symbol_mult = learned from trade history per symbol
  contra_penalty = heavy penalty for contradictory signals
"""

import json
import os
from pathlib import Path
from typing import Optional, Tuple, Dict

import pandas as pd

# ── CONFIGURATION (loaded from calibration.json if exists) ─────────────────

_CALIBRATION_PATH = Path(os.environ.get(
    "VLTHR_CAL_OVERRIDE",
    Path(__file__).resolve().parent / "calibration.json"
))

# Default calibration (will be overwritten by calibration engine)
DEFAULT_CALIBRATION = {
    "version": 5,
    "v7_gold_standard": True,
    "session_multipliers": {
        "london": 1.26,
        "ny_open": 1.15,
        "ny_late": 0.85,
        "asian": 1.00,
    },
    "symbol_multipliers": {
        "BTCUSDT": 1.42,
        "SOLUSDT": 1.45,
        "ETHUSDT": 0.88,
        "XRPUSDT": 1.20,
        "BNBUSDT": 0.55,
        "DOGEUSDT": 0.0,
    },
    "trend_mult": {
        "adx_ge_50": 1.35,
        "adx_ge_40": 1.20,
        "adx_ge_30": 1.00,
        "adx_ge_25": 0.80,
        "adx_lt_25": 0.50,
    },
    "pullback_bonus": {
        "rsi_lt_20": 20,
        "rsi_lt_30": 15,
        "rsi_lt_35": 10,
        "rsi_lt_40": 5,
        "rsi_ge_40": 0,
    },
    "liquidity_bonus": {
        "aligned": 15,      # negative funding + falling OI + positive imbalance
        "partial": 8,       # 2 of 3 aligned
        "neutral": 0,
        "hostile": -10,     # positive funding + rising OI + negative imbalance
    },
    "domain_weights": {
        "technical": 0.40,
        "structure": 0.30,
        "context": 0.30,
    },
    "contra_penalty": {
        "n1": 5,
        "n2": 15,
        "n3_plus": 30,
    },
    "min_dqs_to_track": 40,
    "min_dqs_to_execute": 50,
    "min_dqs_to_pending": 50,
    "min_dqs_excellent": 75,
    "min_dqs_good": 65,
    "min_dqs_fair": 50
}


def _load_calibration() -> dict:
    if _CALIBRATION_PATH.exists():
        with open(_CALIBRATION_PATH) as f:
            return json.load(f)
    return DEFAULT_CALIBRATION


def lookup_calibrated_prob(dqs: float, symbol: str = None) -> float:
    """
    Look up calibrated win probability for a given DQS score.
    Falls back to symbol-specific curve if available, otherwise global.
    """
    cal = _load_calibration()
    curve = cal.get("calibration_curve", {})
    if not curve:
        return 0.5  # neutral when uncalibrated

    # Try symbol-specific curve first
    sym_curves = curve.get("symbol_curves", {})
    use_curve = sym_curves.get(symbol, curve)

    bps = use_curve.get("dqs_breakpoints", [])
    probs = use_curve.get("win_probabilities", [])
    if not bps or not probs:
        return 0.5

    # Linear interpolation between breakpoints
    dqs = max(0, min(100, dqs))
    for i in range(len(bps) - 1):
        if bps[i] <= dqs <= bps[i + 1]:
            t = (dqs - bps[i]) / (bps[i + 1] - bps[i]) if (bps[i + 1] - bps[i]) > 0 else 0
            return round(probs[i] + t * (probs[i + 1] - probs[i]), 4)
    # Below first or above last
    if dqs <= bps[0]:
        return probs[0]
    return probs[-1]


CAL = _load_calibration()


# ── DOMAIN SCORING (raw 0-100 each, before multipliers) ─────────────────────

def _score_technical_raw(rsi: float, adx: float, log_ret_4h: float, rsi_threshold: float = 40.0, direction: str = "LONG") -> float:
    """
    Technical raw score 0-100. Different from old scorer:
    - RSI gives DEEP pullback bonus for LONG (lower = better)
    - RSI gives OVERBOUGHT bonus for SHORT (higher = better)
    - ADX gives trend strength bonus (higher = better)
    - Momentum confirms direction
    """
    if direction == "SHORT":
        # Overbought = good entry for SHORT
        if rsi > 80:
            rsi_score = 100
        elif rsi > 70:
            rsi_score = 85
        elif rsi > 60:
            rsi_score = 50
        elif rsi >= rsi_threshold:
            rsi_score = 30
        else:
            rsi_score = 0  # not overbought enough
    else:
        # Pullback depth: deeper = higher score (LONG)
        if rsi < 20:
            rsi_score = 100
        elif rsi < 28:
            rsi_score = 85
        elif rsi < 35:
            rsi_score = 70
        elif rsi < 40:
            rsi_score = 50
        elif rsi < rsi_threshold:
            rsi_score = 30
        else:
            rsi_score = 0  # failed gate

    # Trend strength: stronger = higher score
    if adx >= 50:
        adx_score = 100
    elif adx >= 40:
        adx_score = 85
    elif adx >= 30:
        adx_score = 70
    elif adx >= 25:
        adx_score = 50
    else:
        adx_score = 20  # weak trend, not zero

    # Momentum alignment: confirms the 4h direction
    if abs(log_ret_4h) >= 0.015:
        mom_score = 100
    elif abs(log_ret_4h) >= 0.008:
        mom_score = 80
    elif abs(log_ret_4h) >= 0.003:
        mom_score = 60
    elif abs(log_ret_4h) > 0:
        mom_score = 40
    else:
        mom_score = 0

    return round((rsi_score * 0.50 + adx_score * 0.35 + mom_score * 0.15), 2)


def _score_structure_raw(ob_features, funding_rate: float, oi_delta_pct: float,
                         direction: str = "LONG") -> float:
    """
    Structure raw score 0-100. Focuses on liquidity alignment:
    - Spread (tight = good)
    - Depth (deep = good)
    - Imbalance (bid-heavy = good for LONG, ask-heavy = good for SHORT)
    - Funding (negative = good for LONG, positive = good for SHORT)
    - OI delta (falling = good for LONG = less crowded; rising = good for SHORT = crowded longs)
    """
    if ob_features is None:
        # Missing data = penalize but not zero
        return 35.0

    spread = ob_features.get("spread_pct", 1.0)
    depth = ob_features.get("depth_usd", 0)
    imbalance = ob_features.get("imbalance_pct", 0)

    # Spread score (direction-agnostic)
    if spread < 0.005:
        spread_score = 100
    elif spread < 0.01:
        spread_score = 85
    elif spread < 0.03:
        spread_score = 65
    elif spread < 0.10:
        spread_score = 40
    else:
        spread_score = 15

    # Depth score (direction-agnostic)
    if depth >= 1_000_000:
        depth_score = 100
    elif depth >= 500_000:
        depth_score = 80
    elif depth >= 200_000:
        depth_score = 60
    elif depth >= 100_000:
        depth_score = 40
    else:
        depth_score = 20

    if direction == "SHORT":
        # Imbalance: ask-heavy (negative) = good for SHORT
        if imbalance <= -15:
            imb_score = 100
        elif imbalance <= -5:
            imb_score = 75
        elif imbalance <= 0:
            imb_score = 50
        elif imbalance <= 10:
            imb_score = 30
        else:
            imb_score = 10

        # Funding: positive = good for SHORT (shorts receive funding)
        if funding_rate > 0.0005:
            fund_score = 100
        elif funding_rate > 0:
            fund_score = 80
        elif funding_rate > -0.0001:
            fund_score = 60
        elif funding_rate > -0.0005:
            fund_score = 30
        else:
            fund_score = 10

        # OI delta: rising = crowded longs = good for SHORT
        if oi_delta_pct > 2:
            oi_score = 100
        elif oi_delta_pct > 0:
            oi_score = 80
        elif oi_delta_pct > -3:
            oi_score = 50
        else:
            oi_score = 20
    else:
        # Imbalance: bid-heavy (positive) = good for LONG
        if imbalance >= 15:
            imb_score = 100
        elif imbalance >= 5:
            imb_score = 75
        elif imbalance >= 0:
            imb_score = 50
        elif imbalance >= -10:
            imb_score = 30
        else:
            imb_score = 10

        # Funding: negative = good for LONG
        if funding_rate < -0.0005:
            fund_score = 100
        elif funding_rate < 0:
            fund_score = 80
        elif funding_rate < 0.0001:
            fund_score = 60
        elif funding_rate < 0.0005:
            fund_score = 30
        else:
            fund_score = 10

        # OI delta: falling = less crowded = good for LONG
        if oi_delta_pct < -2:
            oi_score = 100
        elif oi_delta_pct < 0:
            oi_score = 80
        elif oi_delta_pct < 3:
            oi_score = 50
        else:
            oi_score = 20

    return round((spread_score * 0.20 + depth_score * 0.20 + imb_score * 0.25 +
                  fund_score * 0.20 + oi_score * 0.15), 2)


def _score_context_raw(session: str, symbol: str, macro_penalty: int = 0) -> float:
    """
    Context raw score 0-100. Session quality + symbol proven quality.
    """
    session_scores = {
        "london": 90,
        "ny_open": 75,
        "ny_late": 70,
        "asian": 50,
    }
    sess_score = session_scores.get(session, 40)

    # Symbol quality from calibration
    sym_mult = CAL["symbol_multipliers"].get(symbol, 1.0)
    # Map multiplier to a base score
    if sym_mult >= 1.2:
        sym_score = 100
    elif sym_mult >= 1.1:
        sym_score = 85
    elif sym_mult >= 1.0:
        sym_score = 70
    elif sym_mult >= 0.8:
        sym_score = 50
    else:
        sym_score = 35

    raw = (sess_score + sym_score) / 2
    return round(max(0, raw - macro_penalty), 2)


# ── MULTIPLIERS & BONUSES ───────────────────────────────────────────────────

def _trend_multiplier(adx: float) -> float:
    cfg = CAL["trend_mult"]
    if adx >= 50:
        return cfg["adx_ge_50"]
    elif adx >= 40:
        return cfg["adx_ge_40"]
    elif adx >= 30:
        return cfg["adx_ge_30"]
    elif adx >= 25:
        return cfg["adx_ge_25"]
    return cfg["adx_lt_25"]


def _pullback_bonus(rsi: float, direction: str = "LONG") -> float:
    cfg = CAL["pullback_bonus"]
    if direction == "SHORT":
        # Overbought = good for SHORT
        if rsi > 80:
            return cfg["rsi_lt_20"]
        elif rsi > 70:
            return cfg["rsi_lt_30"]
        elif rsi > 65:
            return cfg["rsi_lt_35"]
        elif rsi > 60:
            return cfg["rsi_lt_40"]
        return cfg["rsi_ge_40"]
    # LONG (original)
    if rsi < 20:
        return cfg["rsi_lt_20"]
    elif rsi < 30:
        return cfg["rsi_lt_30"]
    elif rsi < 35:
        return cfg["rsi_lt_35"]
    elif rsi < 40:
        return cfg["rsi_lt_40"]
    return cfg["rsi_ge_40"]


def _liquidity_bonus(funding_rate: float, oi_delta_pct: float, ob_imbalance_pct: float,
                     direction: str = "LONG") -> float:
    """
    Composite liquidity alignment bonus.
    LONG winner fingerprint: negative funding + falling OI + positive imbalance.
    SHORT winner fingerprint: positive funding + rising OI + negative imbalance.
    """
    aligned = 0
    hostile = 0

    if direction == "SHORT":
        if funding_rate > 0:
            aligned += 1
        elif funding_rate < 0:
            hostile += 1
        if oi_delta_pct > 2:
            aligned += 1
        elif oi_delta_pct < -2:
            hostile += 1
        if ob_imbalance_pct < -5:
            aligned += 1
        elif ob_imbalance_pct > 5:
            hostile += 1
    else:
        if funding_rate < 0:
            aligned += 1
        elif funding_rate > 0:
            hostile += 1
        if oi_delta_pct < 0:
            aligned += 1
        elif oi_delta_pct > 2:
            hostile += 1
        if ob_imbalance_pct > 5:
            aligned += 1
        elif ob_imbalance_pct < -5:
            hostile += 1

    cfg = CAL["liquidity_bonus"]
    if aligned >= 3:
        return cfg["aligned"]
    elif aligned >= 2:
        return cfg["partial"]
    elif hostile >= 2:
        return cfg["hostile"]
    return cfg["neutral"]


def _session_multiplier(session: str) -> float:
    return CAL["session_multipliers"].get(session, 1.0)


def _symbol_multiplier(symbol: str) -> float:
    return CAL["symbol_multipliers"].get(symbol, 1.0)


# ── CONTRADICTION ENGINE ──────────────────────────────────────────────────

def detect_contradictions(tech_raw: float, struct_raw: float, ctx_raw: float,
                          ob_features, rsi: float, adx: float,
                          funding_rate: float, oi_delta_pct: float,
                          direction: str = "LONG") -> list:
    """
    Detect contradictions between domains. Heavy penalty for real conflicts.
    Direction-aware: checks differ for LONG vs SHORT signals.
    """
    contradictions = []

    # Strong technical but weak structure (direction-agnostic)
    if tech_raw >= 70 and struct_raw < 40:
        contradictions.append("Strong technical but weak liquidity structure")

    # Strong context but weak technical (direction-agnostic)
    if ctx_raw >= 70 and tech_raw < 40:
        contradictions.append("Good session/symbol but weak technical setup")

    if direction == "SHORT":
        # Overbought but no trend strength (mirror of LONG dead cat bounce)
        if rsi > 65 and adx < 25:
            contradictions.append("Overbought but no trend strength — fake overbought risk")

        # Positive funding but OI falling (longs unwinding, not crowded enough)
        if funding_rate > 0 and oi_delta_pct < -2:
            contradictions.append("Positive funding but OI falling — longs unwinding, short fuel drying up")
    else:
        # Deep pullback but no trend (ADX < 25)
        if rsi < 35 and adx < 25:
            contradictions.append("Deep pullback but no trend strength — dead cat bounce risk")

        # Negative funding but OI rising (crowded short)
        if funding_rate < 0 and oi_delta_pct > 3:
            contradictions.append("Negative funding but OI rising — crowded counter-trend")

    # Strong imbalance but spread too wide (direction-agnostic)
    if ob_features is not None:
        imbalance = ob_features.get("imbalance_pct", 0)
        spread = ob_features.get("spread_pct", 1.0)
        if abs(imbalance) >= 20 and spread > 0.20:
            contradictions.append("High imbalance but wide spread — illiquid manipulation risk")

    return contradictions


# ── MAIN ADAPTIVE SCORING ───────────────────────────────────────────────────

def score_signal_adaptive(symbol: str, enriched_df: pd.DataFrame,
                          ob_features=None, session: str = "", idx: int = -1,
                          rsi_override=None, adx_override=None,
                          log_ret_override=None) -> Tuple[float, dict]:
    """
    Compute adaptive DQS using weighted-max architecture.
    Returns (dqs: float, breakdown: dict)
    """
    row = enriched_df.iloc[idx]

    rsi = float(rsi_override) if rsi_override is not None else float(row.get("rsi", 100) if not pd.isna(row.get("rsi")) else 100)
    adx = float(adx_override) if adx_override is not None else float(row.get("adx", 0) if not pd.isna(row.get("adx")) else 0)
    log_ret = float(log_ret_override) if log_ret_override is not None else float(row.get("ctx_log_ret_4h", 0) if not pd.isna(row.get("ctx_log_ret_4h")) else 0)

    funding_rate = float(row.get("funding_rate", 0)) if not pd.isna(row.get("funding_rate")) else 0

    # Funding rate z-score: how extreme is current funding vs recent history
    funding_zscore = 0.0
    if "funding_rate" in enriched_df.columns and len(enriched_df) >= 97:
        funding_window = enriched_df["funding_rate"].iloc[max(0, idx - 96):idx + 1].dropna()
        if len(funding_window) >= 10:
            mean_fr = funding_window.mean()
            std_fr = funding_window.std()
            if std_fr > 0:
                funding_zscore = (funding_rate - mean_fr) / std_fr

    # Correlation regime: rolling BTC-alt return correlation
    correlation_regime = _compute_correlation_regime(symbol, enriched_df, idx)

    # Sentiment features from enriched data (data-only passthrough, no DQS impact)
    fear_greed = float(row.get("fear_greed", 0)) if not pd.isna(row.get("fear_greed")) else 0
    btc_dominance = float(row.get("btc_dominance", 0)) if not pd.isna(row.get("btc_dominance")) else 0
    taker_buy_ratio = float(row.get("taker_buy_ratio", 0)) if not pd.isna(row.get("taker_buy_ratio")) else 0
    binance_ls_ratio = float(row.get("longShortRatio", 0)) if not pd.isna(row.get("longShortRatio")) else 0

    oi_delta_pct = 0.0
    oi_now = float(row.get("open_interest", 0)) if not pd.isna(row.get("open_interest")) else 0
    if len(enriched_df) >= 97:
        oi_old = float(enriched_df.iloc[idx - 96].get("open_interest", oi_now))
        if oi_old > 0:
            oi_delta_pct = ((oi_now - oi_old) / oi_old) * 100

    # Load per-symbol RSI threshold (session-aware)
    from portfolio_config import get_symbol_params, get_strategy
    sym_params = get_symbol_params(symbol, session if session else None)
    rsi_thresh = sym_params.get("rsi_threshold", 40.0)

    # Determine regime for strategy resolution (Phase 2: HMM-based)
    regime = "MIXED"
    try:
        from regime_router import classify_regime
        regime, _, _, _ = classify_regime(enriched_df, symbol=symbol)
    except Exception:
        pass

    # Resolve strategy from session + regime (Phase 2)
    strategy = get_strategy(symbol, session if session else None, regime)
    if strategy == "mean_reversion":
        rsi_overbought = sym_params.get("rsi_overbought", 65)
        rsi_oversold = sym_params.get("rsi_oversold", 30)
        if rsi >= rsi_overbought:
            direction = "SHORT"
        elif rsi_oversold > 0 and rsi <= rsi_oversold:
            direction = "LONG"
        else:
            direction = "LONG" if log_ret >= 0 else "SHORT"
    else:
        direction = "LONG" if log_ret >= 0 else "SHORT"

    # Compute raw domain scores
    tech_raw = _score_technical_raw(rsi, adx, log_ret, rsi_threshold=rsi_thresh, direction=direction)
    struct_raw = _score_structure_raw(ob_features, funding_rate, oi_delta_pct, direction=direction)
    ctx_raw = _score_context_raw(session, symbol)

    # Weighted average: technical carries 40%, structure 30%, context 30%
    # This ensures a failed technical domain drags the score down
    # instead of being masked by a strong context (session+symbol) domain
    w_tech = CAL["domain_weights"]["technical"]
    w_struct = CAL["domain_weights"]["structure"]
    w_ctx = CAL["domain_weights"]["context"]

    domain_max = max(tech_raw * w_tech, struct_raw * w_struct, ctx_raw * w_ctx)
    domain_avg = (tech_raw + struct_raw + ctx_raw) / 3

    base_score = tech_raw * w_tech + struct_raw * w_struct + ctx_raw * w_ctx

    # Bonuses
    pb_bonus = _pullback_bonus(rsi, direction=direction)
    liq_bonus = _liquidity_bonus(funding_rate, oi_delta_pct,
                                  ob_features.get("imbalance_pct", 0) if ob_features else 0,
                                  direction=direction)

    # Multipliers (capped at 1.20x to prevent false EXCELLENT from multiplier stacking)
    trend_mult = _trend_multiplier(adx)
    sess_mult = _session_multiplier(session)
    sym_mult = _symbol_multiplier(symbol)
    combined_mult = min(trend_mult * sess_mult * sym_mult, 1.20)

    # ── Multiplier cap hit detection + tech-quality-gated downgrade ──
    mult_cap_hit = combined_mult >= 1.19
    mult_cap_downgrade = False
    if mult_cap_hit and tech_raw < 70:
        # Technical quality is weak — multiplier is carrying the signal
        mult_cap_downgrade = True

    # ── RSI overbought LONG rejection (per-symbol, strategy-aware) ──
    rsi_overbought_long = False
    if direction == "LONG":
        if strategy == "mean_reversion":
            rsi_ob_thresh = sym_params.get("rsi_overbought", 70)
            if rsi > rsi_ob_thresh:
                rsi_overbought_long = True
        else:  # trend_following
            if rsi > 80:
                rsi_overbought_long = True

    # Contradictions
    contradictions = detect_contradictions(
        tech_raw, struct_raw, ctx_raw, ob_features, rsi, adx, funding_rate, oi_delta_pct,
        direction=direction
    )
    n_contra = len(contradictions)
    if n_contra >= 3:
        contra_penalty = CAL["contra_penalty"]["n3_plus"]
    elif n_contra >= 2:
        contra_penalty = CAL["contra_penalty"]["n2"]
    elif n_contra >= 1:
        contra_penalty = CAL["contra_penalty"]["n1"]
    else:
        contra_penalty = 0

    # Regime detection
    regime = _detect_regime(adx, log_ret, enriched_df)

    # Regime penalty: counter-trend signals in strong trends get penalised
    trend_direction = "LONG" if log_ret >= 0 else "SHORT"
    regime_penalty = 0
    if regime == "TRENDING" and direction != trend_direction:
        regime_penalty = 15  # counter-trend signal in strong trend
    elif regime == "VOLATILE":
        regime_penalty = 8
    elif regime == "RANGING":
        regime_penalty = 3

    # Final DQS
    dqs = (base_score + pb_bonus + liq_bonus) * combined_mult - contra_penalty - regime_penalty
    dqs = max(0, min(100, dqs))

    # ── Apply tech-quality-gated multiplier cap downgrade ──
    if mult_cap_downgrade:
        dqs = min(dqs, 79)

    # ── Apply RSI overbought LONG hard reject ──
    if rsi_overbought_long:
        dqs = min(dqs, 49)  # below execution threshold

    # Grade
    if dqs >= CAL["min_dqs_excellent"]:
        strength = "EXCELLENT"
    elif dqs >= CAL["min_dqs_good"]:
        strength = "GOOD"
    elif dqs >= CAL["min_dqs_fair"]:
        strength = "FAIR"
    else:
        strength = "WEAK"

    # v0.3.2 — Calibrated win probability
    calibrated_prob = lookup_calibrated_prob(dqs, symbol)

    return dqs, {
        "dqs": round(dqs, 2),
        "total": dqs,
        "technical": tech_raw,
        "structure": struct_raw,
        "context": ctx_raw,
        "technical_raw": tech_raw,
        "structure_raw": struct_raw,
        "context_raw": ctx_raw,
        "domain_max": round(domain_max, 2),
        "domain_avg": round(domain_avg, 2),
        "pullback_bonus": pb_bonus,
        "liquidity_bonus": liq_bonus,
        "trend_mult": round(trend_mult, 2),
        "session_mult": round(sess_mult, 2),
        "symbol_mult": round(sym_mult, 2),
        "combined_mult": round(combined_mult, 2),
        "mult_cap_hit": mult_cap_hit,
        "mult_cap_downgrade": mult_cap_downgrade,
        "rsi_overbought_long": rsi_overbought_long,
        "tech_raw": tech_raw,
        "struct_raw": struct_raw,
        "ctx_raw": ctx_raw,
        "contra_penalty": contra_penalty,
        "contradictions": contradictions,
        "strength": strength,
        "grade": strength,
        "tradeable": dqs >= CAL["min_dqs_good"],
        "trackable": dqs >= CAL["min_dqs_to_track"],
        "regime": regime,
        "calibrated_prob": calibrated_prob,
        "rsi": round(rsi, 2),
        "adx": round(adx, 2),
        "bull_4h": int(row.get("bull_4h", 0)) if not pd.isna(row.get("bull_4h")) else 0,
        "log_ret_4h": round(log_ret, 6),
        "funding_rate": funding_rate,
        "oi_delta_pct": round(oi_delta_pct, 3),
        "oi_now": oi_now,
        "session": session,
        "symbol": symbol,
        "strategy": strategy,
        "direction": direction,
        "struct_meta": ob_features or {"note": "No orderbook data"},
        "funding_oi": struct_raw,
        "session_symbol": ctx_raw,
        "funding_oi_meta": {
            "funding_rate": funding_rate,
            "oi_delta_pct": round(oi_delta_pct, 3),
            "oi_now": oi_now,
        },
        "session_meta": {"session": session, "multiplier": sess_mult},
        # Derived features (data-only, no DQS formula impact)
        "funding_zscore": round(funding_zscore, 4),
        "correlation_regime": correlation_regime,
        "fear_greed": fear_greed,
        "btc_dominance": btc_dominance,
        "taker_buy_ratio": round(taker_buy_ratio, 6),
        "binance_ls_ratio": binance_ls_ratio,
    }


# ── CORRELATION REGIME ─────────────────────────────────────────────────────

_BTC_ENRICHED_CACHE = None
_BTC_ENRICHED_TS = None

def _load_btc_enriched_cached() -> pd.DataFrame:
    """Load BTC enriched 15m with module-level cache (refreshed every 5 min)."""
    global _BTC_ENRICHED_CACHE, _BTC_ENRICHED_TS
    import time as _time
    now = _time.time()
    if _BTC_ENRICHED_CACHE is not None and _BTC_ENRICHED_TS and (now - _BTC_ENRICHED_TS) < 300:
        return _BTC_ENRICHED_CACHE
    try:
        from confidence_engine import load_enriched as _load_enriched
        df = _load_enriched("BTCUSDT", "15m")
        if df is not None and len(df) > 0:
            _BTC_ENRICHED_CACHE = df
            _BTC_ENRICHED_TS = now
            return df
    except Exception:
        pass
    return pd.DataFrame()


def _compute_correlation_regime(symbol: str, enriched_df: pd.DataFrame, idx: int) -> str:
    """Classify BTC-alt rolling return correlation.

    High corr = macro-driven market. Low corr = idiosyncratic (symbol-specific).
    Returns 'macro', 'idiosyncratic', 'mixed', or 'n/a' (for BTC itself).
    """
    if symbol == "BTCUSDT":
        return "n/a"
    if len(enriched_df) < 97:
        return "unknown"

    btc_df = _load_btc_enriched_cached()
    if btc_df is None or len(btc_df) < 97:
        return "unknown"

    window = 96  # 24h on 15m timeframe
    actual_idx = idx if idx >= 0 else len(enriched_df) + idx
    end = actual_idx + 1
    start = max(0, end - window)

    alt_close = enriched_df["close"].iloc[start:end]
    btc_close = btc_df["close"].iloc[start:end]

    min_len = min(len(alt_close), len(btc_close))
    if min_len < 20:
        return "unknown"

    alt_ret = alt_close.iloc[-min_len:].pct_change().dropna()
    btc_ret = btc_close.iloc[-min_len:].pct_change().dropna()

    n = min(len(alt_ret), len(btc_ret))
    if n < 20:
        return "unknown"

    corr = alt_ret.iloc[-n:].corr(btc_ret.iloc[-n:])

    if corr >= 0.7:
        return "macro"
    elif corr < 0.3:
        return "idiosyncratic"
    else:
        return "mixed"


def _detect_regime(adx: float, log_ret_4h: float, enriched_df: pd.DataFrame) -> str:
    """
    Detect market regime: TRENDING, RANGING, or VOLATILE.
    """
    if len(enriched_df) >= 20:
        recent_atr = enriched_df["atr"].iloc[-20:]
        atr_mean = recent_atr.mean()
        atr_std = recent_atr.std()
        atr_spike = (recent_atr.iloc[-1] - atr_mean) / atr_std if atr_std > 0 else 0
    else:
        atr_spike = 0

    if adx >= 40 and abs(log_ret_4h) >= 0.005:
        return "TRENDING"
    elif atr_spike >= 2.0:
        return "VOLATILE"
    elif adx < 20:
        return "RANGING"
    else:
        return "MIXED"


# ── THRESHOLD HELPERS ───────────────────────────────────────────────────────

def get_thresholds() -> dict:
    """Return current DQS thresholds."""
    return {
        "min_to_track": CAL["min_dqs_to_track"],
        "min_to_execute": CAL["min_dqs_to_execute"],
        "min_to_pending": CAL["min_dqs_to_pending"],
        "min_fair": CAL["min_dqs_fair"],
        "min_good": CAL["min_dqs_good"],
        "min_excellent": CAL["min_dqs_excellent"],
    }
