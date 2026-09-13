"""
Trade Debugger
==============
Rule-based failure classifier for closed paper trades.
Analyzes entry/exit context snapshots to categorize why a trade lost.

Categories:
  MACRO_HEADWIND — BTC selling off against trade direction
  TIMING         — Signal fired too early (weak ADX, SL swept quickly)
  WHIPSAW        — ATR expansion after entry
  CORRECT_SIGNAL — Price hit TP area but TIME_EXIT fired first
  ENTRY_QUALITY  — Hostile liquidity at entry (funding +, OI rising, imbalance)
"""

from typing import Optional


def classify_failure(entry_ctx: dict, exit_ctx: dict, trade: dict) -> str:
    """
    Rule-based classifier. Returns one of:
      MACRO_HEADWIND, TIMING, WHIPSAW, CORRECT_SIGNAL, ENTRY_QUALITY, UNKNOWN
    """
    side = (trade.get("side") or trade.get("trade_decision") or "LONG").upper()
    exit_reason = (trade.get("exit_reason") or "").upper()
    hours_held = float(trade.get("hours_held") or 0)
    net_pnl = float(trade.get("net_pnl_usd") or trade.get("net_pnl_pct") or 0)

    # Only classify losing trades; winners get CORRECT_SIGNAL if they reached TP area
    if net_pnl >= 0:
        if exit_reason == "TIME_EXIT":
            return "CORRECT_SIGNAL"
        return "UNKNOWN"

    # 1. MACRO_HEADWIND: BTC 24h return against trade direction
    btc_ret = float(entry_ctx.get("btc_24h_ret") or 0)
    if side == "LONG" and btc_ret < -0.02:
        return "MACRO_HEADWIND"
    if side == "SHORT" and btc_ret > 0.02:
        return "MACRO_HEADWIND"

    # 2. WHIPSAW: ATR expansion > 1.5x at exit vs entry
    entry_atr = max(float(entry_ctx.get("atr_15m") or 1), 0.0001)
    exit_atr = float(exit_ctx.get("atr_15m") or entry_atr)
    if exit_atr / entry_atr > 1.5:
        return "WHIPSAW"

    # 3. CORRECT_SIGNAL: Price reached TP area but TIME_EXIT fired (already handled for winners above)
    # For losers, if held >= 5h and TIME_EXIT, it means the signal was probably right but didn't move fast enough
    if hours_held >= 5 and exit_reason == "TIME_EXIT":
        return "TIMING"

    # 4. ENTRY_QUALITY: Hostile liquidity at entry
    ob_imb = float(entry_ctx.get("ob_imbalance") or entry_ctx.get("ob_imbalance_pct") or 0)
    funding = float(entry_ctx.get("funding_rate") or 0)
    oi_delta = float(entry_ctx.get("oi_delta_pct") or 0)
    # Hostile = positive funding (costs longs), OI rising (crowded), imbalance negative
    if side == "LONG" and funding > 0.0001 and oi_delta > 2 and ob_imb < -5:
        return "ENTRY_QUALITY"
    if side == "SHORT" and funding < -0.0001 and oi_delta > 2 and ob_imb > 5:
        return "ENTRY_QUALITY"

    # 5. TIMING: Early signal, weak trend, SL swept quickly
    adx = float(entry_ctx.get("adx_4h") or 0)
    rsi = float(entry_ctx.get("rsi_15m") or 50)
    sl_dist = float(trade.get("sl_distance_pct") or 1)
    if adx < 25 and rsi < 35 and hours_held < 3 and exit_reason in ("SL_HIT", "LIQUIDATED"):
        return "TIMING"

    return "UNKNOWN"


def suggested_fix(category: str) -> str:
    """Return a one-line actionable recommendation for each failure category."""
    fixes = {
        "MACRO_HEADWIND": "Add BTC trend gate: skip LONG if BTC 4h structure bearish",
        "TIMING": "Wait for ADX >= 25 and RSI >= 30 before entry; tighten entry timing",
        "WHIPSAW": "Widen SL by regime factor when ATR > 1.2x 20-bar average",
        "CORRECT_SIGNAL": "Extend hold time or widen TP for high-conviction setups",
        "ENTRY_QUALITY": "Raise liquidity threshold: require aligned funding + falling OI",
        "UNKNOWN": "Review raw DQS breakdown and macro context for edge cases",
    }
    return fixes.get(category, "Review trade context manually")


def debug_trade(entry_ctx: dict, exit_ctx: dict, trade: dict) -> dict:
    """
    Full debug pipeline: classify + generate fix.
    Returns a dict ready for trade_debug_log.failure_category / suggested_fix.
    """
    category = classify_failure(entry_ctx, exit_ctx, trade)
    fix = suggested_fix(category)
    return {
        "failure_category": category,
        "suggested_fix": fix,
    }
