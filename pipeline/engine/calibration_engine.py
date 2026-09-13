"""
Calibration Engine
==================
Learns optimal multipliers, bonuses, and thresholds from historical
paper trade outcomes. Analyzes what made winners win and losers lose.

Produces calibration.json used by adaptive_scorer.py.
"""

import json
import os
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple
from datetime import datetime, timezone

import psycopg2
from sklearn.isotonic import IsotonicRegression

DB_URL = os.environ.get(
    "DB_URL",
    "",
)

CALIBRATION_PATH = Path(__file__).resolve().parent / "calibration.json"


def fetch_trade_history(conn) -> List[dict]:
    """Pull all closed paper trades with their signal features."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            pt.symbol, pt.side, pt.confidence, pt.exit_reason,
            pt.hours_held, pt.net_pnl_pct, pt.net_pnl_usd,
            pt.created_at, pt.exit_time_utc,
            hcs.technical_score, hcs.market_structure_score, hcs.funding_oi_score,
            hcs.session_symbol_score, hcs.session, hcs.h4_direction,
            hcs.rsi_15m, hcs.adx_4h, hcs.funding_rate, hcs.oi_delta_pct,
            hcs.ob_imbalance_pct, hcs.ob_spread_pct
        FROM paper_trades pt
        LEFT JOIN high_confidence_signals hcs ON hcs.id = pt.signal_id
        WHERE pt.status = 'CLOSED'
        ORDER BY pt.created_at DESC
        """
    )
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]


def compute_feature_importance(trades: List[dict]) -> Dict[str, float]:
    """
    Compute how much each feature correlates with positive PnL.
    Returns feature -> correlation coefficient (Pearson approx).
    """
    def _correlation(x_list, y_list):
        n = len(x_list)
        if n < 3:
            return 0.0
        mx = sum(x_list) / n
        my = sum(y_list) / n
        num = sum((x - mx) * (y - my) for x, y in zip(x_list, y_list))
        den_x = sum((x - mx) ** 2 for x in x_list) ** 0.5
        den_y = sum((y - my) ** 2 for y in y_list) ** 0.5
        if den_x == 0 or den_y == 0:
            return 0.0
        return num / (den_x * den_y)

    pnl_vals = [float(t["net_pnl_pct"] or 0) for t in trades]
    def fv(key):
        return [float(t[key] or 0) for t in trades]
    features = {
        "confidence": fv("confidence"),
        "technical_score": fv("technical_score"),
        "market_structure_score": fv("market_structure_score"),
        "funding_oi_score": fv("funding_oi_score"),
        "rsi_15m": fv("rsi_15m"),
        "adx_4h": fv("adx_4h"),
        "funding_rate": fv("funding_rate"),
        "oi_delta_pct": fv("oi_delta_pct"),
        "ob_imbalance_pct": fv("ob_imbalance_pct"),
        "hours_held": fv("hours_held"),
    }

    correlations = {}
    for feat, vals in features.items():
        correlations[feat] = _correlation(vals, pnl_vals)
    return correlations


def compute_session_multipliers(trades: List[dict]) -> Dict[str, float]:
    """
    Compute session multipliers based on empirical win rate and avg PnL.
    Base = 1.0, positive sessions boosted, negative sessions penalized.
    """
    session_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "avg_pnl": [], "total": 0})

    for t in trades:
        sess = t.get("session") or "unknown"
        pnl = t["net_pnl_pct"] or 0
        session_stats[sess]["total"] += 1
        session_stats[sess]["avg_pnl"].append(pnl)
        if pnl > 0:
            session_stats[sess]["wins"] += 1
        else:
            session_stats[sess]["losses"] += 1

    # Compute multipliers
    multipliers = {}
    for sess, stats in session_stats.items():
        avg_pnl = float(sum(stats["avg_pnl"]) / len(stats["avg_pnl"])) if stats["avg_pnl"] else 0.0
        wr = stats["wins"] / stats["total"] if stats["total"] > 0 else 0.5

        # Formula: base 1.0, adjust by win rate deviation from 50% and avg pnl
        mult = 1.0 + (wr - 0.5) * 0.8 + avg_pnl * 0.02
        mult = max(0.5, min(1.5, mult))  # clamp
        multipliers[sess] = round(mult, 2)

    # Ensure all expected sessions exist
    for sess in ["london", "ny_open", "ny_late", "asian"]:
        if sess not in multipliers:
            multipliers[sess] = 1.0

    return multipliers


