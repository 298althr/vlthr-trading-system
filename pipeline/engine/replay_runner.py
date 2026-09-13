"""
VLTHR Replay Runner — Faithful D5 Replica
==========================================
Bar-by-bar historical simulation that EXACTLY mirrors the D5/D6/D7 backtester.

Key design decisions (matching D5 realworld_portfolio.py):
  1. Dual-timeframe: 15m execution + 4h context (merged via merge_asof)
  2. Binary gates ONLY (session, ADX, direction, RSI) — NO DQS scoring
  3. Per-symbol params from proven D5 config
  4. Position sizing: risk_pct per symbol = (capital/6) × risk_pct
  5. Leverage 4× applied to PnL (D5: equity_lev = init + (equity−init) × 4)
  6. Real-world frictions: taker 0.055% per side + 0.1% slippage on entry
  7. One signal per bar per symbol (no stacking until previous closes)

Usage:
    from replay_runner import ReplayRunner
    runner = ReplayRunner(conn, start="2025-06-01", end="2026-06-11")
    results = runner.run()
"""
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, List

_engine = Path(__file__).resolve().parent
try:
    _repo = _engine.parents[3]
except IndexError:
    _repo = _engine.parent
sys.path.insert(0, str(_engine))
sys.path.insert(0, str(_repo / "departments" / "strategy_research" / "BACKTESTER" / "scripts" / "common"))

import pandas as pd
import numpy as np

from portfolio_config import SYMBOLS, get_symbol_params


# ── D5 CONSTANTS ──────────────────────────────────────────────────────────
LEVERAGE = 4
FEE_TAKER = 0.00055   # 0.055% per side
SPREAD = 0.0002       # 0.02% spread
SLIPPAGE_ATR_PCT = 0.10  # 10% of ATR as slippage (matches D5)
INIT_CAPITAL = 10000.0


def _classify_session(ts) -> str:
    """Classify UTC timestamp into trading session.

    Delegates to portfolio_config.classify_session for single source of truth.
    Falls back to local definition if portfolio_config is not importable.
    """
    try:
        from portfolio_config import classify_session
        return classify_session(ts)
    except ImportError:
        pass
    hour = ts.hour
    if 0 <= hour < 8:
        return "asian"
    elif 8 <= hour < 12:
        return "london"
    elif 12 <= hour < 17:
        return "ny_open"
    else:
        return "ny_late"


def _add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Compute Average True Range."""
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.rolling(window=period, min_periods=period).mean()
    return df


def _add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Compute RSI."""
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def _add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    """Compute ADX."""
    high_diff = df["high"].diff()
    low_diff = (-df["low"]).diff()
    dm_plus = high_diff.where((high_diff > low_diff) & (high_diff > 0), 0)
    dm_minus = low_diff.where((low_diff > high_diff) & (low_diff > 0), 0)
    tr = df["high"] - df["low"]
    tr = tr.combine_first((df["high"] - df["close"].shift()).abs())
    tr = tr.combine_first((df["low"] - df["close"].shift()).abs())
    atr = tr.rolling(window=period, min_periods=period).mean()
    di_plus = (dm_plus.rolling(window=period).mean() / atr) * 100
    di_minus = (dm_minus.rolling(window=period).mean() / atr) * 100
    dx = (di_plus - di_minus).abs() / (di_plus + di_minus) * 100
    df["adx"] = dx.rolling(window=period).mean()
    return df


