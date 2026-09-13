"""
Backtest the mean-reversion SHORT strategy for BNB/ETH/DOGE.
Uses enriched 15m parquet data from the last 90 days.

Simulates:
  - RSI-based direction (SHORT when RSI >= overbought threshold)
  - Percentage-based SL/TP
  - Per-symbol max hold times
  - DQS scoring with the new direction-aware adaptive scorer

Usage:
    python backtest_mean_reversion.py
"""
import sys
import os
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta, timezone

_ENGINE = Path(__file__).resolve().parent
_REPO = Path("c:/Users/Sav/Documents/VLTHR")
sys.path.insert(0, str(_ENGINE))
sys.path.insert(0, str(_REPO / "departments" / "strategy_research" / "BACKTESTER" / "scripts" / "common"))

from portfolio_config import get_symbol_params, get_sl_tp_pct, get_max_trade_hours
from indicators import add_atr, add_rsi, add_adx

DATA_ROOT = Path(_REPO / "data" / "bybit")

BACKTEST_SYMBOLS = ["BNBUSDT", "ETHUSDT", "DOGEUSDT"]
BACKTEST_DAYS = 90
INITIAL_BALANCE = 10000
FEE_RATE = 0.00055  # 0.055% per side


def load_enriched_data(symbol: str, days: int = 90) -> pd.DataFrame:
    """Load enriched 15m parquet data for the last N days."""
    enriched_dir = DATA_ROOT / symbol / "enriched" / "15m"
    if not enriched_dir.exists():
        print(f"  [Warning] No enriched data for {symbol}")
        return pd.DataFrame()

    # Load recent months (load extra for indicator warmup)
    now = datetime.now(timezone.utc)
    files_to_load = []
    for year in range(now.year - 1, now.year + 1):
        year_dir = enriched_dir / str(year)
        if not year_dir.exists():
            continue
        for month in range(1, 13):
            f = year_dir / f"{month:02d}.parquet"
            if f.exists():
                files_to_load.append(f)

    if not files_to_load:
        return pd.DataFrame()

    dfs = [pd.read_parquet(f) for f in sorted(files_to_load)]
    df = pd.concat(dfs, ignore_index=True)

    # Compute indicators (raw parquet only has OHLCV + funding + OI)
    df = add_atr(df, 14)
    df = add_rsi(df, 14)
    df = add_adx(df, 14)

    # Filter to last N days (after indicator warmup)
    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        cutoff = now - timedelta(days=days)
        df = df[df["timestamp"] >= cutoff].reset_index(drop=True)

    return df


def simulate_symbol(symbol: str, df: pd.DataFrame, short_only: bool = False) -> dict:
    """Run backtest simulation for a single symbol."""
    if df.empty or len(df) < 100:
        return {"symbol": symbol, "trades": [], "n_trades": 0, "wins": 0, "losses": 0, "wr": 0, "pnl": 0}

    params = get_symbol_params(symbol)
    rsi_overbought = params.get("rsi_overbought", 65)
    rsi_oversold = params.get("rsi_oversold", 30)
    sl_pct = params.get("sl_pct", 1.5)
    tp_pct = params.get("tp_pct", 3.0)
    max_hours = params.get("max_trade_hours", 12)
    adx_min = params.get("adx_min", 20)

    trades = []
    in_trade = False
    current_trade = None

    for i in range(len(df)):
        row = df.iloc[i]
        rsi = float(row.get("rsi", 50)) if not pd.isna(row.get("rsi")) else 50
        adx = float(row.get("adx", 0)) if not pd.isna(row.get("adx")) else 0
        close = float(row["close"])
        ts = row.get("timestamp")

        # Check if we need to close current trade
        if in_trade and current_trade:
            hours_held = 0
            if current_trade["entry_ts"] and ts:
                hours_held = (ts - current_trade["entry_ts"]).total_seconds() / 3600

            # Check exit conditions
            exit_reason = None
            exit_price = close

            if current_trade["side"] == "SHORT":
                # SHORT: TP below entry, SL above entry
                if close <= current_trade["tp"]:
                    exit_reason = "TP_HIT"
                    exit_price = current_trade["tp"]
                elif close >= current_trade["sl"]:
                    exit_reason = "SL_HIT"
                    exit_price = current_trade["sl"]
                elif hours_held >= max_hours:
                    exit_reason = "TIME_EXIT"
            else:
                # LONG: TP above entry, SL below entry
                if close >= current_trade["tp"]:
                    exit_reason = "TP_HIT"
                    exit_price = current_trade["tp"]
                elif close <= current_trade["sl"]:
                    exit_reason = "SL_HIT"
                    exit_price = current_trade["sl"]
                elif hours_held >= max_hours:
                    exit_reason = "TIME_EXIT"

            if exit_reason:
                # Calculate PnL
                entry = current_trade["entry"]
                qty = current_trade["qty"]
                fee = entry * qty * FEE_RATE * 2  # entry + exit

                if current_trade["side"] == "SHORT":
                    raw_pnl = (entry - exit_price) * qty
                else:
                    raw_pnl = (exit_price - entry) * qty

                net_pnl = raw_pnl - fee
                current_trade["exit_reason"] = exit_reason
                current_trade["exit_price"] = exit_price
                current_trade["net_pnl"] = round(net_pnl, 2)
                current_trade["hours_held"] = round(hours_held, 1)
                current_trade["win"] = net_pnl > 0
                trades.append(current_trade)

                in_trade = False
                current_trade = None

        # Check entry conditions (only if not in a trade)
        if not in_trade:
            # Mean-reversion: SHORT when overbought, LONG when oversold
            if rsi >= rsi_overbought and adx >= adx_min:
                side = "SHORT"
            elif rsi <= rsi_oversold and adx >= adx_min and not short_only:
                side = "LONG"
            else:
                continue

            sl, tp, rr = get_sl_tp_pct(close, sl_pct, tp_pct, side=side)

            # Position size: risk 2% of balance per trade
            risk_usd = INITIAL_BALANCE * 0.02
            sl_dist = abs(close - sl)
            qty = risk_usd / sl_dist if sl_dist > 0 else 0

            # Min notional check
            notional = close * qty
            if notional < 5:
                continue

            in_trade = True
            current_trade = {
                "symbol": symbol,
                "side": side,
                "entry": close,
                "sl": sl,
                "tp": tp,
                "rr": rr,
                "qty": qty,
                "rsi_at_entry": round(rsi, 2),
                "adx_at_entry": round(adx, 2),
                "entry_ts": ts,
            }

    # Close any remaining open trade at last close
    if in_trade and current_trade:
        last_close = float(df.iloc[-1]["close"])
        entry = current_trade["entry"]
        qty = current_trade["qty"]
        fee = entry * qty * FEE_RATE * 2
        if current_trade["side"] == "SHORT":
            raw_pnl = (entry - last_close) * qty
        else:
            raw_pnl = (last_close - entry) * qty
        current_trade["exit_reason"] = "EOD"
        current_trade["exit_price"] = last_close
        current_trade["net_pnl"] = round(raw_pnl - fee, 2)
        current_trade["hours_held"] = 0
        current_trade["win"] = (raw_pnl - fee) > 0
        trades.append(current_trade)

    # Stats
    wins = sum(1 for t in trades if t["win"])
    losses = sum(1 for t in trades if not t["win"])
    total_pnl = sum(t["net_pnl"] for t in trades)
    n = len(trades)
    wr = (wins / n * 100) if n > 0 else 0

    return {
        "symbol": symbol,
        "trades": trades,
        "n_trades": n,
        "wins": wins,
        "losses": losses,
        "wr": round(wr, 1),
        "pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / n, 2) if n > 0 else 0,
    }


