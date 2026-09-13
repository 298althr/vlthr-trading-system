"""
VLTHR Portfolio Configuration — Single Source of Truth
=====================================================
All constants for the portfolio-centric DQS pipeline.
Import from here everywhere. No magic numbers in business logic.
"""
from dataclasses import dataclass
from typing import Dict, Set

# ── SYMBOLS ──────────────────────────────────────────────────────────────
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]
DISABLED_SYMBOLS = ["DOGEUSDT"]  # V7: WR=35%, net negative PnL

# ── PER-SYMBOL PROVEN CONFIG (from D5 realworld backtest) ────────────────
# INTERIM FIX (pre-reoptimization): hard floor R:R >= 1.5 for all symbols.
# SOL/BNB/DOGE previously had inverted R:R (<1.0) — now corrected to safe defaults.
# Full re-optimization via D1-D7 will derive fresh, regime-specific values.
PER_SYMBOL_PARAMS = {
    "BTCUSDT":  {"rsi_threshold": 42.0,  "sl_mult": 2.5, "tp_mult": 7.5, "adx_min": 20.0, "risk_pct": 0.05,
                 "strategy": "trend_following", "sl_mode": "atr", "sl_pct": 1.0, "tp_pct": 3.0,
                 "max_trade_hours": 12},
    "ETHUSDT":  {"rsi_threshold": 50.1,  "sl_mult": 2.0, "tp_mult": 6.0, "adx_min": 18.0, "risk_pct": 0.10,
                 "strategy": "mean_reversion", "sl_mode": "atr", "sl_pct": 1.2, "tp_pct": 3.6,
                 "rsi_overbought": 65, "rsi_oversold": 0, "max_trade_hours": 12},
    "SOLUSDT":  {"rsi_threshold": 49.3,  "sl_mult": 2.5, "tp_mult": 7.5, "adx_min": 21.0, "risk_pct": 0.05,
                 "strategy": "trend_following", "sl_mode": "atr", "sl_pct": 1.5, "tp_pct": 4.5,
                 "max_trade_hours": 12},
    "XRPUSDT":  {"rsi_threshold": 49.1,  "sl_mult": 2.5, "tp_mult": 6.0, "adx_min": 18.5, "risk_pct": 0.05,
                 "strategy": "mean_reversion", "sl_mode": "atr", "sl_pct": 1.5, "tp_pct": 3.0,
                 "rsi_overbought": 70, "rsi_oversold": 30, "max_trade_hours": 12},
    "BNBUSDT":  {"rsi_threshold": 43.1,  "sl_mult": 2.0, "tp_mult": 5.0, "adx_min": 20.5, "risk_pct": 0.05,
                 "strategy": "mean_reversion", "sl_mode": "atr", "sl_pct": 1.5, "tp_pct": 3.0,
                 "rsi_overbought": 70, "rsi_oversold": 30, "max_trade_hours": 12},
    # DOGEUSDT disabled in V7 (WR=35%, net negative PnL)
}

# Backtest Sharpe scores per symbol (used in Context Domain scoring)
SYMBOL_QUALITY = {
    "BTCUSDT": 5.18,
    "ETHUSDT": 5.81,
    "SOLUSDT": 6.16,
    "XRPUSDT": 4.72,
    "BNBUSDT": 5.89,
    "DOGEUSDT": 5.28,
}

# ── TIME FRAMES ───────────────────────────────────────────────────────────
EXECUTION_TF = "15m"
CONTEXT_TF = "4h"

# ── SESSIONS ──────────────────────────────────────────────────────────────
ACTIVE_SESSIONS: Set[str] = {"london", "ny_open", "ny_late"}

# ── STRATEGY GATES ────────────────────────────────────────────────────────
PARAMS = {
    "rsi_threshold": 40,
    "sl_mult": 1.5,
    "tp_mult": 2.0,
    "adx_min": 25.0,
    "leverage": 4,
    "taker_fee_pct": 0.055,   # 0.055% per side
}


