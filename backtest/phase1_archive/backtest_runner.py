#!/usr/bin/env python3
"""
VLTHR Backtest Runner — Control vs Treatment A/B
=================================================
Standalone bar-by-bar replay of the pipeline logic over historical enriched data.
No DB required — all portfolio state tracked in memory.

Modes:
  control           — Current pipeline (V7 veto in scanner + reprice + gate)
  treatment_a_phase1 — V7 veto moved to risk gate as sizing rule, equity fix
  treatment_b_phase2 — Signal engine as pure function, intent-vs-outcome logging

Usage:
  cd /path/to/DEVOPS/backtest
  python3 backtest_runner.py --mode control --start 2026-01-01 --end 2026-06-30
  python3 backtest_runner.py --mode treatment_a_phase1 --start 2026-01-01 --end 2026-06-30
  python3 backtest_runner.py --mode treatment_b_phase2 --start 2026-01-01 --end 2026-06-30
  python3 backtest_runner.py --all --start 2026-01-01 --end 2026-06-30
"""
import sys
import os
import argparse
import csv
import glob
import warnings
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field

import pandas as pd
import numpy as np

# ── PATH SETUP ──────────────────────────────────────────────────────────────
_BACKTEST_DIR = Path(__file__).resolve().parent
_DEVOPS = _BACKTEST_DIR.parent
_ENGINE = _DEVOPS / "pipeline" / "engine"
_DEPARTMENTS = _DEVOPS / "departments" / "strategy_research" / "BACKTESTER"

sys.path.insert(0, str(_ENGINE))
sys.path.insert(0, str(_DEPARTMENTS / "scripts" / "common"))
sys.path.insert(0, str(_DEPARTMENTS / "strategy" / "signals"))

# ── IMPORTS FROM PIPELINE ───────────────────────────────────────────────────
from portfolio_config import (
    SYMBOLS, DISABLED_SYMBOLS, DQS_THRESHOLDS, DQS_VETO_THRESHOLD,
    KELLY_V7_BANDS, MR_BOOST, PORTFOLIO, RISK_TIERS,
    get_risk_tier, get_sl_tp, get_sl_tp_pct, get_symbol_params,
    cap_position_size, get_kelly_v7, get_max_trade_hours,
    QTY_STEP, MMR, TAKER_FEE, MIN_NOTIONAL,
    REGIME_MIN_DQS, REGIME_SL_TP_MULTIPLIERS, MIN_RR_FLOOR,
    CAPITAL_PER_SYMBOL, SYMBOL_ALLOC_CAP,
)
from adaptive_scorer import score_signal_adaptive
from v2_filters import load_daily_bias, get_btc_momentum, apply_v2_filters, compute_regime

warnings.filterwarnings("ignore", category=UserWarning)

# ── CONSTANTS ───────────────────────────────────────────────────────────────
DATA_ROOT = _DEVOPS / "data" / "bybit"
INIT_BALANCE = 10000.0
LEVERAGE = 4
CSV_COLUMNS = [
    "symbol", "side", "entry_date", "entry_price", "sl_price", "tp_price",
    "dqs_score", "regime", "strategy", "approved", "reject_reason", "promoted",
    "exit_date", "exit_price", "exit_reason", "hours_held",
    "pnl_usd", "pnl_pct", "risk_usd", "kelly_fraction", "win", "notes",
]

# ── DATA LOADING ────────────────────────────────────────────────────────────

def _add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df["atr"] = tr.rolling(window=period, min_periods=period).mean()
    return df


def _add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    return df


