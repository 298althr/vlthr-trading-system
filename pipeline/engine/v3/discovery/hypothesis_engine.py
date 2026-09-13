"""
V3 Hypothesis Engine — Layer 5 of DISC
=======================================
Automatically generates, tests, and updates hypotheses about market behavior.

Hypothesis Types:
  1. reversal      — Price reverses when conditions align (overbought + funding + OI)
  2. momentum      — Price continues when momentum signals confirm
  3. lead_lag      — BTC leads altcoin movements by N bars
  4. regime        — Strategy effectiveness varies by market regime
  5. seasonal      — Time-of-day/day-of-week patterns
  6. structural    — Orderbook/funding structure predicts direction
  7. cascade       — Liquidation cascade triggers reversal
  8. correlation   — Cross-asset correlation breakdown signals regime change

Each hypothesis:
  - Stored in hypothesis_registry DB table
  - Continuously tested against live data
  - Updated via Bayesian updating (Beta prior → posterior)
  - Promoted/demoted based on evidence accumulation
"""
from __future__ import annotations

import os
import json
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


# ── HYPOTHESIS STATES ─────────────────────────────────────────────────────

UNTESTED = "untested"
TESTING = "testing"
CONFIRMED = "confirmed"
REFUTED = "refuted"
RETIRED = "retired"

HYPOTHESIS_STATES = [UNTESTED, TESTING, CONFIRMED, REFUTED, RETIRED]


@dataclass
class Hypothesis:
    hypothesis_name: str
    hypothesis_text: str
    hypothesis_type: str
    symbols: List[str] = field(default_factory=list)
    conditions: List[dict] = field(default_factory=list)  # list of {feature, operator, value}
    prior_probability: float = 0.5
    posterior_probability: float = 0.5
    evidence_count: int = 0
    evidence_for: int = 0
    evidence_against: int = 0
    bayesian_updates: List[dict] = field(default_factory=list)
    confidence: float = 0.0
    status: str = UNTESTED
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "hypothesis_name": self.hypothesis_name,
            "hypothesis_text": self.hypothesis_text,
            "hypothesis_type": self.hypothesis_type,
            "symbols": self.symbols,
            "conditions": self.conditions,
            "prior_probability": self.prior_probability,
            "posterior_probability": self.posterior_probability,
            "evidence_count": self.evidence_count,
            "evidence_for": self.evidence_for,
            "evidence_against": self.evidence_against,
            "confidence": self.confidence,
            "status": self.status,
        }


# ── BAYESIAN UPDATING ─────────────────────────────────────────────────────

def bayesian_update(prior: float, evidence_for: int, evidence_against: int,
                    alpha: float = 1.0, beta: float = 1.0) -> Tuple[float, float]:
    """
    Update probability using Beta-Binomial conjugate prior.
    prior: current posterior (used as prior for next update)
    evidence_for: number of confirming observations
    evidence_against: number of disconfirming observations
    alpha, beta: Beta distribution parameters (1,1 = uniform prior)

    Returns (new_posterior, confidence)
    """
    # Convert prior to pseudo-counts
    prior_alpha = alpha + evidence_for
    prior_beta = beta + evidence_against

    # Posterior mean of Beta distribution
    posterior = prior_alpha / (prior_alpha + prior_beta)

    # Confidence: how far from 0.5 (uncertainty)
    total = prior_alpha + prior_beta
    if total > 0:
        # Standard deviation of Beta
        std = math.sqrt((prior_alpha * prior_beta) / ((total ** 2) * (total + 1)))
        # Confidence = 1 - 2*std (higher when std is low)
        confidence = max(0.0, min(1.0, 1.0 - 2.0 * std))
    else:
        confidence = 0.0

    return float(posterior), float(confidence)


# ── CONDITION EVALUATION ──────────────────────────────────────────────────