def get_symbol_params(symbol: str) -> dict:
    """Return proven per-symbol params, falling back to defaults."""
    return PER_SYMBOL_PARAMS.get(symbol, PARAMS)

# ── DQS THRESHOLDS ────────────────────────────────────────────────────────
DQS_THRESHOLDS = {
    "min_to_track": 40,       # monitoring threshold
    "min_to_execute": 50,     # signals >= 50 appear in high_confidence_signals
    "min_to_pending": 50,     # signals >= 50 get auto-pending
    "min_fair": 50,           # FAIR  (50-64)
    "min_good": 65,           # GOOD  (65-74)
    "min_excellent": 75,      # EXCELLENT (75+)
}

# ── DQS WEIGHTS ─────────────────────────────────────────────────────────────
DQS_WEIGHTS = {
    "technical": 0.40,
    "structure": 0.30,
    "context": 0.30,
}

# V7: DQS 85+ has negative Kelly — veto all trades at this threshold
DQS_VETO_THRESHOLD = 85

# V7 Kelly bands (from DQRAE calibration + institutional validation)
KELLY_V7_BANDS = {
    "dqs_ge_85": 0.25,
    "dqs_ge_80": 2.5,
    "dqs_ge_75": 1.5,
    "dqs_ge_70": 1.0,
    "dqs_ge_65": 0.7,
    "dqs_lt_65": 0.3,
}
MR_BOOST = 3.0  # V7: mean_reversion 3x boost for concentration balance
SYMBOL_ALLOC_CAP = 0.25  # V7: max 25% allocation per symbol
SYMBOL_PNL_CAP = 0.30  # V7: max 30% PnL share per symbol

# ── RISK PER TRADE (tiered by DQS) ────────────────────────────────────────
# V7: Kelly-band sizing replaces old RISK_TIERS. See KELLY_V7_BANDS above.
# ponytail: old RISK_TIERS kept for get_risk_tier() backward compat until orchestrator fully migrates
RISK_TIERS = {
    "fair":    {"min_dqs": 50, "max_dqs": 64, "risk_pct": 2.0, "sl_mult": 3.0, "tp_mult": 3.0, "rr": 1.00},
    "good":    {"min_dqs": 65, "max_dqs": 74, "risk_pct": 3.0, "sl_mult": 3.0, "tp_mult": 4.0, "rr": 1.33},
    "excellent": {"min_dqs": 75, "max_dqs": 100, "risk_pct": 5.0, "sl_mult": 3.0, "tp_mult": 5.0, "rr": 1.67},
}

# ── HARD R:R FLOOR (enforced by get_sl_tp regardless of tier/symbol) ─────
MIN_RR_FLOOR = 1.0  # absolute minimum risk:reward — lowered from 2.0 to allow tighter TP targets

# ── REGIME-AWARE SL/TP MULTIPLIERS ──────────────────────────────────────────
# Scales the base tier sl_mult/tp_mult based on detected market regime.
# TRENDING: wider SL (pullbacks), wider TP (ride the trend)
# RANGING:  tighter SL/TP (mean reversion targets are closer)
# VOLATILE: much wider SL (noise), normal TP (volatility itself doesn't extend target)
# MIXED:    baseline (no adjustment)
REGIME_SL_TP_MULTIPLIERS = {
    "TRENDING": {"sl_mult_scale": 1.3, "tp_mult_scale": 1.0},
    "RANGING":  {"sl_mult_scale": 0.8, "tp_mult_scale": 0.6},
    "VOLATILE": {"sl_mult_scale": 1.5, "tp_mult_scale": 0.8},
    "MIXED":    {"sl_mult_scale": 1.0, "tp_mult_scale": 1.0},
}