def compute_symbol_multipliers(trades: List[dict]) -> Dict[str, float]:
    """
    Compute symbol multipliers based on empirical win rate and avg PnL.
    """
    sym_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "avg_pnl": [], "total": 0})

    for t in trades:
        sym = t.get("symbol") or "UNKNOWN"
        pnl = t["net_pnl_pct"] or 0
        sym_stats[sym]["total"] += 1
        sym_stats[sym]["avg_pnl"].append(pnl)
        if pnl > 0:
            sym_stats[sym]["wins"] += 1
        else:
            sym_stats[sym]["losses"] += 1

    multipliers = {}
    for sym, stats in sym_stats.items():
        avg_pnl = float(sum(stats["avg_pnl"]) / len(stats["avg_pnl"])) if stats["avg_pnl"] else 0.0
        wr = stats["wins"] / stats["total"] if stats["total"] > 0 else 0.5
        mult = 1.0 + (wr - 0.5) * 0.6 + avg_pnl * 0.015
        mult = max(0.5, min(1.5, mult))
        multipliers[sym] = round(mult, 2)

    # Ensure all tracked symbols exist
    for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]:
        if sym not in multipliers:
            multipliers[sym] = 1.0

    return multipliers


def compute_optimal_thresholds(trades: List[dict]) -> Dict[str, int]:
    """
    Find DQS thresholds that maximize the separation between winners and losers.
    Uses a simple grid search over confidence values.
    """
    # Try different thresholds and find the one with best win rate above it
    best_track = 40
    best_execute = 50
    best_pending = 55
    best_good = 60
    best_excellent = 75

    # For pending threshold: find the confidence level where WR above it is best
    conf_levels = list(range(40, 85, 5))
    best_wr = 0
    for conf in conf_levels:
        above = [t for t in trades if (t["confidence"] or 0) >= conf]
        if len(above) >= 3:
            wins = sum(1 for t in above if (t["net_pnl_pct"] or 0) > 0)
            wr = wins / len(above)
            if wr > best_wr:
                best_wr = wr
                best_pending = conf
                best_good = max(conf - 5, 50)
                best_excellent = conf + 15

    return {
        "min_dqs_to_track": best_track,
        "min_dqs_to_execute": best_execute,
        "min_dqs_to_pending": best_pending,
        "min_dqs_fair": best_track,
        "min_dqs_good": best_good,
        "min_dqs_excellent": best_excellent,
    }


def calibrate_win_probability(conn) -> Tuple[Dict, Dict]:
    """
    Fit IsotonicRegression on (dqs, won) from closed paper trades.
    Returns (global_curve, symbol_curves).
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT pt.confidence, pt.net_pnl_usd, pt.symbol
        FROM paper_trades pt
        WHERE pt.status = 'CLOSED' AND pt.confidence IS NOT NULL
    """)
    rows = cur.fetchall()
    cur.close()

    if len(rows) < 10:
        print(f"[Calibration] Only {len(rows)} closed trades — skipping win-probability calibration")
        return {}, {}

    # Global curve
    X = []
    y = []
    symbol_data = defaultdict(list)
    for conf, pnl, symbol in rows:
        dqs = float(conf or 0)
        won = 1 if (pnl or 0) > 0 else 0
        X.append(dqs)
        y.append(won)
        symbol_data[symbol].append((dqs, won))

    # Fit global IsotonicRegression
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(X, y)

    # Build lookup breakpoints (every 5 DQS points)
    dqs_breakpoints = list(range(50, 101, 5))
    global_probs = [round(float(iso.predict([[bp]])[0]), 4) for bp in dqs_breakpoints]

    global_curve = {
        "dqs_breakpoints": dqs_breakpoints,
        "win_probabilities": global_probs,
        "n_samples_used": len(X),
        "last_calibrated_utc": datetime.now(timezone.utc).isoformat(),
    }

    # Per-symbol curves (only if >= 15 trades)
    symbol_curves = {}
    for sym, data in symbol_data.items():
        if len(data) < 15:
            continue
        sx = [d[0] for d in data]
        sy = [d[1] for d in data]
        sym_iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
        sym_iso.fit(sx, sy)
        sym_probs = [round(float(sym_iso.predict([[bp]])[0]), 4) for bp in dqs_breakpoints]
        symbol_curves[sym] = {
            "dqs_breakpoints": dqs_breakpoints,
            "win_probabilities": sym_probs,
            "n_samples_used": len(sx),
        }

    print(f"[Calibration] Win-probability curve fitted on {len(X)} trades")
    for bp, prob in zip(dqs_breakpoints, global_probs):
        print(f"  DQS {bp:>3} -> {prob:.2%}")

    return global_curve, symbol_curves