class ReplayRunner:
    """Bar-by-bar historical replay — faithful D5 replica."""

    def __init__(self, conn, start: str, end: str, initial_balance: float = 10000.0):
        self.conn = conn
        self.start = pd.to_datetime(start, utc=True)
        self.end = pd.to_datetime(end, utc=True)
        self.balance = initial_balance
        self.equity = initial_balance
        self.trades: List[dict] = []
        self.snapshots: List[dict] = []

    def _load_dual_tf(self, symbol: str) -> pd.DataFrame:
        """
        Load 15m + 4h data, merge context columns onto 15m index.
        Returns 15m DataFrame with ctx_adx, ctx_log_ret_4h, etc.
        """
        from data_loader import load_symbol_tf

        # Load 15m execution data
        exec_df = load_symbol_tf(symbol, "15m")
        exec_df["timestamp"] = pd.to_datetime(exec_df["timestamp"], utc=True)
        exec_df = exec_df.sort_values("timestamp").reset_index(drop=True)
        exec_df = exec_df[(exec_df["timestamp"] >= self.start) & (exec_df["timestamp"] <= self.end)]

        # Load 4h context data
        ctx_df = load_symbol_tf(symbol, "4h")
        ctx_df["timestamp"] = pd.to_datetime(ctx_df["timestamp"], utc=True)
        ctx_df = ctx_df.sort_values("timestamp").reset_index(drop=True)

        # Compute 4h ADX and log return on 4h bars
        ctx_df = _add_adx(ctx_df, 14)
        ctx_df["log_ret_4h"] = np.log(ctx_df["close"] / ctx_df["close"].shift(1))

        # Merge 4h context onto 15m bars (backward-asof)
        merged = pd.merge_asof(
            exec_df.sort_values("timestamp"),
            ctx_df[["timestamp", "adx", "log_ret_4h", "close"]].rename(columns={
                "adx": "ctx_adx",
                "log_ret_4h": "ctx_log_ret_4h",
                "close": "ctx_close_4h"
            }).sort_values("timestamp"),
            on="timestamp",
            direction="backward"
        )

        # Add session and 15m indicators
        merged["session"] = merged["timestamp"].apply(_classify_session)
        merged = _add_atr(merged, 14)
        merged = _add_rsi(merged, 14)
        merged = _add_adx(merged, 14)

        return merged.sort_values("timestamp").reset_index(drop=True)

    def _simulate_sl_tp(self, trade: dict, bar: pd.Series) -> Optional[str]:
        """Check if SL or TP was hit on this bar."""
        sl = trade["sl"]
        tp = trade["tp"]
        low = float(bar["low"])
        high = float(bar["high"])

        if low <= sl:
            return "SL_HIT"
        if high >= tp:
            return "TP_HIT"
        return None

    def _calc_pnl(self, trade: dict, exit_price: float) -> float:
        """
        Calculate net PnL for a closed trade.
        D5 style: slippage = 10% of ATR, fees = 0.055% + 0.02% spread + slippage.
        Returns RAW net PnL (leverage applied at portfolio level, not per-trade).
        """
        entry = trade["entry"]
        qty = trade["qty"]
        atr = trade.get("atr", 0)
        slippage = entry * SLIPPAGE_ATR_PCT * (atr / entry) if entry > 0 else 0
        effective_entry = entry + slippage
        gross = (exit_price - effective_entry) * qty
        total_fee_rate = FEE_TAKER + SPREAD + (SLIPPAGE_ATR_PCT * atr / entry if entry > 0 else 0)
        fees = (effective_entry + exit_price) * qty * total_fee_rate
        return gross - fees

    def run(self) -> dict:
        """Execute the full bar-by-bar replay — EXACT D5 replica."""
        print(f"\n{'='*70}")
        print(f"  VLTHR Replay Runner (D5 Replica)")
        print(f"  {self.start.date()} → {self.end.date()}")
        print(f"  Initial: ${INIT_CAPITAL:,.2f} | Leverage: {LEVERAGE}x")
        print(f"{'='*70}")

        # Load dual-timeframe data for all symbols
        all_data = {}
        for sym in SYMBOLS:
            try:
                df = self._load_dual_tf(sym)
                if len(df):
                    all_data[sym] = df
                    print(f"  Loaded {sym}: {len(df)} bars (15m) | 4h ctx merged")
            except Exception as e:
                print(f"  [WARN] Failed to load {sym}: {e}")
                continue

        if not all_data:
            print("  No data loaded — aborting.")
            return {}

        # Build unified timeline
        timestamps = sorted(set(pd.concat([df["timestamp"] for df in all_data.values()])))
        print(f"  Total unique timestamps: {len(timestamps)}")

        # Per-symbol state (matching D5 architecture)
        init_per_sym = INIT_CAPITAL / len(SYMBOLS)
        sym_equity = {sym: init_per_sym for sym in all_data}
        sym_cash = {sym: init_per_sym for sym in all_data}
        open_trades: List[dict] = []
        pending_entries: Dict[str, dict] = {}  # symbol → trade to open next bar

        signals_scanned = 0
        signals_confirmed = 0
        total_tp = total_sl = 0

        for i, ts in enumerate(timestamps):
            for sym, df in all_data.items():
                bar = df[df["timestamp"] == ts]
                if len(bar) == 0:
                    continue
                bar = bar.iloc[0]

                # ── 1. Check open trades for SL/TP FIRST ──
                for trade in list(open_trades):
                    if trade["symbol"] != sym:
                        continue
                    hit = self._simulate_sl_tp(trade, bar)
                    if hit:
                        exit_p = trade["tp"] if hit == "TP_HIT" else trade["sl"]
                        pnl = self._calc_pnl(trade, exit_p)
                        sym_cash[sym] += trade["position_value"] + pnl
                        trade["exit_price"] = exit_p
                        trade["exit_reason"] = hit
                        trade["exit_time"] = ts
                        trade["pnl"] = pnl
                        open_trades.remove(trade)
                        self.trades.append(trade)
                        if hit == "TP_HIT":
                            total_tp += 1
                        else:
                            total_sl += 1
                        print(f"    [{sym}] {hit} @ {exit_p:.2f} | PnL: ${pnl:+.2f}")

                # ── 2. Execute pending entry from PREVIOUS bar (D5 delay) ──
                if sym in pending_entries:
                    trade = pending_entries.pop(sym)
                    # Only open if no existing trade for this symbol
                    already_open = any(t["symbol"] == sym and t.get("exit_reason") is None for t in open_trades)
                    if not already_open and sym_cash[sym] >= trade["position_value"]:
                        open_trades.append(trade)
                        sym_cash[sym] -= trade["position_value"]
                        print(f"    [{sym}] OPEN @ {trade['entry']:.2f} | SL={trade['sl']:.2f} | TP={trade['tp']:.2f} | Qty={trade['qty']:.4f}")

                # ── 3. Signal generation (binary gates, NO DQS) ──
                signals_scanned += 1

                params = get_symbol_params(sym)
                rsi = bar.get("rsi", 100)
                adx = bar.get("ctx_adx", 0)
                log_ret = bar.get("ctx_log_ret_4h", 0)
                session = bar.get("session", "")

                gate_rsi = rsi < params["rsi_threshold"]
                gate_adx = adx >= params["adx_min"]
                gate_direction = log_ret > 0
                gate_session = session in {"london", "ny_late"}

                if not (gate_rsi and gate_adx and gate_direction and gate_session):
                    continue

                signals_confirmed += 1

                # ── 4. Queue entry for NEXT bar (D5 1-bar delay) ──
                entry = float(bar["close"])
                atr = float(bar.get("atr", 0))
                if atr <= 0 or pd.isna(atr):
                    continue

                sl = entry - atr * params["sl_mult"]
                tp = entry + atr * params["tp_mult"]
                sl_dist = entry - sl
                sl_pct = sl_dist / entry if entry > 0 else 0

                size_pct = (params["risk_pct"] / sl_pct) if sl_pct > 0 else 0
                size_pct = min(size_pct, 1.0)
                # Use CURRENT symbol equity for sizing (matches vectorbt compounding)
                position_value = sym_equity[sym] * size_pct
                qty = position_value / entry if entry > 0 else 0

                if qty <= 0:
                    continue

                pending_entries[sym] = {
                    "symbol": sym,
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "qty": qty,
                    "position_value": position_value,
                    "atr": atr,
                    "open_time": ts,
                }

            # ── 5. Equity snapshot (per-symbol, then portfolio) ──
            for sym in all_data:
                sym_unrealized = sum(
                    self._sym_unrealized(t, all_data.get(t["symbol"]), ts)
                    for t in open_trades if t["symbol"] == sym
                )
                sym_equity[sym] = sym_cash[sym] + sym_unrealized

            # D5 leverage formula: equity_lev = init + (equity − init) × leverage
            # Clamp to 0 (D5: np.maximum(equity_lev, 0))
            portfolio_equity = sum(
                max(0, init_per_sym + (sym_equity[sym] - init_per_sym) * LEVERAGE)
                for sym in all_data
            )
            # Apply funding penalty (0.5% over full period, D5 applies uniformly)
            # We apply it as a running compounding factor instead

            self.equity = portfolio_equity
            self.snapshots.append({
                "timestamp": ts,
                "equity": self.equity,
                "open_trades": len(open_trades),
            })

            if i % 96 == 0 and i > 0:
                print(f"  Day {i//96}: Equity=${self.equity:,.2f}, Open={len(open_trades)}, "
                      f"Closed={total_tp+total_sl}")

        # ── Apply D5 funding penalty (0.5% flat) ──
        final_equity = self.equity * 0.995

        # ── Summary ──
        df_snap = pd.DataFrame(self.snapshots)
        total_return = (final_equity / INIT_CAPITAL - 1) * 100
        max_dd = self._max_drawdown(df_snap)

        print(f"\n{'='*70}")
        print(f"  VLTHR — Replay Report (D5 Replica)")
        print(f"{'='*70}")
        print(f"  Period:        {self.start.date()} → {self.end.date()}")
        print(f"  Total bars:    {len(timestamps)}")
        print(f"  Symbols:       {len(SYMBOLS)}")
        print(f"")
        print(f"  SIGNAL FUNNEL:")
        print(f"    Scanned:     {signals_scanned}")
        print(f"    Confirmed:   {signals_confirmed} (passed 4-gate criteria)")
        print(f"    Trades:      {len(self.trades)}")
        print(f"")
        print(f"  TRADE RESULTS:")
        print(f"    TP hit:      {total_tp}")
        print(f"    SL hit:      {total_sl}")
        print(f"    Win rate:    {(total_tp / len(self.trades) * 100) if self.trades else 0:.1f}%")
        print(f"    Final equity:  ${final_equity:,.2f} ({total_return:+.2f}%)")
        print(f"    Max drawdown:  {max_dd:.2f}%")
        print(f"{'='*70}")

        return {
            "equity_curve": df_snap,
            "trades": self.trades,
            "signals_scanned": signals_scanned,
            "signals_confirmed": signals_confirmed,
            "final_equity": final_equity,
            "total_return_pct": total_return,
            "max_drawdown_pct": max_dd,
            "win_rate_pct": (total_tp / len(self.trades) * 100) if self.trades else 0,
            "tp_hits": total_tp,
            "sl_hits": total_sl,
        }

    def _sym_unrealized(self, trade: dict, df: Optional[pd.DataFrame], ts) -> float:
        """Current market value of open position at timestamp ts (raw, no leverage)."""
        if df is None or len(df) == 0:
            return 0
        bar = df[df["timestamp"] == ts]
        if len(bar) == 0:
            return 0
        price = float(bar.iloc[0]["close"])
        # Market value of the position (not PnL), so equity = cash + market_value
        return price * trade["qty"]

    def _max_drawdown(self, df: pd.DataFrame) -> float:
        """Calculate max drawdown from equity curve."""
        if len(df) == 0:
            return 0
        peak = df["equity"].cummax()
        dd = (df["equity"] - peak) / peak * 100
        return dd.min()