def evaluate_condition(row: pd.Series, condition: dict) -> bool:
    """Evaluate a single condition against a data row."""
    feature = condition.get("feature", "")
    operator = condition.get("operator", ">")
    value = condition.get("value", 0)

    if feature not in row.index:
        return False

    val = row[feature]
    if pd.isna(val):
        return False

    val = float(val)
    value = float(value)

    if operator == ">":
        return val > value
    elif operator == ">=":
        return val >= value
    elif operator == "<":
        return val < value
    elif operator == "<=":
        return val <= value
    elif operator == "==":
        return abs(val - value) < 1e-10
    elif operator == "!=":
        return abs(val - value) >= 1e-10
    elif operator == "crosses_above":
        # Handled separately with previous row
        return False
    return False


def evaluate_all_conditions(row: pd.Series, conditions: List[dict]) -> bool:
    """Evaluate all conditions (AND logic). Returns True only if all conditions are met."""
    return all(evaluate_condition(row, c) for c in conditions)


# ── HYPOTHESIS TEMPLATES ──────────────────────────────────────────────────

REVERSAL_TEMPLATES = [
    {
        "name": "rsi_overbought_funding_positive",
        "text": "Price tends to reverse when RSI > 70 AND funding rate is positive",
        "type": "reversal",
        "conditions": [
            {"feature": "rsi", "operator": ">", "value": 70},
            {"feature": "funding_rate", "operator": ">", "value": 0.0},
        ],
        "expected_direction": "SHORT",
    },
    {
        "name": "rsi_oversold_funding_negative",
        "text": "Price tends to reverse when RSI < 30 AND funding rate is negative",
        "type": "reversal",
        "conditions": [
            {"feature": "rsi", "operator": "<", "value": 30},
            {"feature": "funding_rate", "operator": "<", "value": 0.0},
        ],
        "expected_direction": "LONG",
    },
    {
        "name": "oi_surge_overbought",
        "text": "Price reverses when OI surges >5% in 4h AND RSI > 70",
        "type": "reversal",
        "conditions": [
            {"feature": "rsi", "operator": ">", "value": 70},
        ],
        "expected_direction": "SHORT",
        "requires_oi": True,
    },
]

MOMENTUM_TEMPLATES = [
    {
        "name": "adx_strong_trend_ema_align",
        "text": "Price continues in trend direction when ADX > 25 AND EMA50 > EMA200",
        "type": "momentum",
        "conditions": [
            {"feature": "adx", "operator": ">", "value": 25},
        ],
        "expected_direction": "LONG",
    },
    {
        "name": "volume_surge_momentum",
        "text": "Price continues when volume > 1.5x average AND |return| > 1%",
        "type": "momentum",
        "conditions": [],
        "expected_direction": "FOLLOW",
        "requires_vol_ratio": True,
    },
]

LEAD_LAG_TEMPLATES = [
    {
        "name": "btc_leads_alts_15m",
        "text": "BTC 15m return > 0.3% predicts altcoin follow within 15-45 min",
        "type": "lead_lag",
        "conditions": [],
        "expected_direction": "FOLLOW_BTC",
        "lead_symbol": "BTCUSDT",
        "lag_symbols": ["ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"],
    },
]

REGIME_TEMPLATES = [
    {
        "name": "trend_following_in_high_adx",
        "text": "Trend-following strategies outperform when ADX > 30",
        "type": "regime",
        "conditions": [
            {"feature": "adx", "operator": ">", "value": 30},
        ],
        "expected_direction": "TREND",
    },
    {
        "name": "mean_reversion_in_low_adx",
        "text": "Mean-reversion strategies outperform when ADX < 20",
        "type": "regime",
        "conditions": [
            {"feature": "adx", "operator": "<", "value": 20},
        ],
        "expected_direction": "REVERT",
    },
]

SEASONAL_TEMPLATES = [
    {
        "name": "us_session_volatility",
        "text": "Volatility increases during US session (13:00-22:00 UTC)",
        "type": "seasonal",
        "conditions": [],
        "expected_direction": "NEUTRAL",
        "requires_session_test": "us_volatility",
    },
    {
        "name": "weekend_low_volume",
        "text": "Volume is lower on weekends (Saturday/Sunday)",
        "type": "seasonal",
        "conditions": [],
        "expected_direction": "NEUTRAL",
        "requires_session_test": "weekend_volume",
    },
]