def _add_adx(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
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
    dx = (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan) * 100
    df["adx"] = dx.rolling(window=period).mean()
    return df


def _classify_session(ts) -> str:
    hour = ts.hour
    if 7 <= hour < 12:
        return "london"
    elif 12 <= hour < 14:
        return "overlap"
    elif 14 <= hour < 21:
        return "ny_open"
    elif 20 <= hour < 24:
        return "ny_late"
    else:
        return "asian"


def load_symbol_enriched(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Load enriched 15m parquet + 4h context, compute indicators."""
    enriched_dir = DATA_ROOT / symbol / "enriched" / "15m"
    if not enriched_dir.exists():
        raise FileNotFoundError(f"No enriched data for {symbol}")

    files = sorted(enriched_dir.rglob("*.parquet"))
    dfs = []
    for f in files:
        try:
            df = pd.read_parquet(f)
            if not df.empty and "timestamp" in df.columns:
                dfs.append(df)
        except Exception:
            continue
    if not dfs:
        raise FileNotFoundError(f"No readable parquet for {symbol}")

    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    # Add indicators
    df = _add_atr(df, 14)
    df = _add_rsi(df, 14)
    df = _add_adx(df, 14)
    df["session"] = df["timestamp"].apply(_classify_session)

    # Load 4h context
    ctx_dir = DATA_ROOT / symbol / "enriched" / "4h"
    if not ctx_dir.exists():
        ctx_dir = DATA_ROOT / symbol / "4h"
    if ctx_dir.exists():
        ctx_files = sorted(ctx_dir.rglob("*.parquet"))
        if ctx_files:
            ctx_dfs = []
            for f in ctx_files:
                try:
                    ctx_dfs.append(pd.read_parquet(f))
                except Exception:
                    continue
            if ctx_dfs:
                ctx_df = pd.concat(ctx_dfs, ignore_index=True)
                ctx_df["timestamp"] = pd.to_datetime(ctx_df["timestamp"], utc=True)
                ctx_df = ctx_df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
                ctx_df = _add_adx(ctx_df, 14)
                ctx_df["ctx_log_ret_4h"] = np.log(ctx_df["close"] / ctx_df["close"].shift(1))
                # EMA trend
                ctx_df["ema_8"] = ctx_df["close"].ewm(span=8, adjust=False).mean()
                ctx_df["ema_21"] = ctx_df["close"].ewm(span=21, adjust=False).mean()
                ctx_df["bull_4h"] = (ctx_df["ema_8"] > ctx_df["ema_21"]).astype(int)

                df["timestamp"] = df["timestamp"].astype("datetime64[ns, UTC]")
                ctx_df["timestamp"] = ctx_df["timestamp"].astype("datetime64[ns, UTC]")
                df = pd.merge_asof(
                    df.sort_values("timestamp"),
                    ctx_df[["timestamp", "adx", "ctx_log_ret_4h", "bull_4h"]].rename(
                        columns={"adx": "ctx_adx"}
                    ).sort_values("timestamp"),
                    on="timestamp",
                    direction="backward",
                ).sort_values("timestamp").reset_index(drop=True)

    # Fill NaN context
    if "ctx_adx" not in df.columns:
        df["ctx_adx"] = 0.0
    if "ctx_log_ret_4h" not in df.columns:
        df["ctx_log_ret_4h"] = 0.0
    if "bull_4h" not in df.columns:
        df["bull_4h"] = 0

    df["ctx_adx"] = df["ctx_adx"].fillna(0)
    df["ctx_log_ret_4h"] = df["ctx_log_ret_4h"].fillna(0)
    df["bull_4h"] = df["bull_4h"].fillna(0)

    # Filter to date range
    start_ts = pd.to_datetime(start, utc=True)
    end_ts = pd.to_datetime(end, utc=True)
    df = df[(df["timestamp"] >= start_ts) & (df["timestamp"] <= end_ts)].reset_index(drop=True)

    return df


# ── PORTFOLIO STATE (in-memory) ─────────────────────────────────────────────

@dataclass
class Trade:
    symbol: str
    side: str
    entry_price: float
    sl_price: float
    tp_price: float
    qty: float
    entry_time: pd.Timestamp
    dqs: float
    regime: str
    strategy: str
    kelly_fraction: float
    risk_usd: float
    status: str = "OPEN"  # OPEN, CLOSED, EXPIRED
    exit_price: float = 0.0
    exit_reason: str = ""
    exit_time: Optional[pd.Timestamp] = None
    hours_held: float = 0.0
    pnl_usd: float = 0.0
    pnl_pct: float = 0.0


@dataclass
class PendingTrade:
    symbol: str
    side: str
    entry_price: float
    sl_price: float
    tp_price: float
    qty: float
    signal_bar_utc: pd.Timestamp
    dqs: float
    regime: str
    strategy: str
    kelly_fraction: float
    risk_usd: float


@dataclass
class SignalRecord:
    """Complete record for CSV output — tracks signal from birth to death."""
    symbol: str
    side: str
    entry_date: str
    entry_price: float
    sl_price: float
    tp_price: float
    dqs_score: float
    regime: str
    strategy: str
    approved: bool
    reject_reason: str
    promoted: bool
    exit_date: str
    exit_price: float
    exit_reason: str
    hours_held: float
    pnl_usd: float
    pnl_pct: float
    risk_usd: float
    kelly_fraction: float
    win: bool
    notes: str


class PortfolioState:
    """In-memory portfolio state tracker."""
    def __init__(self, initial_balance: float = INIT_BALANCE):
        self.balance = initial_balance
        self.equity = initial_balance
        self.margin_used = 0.0
        self.open_trades: List[Trade] = []
        self.pending_trades: List[PendingTrade] = []
        self.daily_trade_count: Dict[str, int] = {}  # date_str -> count
        self.session_trade_count: Dict[str, int] = {}  # "date|session" -> count

    def get_active_symbols(self) -> List[str]:
        return [t.symbol for t in self.open_trades]

    def get_active_sides(self) -> List[str]:
        return [t.side.upper() for t in self.open_trades]

    def get_total_open_risk(self) -> float:
        return sum(t.risk_usd for t in self.open_trades)

    def get_true_equity(self, fix_equity: bool = False) -> float:
        if fix_equity:
            return self.equity + self.margin_used
        return self.equity

    def can_open_correlation(self, symbol: str) -> Tuple[bool, str]:
        active = self.get_active_symbols()
        if len(active) >= PORTFOLIO["max_active_trades"]:
            return False, f"max {PORTFOLIO['max_active_trades']} open positions"
        if symbol in active:
            return False, f"{symbol} already has open position"
        return True, ""

    def can_open_count(self, session: str, date_str: str) -> Tuple[bool, str]:
        if len(self.open_trades) >= PORTFOLIO["max_active_trades"]:
            return False, "max active trades"
        daily = self.daily_trade_count.get(date_str, 0)
        if daily >= PORTFOLIO["max_daily_trades"]:
            return False, "daily limit"
        sess_key = f"{date_str}|{session}"
        if session in PORTFOLIO.get("session_limits", {}):
            if self.session_trade_count.get(sess_key, 0) >= PORTFOLIO["session_limits"][session]:
                return False, f"session limit ({session})"
        return True, ""

    def can_open_directional(self, side: str) -> Tuple[bool, str]:
        sides = self.get_active_sides()
        same_side = sides.count(side.upper())
        if same_side >= PORTFOLIO.get("max_same_side", 3):
            return False, f"directional: {same_side} {side} open (max {PORTFOLIO['max_same_side']})"
        return True, ""

    def has_risk_capacity(self, new_risk: float, fix_equity: bool = False) -> Tuple[bool, str]:
        eq = self.get_true_equity(fix_equity)
        if eq <= 0:
            return False, "equity <= 0"
        current = self.get_total_open_risk()
        max_allowed = eq * (PORTFOLIO["max_portfolio_risk_pct"] / 100)
        if current + new_risk > max_allowed:
            return False, f"risk budget full ({current + new_risk:.2f} > {max_allowed:.2f})"
        return True, ""

    def record_trade_open(self, session: str, date_str: str):
        self.daily_trade_count[date_str] = self.daily_trade_count.get(date_str, 0) + 1
        sess_key = f"{date_str}|{session}"
        self.session_trade_count[sess_key] = self.session_trade_count.get(sess_key, 0) + 1

    def close_trade(self, trade: Trade, exit_price: float, exit_reason: str,
                    exit_time: pd.Timestamp, fees: float = 0.0):
        is_long = trade.side.upper() == "LONG"
        gross = (exit_price - trade.entry_price) * trade.qty if is_long else (trade.entry_price - exit_price) * trade.qty
        net = gross - fees
        trade.exit_price = exit_price
        trade.exit_reason = exit_reason
        trade.exit_time = exit_time
        trade.pnl_usd = net
        trade.pnl_pct = (net / trade.risk_usd * 100) if trade.risk_usd > 0 else 0
        trade.hours_held = (exit_time - trade.entry_time).total_seconds() / 3600
        trade.status = "CLOSED"
        self.balance += net
        self.margin_used -= trade.entry_price * trade.qty / LEVERAGE
        if self.margin_used < 0:
            self.margin_used = 0
        if trade in self.open_trades:
            self.open_trades.remove(trade)


# ── BACKTEST ENGINE ─────────────────────────────────────────────────────────

class BacktestEngine:
    """Bar-by-bar pipeline simulation."""

    def __init__(self, mode: str, start: str, end: str,
                 walk_forward: bool = False, verbose: bool = True):
        self.mode = mode
        self.start = start
        self.end = end
        self.walk_forward = walk_forward
        self.verbose = verbose
        self.results: List[SignalRecord] = []
        self.portfolio = PortfolioState(INIT_BALANCE)

        # Load data for all symbols
        self.data: Dict[str, pd.DataFrame] = {}
        self._load_data()

        # Build unified timeline
        all_ts = set()
        for df in self.data.values():
            all_ts.update(df["timestamp"].tolist())
        self.timeline = sorted(all_ts)
        self.n_bars = len(self.timeline)

        # Walk-forward split
        if walk_forward:
            split_idx = int(self.n_bars * 0.7)
            self.is_end = self.timeline[split_idx]
        else:
            self.is_end = None

    def _load_data(self):
        for sym in SYMBOLS:
            try:
                df = load_symbol_enriched(sym, self.start, self.end)
                if len(df) > 0:
                    self.data[sym] = df
                    if self.verbose:
                        print(f"  Loaded {sym}: {len(df)} bars | {df['timestamp'].iloc[0]} → {df['timestamp'].iloc[-1]}")
            except Exception as e:
                print(f"  [WARN] Failed to load {sym}: {e}")

    def _get_bar(self, symbol: str, ts: pd.Timestamp) -> Optional[pd.Series]:
        df = self.data.get(symbol)
        if df is None:
            return None
        mask = df["timestamp"] == ts
        if not mask.any():
            return None
        return df[mask].iloc[0]

    def _get_enriched_slice(self, symbol: str, ts: pd.Timestamp) -> Optional[pd.DataFrame]:
        """Get enriched data up to and including ts (for DQS scoring)."""
        df = self.data.get(symbol)
        if df is None:
            return None
        mask = df["timestamp"] <= ts
        sliced = df[mask]
        if len(sliced) < 50:
            return None
        return sliced

    def _check_exits(self, ts: pd.Timestamp):
        """Check all open trades for SL/TP/time-exit using current bar."""
        for trade in list(self.portfolio.open_trades):
            bar = self._get_bar(trade.symbol, ts)
            if bar is None:
                continue

            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
            is_long = trade.side.upper() == "LONG"

            # Determine exit
            reason = None
            exit_price = 0.0

            # Time exit
            hours_held = (ts - trade.entry_time).total_seconds() / 3600
            max_hours = get_max_trade_hours(trade.symbol)
            is_winning = (close - trade.entry_price) * trade.qty > 0 if is_long else (trade.entry_price - close) * trade.qty > 0
            effective_max = max_hours + 2 if is_winning else max_hours

            if hours_held >= effective_max:
                reason = "TIME_EXIT"
                exit_price = close
            elif is_long:
                if trade.tp_price > 0 and high >= trade.tp_price:
                    reason = "TP_HIT"
                    exit_price = trade.tp_price
                elif trade.sl_price > 0 and low <= trade.sl_price:
                    reason = "SL_HIT"
                    exit_price = trade.sl_price
            else:
                if trade.tp_price > 0 and low <= trade.tp_price:
                    reason = "TP_HIT"
                    exit_price = trade.tp_price
                elif trade.sl_price > 0 and high >= trade.sl_price:
                    reason = "SL_HIT"
                    exit_price = trade.sl_price

            if reason:
                notional = trade.entry_price * trade.qty
                exit_notional = exit_price * trade.qty
                open_fee = notional * TAKER_FEE
                close_fee = exit_notional * TAKER_FEE
                fees = open_fee + close_fee
                self.portfolio.close_trade(trade, exit_price, reason, ts, fees)
                self._log_closed_trade(trade)

    def _reprice_pending(self, ts: pd.Timestamp):
        """Re-price PENDING trades — simulate Step 1c."""
        fix_equity = self.mode in ("treatment_a_phase1", "treatment_b_phase2")

        for pending in list(self.portfolio.pending_trades):
            enriched = self._get_enriched_slice(pending.symbol, ts)
            if enriched is None or len(enriched) == 0:
                # Expire — no data
                self._expire_pending(pending, "No data on reprice", ts)
                continue

            last_bar = enriched.iloc[-1]
            close_price = float(last_bar["close"])
            atr = float(last_bar.get("atr", 0) or 0)

            # Recompute DQS
            session = last_bar.get("session", "")
            dqs, breakdown = score_signal_adaptive(pending.symbol, enriched, None, session)

            # DQS drop check
            if dqs < DQS_THRESHOLDS["min_to_track"]:
                self._expire_pending(pending, f"DQS dropped to {dqs:.0f}", ts)
                continue

            # V7 veto in reprice — CONTROL ONLY
            if self.mode == "control" and dqs >= DQS_VETO_THRESHOLD:
                self._expire_pending(pending, f"V7 veto: DQS {dqs:.0f} >= {DQS_VETO_THRESHOLD}", ts)
                continue

            # Recompute SL/TP — use max(reprice_dqs, 50) to avoid get_sl_tp returning None
            # when DQS dropped to 40-49 (still trackable but below executable threshold)
            sl_tp_dqs = max(int(dqs), 50)
            sym_params = get_symbol_params(pending.symbol)
            regime_val = breakdown.get("regime", "MIXED")
            sl_mode = sym_params.get("sl_mode", "atr")
            if sl_mode == "pct":
                sl_price, tp_price, rr = get_sl_tp_pct(
                    close_price, sym_params["sl_pct"], sym_params["tp_pct"],
                    side=pending.side, regime=regime_val)
            else:
                if atr <= 0:
                    self._expire_pending(pending, "ATR is zero on reprice", ts)
                    continue
                sl_price, tp_price, rr = get_sl_tp(close_price, atr, sl_tp_dqs,
                                                    side=pending.side, regime=regime_val)

            if sl_price is None:
                self._expire_pending(pending, "SL computation failed on reprice", ts)
                continue

            # Recompute Kelly and sizing
            tier = get_risk_tier(int(dqs))
            strategy = pending.strategy or sym_params.get("strategy", "trend_following")
            kelly_mult = get_kelly_v7(dqs, strategy)
            risk_pct = tier["risk_pct"] * kelly_mult

            # Treatment A/B: Kelly=0 means zero size — block but log
            if kelly_mult == 0.0 and self.mode in ("treatment_a_phase1", "treatment_b_phase2"):
                self._expire_pending(pending, "V7_KELLY_ZERO: Kelly=0 (sizing veto)", ts)
                continue

            sl_dist = abs(close_price - sl_price)
            account_balance = self.portfolio.balance
            dollar_risk = account_balance * (risk_pct / 100)
            qty = (dollar_risk / sl_dist) if sl_dist > 0 else 0
            position_size = close_price * qty
            position_size = cap_position_size(position_size, account_balance)
            qty = position_size / close_price if close_price > 0 else 0

            step = QTY_STEP.get(pending.symbol, 0.001)
            qty = (int(qty / step) * step) if step > 0 else qty
            notional = close_price * qty

            if notional < MIN_NOTIONAL:
                self._expire_pending(pending, f"Below min notional (${notional:.2f})", ts)
                continue

            dollar_risk = sl_dist * qty
            margin_req = (notional / LEVERAGE) + (notional * TAKER_FEE) if LEVERAGE > 0 else 0

            # Gate checks at promotion
            date_str = ts.strftime("%Y-%m-%d")
            ok_corr, reason_corr = self.portfolio.can_open_correlation(pending.symbol)
            if not ok_corr:
                self._expire_pending(pending, f"CorrelationGuard: {reason_corr}", ts)
                continue

            ok_count, reason_count = self.portfolio.can_open_count(session, date_str)
            if not ok_count:
                self._expire_pending(pending, f"TradeCountGuard: {reason_count}", ts)
                continue

            ok_dir, reason_dir = self.portfolio.can_open_directional(pending.side)
            if not ok_dir:
                self._expire_pending(pending, f"DirectionalGuard: {reason_dir}", ts)
                continue

            ok_risk, reason_risk = self.portfolio.has_risk_capacity(dollar_risk, fix_equity)
            if not ok_risk:
                self._expire_pending(pending, f"RiskBudgetLedger: {reason_risk}", ts)
                continue

            # Promote PENDING → OPEN
            trade = Trade(
                symbol=pending.symbol,
                side=pending.side,
                entry_price=close_price,
                sl_price=sl_price,
                tp_price=tp_price,
                qty=qty,
                entry_time=ts,
                dqs=dqs,
                regime=regime_val,
                strategy=strategy,
                kelly_fraction=kelly_mult,
                risk_usd=dollar_risk,
            )
            self.portfolio.open_trades.append(trade)
            self.portfolio.margin_used += margin_req
            self.portfolio.record_trade_open(session, date_str)
            self.portfolio.pending_trades.remove(pending)

            if self.verbose:
                print(f"  [Reprice] {pending.symbol} PENDING→OPEN — DQS={dqs:.0f}, entry={close_price:.2f}")

    def _expire_pending(self, pending: PendingTrade, reason: str, ts: pd.Timestamp):
        """Expire a PENDING trade and log to results."""
        self.portfolio.pending_trades.remove(pending)
        self.results.append(SignalRecord(
            symbol=pending.symbol, side=pending.side,
            entry_date=pending.signal_bar_utc.strftime("%Y-%m-%d %H:%M:%S"),
            entry_price=pending.entry_price,
            sl_price=pending.sl_price, tp_price=pending.tp_price,
            dqs_score=round(pending.dqs, 2),
            regime=pending.regime, strategy=pending.strategy,
            approved=True, reject_reason=reason,
            promoted=False,
            exit_date=ts.strftime("%Y-%m-%d %H:%M:%S"),
            exit_price=0, exit_reason="EXPIRED",
            hours_held=0, pnl_usd=0, pnl_pct=0,
            risk_usd=pending.risk_usd,
            kelly_fraction=pending.kelly_fraction,
            win=False, notes=self.mode,
        ))

    def _scan_symbol(self, symbol: str, ts: pd.Timestamp) -> Optional[dict]:
        """Scan a single symbol — returns signal dict or None."""
        enriched = self._get_enriched_slice(symbol, ts)
        if enriched is None or len(enriched) == 0:
            return None

        last_bar = enriched.iloc[-1]
        session = last_bar.get("session", "")
        dqs, breakdown = score_signal_adaptive(symbol, enriched, None, session)

        if dqs < DQS_THRESHOLDS["min_to_track"]:
            return None

        close_price = float(last_bar["close"])
        atr = float(last_bar.get("atr", 0) or 0)
        side = breakdown.get("direction", "LONG")
        strategy = breakdown.get("strategy", "trend_following")
        regime_val = breakdown.get("regime", "MIXED")

        # V2 filters
        daily_bias = None
        try:
            daily_bias = load_daily_bias(symbol)
        except Exception:
            pass
        btc_mom = 0.0
        try:
            btc_mom = get_btc_momentum()
        except Exception:
            pass

        v2_passed, strategy, tp_mult_scale, v2_reason = apply_v2_filters(
            symbol, side, strategy, enriched, daily_bias, btc_mom)
        if not v2_passed:
            return None

        # V7 veto in scanner — CONTROL ONLY
        # In control mode, DQS >= 85 signals are silently dropped at scanner.
        # Log them as rejected so the CSV captures the full funnel.
        if self.mode == "control" and dqs >= DQS_VETO_THRESHOLD:
            return {"_vetoed": True, "symbol": symbol, "dqs": dqs, "side": side,
                    "strategy": strategy, "regime": regime_val,
                    "close": close_price, "atr": atr, "session": session,
                    "signal_bar_utc": ts}

        # SL/TP computation
        sym_params = get_symbol_params(symbol)
        sl_mode = sym_params.get("sl_mode", "atr")
        if sl_mode == "pct":
            sl_price, tp_price, rr = get_sl_tp_pct(
                close_price, sym_params["sl_pct"], sym_params["tp_pct"],
                side=side, regime=regime_val)
        else:
            if atr <= 0:
                return None
            sl_price, tp_price, rr = get_sl_tp(close_price, atr, int(dqs),
                                                side=side, regime=regime_val)
        if sl_price is None:
            return None

        # TP scaling by ATR percentile
        if tp_mult_scale != 1.0 and sl_mode != "pct":
            tp_dist = abs(tp_price - close_price)
            new_tp_dist = tp_dist * tp_mult_scale
            if side.upper() == "SHORT":
                tp_price = round(close_price - new_tp_dist, 6)
            else:
                tp_price = round(close_price + new_tp_dist, 6)

        # Kelly and sizing
        tier = get_risk_tier(int(dqs))
        kelly_mult = get_kelly_v7(dqs, strategy)
        risk_pct = tier["risk_pct"] * kelly_mult
        sl_dist = abs(close_price - sl_price)
        account_balance = self.portfolio.balance
        dollar_risk = account_balance * (risk_pct / 100)
        qty = (dollar_risk / sl_dist) if sl_dist > 0 else 0
        position_size = close_price * qty
        position_size = cap_position_size(position_size, account_balance)
        qty = position_size / close_price if close_price > 0 else 0

        step = QTY_STEP.get(symbol, 0.001)
        qty = (int(qty / step) * step) if step > 0 else qty
        notional = close_price * qty

        # When Kelly=0 (DQS >= 85 in treatment mode), notional will be 0.
        # Don't drop the signal — let it through to the risk gate which will
        # apply the Kelly=0 sizing veto and log it properly.
        if kelly_mult > 0 and notional < MIN_NOTIONAL:
            return None

        dollar_risk = sl_dist * qty

        return {
            "symbol": symbol,
            "dqs": dqs,
            "side": side,
            "strategy": strategy,
            "regime": regime_val,
            "close": close_price,
            "atr": atr,
            "sl": sl_price,
            "tp": tp_price,
            "rr": rr,
            "kelly_mult": kelly_mult,
            "risk_pct": risk_pct,
            "dollar_risk": dollar_risk,
            "qty": qty,
            "notional": notional,
            "session": session,
            "signal_bar_utc": ts,
            "breakdown": breakdown,
        }

    def _apply_risk_gate(self, sig: dict, ts: pd.Timestamp) -> Tuple[bool, str]:
        """Apply portfolio gates. Returns (approved, reject_reason)."""
        symbol = sig["symbol"]
        dqs = sig["dqs"]
        session = sig.get("session", "")
        date_str = ts.strftime("%Y-%m-%d")
        fix_equity = self.mode in ("treatment_a_phase1", "treatment_b_phase2")

        # V7 veto in risk gate — CONTROL ONLY
        if self.mode == "control" and dqs >= DQS_VETO_THRESHOLD:
            return False, f"V7_DQS_VETO: DQS {dqs:.0f} >= {DQS_VETO_THRESHOLD}"

        # Disabled symbols
        if symbol in DISABLED_SYMBOLS:
            return False, "Symbol disabled in V7"

        # Regime gate
        sig_regime = (sig.get("regime") or "MIXED").upper()
        min_dqs_for_regime = REGIME_MIN_DQS.get(sig_regime, 50)
        if dqs < min_dqs_for_regime:
            return False, f"RegimeGate: DQS {dqs:.0f} < {min_dqs_for_regime} for {sig_regime}"

        # Correlation guard
        ok, reason = self.portfolio.can_open_correlation(symbol)
        if not ok:
            return False, f"CorrelationGuard: {reason}"

        # Trade count guard
        ok, reason = self.portfolio.can_open_count(session, date_str)
        if not ok:
            return False, f"TradeCountGuard: {reason}"

        # Directional guard
        ok, reason = self.portfolio.can_open_directional(sig["side"])
        if not ok:
            return False, f"DirectionalGuard: {reason}"

        # Risk budget
        ok, reason = self.portfolio.has_risk_capacity(sig["dollar_risk"], fix_equity)
        if not ok:
            return False, f"RiskBudgetLedger: {reason}"

        # Treatment A/B: Kelly=0 is a sizing veto
        if self.mode in ("treatment_a_phase1", "treatment_b_phase2"):
            if sig["kelly_mult"] == 0.0:
                return False, "V7_KELLY_ZERO: Kelly=0 (sizing veto)"

        return True, ""

    def _create_pending(self, sig: dict, ts: pd.Timestamp):
        """Create a PENDING trade for an approved signal."""
        pending = PendingTrade(
            symbol=sig["symbol"],
            side=sig["side"],
            entry_price=sig["close"],
            sl_price=sig["sl"],
            tp_price=sig["tp"],
            qty=sig["qty"],
            signal_bar_utc=ts,
            dqs=sig["dqs"],
            regime=sig["regime"],
            strategy=sig["strategy"],
            kelly_fraction=sig["kelly_mult"],
            risk_usd=sig["dollar_risk"],
        )
        self.portfolio.pending_trades.append(pending)

    def _log_rejected_signal(self, sig: dict, reason: str, ts: pd.Timestamp):
        """Log a signal that was rejected by the risk gate."""
        self.results.append(SignalRecord(
            symbol=sig["symbol"], side=sig["side"],
            entry_date=ts.strftime("%Y-%m-%d %H:%M:%S"),
            entry_price=sig["close"],
            sl_price=sig["sl"], tp_price=sig["tp"],
            dqs_score=round(sig["dqs"], 2),
            regime=sig["regime"], strategy=sig["strategy"],
            approved=False, reject_reason=reason,
            promoted=False,
            exit_date="", exit_price=0, exit_reason="REJECTED",
            hours_held=0, pnl_usd=0, pnl_pct=0,
            risk_usd=sig["dollar_risk"],
            kelly_fraction=sig["kelly_mult"],
            win=False, notes=self.mode,
        ))

    def _log_closed_trade(self, trade: Trade):
        """Log a closed trade to results."""
        self.results.append(SignalRecord(
            symbol=trade.symbol, side=trade.side,
            entry_date=trade.entry_time.strftime("%Y-%m-%d %H:%M:%S"),
            entry_price=trade.entry_price,
            sl_price=trade.sl_price, tp_price=trade.tp_price,
            dqs_score=round(trade.dqs, 2),
            regime=trade.regime, strategy=trade.strategy,
            approved=True, reject_reason="",
            promoted=True,
            exit_date=trade.exit_time.strftime("%Y-%m-%d %H:%M:%S") if trade.exit_time else "",
            exit_price=trade.exit_price,
            exit_reason=trade.exit_reason,
            hours_held=round(trade.hours_held, 2),
            pnl_usd=round(trade.pnl_usd, 2),
            pnl_pct=round(trade.pnl_pct, 2),
            risk_usd=round(trade.risk_usd, 2),
            kelly_fraction=trade.kelly_fraction,
            win=trade.pnl_usd > 0,
            notes=self.mode,
        ))

    def run(self) -> List[SignalRecord]:
        """Execute the full bar-by-bar backtest."""
        print(f"\n{'='*70}")
        print(f"  VLTHR Backtest — Mode: {self.mode}")
        print(f"  Period: {self.start} → {self.end}")
        print(f"  Bars: {self.n_bars} | Symbols: {list(self.data.keys())}")
        if self.walk_forward:
            print(f"  Walk-forward: IS ends at {self.is_end}")
        print(f"{'='*70}")

        # Track closed trades for logging
        closed_trades_log: List[Trade] = []

        for i, ts in enumerate(self.timeline):
            # 1. Check exits for open trades (logs closed trades internally)
            self._check_exits(ts)

            # 2. Reprice PENDING trades (Step 1c)
            if self.portfolio.pending_trades:
                self._reprice_pending(ts)

            # 3. Scan all symbols (Step 1)
            new_signals = []
            for sym in SYMBOLS:
                if sym in DISABLED_SYMBOLS:
                    continue
                sig = self._scan_symbol(sym, ts)
                if sig is not None:
                    if sig.get("_vetoed"):
                        # Control mode: V7 veto at scanner — log as rejected
                        self.results.append(SignalRecord(
                            symbol=sig["symbol"], side=sig["side"],
                            entry_date=ts.strftime("%Y-%m-%d %H:%M:%S"),
                            entry_price=sig["close"],
                            sl_price=0, tp_price=0,
                            dqs_score=round(sig["dqs"], 2),
                            regime=sig["regime"], strategy=sig["strategy"],
                            approved=False,
                            reject_reason=f"V7_SCANNER_VETO: DQS {sig['dqs']:.0f} >= {DQS_VETO_THRESHOLD}",
                            promoted=False,
                            exit_date="", exit_price=0, exit_reason="REJECTED",
                            hours_held=0, pnl_usd=0, pnl_pct=0,
                            risk_usd=0, kelly_fraction=0,
                            win=False, notes=self.mode,
                        ))
                        continue
                    new_signals.append(sig)

            # 4. Apply risk gates (Step 4)
            for sig in new_signals:
                approved, reason = self._apply_risk_gate(sig, ts)
                if approved:
                    # 5. Create PENDING trade
                    self._create_pending(sig, ts)
                else:
                    self._log_rejected_signal(sig, reason, ts)

            # Progress
            if self.verbose and i > 0 and i % 288 == 0:  # ~every 3 days
                print(f"  Day {i//96}: Balance=${self.portfolio.balance:.2f}, "
                      f"Open={len(self.portfolio.open_trades)}, "
                      f"Pending={len(self.portfolio.pending_trades)}, "
                      f"Results={len(self.results)}")

        # Force-close any remaining open trades at the end
        last_ts = self.timeline[-1] if self.timeline else pd.Timestamp.now(tz="UTC")
        for trade in list(self.portfolio.open_trades):
            bar = self._get_bar(trade.symbol, last_ts)
            close_price = float(bar["close"]) if bar is not None else trade.entry_price
            notional = trade.entry_price * trade.qty
            fees = notional * TAKER_FEE * 2
            self.portfolio.close_trade(trade, close_price, "BACKTEST_END", last_ts, fees)
            self._log_closed_trade(trade)

        # Expire remaining pending trades
        for pending in list(self.portfolio.pending_trades):
            self._expire_pending(pending, "Backtest ended", last_ts)

        print(f"\n  Complete: {len(self.results)} signal records")
        return self.results


# ── METRICS COMPUTATION ─────────────────────────────────────────────────────

def compute_metrics(records: List[SignalRecord]) -> dict:
    """Compute validation metrics from signal records."""
    closed = [r for r in records if r.exit_reason in ("TP_HIT", "SL_HIT", "TIME_EXIT", "BACKTEST_END")]
    expired = [r for r in records if r.exit_reason == "EXPIRED"]
    rejected = [r for r in records if not r.approved]
    approved = [r for r in records if r.approved]
    promoted = [r for r in records if r.promoted]

    wins = [r for r in closed if r.pnl_usd > 0]
    losses = [r for r in closed if r.pnl_usd <= 0]

    gross_profit = sum(r.pnl_usd for r in wins)
    gross_loss = abs(sum(r.pnl_usd for r in losses))

    win_rate = (len(wins) / len(closed) * 100) if closed else 0
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float('inf') if gross_profit > 0 else 0
    expectancy = (sum(r.pnl_usd for r in closed) / len(closed)) if closed else 0
    tp_hits = sum(1 for r in closed if r.exit_reason == "TP_HIT")
    sl_hits = sum(1 for r in closed if r.exit_reason == "SL_HIT")
    time_exits = sum(1 for r in closed if r.exit_reason == "TIME_EXIT")

    expired_rate = (len(expired) / len(records) * 100) if records else 0
    tp_hit_rate = (tp_hits / len(closed) * 100) if closed else 0
    sl_hit_rate = (sl_hits / len(closed) * 100) if closed else 0

    approval_rate = (len(approved) / len(records) * 100) if records else 0
    promote_rate = (len(promoted) / len(approved) * 100) if approved else 0

    avg_hold = (sum(r.hours_held for r in closed) / len(closed)) if closed else 0

    # Max drawdown (simplified from PnL stream)
    equity = INIT_BALANCE
    peak = equity
    max_dd = 0
    for r in closed:
        equity += r.pnl_usd
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    return {
        "total_signals": len(records),
        "approved": len(approved),
        "rejected": len(rejected),
        "promoted": len(promoted),
        "closed_trades": len(closed),
        "expired": len(expired),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2),
        "expectancy_usd": round(expectancy, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "expired_rate_pct": round(expired_rate, 1),
        "tp_hit_rate_pct": round(tp_hit_rate, 1),
        "sl_hit_rate_pct": round(sl_hit_rate, 1),
        "approval_rate_pct": round(approval_rate, 1),
        "promote_rate_pct": round(promote_rate, 1),
        "avg_hold_hours": round(avg_hold, 1),
        "tp_hits": tp_hits,
        "sl_hits": sl_hits,
        "time_exits": time_exits,
        "final_balance": round(equity, 2),
    }


def check_gates(metrics: dict) -> dict:
    """Check validation gates."""
    return {
        "win_rate_ge_30": metrics["win_rate_pct"] >= 30,
        "profit_factor_ge_1.5": metrics["profit_factor"] >= 1.5,
        "expectancy_positive": metrics["expectancy_usd"] > 0,
        "max_dd_lt_15": metrics["max_drawdown_pct"] < 15,
        "expired_rate_lt_30": metrics["expired_rate_pct"] < 30,
        "tp_hit_rate_ge_40": metrics["tp_hit_rate_pct"] >= 40,
    }


def print_report(metrics: dict, gates: dict, mode: str):
    """Print a formatted metrics report."""
    print(f"\n{'='*70}")
    print(f"  Backtest Report — {mode}")
    print(f"{'='*70}")
    print(f"  SIGNAL FUNNEL:")
    print(f"    Total signals:    {metrics['total_signals']}")
    print(f"    Approved:         {metrics['approved']} ({metrics['approval_rate_pct']:.1f}%)")
    print(f"    Rejected:         {metrics['rejected']}")
    print(f"    Promoted:         {metrics['promoted']} ({metrics['promote_rate_pct']:.1f}% of approved)")
    print(f"    Expired:          {metrics['expired']} ({metrics['expired_rate_pct']:.1f}%)")
    print(f"    Closed:           {metrics['closed_trades']}")
    print(f"")
    print(f"  TRADE RESULTS:")
    print(f"    Wins:             {metrics['wins']}")
    print(f"    Losses:           {metrics['losses']}")
    print(f"    Win rate:         {metrics['win_rate_pct']:.1f}%")
    print(f"    Profit factor:    {metrics['profit_factor']:.2f}")
    print(f"    Expectancy:       ${metrics['expectancy_usd']:.2f}")
    print(f"    Max drawdown:     {metrics['max_drawdown_pct']:.2f}%")
    print(f"    TP hits:          {metrics['tp_hits']} ({metrics['tp_hit_rate_pct']:.1f}%)")
    print(f"    SL hits:          {metrics['sl_hits']} ({metrics['sl_hit_rate_pct']:.1f}%)")
    print(f"    Time exits:       {metrics['time_exits']}")
    print(f"    Avg hold:         {metrics['avg_hold_hours']:.1f}h")
    print(f"    Final balance:    ${metrics['final_balance']:.2f}")
    print(f"")
    print(f"  VALIDATION GATES:")
    for gate, passed in gates.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"    {gate}: {status}")
    all_pass = all(gates.values())
    print(f"    ALL GATES: {'✅ PASS' if all_pass else '❌ FAIL'}")
    print(f"{'='*70}")


# ── CSV OUTPUT ──────────────────────────────────────────────────────────────

def write_csv(records: List[SignalRecord], filepath: str, append: bool = False):
    """Write signal records to CSV. Append if file exists and append=True."""
    file_exists = os.path.exists(filepath) and os.path.getsize(filepath) > 0
    mode = "a" if (append and file_exists) else "w"

    with open(filepath, mode, newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if mode == "w":
            writer.writeheader()
        for r in records:
            writer.writerow({
                "symbol": r.symbol,
                "side": r.side,
                "entry_date": r.entry_date,
                "entry_price": r.entry_price,
                "sl_price": r.sl_price,
                "tp_price": r.tp_price,
                "dqs_score": r.dqs_score,
                "regime": r.regime,
                "strategy": r.strategy,
                "approved": r.approved,
                "reject_reason": r.reject_reason,
                "promoted": r.promoted,
                "exit_date": r.exit_date,
                "exit_price": r.exit_price,
                "exit_reason": r.exit_reason,
                "hours_held": r.hours_held,
                "pnl_usd": r.pnl_usd,
                "pnl_pct": r.pnl_pct,
                "risk_usd": r.risk_usd,
                "kelly_fraction": r.kelly_fraction,
                "win": r.win,
                "notes": r.notes,
            })


# ── MAIN ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="VLTHR Backtest Runner")
    parser.add_argument("--mode", choices=["control", "treatment_a_phase1", "treatment_b_phase2"],
                        default="control", help="Backtest mode")
    parser.add_argument("--all", action="store_true", help="Run all three modes sequentially")
    parser.add_argument("--start", type=str, default="2026-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2026-06-30", help="End date (YYYY-MM-DD)")
    parser.add_argument("--output", type=str, default=None, help="Output CSV path")
    parser.add_argument("--walk-forward", action="store_true", help="Split 70% IS / 30% OOS")
    parser.add_argument("--quiet", action="store_true", help="Reduce verbose output")
    args = parser.parse_args()

    output_path = args.output or str(_BACKTEST_DIR / "backtest_results.csv")
    verbose = not args.quiet

    modes = ["control", "treatment_a_phase1", "treatment_b_phase2"] if args.all else [args.mode]

    all_metrics = {}

    for i, mode in enumerate(modes):
        print(f"\n{'#'*70}")
        print(f"  Running mode: {mode}")
        print(f"{'#'*70}")

        engine = BacktestEngine(mode, args.start, args.end,
                                walk_forward=args.walk_forward, verbose=verbose)
        records = engine.run()

        # Write CSV
        append = i > 0  # append for subsequent modes
        write_csv(records, output_path, append=append)
        print(f"  CSV written to: {output_path} ({len(records)} rows, {'appended' if append else 'new'})")

        # Metrics
        metrics = compute_metrics(records)
        gates = check_gates(metrics)
        all_metrics[mode] = (metrics, gates)
        print_report(metrics, gates, mode)

        # Walk-forward OOS
        if args.walk_forward and engine.is_end:
            oos_records = [r for r in records if r.entry_date >= engine.is_end.strftime("%Y-%m-%d")]
            if oos_records:
                oos_metrics = compute_metrics(oos_records)
                oos_gates = check_gates(oos_metrics)
                print(f"\n  --- OOS Results (after {engine.is_end.date()}) ---")
                print_report(oos_metrics, oos_gates, f"{mode}_OOS")

    # Summary comparison
    if len(modes) > 1:
        print(f"\n{'='*70}")
        print(f"  COMPARISON SUMMARY")
        print(f"{'='*70}")
        print(f"  {'Metric':<25} {'Control':>12} {'Treatment A':>12} {'Treatment B':>12}")
        print(f"  {'-'*25} {'-'*12} {'-'*12} {'-'*12}")
        key_metrics = ["total_signals", "approved", "closed_trades", "win_rate_pct",
                       "profit_factor", "expectancy_usd", "max_drawdown_pct",
                       "expired_rate_pct", "tp_hit_rate_pct", "final_balance"]
        for key in key_metrics:
            vals = []
            for m in modes:
                metrics = all_metrics.get(m, (None, None))[0]
                if metrics:
                    vals.append(f"{metrics[key]:>12}" if isinstance(metrics[key], (int, float)) else f"{str(metrics[key]):>12}")
                else:
                    vals.append(f"{'N/A':>12}")
            print(f"  {key:<25} {vals[0]} {vals[1] if len(vals) > 1 else '':>12} {vals[2] if len(vals) > 2 else '':>12}")

        print(f"\n  GATES:")
        for gate_name in ["win_rate_ge_30", "profit_factor_ge_1.5", "expectancy_positive",
                          "max_dd_lt_15", "expired_rate_lt_30", "tp_hit_rate_ge_40"]:
            vals = []
            for m in modes:
                gates = all_metrics.get(m, (None, None))[1]
                if gates:
                    vals.append("✅" if gates.get(gate_name) else "❌")
                else:
                    vals.append("N/A")
            print(f"    {gate_name:<25} {vals[0]:>6} {vals[1] if len(vals) > 1 else '':>6} {vals[2] if len(vals) > 2 else '':>6}")
        print(f"{'='*70}")


if __name__ == "__main__":
    main()