# ── REGIME-SKIP GATE ────────────────────────────────────────────────────────
# Minimum DQS required to trade in each regime. Higher = more selective.
# VOLATILE/MIXED require higher DQS because edge is less reliable.
REGIME_MIN_DQS = {
    "TRENDING": 50,  # baseline — trends are tradeable
    "RANGING":  55,  # slightly higher — ranging markets are noisier
    "VOLATILE": 60,  # high — volatile regimes are dangerous
    "MIXED":    60,  # elevated — unclear regime, be cautious
}

# ── POSITION CAP ──────────────────────────────────────────────────────────
# Per PAPER_TRADE_RISK_SIZING.md: equal allocation, no leg outspends another
CAPITAL_PER_SYMBOL = 2000.0  # V7: $10k / 5 symbols (DOGE disabled)

# ── PORTFOLIO LIMITS ──────────────────────────────────────────────────────
PORTFOLIO = {
    "max_active_trades": 8,           # correlation cap (crypto cluster) — was 5, raised for faster cycling
    "max_daily_trades": 10,
    "session_limits": {"london": 4, "ny_late": 4},
    "account_drawdown_halt_pct": 0.20,  # halt all if -20%
    "max_portfolio_risk_pct": 12.0,     # total open risk <= 12% of balance — was 10%, raised for more capacity
    "max_same_side": 5,                 # max concurrent positions in same direction — was 3
}

# ── CORRELATION ─────────────────────────────────────────────────────────────
# All 6 crypto symbols treated as one cluster
correlation_max_open = 8

# ── SIGNAL LIFECYCLE ────────────────────────────────────────────────────────
SIGNAL_LIFECYCLE = {
    "max_age_hours": 2.0,          # raw signal expiration (ABANDONED if never traded)
    "max_trade_hours": 12.0,       # max hold time for OPEN trades before TIME_EXIT
    "refresh_interval_min": 15,    # 15-minute scheduler cadence
    "trajectory_improvement_threshold": 3,  # consecutive improvements for BOOSTED
    "trajectory_decline_threshold": 2,     # consecutive declines for EXPIRED
    "boost_bonus": 5,              # +5 points for 3 consecutive improvements
    "decline_penalty": -10,        # -10 points for 2 consecutive declines
}

# ── FRESHNESS ───────────────────────────────────────────────────────────────
FRESHNESS = {
    "bar_max_age_min": 10,         # skip run if latest 1m bar > 10 min old (1m bars ingested every 60s)
    "signal_max_age_hours": 2.0,   # signals older than this are stale
}

# ── TELEGRAM ────────────────────────────────────────────────────────────────
TELEGRAM_BOT_NAME = "Vlthr_Whisper"

# ── REPLAY ──────────────────────────────────────────────────────────────────
REPLAY = {
    "bars_per_day": 96,            # 15m bars
    "speed_delay_sec": 0.1,       # 10 bars/second
    "schema_prefix": "replay_",    # separate DB tables during replay
}

# ── FILE PATHS (relative to engine/) ────────────────────────────────────────
from pathlib import Path
ENGINE_DIR = Path(__file__).resolve().parent
try:
    REPO_ROOT = ENGINE_DIR.parents[2]  # local dev
except IndexError:
    REPO_ROOT = ENGINE_DIR.parent       # Docker
DATA_ROOT = REPO_ROOT / "data" / "bybit"

# ── BYBIT MAINNET MECHANICS (v0.3.0) ──────────────────────────────────────
QTY_STEP = {
    "BTCUSDT": 0.001, "ETHUSDT": 0.001, "SOLUSDT": 0.1,
    "XRPUSDT": 1, "BNBUSDT": 0.01, "DOGEUSDT": 1,
}
MMR = 0.005                     # Tier 1 Maintenance Margin Rate = 0.5%
TAKER_FEE = 0.00055             # 0.055% per side
MIN_NOTIONAL = 5.0              # $5 USD minimum for USDT perps

# ── DB ──────────────────────────────────────────────────────────────────────
DB_URL_ENV = "DB_URL"