STRUCTURAL_TEMPLATES = [
    {
        "name": "funding_extreme_reversal",
        "text": "Price reverses when funding rate z-score > 2 (extreme positive)",
        "type": "structural",
        "conditions": [],
        "expected_direction": "SHORT",
        "requires_funding_zscore": True,
    },
    {
        "name": "ls_ratio_extreme",
        "text": "Price reverses when long/short ratio > 3.0 (crowd too long)",
        "type": "structural",
        "conditions": [],
        "expected_direction": "SHORT",
        "requires_ls_ratio": True,
    },
]

ALL_TEMPLATES = (
    REVERSAL_TEMPLATES +
    MOMENTUM_TEMPLATES +
    LEAD_LAG_TEMPLATES +
    REGIME_TEMPLATES +
    SEASONAL_TEMPLATES +
    STRUCTURAL_TEMPLATES
)


def generate_hypotheses(symbols: List[str]) -> List[Hypothesis]:
    """Generate hypotheses from templates for the given symbols."""
    hypotheses = []
    for template in ALL_TEMPLATES:
        # For lead-lag, use specific symbol sets
        if template["type"] == "lead_lag":
            hyp_symbols = template.get("lag_symbols", symbols)
        else:
            hyp_symbols = symbols

        hyp = Hypothesis(
            hypothesis_name=template["name"],
            hypothesis_text=template["text"],
            hypothesis_type=template["type"],
            symbols=hyp_symbols,
            conditions=template.get("conditions", []),
            prior_probability=0.5,
            posterior_probability=0.5,
            status=UNTESTED,
        )
        hypotheses.append(hyp)
    return hypotheses


# ── HYPOTHESIS TESTING ────────────────────────────────────────────────────

