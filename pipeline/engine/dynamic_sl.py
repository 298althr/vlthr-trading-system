"""
Dynamic Stop-Loss Engine (Phase 6)
====================================
Python port of server.cjs computeDynamicSL for testing and reuse.
"""

from portfolio_config import SL_TIME_DECAY_RATE


def compute_dynamic_sl(trade: dict, live_price: float, hours_open: float) -> dict:
    """
    Compute dynamic stop-loss based on three rules:
    1. Break-even: move SL to entry when floating PnL > 50% of TP distance
    2. Time decay: tighten SL by SL_TIME_DECAY_RATE x ATR per hour after hour 3
    3. ATR expansion: widen SL when live volatility > 1.5x entry ATR

    Returns {"new_sl": float, "reasons": list} or None if no change needed.
    """
    side = (trade.get("side") or "LONG").upper()
    entry = float(trade.get("entry_price_actual") or trade.get("entry_price_planned") or 0)
    tp = float(trade.get("tp_price") or 0)
    sl_original = float(trade.get("sl_original") or trade.get("sl_price") or 0)
    atr_entry = float(trade.get("atr_at_entry") or 0)

    if entry <= 0 or sl_original <= 0:
        return None

    new_sl = sl_original
    reasons = []

    # 1. Break-even stop
    tp_dist = abs(tp - entry)
    if tp_dist > 0:
        pnl_frac = (live_price - entry) / tp_dist if side == "LONG" else (entry - live_price) / tp_dist
        if pnl_frac > 0.5:
            new_sl = entry * 0.9995 if side == "LONG" else entry * 1.0005
            reasons.append("BREAK_EVEN")

    # 2. Time decay
    if atr_entry > 0 and hours_open > 3:
        tighten_amt = SL_TIME_DECAY_RATE * atr_entry * (hours_open - 3)
        if side == "LONG":
            decay_sl = max(entry, sl_original + tighten_amt)
            if decay_sl > new_sl:
                new_sl = decay_sl
                reasons.append("TIME_DECAY")
        else:
            decay_sl = min(entry, sl_original - tighten_amt)
            if decay_sl < new_sl:
                new_sl = decay_sl
                reasons.append("TIME_DECAY")

    # 3. ATR expansion (simplified: requires live_atr passed in trade dict)
    live_atr = float(trade.get("live_atr") or atr_entry)
    if atr_entry > 0 and live_atr > 1.5 * atr_entry:
        factor = live_atr / atr_entry
        if side == "LONG":
            expanded_sl = entry - (entry - sl_original) * factor
            if expanded_sl < new_sl:
                new_sl = expanded_sl
                reasons.append("ATR_EXPANSION")
        else:
            expanded_sl = entry + (sl_original - entry) * factor
            if expanded_sl > new_sl:
                new_sl = expanded_sl
                reasons.append("ATR_EXPANSION")

    return {"new_sl": round(new_sl, 8), "reasons": reasons}