# ── LIQUIDATION PRICE (Bybit formula) ───────────────────────────────────────
def get_liquidation_price(entry: float, qty: float, margin: float, mm: float, side: str) -> float:
    """Return liquidation price. LONG: entry - ((IM - MM)/qty), SHORT: entry + ((IM - MM)/qty)"""
    if qty <= 0 or margin <= mm:
        return 0.0
    im_minus_mm = margin - mm
    if side == "LONG":
        return entry - (im_minus_mm / qty)
    return entry + (im_minus_mm / qty)

# ── DERIVED HELPERS ─────────────────────────────────────────────────────────
def get_risk_tier(dqs: int) -> dict:
    """Return risk tier dict for a given DQS score."""
    for tier_name in ["excellent", "good", "fair"]:
        tier = RISK_TIERS[tier_name]
        if tier["min_dqs"] <= dqs <= tier["max_dqs"]:
            return {"tier": tier_name, **tier}
    return {"tier": "none", "risk_pct": 0, "sl_mult": 0, "tp_mult": 0, "rr": 0}


def get_sl_tp(entry: float, atr: float, dqs: int, side: str = "LONG", regime: str = None) -> tuple:
    """Return (sl_price, tp_price, rr) based on DQS tier + optional regime scaling.
    For LONG: SL below entry, TP above entry.
    For SHORT: SL above entry, TP below entry."""
    if entry <= 0:
        raise ValueError(f"get_sl_tp: entry must be positive, got {entry}")
    if atr <= 0:
        raise ValueError(f"get_sl_tp: atr must be positive, got {atr}")
    if dqs < 50:
        return None, None, 0  # Skip non-executable signals gracefully
    tier = get_risk_tier(dqs)

    sl_mult = tier["sl_mult"]
    tp_mult = tier["tp_mult"]
    if regime:
        r = REGIME_SL_TP_MULTIPLIERS.get(regime.upper(), {})
        sl_mult *= r.get("sl_mult_scale", 1.0)
        tp_mult *= r.get("tp_mult_scale", 1.0)

    if side.upper() == "SHORT":
        sl = entry + atr * sl_mult
        tp = entry - atr * tp_mult

        sl_dist = sl - entry
        tp_dist = entry - tp
        if sl_dist > 0 and (tp_dist / sl_dist) < MIN_RR_FLOOR:
            tp = entry - sl_dist * MIN_RR_FLOOR

        if sl <= entry:
            raise ValueError(f"get_sl_tp: SHORT SL {sl} <= entry {entry} (atr={atr}, mult={sl_mult})")
        if tp >= entry:
            raise ValueError(f"get_sl_tp: SHORT TP {tp} >= entry {entry} (atr={atr}, mult={tp_mult})")
        rr = round((entry - tp) / (sl - entry), 2)
    else:
        sl = entry - atr * sl_mult
        tp = entry + atr * tp_mult

        sl_dist = entry - sl
        tp_dist = tp - entry
        if sl_dist > 0 and (tp_dist / sl_dist) < MIN_RR_FLOOR:
            tp = entry + sl_dist * MIN_RR_FLOOR

        if sl >= entry:
            raise ValueError(f"get_sl_tp: LONG SL {sl} >= entry {entry} (atr={atr}, mult={sl_mult})")
        if tp <= entry:
            raise ValueError(f"get_sl_tp: LONG TP {tp} <= entry {entry} (atr={atr}, mult={tp_mult})")
        rr = round((tp - entry) / (entry - sl), 2)

    return round(sl, 4), round(tp, 4), rr


def cap_position_size(position_size_1x: float, account_balance: float) -> float:
    """Cap position size per symbol to prevent over-concentration. V7: 25% cap."""
    return min(position_size_1x, CAPITAL_PER_SYMBOL, account_balance * SYMBOL_ALLOC_CAP)