def build_calibration(conn) -> dict:
    """Build complete calibration from trade history."""
    trades = fetch_trade_history(conn)
    if len(trades) < 5:
        print(f"[Calibration] Only {len(trades)} trades — using defaults")
        return None

    print(f"[Calibration] Analyzing {len(trades)} historical trades...")

    feature_corr = compute_feature_importance(trades)
    session_mult = compute_session_multipliers(trades)
    symbol_mult = compute_symbol_multipliers(trades)
    thresholds = compute_optimal_thresholds(trades)

    # v0.3.2 — Win probability calibration
    global_curve, symbol_curves = calibrate_win_probability(conn)

    calibration = {
        "version": 3,
        "trained_on": len(trades),
        "feature_correlations": {k: round(v, 3) for k, v in feature_corr.items()},
        "session_multipliers": session_mult,
        "symbol_multipliers": symbol_mult,
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
            "aligned": 15,
            "partial": 8,
            "neutral": 0,
            "hostile": -10,
        },
        "domain_weights": {
            "technical": 1.0,
            "structure": 1.0,
            "context": 1.0,
        },
        "contra_penalty": {
            "n1": 5,
            "n2": 15,
            "n3_plus": 30,
        },
        **thresholds,
    }

    if global_curve:
        calibration["calibration_curve"] = {
            **global_curve,
            "symbol_curves": symbol_curves,
        }

    # Print insights
    print("\n[Calibration] Feature correlations with PnL:")
    for feat, corr in sorted(feature_corr.items(), key=lambda x: abs(x[1]), reverse=True):
        print(f"  {feat}: {corr:+.3f}")

    print("\n[Calibration] Session multipliers:")
    for sess, mult in sorted(session_mult.items(), key=lambda x: x[1], reverse=True):
        print(f"  {sess}: {mult}")

    print("\n[Calibration] Symbol multipliers:")
    for sym, mult in sorted(symbol_mult.items(), key=lambda x: x[1], reverse=True):
        print(f"  {sym}: {mult}")

    print(f"\n[Calibration] Thresholds: track={thresholds['min_dqs_to_track']}, "
          f"execute={thresholds['min_dqs_to_execute']}, pending={thresholds['min_dqs_to_pending']}, "
          f"good={thresholds['min_dqs_good']}, excellent={thresholds['min_dqs_excellent']}")

    return calibration


def run_calibration():
    """Main entry point — builds calibration and writes JSON."""
    conn = psycopg2.connect(DB_URL, sslmode="prefer")
    try:
        calibration = build_calibration(conn)
        if calibration:
            with open(CALIBRATION_PATH, "w") as f:
                json.dump(calibration, f, indent=2)
            print(f"\n[Calibration] Saved to {CALIBRATION_PATH}")
        else:
            print("[Calibration] Using defaults — not enough trade history")
    finally:
        conn.close()


if __name__ == "__main__":
    run_calibration()