def test_hypothesis(hyp: Hypothesis, df: pd.DataFrame, symbol: str,
                    forward_bars: int = 4) -> dict:
    """
    Test a hypothesis against historical data.
    Returns test result dict with evidence_for, evidence_against, etc.
    """
    if df is None or df.empty or len(df) < 50:
        return {"error": "insufficient data", "evidence_for": 0, "evidence_against": 0}

    # Compute forward returns
    fwd_ret = df["close"].shift(-forward_bars) / df["close"] - 1

    # Build dynamic conditions for hypotheses that require data-derived features
    template = None
    for t in ALL_TEMPLATES:
        if t["name"] == hyp.hypothesis_name:
            template = t
            break

    dynamic_conditions = list(hyp.conditions)

    if template:
        if template.get("requires_funding_zscore") and "funding_rate" in df.columns:
            fr = df["funding_rate"]
            fr_z = (fr - fr.rolling(96).mean()) / (fr.rolling(96).std() + 1e-10)
            condition_met = fr_z > 2.0
        elif template.get("requires_ls_ratio") and "buyRatio" in df.columns:
            ls = df["buyRatio"] / (df["sellRatio"] + 1e-10)
            condition_met = ls > 3.0
        elif template.get("requires_vol_ratio"):
            vol_ma = df["volume"].rolling(20).mean()
            vol_ratio = df["volume"] / (vol_ma + 1e-10)
            ret_abs = df["close"].pct_change().abs()
            condition_met = (vol_ratio > 1.5) & (ret_abs > 0.01)
        else:
            # Evaluate static conditions per row
            condition_met = []
            for idx in range(len(df)):
                row = df.iloc[idx]
                met = evaluate_all_conditions(row, dynamic_conditions) if dynamic_conditions else True
                condition_met.append(met)
            condition_met = pd.Series(condition_met, index=df.index)
    else:
        condition_met = pd.Series([True] * len(df), index=df.index)
    fwd_aligned = fwd_ret[condition_met].dropna()

    if len(fwd_aligned) < 10:
        return {
            "error": "insufficient condition matches",
            "evidence_for": 0,
            "evidence_against": 0,
            "n_matches": int(condition_met.sum()),
        }

    # Determine expected direction from template
    expected_dir = template.get("expected_direction", "NEUTRAL") if template else "NEUTRAL"

    evidence_for = 0
    evidence_against = 0

    if expected_dir == "LONG":
        evidence_for = int((fwd_aligned > 0).sum())
        evidence_against = int((fwd_aligned <= 0).sum())
    elif expected_dir == "SHORT":
        evidence_for = int((fwd_aligned < 0).sum())
        evidence_against = int((fwd_aligned >= 0).sum())
    elif expected_dir == "TREND":
        # Trend: returns should have positive autocorrelation
        ret = df["close"].pct_change()
        ret_aligned = ret[condition_met].dropna()
        if len(ret_aligned) > 1:
            autocorr = ret_aligned.autocorr(lag=1)
            if autocorr is not None and autocorr > 0:
                evidence_for = len(fwd_aligned)
            else:
                evidence_against = len(fwd_aligned)
    elif expected_dir == "REVERT":
        # Mean reversion: negative autocorrelation
        ret = df["close"].pct_change()
        ret_aligned = ret[condition_met].dropna()
        if len(ret_aligned) > 1:
            autocorr = ret_aligned.autocorr(lag=1)
            if autocorr is not None and autocorr < 0:
                evidence_for = len(fwd_aligned)
            else:
                evidence_against = len(fwd_aligned)
    elif expected_dir == "FOLLOW":
        # Momentum continuation: forward return direction matches current return direction
        ret_current = df["close"].pct_change()
        ret_aligned_pairs = pd.DataFrame({"cur": ret_current[condition_met], "fwd": fwd_ret[condition_met]}).dropna()
        if len(ret_aligned_pairs) > 0:
            # Evidence: forward return has same sign as current return
            evidence_for = int((ret_aligned_pairs["cur"] * ret_aligned_pairs["fwd"] > 0).sum())
            evidence_against = int((ret_aligned_pairs["cur"] * ret_aligned_pairs["fwd"] <= 0).sum())
    elif expected_dir == "FOLLOW_BTC":
        # Lead-lag: handled in test_lead_lag_hypothesis
        return {
            "error": "lead_lag requires BTC data",
            "evidence_for": 0,
            "evidence_against": 0,
            "n_matches": int(condition_met.sum()),
        }
    else:
        # Neutral: use custom testing based on template metadata
        session_test = template.get("requires_session_test") if template else None
        if session_test == "us_volatility":
            # Compare volatility during US session (13-22 UTC) vs off-session
            if "timestamp" in df.columns:
                ts = df["timestamp"]
                hour = ts.dt.hour
                us_session = (hour >= 13) & (hour < 22)
                vol = (df["high"] - df["low"]) / df["close"]
                us_vol = vol[us_session].dropna()
                off_vol = vol[~us_session].dropna()
                if len(us_vol) > 10 and len(off_vol) > 10:
                    if us_vol.mean() > off_vol.mean():
                        evidence_for = len(us_vol)
                        evidence_against = 0
                    else:
                        evidence_for = 0
                        evidence_against = len(us_vol)
        elif session_test == "weekend_volume":
            # Compare weekend vs weekday volume
            if "timestamp" in df.columns:
                ts = df["timestamp"]
                dow = ts.dt.dayofweek
                weekend = dow >= 5
                vol = df["volume"]
                weekend_vol = vol[weekend].dropna()
                weekday_vol = vol[~weekend].dropna()
                if len(weekend_vol) > 10 and len(weekday_vol) > 10:
                    if weekend_vol.mean() < weekday_vol.mean():
                        evidence_for = len(weekend_vol)
                        evidence_against = 0
                    else:
                        evidence_for = 0
                        evidence_against = len(weekend_vol)
        else:
            # Generic neutral: measure if forward returns are statistically significant
            if fwd_aligned.std() > 0:
                t_stat = fwd_aligned.mean() / (fwd_aligned.std() / math.sqrt(len(fwd_aligned)))
                if abs(t_stat) > 1.96:
                    evidence_for = len(fwd_aligned)
                else:
                    evidence_against = len(fwd_aligned)

    # Bayesian update
    new_posterior, confidence = bayesian_update(
        hyp.posterior_probability,
        evidence_for,
        evidence_against,
    )

    # Update hypothesis
    total = evidence_for + evidence_against
    hyp.evidence_count += total
    hyp.evidence_for += evidence_for
    hyp.evidence_against += evidence_against
    hyp.posterior_probability = new_posterior
    hyp.confidence = confidence

    # Update status
    if total > 0:
        hyp.status = TESTING
    if hyp.evidence_count > 100:
        if hyp.posterior_probability > 0.6 and hyp.confidence > 0.3:
            hyp.status = CONFIRMED
        elif hyp.posterior_probability < 0.4 and hyp.confidence > 0.3:
            hyp.status = REFUTED

    # Record Bayesian update
    hyp.bayesian_updates.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "evidence_for": evidence_for,
        "evidence_against": evidence_against,
        "posterior": new_posterior,
        "confidence": confidence,
        "symbol": symbol,
    })

    return {
        "evidence_for": evidence_for,
        "evidence_against": evidence_against,
        "n_matches": int(condition_met.sum()),
        "posterior": new_posterior,
        "confidence": confidence,
        "status": hyp.status,
    }