def get_sl_tp_pct(entry: float, sl_pct: float, tp_pct: float, side: str = "LONG", regime: str = None) -> tuple:
    """Return (sl_price, tp_price, rr) using fixed percentages of entry price.
    sl_pct and tp_pct are in percentage terms (e.g., 1.5 = 1.5%).
    Optionally scaled by regime multipliers."""
    if entry <= 0:
        raise ValueError(f"get_sl_tp_pct: entry must be positive, got {entry}")
    _sl_pct = sl_pct
    _tp_pct = tp_pct
    if regime:
        r = REGIME_SL_TP_MULTIPLIERS.get(regime.upper(), {})
        _sl_pct *= r.get("sl_mult_scale", 1.0)
        _tp_pct *= r.get("tp_mult_scale", 1.0)
    sl_dist = entry * (_sl_pct / 100)
    tp_dist = entry * (_tp_pct / 100)
    if side.upper() == "SHORT":
        sl = entry + sl_dist
        tp = entry - tp_dist
    else:
        sl = entry - sl_dist
        tp = entry + tp_dist
    rr = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0
    return round(sl, 4), round(tp, 4), rr


def get_kelly_v7(dqs: float, strategy: str = "trend_following") -> float:
    """V7 Kelly-band multiplier with MR 3x boost."""
    if dqs >= 85: kelly = KELLY_V7_BANDS["dqs_ge_85"]
    elif dqs >= 80: kelly = KELLY_V7_BANDS["dqs_ge_80"]
    elif dqs >= 75: kelly = KELLY_V7_BANDS["dqs_ge_75"]
    elif dqs >= 70: kelly = KELLY_V7_BANDS["dqs_ge_70"]
    elif dqs >= 65: kelly = KELLY_V7_BANDS["dqs_ge_65"]
    else: kelly = KELLY_V7_BANDS["dqs_lt_65"]
    if strategy == "mean_reversion": kelly *= MR_BOOST
    return kelly


def get_max_trade_hours(symbol: str) -> float:
    """Return per-symbol max trade hold time in hours, falling back to global default."""
    params = get_symbol_params(symbol)
    return params.get("max_trade_hours", SIGNAL_LIFECYCLE["max_trade_hours"])


# ── Feature Flags ──────────────────────────────────────────────────────────

BRAIN_ENABLED = False  # Set to True when brain model accuracy > random

SYMBOL_DISABLE_RULES = {
    "consecutive_sl_threshold": 5,
    "win_rate_lookback": 20,
    "win_rate_threshold": 0.25,
    "auto_reenable_after_hours": 24,
    "min_trades_before_disable": 10,
}

# ── TP/SL TUNING FROM CLOSED TRADES ───────────────────────────────────────
# Runtime-adjusted TP multipliers based on closed-trade outcomes.
# If TP hit rate is 0% with >5 TIME_EXIT winners, tighten TP by 20%.
_TP_TUNING_CACHE = {"applied": False, "adjustments": {}}