def main():
    print(f"{'='*70}")
    print(f"  Mean-Reversion Strategy Backtest (Last {BACKTEST_DAYS} days)")
    print(f"{'='*70}\n")

    # Cache loaded data across modes
    data_cache = {}

    for mode_name, short_only in [("MIXED (LONG+SHORT)", False), ("SHORT-ONLY", True)]:
        print(f"\n{'='*70}")
        print(f"  MODE: {mode_name}")
        print(f"{'='*70}\n")

        all_results = []
        for symbol in BACKTEST_SYMBOLS:
            if symbol not in data_cache:
                print(f"  Loading {symbol}...")
                df = load_enriched_data(symbol, BACKTEST_DAYS)
                data_cache[symbol] = df
            else:
                df = data_cache[symbol]

            if df.empty:
                print(f"  [Skip] No data for {symbol}")
                continue

            if not short_only:
                print(f"  Loaded {len(df)} bars ({df['timestamp'].min()} to {df['timestamp'].max()})")
            result = simulate_symbol(symbol, df, short_only=short_only)
            all_results.append(result)

            print(f"\n  -- {symbol} Results --")
            print(f"  Trades: {result['n_trades']}")
            print(f"  Wins: {result['wins']}  Losses: {result['losses']}")
            print(f"  Win Rate: {result['wr']}%")
            print(f"  Total PnL: ${result['pnl']}")
            print(f"  Avg PnL/Trade: ${result['avg_pnl']}")

            for t in result["trades"][:10]:
                print(f"    {t['side']:5s} RSI={t['rsi_at_entry']:5.1f} ADX={t['adx_at_entry']:5.1f} "
                      f"-> {t['exit_reason']:8s} PnL=${t['net_pnl']:7.2f} held={t['hours_held']:.1f}h")
            if result["n_trades"] > 10:
                print(f"    ... and {result['n_trades'] - 10} more trades")

        print(f"\n  SUMMARY ({mode_name})")
        print(f"  {'-'*50}")
        total_trades = sum(r["n_trades"] for r in all_results)
        total_wins = sum(r["wins"] for r in all_results)
        total_pnl = sum(r["pnl"] for r in all_results)
        overall_wr = (total_wins / total_trades * 100) if total_trades > 0 else 0

        print(f"  Total Trades: {total_trades}")
        print(f"  Total Wins: {total_wins}")
        print(f"  Overall Win Rate: {overall_wr:.1f}%")
        print(f"  Total PnL: ${total_pnl:.2f}")
        print()

        for r in all_results:
            status = "PASS" if r["wr"] >= 45 and r["pnl"] > 0 else "FAIL"
            print(f"  {r['symbol']:10s} WR={r['wr']:5.1f}%  PnL=${r['pnl']:8.2f}  [{status}]")

        print(f"\n  Target: >45% WR and positive expectancy per symbol")


if __name__ == "__main__":
    main()