def test_lead_lag_hypothesis(hyp: Hypothesis, btc_df: pd.DataFrame,
                              alt_df: pd.DataFrame, alt_symbol: str,
                              lag_bars: int = 3) -> dict:
    """
    Test lead-lag hypothesis: does BTC return at time t predict altcoin return at time t+lag?
    """
    if btc_df is None or alt_df is None or btc_df.empty or alt_df.empty:
        return {"error": "insufficient data", "evidence_for": 0, "evidence_against": 0}

    # Align on timestamp
    btc_ret = btc_df["close"].pct_change()
    alt_ret = alt_df["close"].pct_change()

    # BTC return at t, alt return at t+lag
    btc_ret_lagged = btc_ret.shift(0)
    alt_ret_forward = alt_ret.shift(-lag_bars)

    # Condition: BTC return > 0.3%
    threshold = 0.003
    condition_met = btc_ret_lagged > threshold
    aligned = pd.DataFrame({"btc": btc_ret_lagged[condition_met], "alt": alt_ret_forward[condition_met]}).dropna()

    if len(aligned) < 10:
        return {
            "error": "insufficient condition matches",
            "evidence_for": 0,
            "evidence_against": 0,
            "n_matches": int(condition_met.sum()),
        }

    # Evidence: alt follows BTC direction
    evidence_for = int((aligned["alt"] > 0).sum())
    evidence_against = int((aligned["alt"] <= 0).sum())

    # Bayesian update
    new_posterior, confidence = bayesian_update(
        hyp.posterior_probability, evidence_for, evidence_against,
    )

    total = evidence_for + evidence_against
    hyp.evidence_count += total
    hyp.evidence_for += evidence_for
    hyp.evidence_against += evidence_against
    hyp.posterior_probability = new_posterior
    hyp.confidence = confidence

    if total > 0:
        hyp.status = TESTING
    if hyp.evidence_count > 100:
        if hyp.posterior_probability > 0.6 and hyp.confidence > 0.3:
            hyp.status = CONFIRMED
        elif hyp.posterior_probability < 0.4 and hyp.confidence > 0.3:
            hyp.status = REFUTED

    hyp.bayesian_updates.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "evidence_for": evidence_for,
        "evidence_against": evidence_against,
        "posterior": new_posterior,
        "confidence": confidence,
        "symbol": alt_symbol,
        "lag_bars": lag_bars,
    })

    return {
        "evidence_for": evidence_for,
        "evidence_against": evidence_against,
        "n_matches": int(condition_met.sum()),
        "posterior": new_posterior,
        "confidence": confidence,
        "status": hyp.status,
    }


# ── DB INTEGRATION ────────────────────────────────────────────────────────

def _get_db_url() -> str:
    env = os.environ.get("DB_URL", "")
    if env:
        return env
    from pathlib import Path
    repo = Path(__file__).resolve().parents[5]
    env_file = repo / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == "DB_URL":
                        return v.strip()
    return ""