def tune_tp_from_history(conn) -> dict:
    """Query closed trades and adjust TP multipliers if targets are too far.
    Returns dict of tier -> adjusted tp_mult. Only adjusts if >=20 closed trades.
    Only applies ONCE per process lifetime to prevent compounding."""
    if _TP_TUNING_CACHE["applied"]:
        return _TP_TUNING_CACHE["adjustments"]
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT exit_reason, COUNT(*) FROM paper_trades
            WHERE status LIKE 'CLOSED%' AND exit_reason IS NOT NULL
            GROUP BY exit_reason
        """)
        counts = dict(cur.fetchall())
        total = sum(counts.values())
        if total < 20:
            return {}
        tp_hits = counts.get("TP_HIT", 0)
        time_exits = counts.get("TIME_EXIT", 0)
        if tp_hits == 0 and time_exits > 5:
            adjustments = {}
            for tier_name, tier in RISK_TIERS.items():
                old_tp = tier["tp_mult"]
                new_tp = round(old_tp * 0.80, 1)
                new_tp = max(new_tp, tier["sl_mult"] * MIN_RR_FLOOR)
                adjustments[tier_name] = new_tp
                tier["tp_mult"] = new_tp
                tier["rr"] = round(new_tp / tier["sl_mult"], 2)
            _TP_TUNING_CACHE["applied"] = True
            _TP_TUNING_CACHE["adjustments"] = adjustments
            print(f"  [TP_TUNING] Tightened TP multipliers (0 TP hits, {time_exits} TIME_EXITs): {adjustments}")
            return adjustments
    except Exception as e:
        print(f"  [TP_TUNING] Failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    return {}


# ── REGIME FEEDBACK FROM CLOSED TRADES ─────────────────────────────────────
# Runtime-adjusted REGIME_SL_TP_MULTIPLIERS based on closed-trade outcomes per regime.
# If a regime has high SL_HIT rate, widen SL. If TP_HIT is 0 with TIME_EXIT winners,
# tighten TP. Only adjusts if >=10 closed trades per regime.
_REGIME_TUNING_CACHE = {"applied": False, "adjustments": {}}

def tune_regime_from_history(conn) -> dict:
    """Query closed trades grouped by entry_regime and adjust SL/TP multipliers.
    Only applies ONCE per process lifetime to prevent compounding."""
    if _REGIME_TUNING_CACHE["applied"]:
        return _REGIME_TUNING_CACHE["adjustments"]
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT entry_regime, exit_reason, COUNT(*), AVG(net_pnl_usd)
            FROM paper_trades
            WHERE status LIKE 'CLOSED%' AND entry_regime IS NOT NULL
            GROUP BY entry_regime, exit_reason
        """)
        rows = cur.fetchall()
        if not rows:
            return {}

        regime_stats = {}
        for regime, exit_reason, count, avg_pnl in rows:
            r = (regime or "MIXED").upper()
            if r not in regime_stats:
                regime_stats[r] = {"total": 0, "sl_hit": 0, "tp_hit": 0, "time_exit": 0, "time_exit_winners": 0}
            regime_stats[r]["total"] += count
            if exit_reason == "SL_HIT":
                regime_stats[r]["sl_hit"] += count
            elif exit_reason == "TP_HIT":
                regime_stats[r]["tp_hit"] += count
            elif exit_reason == "TIME_EXIT":
                regime_stats[r]["time_exit"] += count
                if avg_pnl and float(avg_pnl) > 0:
                    regime_stats[r]["time_exit_winners"] += count

        adjustments = {}
        for regime, stats in regime_stats.items():
            if stats["total"] < 10:
                continue
            sl_rate = stats["sl_hit"] / stats["total"]
            tp_rate = stats["tp_hit"] / stats["total"]
            base = REGIME_SL_TP_MULTIPLIERS.get(regime, {"sl_mult_scale": 1.0, "tp_mult_scale": 1.0})
            adj = dict(base)

            # High SL rate → widen SL further
            if sl_rate > 0.6:
                adj["sl_mult_scale"] = round(base["sl_mult_scale"] * 1.2, 2)
            # Zero TP hits with TIME_EXIT winners → tighten TP
            if tp_rate == 0 and stats["time_exit_winners"] > 2:
                adj["tp_mult_scale"] = round(base["tp_mult_scale"] * 0.8, 2)

            if adj != base:
                REGIME_SL_TP_MULTIPLIERS[regime] = adj
                adjustments[regime] = adj
                print(f"  [REGIME_TUNING] {regime}: SL rate={sl_rate:.0%}, TP rate={tp_rate:.0%} → adjusted {adj}")

        if adjustments:
            _REGIME_TUNING_CACHE["applied"] = True
            _REGIME_TUNING_CACHE["adjustments"] = adjustments
        return adjustments
    except Exception as e:
        print(f"  [REGIME_TUNING] Failed: {e}")
        try:
            conn.rollback()
        except Exception:
            pass
    return {}