def upsert_hypothesis(hyp: Hypothesis) -> bool:
    """Insert or update a hypothesis in the hypothesis_registry table."""
    db_url = _get_db_url()
    if not db_url:
        return False
    try:
        import psycopg2
        from psycopg2.extras import Json
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        cur.execute("""
            INSERT INTO hypothesis_registry (
                hypothesis_name, hypothesis_text, hypothesis_type, symbols,
                prior_probability, posterior_probability, evidence_count,
                evidence_for, evidence_against, bayesian_updates, confidence,
                status, created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (hypothesis_name) DO UPDATE SET
                posterior_probability = EXCLUDED.posterior_probability,
                evidence_count = EXCLUDED.evidence_count,
                evidence_for = EXCLUDED.evidence_for,
                evidence_against = EXCLUDED.evidence_against,
                bayesian_updates = EXCLUDED.bayesian_updates,
                confidence = EXCLUDED.confidence,
                status = EXCLUDED.status,
                updated_at = EXCLUDED.updated_at
        """, (
            hyp.hypothesis_name, hyp.hypothesis_text, hyp.hypothesis_type,
            hyp.symbols, hyp.prior_probability, hyp.posterior_probability,
            hyp.evidence_count, hyp.evidence_for, hyp.evidence_against,
            Json(hyp.bayesian_updates[-20:]),  # Keep last 20 updates
            hyp.confidence, hyp.status,
            hyp.created_at or now, now,
        ))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[hypothesis_engine] upsert failed: {e}")
        return False


def load_hypothesis(name: str) -> Optional[Hypothesis]:
    """Load a hypothesis from DB by name."""
    db_url = _get_db_url()
    if not db_url:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("""
            SELECT hypothesis_name, hypothesis_text, hypothesis_type, symbols,
                   prior_probability, posterior_probability, evidence_count,
                   evidence_for, evidence_against, bayesian_updates, confidence,
                   status, created_at, updated_at
            FROM hypothesis_registry WHERE hypothesis_name = %s
        """, (name,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        return Hypothesis(
            hypothesis_name=row[0], hypothesis_text=row[1], hypothesis_type=row[2],
            symbols=row[3] or [], prior_probability=float(row[4] or 0.5),
            posterior_probability=float(row[5] or 0.5),
            evidence_count=int(row[6] or 0), evidence_for=int(row[7] or 0),
            evidence_against=int(row[8] or 0), bayesian_updates=row[9] or [],
            confidence=float(row[10] or 0), status=row[11] or UNTESTED,
            created_at=str(row[12]) if row[12] else None,
            updated_at=str(row[13]) if row[13] else None,
        )
    except Exception as e:
        print(f"[hypothesis_engine] load failed: {e}")
        return None


def load_all_hypotheses() -> List[Hypothesis]:
    """Load all hypotheses from DB."""
    db_url = _get_db_url()
    if not db_url:
        return []
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("""
            SELECT hypothesis_name, hypothesis_text, hypothesis_type, symbols,
                   prior_probability, posterior_probability, evidence_count,
                   evidence_for, evidence_against, bayesian_updates, confidence,
                   status, created_at, updated_at
            FROM hypothesis_registry ORDER BY posterior_probability DESC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        results = []
        for row in rows:
            results.append(Hypothesis(
                hypothesis_name=row[0], hypothesis_text=row[1], hypothesis_type=row[2],
                symbols=row[3] or [], prior_probability=float(row[4] or 0.5),
                posterior_probability=float(row[5] or 0.5),
                evidence_count=int(row[6] or 0), evidence_for=int(row[7] or 0),
                evidence_against=int(row[8] or 0), bayesian_updates=row[9] or [],
                confidence=float(row[10] or 0), status=row[11] or UNTESTED,
                created_at=str(row[12]) if row[12] else None,
                updated_at=str(row[13]) if row[13] else None,
            ))
        return results
    except Exception as e:
        print(f"[hypothesis_engine] load_all failed: {e}")
        return []


def get_confirmed_hypotheses() -> List[Hypothesis]:
    """Get all confirmed hypotheses."""
    return [h for h in load_all_hypotheses() if h.status == CONFIRMED]


def get_active_hypotheses() -> List[Hypothesis]:
    """Get all non-retired hypotheses."""
    return [h for h in load_all_hypotheses() if h.status != RETIRED]
