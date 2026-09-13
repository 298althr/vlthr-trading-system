"""
VLTHR Portfolio Orchestrator
==============================
Single entry point for one 5-minute pipeline iteration.

Flow:
  1. Scan all symbols -> raw signals (scan_signals.scan)
  2. Score each with DQS (confidence_engine.score_signal)
  3. Track state + trajectory (signal_state_manager)
  4. Rank and filter (signal_ranker)
  5. Apply portfolio gates (portfolio_gates)
  6. Upsert to high_confidence_signals (sync step)
  7. Take portfolio snapshot
  8. Alert if EXCELLENT signal passes all gates

Usage:
    from portfolio_orchestrator import run_iteration
    run_iteration(conn, now=datetime.now(timezone.utc))
"""
import sys
import warnings
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# Ensure imports resolve
_engine = Path(__file__).resolve().parent
try:
    _repo = _engine.parents[2]
except IndexError:
    _repo = _engine.parent
sys.path.insert(0, str(_engine))
sys.path.insert(0, str(_repo / "departments" / "strategy_research" / "BACKTESTER" / "scripts" / "common"))
sys.path.insert(0, str(_repo / "departments" / "strategy_research" / "BACKTESTER" / "strategy" / "signals"))

import pandas as pd

from portfolio_config import (SYMBOLS, ACTIVE_SESSIONS, DQS_THRESHOLDS, PORTFOLIO, TELEGRAM_BOT_NAME,
    get_risk_tier, get_sl_tp, get_sl_tp_pct, get_symbol_params, cap_position_size, get_max_trade_hours,
    QTY_STEP, MMR, TAKER_FEE, MIN_NOTIONAL, get_liquidation_price,
    BRAIN_ENABLED, SYMBOL_DISABLE_RULES,
    DQS_VETO_THRESHOLD, DISABLED_SYMBOLS, get_kelly_v7, MR_BOOST,
    tune_tp_from_history, tune_regime_from_history, REGIME_MIN_DQS,
    REGIME_STRATEGY_MIN_DQS, SL_TIME_DECAY_RATE,
    CORRELATION_CLUSTERS, CROWDING_DQS_RANGE, CROWDING_KELLY_REDUCTION, DATA_QUALITY_MIN_SCORE,
    EXECUTION_MODE, MAX_LEVERAGE)
from confidence_engine import load_enriched, load_orderbook_features
from adaptive_scorer import score_signal_adaptive
from signal_state_manager import SignalStateManager
from signal_ranker import SignalRanker
from portfolio_gates import RiskBudgetLedger, TradeCountGuard, CorrelationGuard
from safety_layer import SafetyLayer
from pipeline_brain import PipelineBrain, RIVER_AVAILABLE
from pipeline_trace import PipelineTrace
from calibration_lookup import lookup_calibrated_prob, get_calibration_gate
from v2_filters import load_daily_bias, get_btc_momentum, apply_v2_filters
from shadow_engines import ShadowDecisionEngine
from regime_router import evaluate_regime_gate
from shadow_runner import recheck_approved_signals, reconcile_bybit_vs_paper

# Phase 2: Bybit Demo API executor (optional, controlled by EXECUTION_MODE)
from bybit_executor import create_executor_from_env
_bybit_executor = None
if EXECUTION_MODE != "internal":
    _bybit_executor = create_executor_from_env()
    if _bybit_executor:
        print(f"  [Executor] Bybit executor active (mode={EXECUTION_MODE})")
        # vlthr-scale-audit.html Priority 2: reconcile account leverage to MAX_LEVERAGE (4x).
        # Account was observed set to 10x while all sizing math assumes 4x — margin and
        # liquidation-distance calculations are wrong until this matches. Runs once at
        # process start; safe to repeat (Bybit returns retCode 110043 if already set).
        for _lev_symbol in SYMBOLS:
            try:
                _lev_result = _bybit_executor.set_leverage(_lev_symbol, MAX_LEVERAGE)
                if _lev_result.get("retCode") == 0:
                    print(f"  [Executor] Leverage reconciled: {_lev_symbol} -> {MAX_LEVERAGE}x")
                else:
                    print(f"  [Executor] Leverage reconcile FAILED for {_lev_symbol}: {_lev_result.get('retMsg')}")
            except Exception as _lev_e:
                print(f"  [Executor] Leverage reconcile exception for {_lev_symbol}: {_lev_e}")
    else:
        print(f"  [Executor] EXECUTION_MODE={EXECUTION_MODE} but no executor created. Using internal simulation.")

import uuid
import json

# Telegram alert wrapper (from existing module)
sys.path.insert(0, str(_repo / "data" / "bybit" / "workers"))
try:
    from telegram_alerts import send_telegram, alert_trade_close
except ImportError:
    send_telegram = None
    alert_trade_close = None


# ── TREATMENT B: Crowding Detection (validated by backtest) ──────────────────
def _detect_crowding(ranked_signals):
    """Detect crowded symbols from ranked signals at the same scan.

    Returns set of symbol names that are crowded (Kelly should be reduced 50%).
    """
    crowded = set()
    trackable = [s for s in ranked_signals if s.get("adjusted_score", 0) >= 50]
    if len(trackable) < 2:
        return crowded

    for cluster in CORRELATION_CLUSTERS:
        cluster_sigs = [s for s in trackable if s["symbol"] in cluster["symbols"]]
        if len(cluster_sigs) < cluster["min_fire"]:
            continue

        for side in ["LONG", "SHORT"]:
            side_sigs = [s for s in cluster_sigs
                         if (s.get("side") or s.get("direction", "LONG")).upper() == side]
            if len(side_sigs) >= cluster["min_fire"]:
                dqs_vals = [s.get("adjusted_score", 0) for s in side_sigs]
                dqs_range = max(dqs_vals) - min(dqs_vals) if dqs_vals else 0
                if dqs_range <= CROWDING_DQS_RANGE:
                    for s in side_sigs:
                        crowded.add(s["symbol"])

    return crowded


# ── TREATMENT B: Data Quality Scoring (validated by backtest) ────────────────
def _compute_data_quality(symbol, enriched_df):
    """Compute 0-100 data quality score for a symbol's enriched data.

    Score = freshness*0.40 + completeness*0.40 + outliers*0.20
    Returns dict with score, gated flag, and sub-metrics.
    """
    if enriched_df is None or len(enriched_df) == 0:
        return {"score": 0.0, "gated": True, "freshness": 0, "completeness": 0, "outlier": 0}

    import numpy as np

    # Freshness: how recent is the last bar
    if "timestamp" in enriched_df.columns:
        last_ts = pd.to_datetime(enriched_df["timestamp"].iloc[-1], utc=True)
        now = datetime.now(timezone.utc)
        staleness_min = (now - last_ts).total_seconds() / 60
        freshness = max(0, 100 - (staleness_min / 45 * 100))
    else:
        freshness = 100.0

    # Completeness: gap count in last 96 bars (>20min gaps)
    if "timestamp" in enriched_df.columns and len(enriched_df) >= 2:
        ts = pd.to_datetime(enriched_df["timestamp"], utc=True)
        gaps = ts.diff().dt.total_seconds() / 60
        gap_count = (gaps > 20).sum()
        completeness = max(0, 100 - gap_count * 5)
    else:
        completeness = 100.0

    # Outliers: bars where |z-score| > 5σ in last 96 bars
    if "close" in enriched_df.columns and len(enriched_df) >= 20:
        rets = enriched_df["close"].pct_change()
        rolling_std = rets.rolling(96, min_periods=20).std()
        z = (rets - rets.rolling(96, min_periods=20).mean()) / rolling_std.replace(0, np.nan)
        outlier_count = (z.abs() > 5).sum()
        outlier_score = max(0, 100 - outlier_count * 10)
    else:
        outlier_score = 100.0

    score = float(freshness) * 0.40 + float(completeness) * 0.40 + float(outlier_score) * 0.20
    return {
        "score": round(score, 2),
        "gated": bool(score < DATA_QUALITY_MIN_SCORE),
        "freshness": round(float(freshness), 2),
        "completeness": round(float(completeness), 2),
        "outlier": round(float(outlier_score), 2),
    }


def _log_node_decision(conn, run_id, symbol, dqs, side, strategy, session,
                      node, node_idx, action, reason, cal_prob=None, threshold=None,
                      scan_id=None, signal_id=None, details=None):
    """Log a pipeline node decision to signal_node_log."""
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO signal_node_log
                (run_time, pipeline_run_id, symbol, dqs, side, strategy, session,
                 node, node_index, action, reason, calibrated_prob, gate_threshold,
                 detail, scan_id, signal_id)
            VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (run_id, symbol, dqs, side, strategy, session,
              node, node_idx, action, reason, cal_prob, threshold,
              json.dumps(details) if details else None,
              scan_id, signal_id))
        conn.commit()
    except Exception:
        conn.rollback()


def _get_disabled_symbols(conn):
    """Return set of currently disabled symbols."""
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT symbol FROM symbol_status
            WHERE enabled = FALSE
              AND (disabled_until IS NULL OR disabled_until > NOW())
        """)
        return {r[0] for r in cur.fetchall()}
    except Exception:
        conn.rollback()
        return set()


def _check_and_disable_symbols(conn):
    """Check rolling win rate and consecutive SLs; disable underperforming symbols.
    Also auto-re-enables symbols whose disabled_until has expired."""
    cur = conn.cursor()
    rules = SYMBOL_DISABLE_RULES
    lookback = rules["win_rate_lookback"]
    wr_threshold = rules["win_rate_threshold"]
    sl_threshold = rules["consecutive_sl_threshold"]
    reenable_hours = rules["auto_reenable_after_hours"]
    min_trades = rules.get("min_trades_before_disable", 10)

    # Auto-reenable: re-enable symbols whose disable period has expired.
    cur.execute("""
        UPDATE symbol_status
        SET enabled = TRUE, disabled_until = NULL, last_updated = NOW()
        WHERE enabled = FALSE
          AND disabled_until IS NOT NULL
          AND disabled_until <= NOW()
        RETURNING symbol
    """)
    reenabled = cur.fetchall()
    for row in reenabled:
        print(f"  [SYMBOL RE-ENABLE] {row[0]}: disabled_until expired, re-enabling")

    for symbol in SYMBOLS:
        try:
            # Check if this symbol was previously disabled and re-enabled.
            # If so, only evaluate trades closed AFTER the last disabled_at timestamp.
            # This prevents the deadlock where stale losing trades immediately re-disable
            # a symbol after auto-reenable.
            cur.execute("""
                SELECT disabled_at FROM symbol_status
                WHERE symbol = %s AND disabled_at IS NOT NULL
                ORDER BY disabled_at DESC LIMIT 1
            """, (symbol,))
            last_disabled_row = cur.fetchone()
            last_disabled_at = last_disabled_row[0] if last_disabled_row else None

            if last_disabled_at is not None:
                # Symbol was disabled before. Check if it is currently enabled (re-enabled).
                cur.execute("""
                    SELECT enabled FROM symbol_status WHERE symbol = %s
                """, (symbol,))
                is_enabled = cur.fetchone()
                if is_enabled and is_enabled[0]:
                    # Only look at trades closed AFTER the last disable time.
                    cur.execute("""
                        SELECT exit_reason, net_pnl_usd
                        FROM paper_trades
                        WHERE symbol = %s AND status LIKE 'CLOSED%%'
                          AND exit_time_utc > %s
                        ORDER BY exit_time_utc DESC LIMIT %s
                    """, (symbol, last_disabled_at, lookback))
                    recent = cur.fetchall()
                    if len(recent) < min_trades:
                        continue
                else:
                    # Still disabled, skip.
                    continue
            else:
                # Never disabled before, use original logic.
                cur.execute("""
                    SELECT exit_reason, net_pnl_usd
                    FROM paper_trades
                    WHERE symbol = %s AND status LIKE 'CLOSED%%'
                    ORDER BY exit_time_utc DESC LIMIT %s
                """, (symbol, lookback))
                recent = cur.fetchall()
                if len(recent) < min_trades:
                    continue

            wins = sum(1 for r in recent if r[0] == "TP_HIT" or (r[0] == "TIME_EXIT" and r[1] and r[1] > 0))
            wr = wins / len(recent)

            consecutive_sl = 0
            for r in recent:
                if r[0] == "SL_HIT" and r[1] is not None and r[1] <= 0:
                    consecutive_sl += 1
                else:
                    break

            should_disable = wr < wr_threshold or consecutive_sl >= sl_threshold
            if should_disable:
                reason = "CONSECUTIVE_SL" if consecutive_sl >= sl_threshold else "LOW_WIN_RATE"
                from datetime import timedelta
                reenable_at = datetime.now(timezone.utc) + timedelta(hours=reenable_hours)
                cur.execute("""
                    UPDATE symbol_status
                    SET enabled=FALSE, disabled_at=NOW(), disabled_reason=%s,
                        disabled_until=%s, last_updated=NOW()
                    WHERE symbol=%s AND enabled=TRUE
                    RETURNING symbol
                """, (reason, reenable_at, symbol))
                if cur.fetchone():
                    print(f"  [SYMBOL DISABLE] {symbol}: {reason} (WR={wr:.1%}, consecutive_sl={consecutive_sl})")
        except Exception as e:
            conn.rollback()
            continue
    conn.commit()


def _get_account_balance(conn):
    """Fetch paper_account wallet balance for position sizing.
    balance = seed + realized_pnl - open_entry_fees - cumulative_funding.
    This is the correct denominator for risk sizing and loss breaker checks."""
    cur = conn.cursor()
    cur.execute("SELECT balance FROM paper_account LIMIT 1")
    row = cur.fetchone()
    if not row or row[0] is None:
        return 10000.0
    return float(row[0])


def _check_daily_loss_breaker(conn, account_balance: float) -> tuple:
    """Daily loss circuit breaker (vlthr-scale-audit.html Priority 3).

    Nothing previously stopped a bad day from becoming a bad week except a human
    noticing. Halts new signal approval for the rest of the UTC day if realized
    PnL on EITHER ledger (paper or Bybit) has dropped below -daily_loss_halt_pct%
    of account balance. Resets automatically at UTC midnight (query is scoped to
    CURRENT_DATE). Does not touch already-OPEN positions — those still exit via
    the existing SL/TP/time-exit monitor regardless of this gate.

    Returns (breached: bool, reason: str)."""
    halt_pct = PORTFOLIO.get("daily_loss_halt_pct", 2.0)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COALESCE(SUM(net_pnl_usd), 0), COALESCE(SUM(bybit_pnl_usd), 0)
            FROM paper_trades
            WHERE status LIKE 'CLOSED%%' AND DATE(exit_time_utc) = CURRENT_DATE
        """)
        row = cur.fetchone()
        paper_daily_pnl = float(row[0] or 0)
        bybit_daily_pnl = float(row[1] or 0)
    except Exception as e:
        print(f"  [DailyLossBreaker] Query failed (treating as not breached): {e}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False, ""

    if account_balance <= 0:
        return False, ""

    paper_daily_pct = (paper_daily_pnl / account_balance) * 100
    # Use Bybit wallet balance for Bybit PnL percentage (not paper balance)
    bybit_balance = account_balance  # fallback to paper balance
    if _bybit_executor is not None:
        try:
            wallet = _bybit_executor.get_wallet_balance()
            if wallet and wallet.get("list"):
                for coin in wallet["list"][0].get("coin", []):
                    if coin.get("coin") == "USDT":
                        bybit_balance = float(coin.get("walletBalance", 0)) or account_balance
                        break
        except Exception:
            pass
    bybit_daily_pct = (bybit_daily_pnl / bybit_balance) * 100 if bybit_balance > 0 else 0

    if paper_daily_pct <= -halt_pct:
        return True, f"paper daily PnL {paper_daily_pct:.2f}% <= -{halt_pct}%"
    if bybit_daily_pct <= -halt_pct:
        return True, f"bybit daily PnL {bybit_daily_pct:.2f}% <= -{halt_pct}%"
    return False, ""


def _fallback_monitor_trades(conn, now):
    """Fallback SL/TP/time-exit check for OPEN trades.

    Runs every pipeline cycle (15min) as a safety net behind the dashboard
    backend's 15s monitor. If the dashboard is working, trades will already
    be CLOSED before this runs. If the dashboard is down, this catches them.
    Uses load_enriched (15m close) — coarser than 1m but sufficient for a fallback.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT id, signal_id, symbol, side, entry_price_actual, sl_price, sl_original, tp_price,
               qty_contracts, leverage, margin_required, entry_fee, funding_total,
               atr_at_entry, created_at, bybit_order_id, bybit_status
        FROM paper_trades WHERE status = 'OPEN'
    """)
    rows = cur.fetchall()
    if not rows:
        return 0

    closed = 0
    for row in rows:
        (tid, sig_id, symbol, side, entry, sl, sl_original, tp, qty, lev, margin_req,
         entry_fee, funding_total, atr_entry, created_at, bybit_oid, bybit_stat) = row
        entry = float(entry or 0)
        sl = float(sl or 0)
        sl_original = float(sl_original or sl or 0)
        tp = float(tp or 0)
        qty = float(qty or 0)
        lev = float(lev or 4)
        atr_entry = float(atr_entry or 0)
        if entry <= 0 or qty <= 0:
            continue

        enriched = load_enriched(symbol)
        if enriched is None or len(enriched) == 0:
            continue
        last_bar = enriched.iloc[-1]
        live_price = float(last_bar["close"])
        bar_high = float(last_bar.get("high", live_price))
        bar_low = float(last_bar.get("low", live_price))
        current_atr = float(last_bar.get("atr", 0) or 0) if not pd.isna(last_bar.get("atr")) else 0
        if live_price <= 0:
            continue
        # Flash-wick guard: skip if price moved >10% in one bar (likely anomalous)
        if len(enriched) >= 2:
            prev_close = float(enriched.iloc[-2]["close"])
            if prev_close > 0 and abs(live_price - prev_close) / prev_close > 0.10:
                print(f"  [FALLBACK_MONITOR] Skipping {symbol}: flash-wick ({live_price:.4f} vs prev {prev_close:.4f})")
                continue

        side = (side or "LONG").upper()
        max_hours = get_max_trade_hours(symbol)
        entry_ts = created_at
        if entry_ts and hasattr(entry_ts, "tzinfo") and entry_ts.tzinfo is None:
            entry_ts = entry_ts.replace(tzinfo=timezone.utc)
        hours_held = (now - entry_ts).total_seconds() / 3600 if entry_ts else 0

        # ── Dynamic SL adjustment (matches backtest _compute_dynamic_sl) ──
        if sl_original > 0 and entry > 0:
            new_sl = sl_original
            # 1. Break-even: move SL to entry when PnL > 75% of TP distance
            tp_dist = abs(tp - entry) if tp > 0 else 0
            if tp_dist > 0:
                pnl_frac = (live_price - entry) / tp_dist if side == "LONG" else (entry - live_price) / tp_dist
                if pnl_frac > 0.75:
                    new_sl = entry * 0.997 if side == "LONG" else entry * 1.003
            # 2. Time decay: tighten SL by SL_TIME_DECAY_RATE x ATR per hour after hour 3
            if atr_entry > 0 and hours_held > 3:
                tighten_amt = SL_TIME_DECAY_RATE * atr_entry * (hours_held - 3)
                if side == "LONG":
                    decay_sl = max(entry, sl_original + tighten_amt)
                    if decay_sl > new_sl:
                        new_sl = decay_sl
                else:
                    decay_sl = min(entry, sl_original - tighten_amt)
                    if decay_sl < new_sl:
                        new_sl = decay_sl
            # 3. ATR expansion: widen SL when live volatility > 1.5x entry ATR
            if atr_entry > 0 and current_atr > 1.5 * atr_entry:
                expansion_factor = current_atr / atr_entry
                if side == "LONG":
                    expanded_sl = entry - (entry - sl_original) * expansion_factor
                    if expanded_sl < new_sl:
                        new_sl = expanded_sl
                else:
                    expanded_sl = entry + (sl_original - entry) * expansion_factor
                    if expanded_sl > new_sl:
                        new_sl = expanded_sl
            # Apply adjusted SL
            if abs(new_sl - sl) > 0.0001:
                sl = new_sl
                cur.execute("UPDATE paper_trades SET sl_price=%s, updated_at=NOW() WHERE id=%s",
                            (round(sl, 8), tid))
                # Sync dynamic SL to Bybit if position is open there
                if bybit_oid and bybit_stat == 'OPEN' and _bybit_executor is not None:
                    try:
                        _bybit_executor.set_trading_stop(symbol=symbol, sl_price=round(sl, 4))
                    except Exception as e:
                        print(f"  [BYBIT_SL_SYNC] {symbol} failed: {e}")

        # Determine exit reason using intrabar high/low (matches backtest logic)
        reason = None
        exit_price = live_price
        is_long = side == "LONG"
        is_winning = (live_price - entry) * qty > 0 if is_long else (entry - live_price) * qty > 0
        effective_max_hours = max_hours + 2 if is_winning else max_hours

        if hours_held >= effective_max_hours:
            reason = "TIME_EXIT"
            exit_price = live_price
        elif is_long:
            if sl > 0 and bar_low <= sl:
                reason = "SL_HIT"
                exit_price = sl
            elif tp > 0 and bar_high >= tp:
                reason = "TP_HIT"
                exit_price = tp
        else:
            if sl > 0 and bar_high >= sl:
                reason = "SL_HIT"
                exit_price = sl
            elif tp > 0 and bar_low <= tp:
                reason = "TP_HIT"
                exit_price = tp

        if not reason:
            continue

        # Compute PnL (same as dashboard server.cjs)
        gross_usd = (exit_price - entry) * qty if is_long else (entry - exit_price) * qty
        gross_pct = ((exit_price - entry) / entry) * 100 if is_long else ((entry - exit_price) / entry) * 100
        notional = entry * qty
        exit_notional = exit_price * qty
        open_fee = float(entry_fee or notional * TAKER_FEE)
        close_fee = exit_notional * TAKER_FEE
        cum_funding = float(funding_total or 0)
        total_fees = open_fee + close_fee + cum_funding
        net_usd = gross_usd - total_fees
        margin_released = float(margin_req or notional / lev) if lev > 0 else notional
        net_pct = (net_usd / margin_released) * 100 if margin_released > 0 else 0

        try:
            cur.execute("""
                UPDATE paper_trades
                SET exit_price=%s, exit_reason=%s, exit_time_utc=NOW(),
                    gross_pnl_pct=%s, gross_pnl_usd=%s, exit_fee=%s, fees_total=%s,
                    net_pnl_pct=%s, net_pnl_usd=%s, hours_held=%s,
                    status='CLOSED', updated_at=NOW()
                WHERE id=%s
            """, (exit_price, reason, gross_pct, gross_usd, close_fee, total_fees,
                  net_pct, net_usd, round(hours_held, 2), tid))

            # paper_account updates handled exclusively by dashboard backend (server.cjs)
            # to prevent dual-writer race conditions.

            cur.execute("""
                UPDATE risk_ledger SET is_open = FALSE, closed_at = NOW()
                WHERE trade_id = %s AND is_open = TRUE
            """, (tid,))

            conn.commit()
            closed += 1
            print(f"  [FALLBACK_MONITOR] Closed trade {tid} {symbol} {side}: {reason} "
                  f"@ {exit_price:.4f}, net_pnl=${net_usd:.2f}, held {hours_held:.1f}h")

            if alert_trade_close:
                try:
                    alert_trade_close(tid, symbol, side, reason, live_price, net_usd, hours_held)
                except Exception:
                    pass
        except Exception as e:
            conn.rollback()
            print(f"  [FALLBACK_MONITOR] ERROR closing trade {tid}: {e}")

    return closed


def _get_active_trades(conn):
    """Fetch all OPEN paper trades."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return pd.read_sql("""
            SELECT id, symbol, side, entry_price_actual, sl_price, tp_price,
                   qty_contracts, net_pnl_usd, status
            FROM paper_trades WHERE status = 'OPEN'
        """, conn)


def _log_signal_audit(conn, signal_id, symbol, dqs, gate_reached, gate_passed, gate_reason, final_action):
    """Write a signal_audit_log row with checksum for tamper detection."""
    try:
        import hashlib as _hl
        cur = conn.cursor()
        _ts = datetime.now(timezone.utc).isoformat()
        _checksum = _hl.sha256(f"{signal_id}|{symbol}|{dqs}|{gate_reached}|{gate_passed}|{gate_reason}|{final_action}|{_ts}".encode()).hexdigest()[:16]
        cur.execute("""
            INSERT INTO signal_audit_log (run_time, signal_id, symbol, dqs, gate_reached, gate_passed, gate_reason, final_action, checksum)
            VALUES (NOW(), %s, %s, %s, %s, %s, %s, %s, %s)
        """, (signal_id, symbol, dqs, gate_reached, gate_passed, gate_reason, final_action, _checksum))
        conn.commit()
    except Exception as e:
        print(f"  [AuditLog] Failed: {e}")
        conn.rollback()


def _upsert_high_confidence(conn, records):
    """Upsert signals into high_confidence_signals table."""
    if not records:
        return 0
    cur = conn.cursor()
    cols = list(records[0].keys())
    placeholders = ",".join(["%s"] * len(cols))
    col_str = ",".join(cols)
    update_cols = [c for c in cols if c not in ("symbol", "signal_bar_utc")]
    update_set = ", ".join([f"{c} = EXCLUDED.{c}" for c in update_cols])

    sql = f"""
        INSERT INTO high_confidence_signals ({col_str}) VALUES ({placeholders})
        ON CONFLICT (symbol, signal_bar_utc) DO UPDATE SET {update_set}
    """
    count = 0
    for rec in records:
        vals = [rec.get(c) for c in cols]
        try:
            cur.execute(sql, vals)
            count += 1
        except Exception as e:
            print(f"[Orchestrator] Upsert error: {e}")
    conn.commit()
    return count


def _reprice_pending_signals(conn, now: datetime, account_balance: float):
    """
    Re-price all PENDING paper trades each run.
    - Re-scan symbol, recompute DQS, entry, SL, TP, qty
    - If DQS >= 50: update PENDING trade + linked HCS row with fresh values
    - If DQS < 50: mark PENDING as EXPIRED, mark HCS as expired
    Returns (updated_count, expired_count).
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT pt.id, pt.signal_id, pt.symbol, pt.side,
               pt.entry_price_actual, pt.sl_price, pt.tp_price,
               pt.qty_contracts, pt.confidence, pt.leverage, pt.entry_strategy
        FROM paper_trades pt
        WHERE pt.status = 'PENDING'
        ORDER BY pt.created_at DESC
    """)
    pending_rows = cur.fetchall()
    if not pending_rows:
        return 0, 0

    updated = 0
    expired = 0
    _rp_corr = CorrelationGuard(conn)
    _rp_guard = TradeCountGuard(conn)
    _rp_ledger = RiskBudgetLedger(conn)

    for row in pending_rows:
        trade_id, signal_id, symbol, side, old_entry, old_sl, old_tp = row[0], row[1], row[2], row[3], float(row[4] or 0), float(row[5] or 0), float(row[6] or 0)
        old_qty, old_conf, lev, stored_strategy = float(row[7] or 0), float(row[8] or 0), float(row[9] or 4), row[10]
        try:
            enriched = load_enriched(symbol)
            if enriched is None or len(enriched) == 0:
                # No data — expire
                cur.execute("UPDATE paper_trades SET status='EXPIRED', updated_at=NOW() WHERE id=%s", (trade_id,))
                if signal_id:
                    cur.execute("UPDATE high_confidence_signals SET is_expired=TRUE, expiry_reason='No data on reprice' WHERE id=%s", (signal_id,))
                _log_signal_audit(conn, signal_id, symbol, int(old_conf), 'REPRICE_NO_DATA', False, 'No enriched data available', 'EXPIRED')
                conn.commit()
                expired += 1
                continue

            ob = load_orderbook_features(symbol)
            session = enriched.iloc[-1].get("session", "")
            dqs, breakdown = score_signal_adaptive(symbol, enriched, ob, session)

            if dqs < DQS_THRESHOLDS["min_to_track"]:
                # Confidence dropped below tracking threshold
                cur.execute("UPDATE paper_trades SET status='EXPIRED', updated_at=NOW() WHERE id=%s", (trade_id,))
                if signal_id:
                    cur.execute("UPDATE high_confidence_signals SET is_expired=TRUE, expiry_reason='Confidence dropped below 50 on reprice' WHERE id=%s", (signal_id,))
                _log_signal_audit(conn, signal_id, symbol, int(dqs), 'REPRICE_DQS_DROP', False, f'DQS dropped to {int(dqs)}', 'EXPIRED')
                conn.commit()
                expired += 1
                print(f"  [Reprice] {symbol} PENDING expired — DQS dropped to {dqs:.0f}")
                continue

            # Phase 1: V7 veto removed from reprice — risk gate handles Kelly=0 veto
            # Recompute geometry with fresh data
            last_bar = enriched.iloc[-1]
            close_price = float(last_bar["close"])
            atr = float(last_bar.get("atr", 0)) if not pd.isna(last_bar.get("atr")) else 0

            sym_params = get_symbol_params(symbol, session)
            regime_val = breakdown.get("regime", "MIXED")
            sl_mode = sym_params.get("sl_mode", "atr")
            if sl_mode == "pct":
                sl_price, tp_price, rr = get_sl_tp_pct(
                    close_price, sym_params["sl_pct"], sym_params["tp_pct"], side=side, regime=regime_val)
            else:
                sl_price, tp_price, rr = get_sl_tp(close_price, atr, int(dqs), side=side, regime=regime_val, symbol=symbol)
            if sl_price is None:
                print(f"  [Reprice] {symbol} PENDING expired — DQS {dqs:.0f} below executable threshold")
                cur.execute("UPDATE paper_trades SET status='EXPIRED', updated_at=NOW() WHERE id=%s", (trade_id,))
                if signal_id:
                    cur.execute("UPDATE high_confidence_signals SET is_expired=TRUE, expiry_reason='DQS dropped below 50 on reprice' WHERE id=%s", (signal_id,))
                expired += 1
                continue
            tier = get_risk_tier(int(dqs))
            reprice_strategy = stored_strategy or sym_params.get("strategy", "trend_following")
            kelly_mult = get_kelly_v7(dqs, reprice_strategy)

            # Phase 1: Kelly=0 sizing veto in reprice — negative edge, expire
            if kelly_mult == 0.0:
                cur.execute("UPDATE paper_trades SET status='EXPIRED', updated_at=NOW() WHERE id=%s", (trade_id,))
                if signal_id:
                    cur.execute("UPDATE high_confidence_signals SET is_expired=TRUE, expiry_reason='V7 Kelly=0 (negative edge)' WHERE id=%s", (signal_id,))
                _log_signal_audit(conn, signal_id, symbol, int(dqs), 'REPRICE_KELLY_ZERO', False, f'Kelly=0 for DQS {int(dqs)}', 'EXPIRED')
                conn.commit()
                expired += 1
                print(f"  [Reprice] {symbol} PENDING expired — V7 Kelly=0 (DQS {dqs:.0f})")
                continue

            risk_pct = tier["risk_pct"] * kelly_mult

            sl_dist = abs(close_price - sl_price)
            dollar_risk = account_balance * (risk_pct / 100)
            qty_contracts = (dollar_risk / sl_dist) if sl_dist > 0 else 0
            position_size_1x = close_price * qty_contracts
            # ── Cap position size per symbol (V7: 25%) ──
            position_size_1x = cap_position_size(position_size_1x, account_balance)
            qty_contracts = position_size_1x / close_price if close_price > 0 else 0

            # v0.3.0 — Enforce qtyStep rounding
            step = QTY_STEP.get(symbol, 0.001)
            qty_contracts = (int(qty_contracts / step) * step) if step > 0 else qty_contracts
            position_size_1x = close_price * qty_contracts
            notional = position_size_1x

            # v0.3.0 — Skip if below min notional
            if notional < MIN_NOTIONAL:
                print(f"  [Reprice] {symbol} skipped: notional ${notional:.2f} < min ${MIN_NOTIONAL}")
                cur.execute("UPDATE paper_trades SET status='EXPIRED', updated_at=NOW() WHERE id=%s", (trade_id,))
                if signal_id:
                    cur.execute("UPDATE high_confidence_signals SET is_expired=TRUE, expiry_reason='Below min notional' WHERE id=%s", (signal_id,))
                _log_signal_audit(conn, signal_id, symbol, int(dqs), 'REPRICE_MIN_NOTIONAL', False, f'Notional ${notional:.2f} < ${MIN_NOTIONAL}', 'EXPIRED')
                expired += 1
                continue

            dollar_risk = sl_dist * qty_contracts
            leveraged_notional = position_size_1x * lev
            actual_dollar_risk = sl_dist * qty_contracts

            # v0.3.0 — Correct Bybit margin: (notional/lev) + fee_to_close
            fee_to_close = notional * TAKER_FEE
            margin_required = (notional / lev) + fee_to_close if lev > 0 else 0

            # v0.3.0 — Liquidation price
            mm = notional * MMR
            liquidation_price = get_liquidation_price(close_price, qty_contracts, margin_required, mm, side)

            signal_bar_utc = last_bar["timestamp"]

            # ── Gate checks at promotion (mirrors Step 4 gates) ──
            _act = _get_active_trades(conn)
            _act_syms = _act["symbol"].tolist() if len(_act) else []
            _act_sides = [s.upper() for s in _act["side"].tolist()] if len(_act) else []

            if not _rp_corr.can_add(_act_syms, symbol):
                _r = 'already has open position' if symbol in _act_syms else f'max {_rp_corr.MAX_OPEN} open'
                print(f"  [Reprice] {symbol} GATE FAIL — CorrelationGuard: {_r}")
                _log_signal_audit(conn, signal_id, symbol, int(dqs), 'REPRICE_GATE_CORR', False, f'CorrelationGuard: {_r}', 'EXPIRED')
                cur.execute("UPDATE paper_trades SET status='EXPIRED', updated_at=NOW() WHERE id=%s", (trade_id,))
                if signal_id:
                    cur.execute("UPDATE high_confidence_signals SET is_expired=TRUE, expiry_reason=%s WHERE id=%s", (f'CorrelationGuard: {_r}', signal_id))
                conn.commit()
                expired += 1
                continue

            if not _rp_guard.can_open(session):
                print(f"  [Reprice] {symbol} GATE FAIL — TradeCountGuard limit")
                _log_signal_audit(conn, signal_id, symbol, int(dqs), 'REPRICE_GATE_COUNT', False, 'TradeCountGuard limit', 'EXPIRED')
                cur.execute("UPDATE paper_trades SET status='EXPIRED', updated_at=NOW() WHERE id=%s", (trade_id,))
                if signal_id:
                    cur.execute("UPDATE high_confidence_signals SET is_expired=TRUE, expiry_reason='TradeCountGuard limit' WHERE id=%s", (signal_id,))
                conn.commit()
                expired += 1
                continue

            _sig_side = (side or "LONG").upper()
            _ss = _act_sides.count(_sig_side)
            if _ss >= PORTFOLIO.get("max_same_side", 3):
                _r = f'DirectionalGuard: {_ss} {_sig_side} open (max {PORTFOLIO["max_same_side"]})'
                print(f"  [Reprice] {symbol} GATE FAIL — {_r}")
                _log_signal_audit(conn, signal_id, symbol, int(dqs), 'REPRICE_GATE_DIR', False, _r, 'EXPIRED')
                cur.execute("UPDATE paper_trades SET status='EXPIRED', updated_at=NOW() WHERE id=%s", (trade_id,))
                if signal_id:
                    cur.execute("UPDATE high_confidence_signals SET is_expired=TRUE, expiry_reason=%s WHERE id=%s", (_r, signal_id))
                conn.commit()
                expired += 1
                continue

            _est_risk = abs(close_price - sl_price) * qty_contracts
            if not _rp_ledger.has_capacity(_est_risk, account_balance):
                print(f"  [Reprice] {symbol} GATE FAIL — RiskBudgetLedger full")
                _log_signal_audit(conn, signal_id, symbol, int(dqs), 'REPRICE_GATE_RISK', False, 'RiskBudgetLedger full', 'EXPIRED')
                cur.execute("UPDATE paper_trades SET status='EXPIRED', updated_at=NOW() WHERE id=%s", (trade_id,))
                if signal_id:
                    cur.execute("UPDATE high_confidence_signals SET is_expired=TRUE, expiry_reason='RiskBudgetLedger full' WHERE id=%s", (signal_id,))
                conn.commit()
                expired += 1
                continue

            # Gate 4d-cal: Calibration gate — reject if wilson_lower < per-symbol threshold
            _rp_side = (side or "LONG").upper()
            _rp_strategy = reprice_strategy or "trend_following"
            _rp_cal_prob, _rp_cal_n, _rp_cal_fb, _rp_cal_wilson = lookup_calibrated_prob(
                dqs, symbol, _rp_side, _rp_strategy, session, conn)
            _rp_min_prob = get_calibration_gate(symbol)
            _rp_gate_val = _rp_cal_wilson if _rp_cal_n >= 5 else _rp_cal_prob
            if _rp_gate_val < _rp_min_prob and _rp_cal_n >= 5:
                print(f"  [Reprice] {symbol} GATE FAIL — CAL_GATE: wilson_lower={_rp_cal_wilson:.3f} < {_rp_min_prob:.3f} (n={_rp_cal_n})")
                _log_signal_audit(conn, signal_id, symbol, int(dqs), 'REPRICE_GATE_CAL', False,
                                  f'wilson_lower={_rp_cal_wilson:.3f} < {_rp_min_prob:.3f}', 'EXPIRED')
                cur.execute("UPDATE paper_trades SET status='EXPIRED', updated_at=NOW() WHERE id=%s", (trade_id,))
                if signal_id:
                    cur.execute("UPDATE high_confidence_signals SET is_expired=TRUE, expiry_reason=%s WHERE id=%s",
                                (f'CAL_GATE: wilson_lower={_rp_cal_wilson:.3f} < {_rp_min_prob:.3f}', signal_id))
                conn.commit()
                expired += 1
                continue

            # Reprice: update values AND promote PENDING → OPEN (confirmation step)
            fresh_entry_fee = notional * TAKER_FEE
            fresh_exit_fee = notional * TAKER_FEE
            fresh_fees_total = fresh_entry_fee + fresh_exit_fee

            cur.execute("""
                UPDATE paper_trades SET
                    signal_bar_utc = %s,
                    entry_price_planned = %s,
                    entry_price_actual = %s,
                    sl_price = %s,
                    sl_original = %s,
                    tp_price = %s,
                    qty_contracts = %s,
                    leveraged_notional = %s,
                    margin_required = %s,
                    liquidation_price = %s,
                    maintenance_margin = %s,
                    confidence = %s,
                    entry_fee = %s,
                    exit_fee = %s,
                    fees_total = %s,
                    entry_regime = %s,
                    entry_strategy = %s,
                    status = 'OPEN',
                    updated_at = NOW()
                WHERE id = %s
            """, (signal_bar_utc, close_price, close_price, sl_price, sl_price, tp_price,
                  round(qty_contracts, 4), round(leveraged_notional, 2), round(margin_required, 2),
                  round(liquidation_price, 4) if liquidation_price > 0 else None,
                  round(mm, 4) if mm > 0 else None,
                  int(dqs), round(fresh_entry_fee, 4), round(fresh_exit_fee, 4),
                  round(fresh_fees_total, 4), regime_val, reprice_strategy, trade_id))

            # Phase 2: Place real order on Bybit Demo API if executor is active
            # Dual execution: calculate Bybit qty from Bybit balance using same risk_pct
            bybit_order_id = None
            bybit_qty_val = 0
            bybit_balance_val = 0
            bybit_dollar_risk_val = 0
            if _bybit_executor is not None:
                bybit_side = "Buy" if side.upper() in ("LONG", "BUY") else "Sell"
                try:
                    # Pre-check: verify no existing Bybit position for this symbol
                    # (prevents accidental position flip or duplicate)
                    existing_positions = _bybit_executor.get_positions(symbol=symbol)
                    skip_bybit = False
                    for ep in existing_positions:
                        if float(ep.get("size", 0)) > 0:
                            ep_side = ep.get("side", "")
                            if ep_side == bybit_side:
                                print(f"  [BYBIT_ORDER] {symbol} {bybit_side} SKIPPED: existing {ep_side} position already open (size={ep.get('size')})")
                                skip_bybit = True
                            else:
                                print(f"  [BYBIT_ORDER] {symbol} {bybit_side} SKIPPED: existing {ep_side} position would be flipped (size={ep.get('size')})")
                                skip_bybit = True
                            break
                    if skip_bybit:
                        # Record as skipped so bybit_status doesn't stay PENDING
                        cur.execute("""
                            UPDATE paper_trades SET bybit_status = 'SKIPPED'
                            WHERE id = %s
                        """, (trade_id,))
                        conn.commit()
                        # Skip to risk_ledger recording
                        bybit_order_id = None
                        bybit_qty_val = 0
                        bybit_balance_val = 0
                        bybit_dollar_risk_val = 0
                    else:
                        # Fetch Bybit wallet balance for recording (reference only)
                        bybit_wallet = _bybit_executor.get_wallet_balance()
                        bybit_balance_val = 0
                        if bybit_wallet and bybit_wallet.get("list"):
                            for coin in bybit_wallet["list"][0].get("coin", []):
                                if coin.get("coin") == "USDT":
                                    bybit_balance_val = float(coin.get("walletBalance", 0))
                                    break
                        if bybit_balance_val == 0:
                            print(f"  [BYBIT_ORDER] {symbol} {bybit_side} WALLET_EMPTY: could not fetch USDT balance from Bybit")

                        # Size Bybit position using PAPER account balance (not Bybit demo wallet).
                        # Bybit demo wallet (~$44K) is 4x larger than paper account (~$10K).
                        # Using it inflates position size 4x, amplifying losses.
                        sl_dist_val = abs(close_price - sl_price)
                        bybit_dollar_risk_val = account_balance * (risk_pct / 100)
                        bybit_qty_val = (bybit_dollar_risk_val / sl_dist_val) if sl_dist_val > 0 else 0

                        # Cap position size using same cap_position_size as paper trades
                        bybit_position_1x = close_price * bybit_qty_val
                        bybit_position_1x = cap_position_size(bybit_position_1x, account_balance)
                        bybit_qty_val = bybit_position_1x / close_price if close_price > 0 else 0

                        # Round to qty step
                        step_val = QTY_STEP.get(symbol, 0.001)
                        bybit_qty_val = (int(bybit_qty_val / step_val) * step_val) if step_val > 0 else bybit_qty_val
                        bybit_dollar_risk_val = sl_dist_val * bybit_qty_val

                        # Skip if below min notional
                        bybit_notional = close_price * bybit_qty_val
                        if bybit_notional < MIN_NOTIONAL:
                            print(f"  [BYBIT_ORDER] {symbol} {bybit_side} SKIPPED: notional ${bybit_notional:.2f} < min ${MIN_NOTIONAL}, qty={bybit_qty_val:.4f}, balance=${bybit_balance_val:.2f}, risk_pct={risk_pct:.1f}%")
                        else:
                            print(f"  [BYBIT_ORDER] {symbol} {bybit_side} ATTEMPT: qty={round(bybit_qty_val, 4)}, entry={close_price:.4f}, sl={sl_price:.4f}, tp={tp_price:.4f}, risk_pct={risk_pct:.1f}%, bybit_balance=${bybit_balance_val:,.2f}")
                            # Phase 1: Place market order without SL/TP to avoid rejection
                            # when price has already breached SL level before the 5-min cycle.
                            order_result = _bybit_executor.place_order(
                                symbol=symbol,
                                side=bybit_side,
                                qty=round(bybit_qty_val, 4),
                                order_type="Market",
                            )
                            if order_result.get("retCode") == 0:
                                bybit_order_id = order_result.get("result", {}).get("orderId", "")
                                print(f"  [BYBIT_ORDER] {symbol} {bybit_side} FILLED: orderId={bybit_order_id}, qty={bybit_qty_val:.4f}")
                                # Phase 2: Set SL/TP on the position via separate API call.
                                # This works even if price has moved past the SL level.
                                sl_tp_result = _bybit_executor.set_trading_stop(
                                    symbol=symbol,
                                    sl_price=round(sl_price, 4) if sl_price else None,
                                    tp_price=round(tp_price, 4) if tp_price else None,
                                )
                                if sl_tp_result.get("retCode") == 0:
                                    print(f"  [BYBIT_ORDER] {symbol} {bybit_side} SL_TP_SET: sl={sl_price:.4f}, tp={tp_price:.4f}")
                                else:
                                    print(f"  [BYBIT_ORDER] {symbol} {bybit_side} SL_TP_FAILED: retCode={sl_tp_result.get('retCode')}, retMsg={sl_tp_result.get('retMsg')}. Position open without SL/TP - manual intervention required.")
                                print(f"  [BYBIT_ORDER] {symbol} {bybit_side} SUCCESS: orderId={bybit_order_id}, qty={bybit_qty_val:.4f}, risk={risk_pct:.1f}%, bybit_balance=${bybit_balance_val:,.2f}")
                            else:
                                print(f"  [BYBIT_ORDER] {symbol} {bybit_side} FAILED: retCode={order_result.get('retCode')}, retMsg={order_result.get('retMsg')}")
                except Exception as e:
                    print(f"  [BYBIT_ORDER] {symbol} {bybit_side} EXCEPTION: {e}")
            else:
                print(f"  [BYBIT_ORDER] {symbol} SKIPPED: no executor (EXECUTION_MODE={EXECUTION_MODE})")

            # Store Bybit execution data in paper_trades
            if _bybit_executor is not None and bybit_order_id:
                cur.execute("""
                    UPDATE paper_trades SET
                        bybit_order_id = %s,
                        bybit_qty = %s,
                        bybit_entry_price = %s,
                        bybit_sl_price = %s,
                        bybit_tp_price = %s,
                        bybit_status = 'OPEN',
                        bybit_account_balance = %s,
                        bybit_risk_pct = %s,
                        bybit_dollar_risk = %s
                    WHERE id = %s
                """, (bybit_order_id, round(bybit_qty_val, 4), close_price,
                      round(sl_price, 4) if sl_price else None,
                      round(tp_price, 4) if tp_price else None,
                      round(bybit_balance_val, 2), round(risk_pct, 2),
                      round(bybit_dollar_risk_val, 2), trade_id))
                conn.commit()
            elif _bybit_executor is not None and bybit_balance_val > 0:
                # Order was attempted but failed or skipped — record the attempt
                bybit_fail_status = 'SKIPPED' if bybit_notional < MIN_NOTIONAL else 'FAILED'
                cur.execute("""
                    UPDATE paper_trades SET
                        bybit_status = %s,
                        bybit_qty = %s,
                        bybit_account_balance = %s,
                        bybit_risk_pct = %s,
                        bybit_dollar_risk = %s
                    WHERE id = %s
                """, (bybit_fail_status, round(bybit_qty_val, 4),
                      round(bybit_balance_val, 2), round(risk_pct, 2),
                      round(bybit_dollar_risk_val, 2), trade_id))
                conn.commit()

            # Record in risk_ledger
            dollar_risk_final = abs(close_price - sl_price) * qty_contracts
            risk_pct_final = (dollar_risk_final / account_balance * 100) if account_balance > 0 else 0
            cur.execute("""
                INSERT INTO risk_ledger (symbol, trade_id, entry_price, sl_price, qty_contracts, dollar_risk, risk_pct_of_account, is_open)
                VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
            """, (symbol, trade_id, close_price, sl_price, round(qty_contracts, 4),
                  round(dollar_risk_final, 2), round(risk_pct_final, 2)))

            # Update linked HCS row (do NOT update signal_bar_utc — it's part of unique key)
            if signal_id:
                cur.execute("""
                    UPDATE high_confidence_signals SET
                        confidence = %s,
                        grade = %s,
                        signal_strength = %s,
                        price_at_signal = %s,
                        atr_15m = %s,
                        sl_price = %s,
                        tp_price = %s,
                        dollar_risk = %s,
                        actual_dollar_risk = %s,
                        qty_contracts = %s,
                        leveraged_notional = %s,
                        rsi_15m = %s,
                        adx_4h = %s,
                        h4_direction = %s,
                        scan_time_utc = %s
                    WHERE id = %s
                """, (int(dqs), breakdown.get("grade", "UNKNOWN"),
                      breakdown.get("strength", ""), close_price, atr,
                      sl_price, tp_price, round(dollar_risk, 2), round(actual_dollar_risk, 2),
                      round(qty_contracts, 4), round(leveraged_notional, 2),
                      round(breakdown.get("rsi", 0), 2), round(breakdown.get("adx", 0), 2),
                      "UP" if breakdown.get("log_ret_4h", 0) > 0 else "DOWN",
                      now.strftime("%Y-%m-%d %H:%M:%S"), signal_id))

            updated += 1
            conn.commit()
            print(f"  [Reprice] {symbol} PENDING → OPEN — DQS={dqs:.0f}, entry={close_price:.2f}, SL={sl_price:.2f}, qty={qty_contracts:.2f}, risk=${dollar_risk_final:.2f}")

        except Exception as e:
            print(f"  [Reprice] {symbol} error: {e}")
            safety = SafetyLayer(conn)
            safety.log_error("reprice_pending", str(e), symbol)

    conn.commit()
    return updated, expired


def _clear_absent_watchlist(conn, scanned_symbols: list, now: datetime):
    """Mark watchlist entries for symbols not in current scan as NO_SIGNAL."""
    if not scanned_symbols:
        return 0
    cur = conn.cursor()
    placeholders = ",".join(["%s"] * len(scanned_symbols))
    cur.execute(f"""
        UPDATE watchlist_current
        SET signal_type = 'NO_SIGNAL', quality = 'SKIP', gates_passed = '0/4',
            notes = 'Dropped: symbol not seen in current scan',
            created_at = NOW()
        WHERE symbol NOT IN ({placeholders})
          AND scan_time_utc < %s
    """, (*scanned_symbols, now))
    count = cur.rowcount
    conn.commit()
    return count


def _upsert_watchlist(conn, signals, now: datetime):
    """Upsert pipeline-scanned symbols into watchlist_current using pipeline gate data."""
    if not signals:
        return 0
    cur = conn.cursor()
    count = 0
    for sig in signals:
        symbol = sig["symbol"]
        session = sig.get("session", "")
        params = get_symbol_params(symbol, session)
        dqs = sig.get("dqs", 0)

        # Compute 4 gates from pipeline raw data
        rsi = sig.get("rsi", 100)
        adx = sig.get("adx", 0)
        log_ret = sig.get("log_ret_4h", 0)

        gate_session = session in ACTIVE_SESSIONS
        gate_adx = adx >= params["adx_min"]
        sig_direction = sig.get("direction") or sig.get("side", "LONG" if log_ret > 0 else "SHORT")
        is_short = sig_direction == "SHORT"
        strategy = params.get("strategy", "trend_following")
        if strategy == "mean_reversion":
            # Mean-reversion: direction is intentionally counter-trend, always passes
            gate_direction = True
            gate_rsi = rsi > params.get("rsi_overbought", params["rsi_threshold"]) if is_short else rsi < params.get("rsi_oversold", params["rsi_threshold"])
        else:
            # Trend-following: direction must match 4h trend
            gate_direction = (log_ret < 0) if is_short else (log_ret > 0)
            gate_rsi = rsi < params["rsi_threshold"]
        gate_bull = sig.get("bull_4h", 0) == 1
        gates_passed = sum([gate_session, gate_adx, gate_direction, gate_rsi])

        # Gate output assertions
        assert 0 <= gates_passed <= 4, f"{symbol}: gates_passed {gates_passed} out of range [0,4]"
        assert all(isinstance(g, bool) for g in [gate_session, gate_adx, gate_direction, gate_rsi, gate_bull]), f"{symbol}: non-bool gate value"
        assert 0 <= dqs <= 100, f"{symbol}: DQS {dqs} out of range [0,100]"

        # Map DQS to watchlist signal_type
        # CONFIRMED requires: DQS >= execute threshold + active session + majority gates (>=3/4)
        if dqs >= DQS_THRESHOLDS["min_to_execute"] and gate_session and gates_passed >= 3:
            signal_type = "CONFIRMED"
            quality = "HIGH"
        elif dqs >= DQS_THRESHOLDS["min_to_execute"] and gate_session:
            # In session, DQS passes, but not enough quality gates
            signal_type = "APPROACHING"
            quality = "WATCH"
        elif dqs >= DQS_THRESHOLDS["min_to_execute"]:
            # Strong DQS but outside active session
            signal_type = "OFF_SESSION"
            quality = "MONITOR"
        elif dqs >= DQS_THRESHOLDS["min_to_track"] and gate_session:
            signal_type = "APPROACHING"
            quality = "WATCH"
        elif dqs >= DQS_THRESHOLDS["min_to_track"]:
            signal_type = "OFF_SESSION"
            quality = "MONITOR"
        else:
            signal_type = "NO_SIGNAL"
            quality = "SKIP"

        h4_direction = "UP" if log_ret > 0 else "DOWN"
        h4_ema_trend = "BULL" if gate_bull else "BEAR"

        cur.execute("""
            INSERT INTO watchlist_current (
                scan_time_utc, symbol, last_bar_utc, current_price,
                rsi_15m_now, adx_4h_now, atr_15m_now, session_now,
                h4_direction, h4_ema_trend, regime, signal_type, quality,
                gates_passed,
                gate_session_ok, gate_adx_ok, gate_direction_ok, gate_rsi_ok, gate_bull_4h_ok,
                sl_level, tp_level, notes
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol, scan_time_utc) DO UPDATE SET
                last_bar_utc = EXCLUDED.last_bar_utc,
                current_price = EXCLUDED.current_price,
                rsi_15m_now = EXCLUDED.rsi_15m_now,
                adx_4h_now = EXCLUDED.adx_4h_now,
                atr_15m_now = EXCLUDED.atr_15m_now,
                session_now = EXCLUDED.session_now,
                h4_direction = EXCLUDED.h4_direction,
                h4_ema_trend = EXCLUDED.h4_ema_trend,
                regime = EXCLUDED.regime,
                signal_type = EXCLUDED.signal_type,
                quality = EXCLUDED.quality,
                gates_passed = EXCLUDED.gates_passed,
                gate_session_ok = EXCLUDED.gate_session_ok,
                gate_adx_ok = EXCLUDED.gate_adx_ok,
                gate_direction_ok = EXCLUDED.gate_direction_ok,
                gate_rsi_ok = EXCLUDED.gate_rsi_ok,
                gate_bull_4h_ok = EXCLUDED.gate_bull_4h_ok,
                sl_level = EXCLUDED.sl_level,
                tp_level = EXCLUDED.tp_level,
                notes = EXCLUDED.notes,
                created_at = NOW()
        """, (
            now, symbol, sig.get("signal_bar_utc"), sig.get("price"),
            rsi, adx, sig.get("atr", 0), session,
            h4_direction, h4_ema_trend, "PULLBACK", signal_type, quality,
            f"{gates_passed}/4",
            gate_session, gate_adx, gate_direction, gate_rsi, gate_bull,
            sig.get("sl"), sig.get("tp"),
            f"DQS={dqs:.0f} | tech={sig.get('technical',0):.0f} struct={sig.get('structure',0):.0f} ctx={sig.get('context',0):.0f}"
        ))
        count += 1
    conn.commit()
    return count


def _take_snapshot(conn, now: datetime):
    """Record portfolio snapshot."""
    cur = conn.cursor()
    cur.execute("SELECT balance, equity, margin_used FROM paper_account LIMIT 1")
    row = cur.fetchone()
    bal = float(row[0]) if row else 10000.0
    eq = float(row[1]) if row else 10000.0
    marg = float(row[2]) if row else 0.0

    cur.execute("SELECT COALESCE(SUM(dollar_risk), 0) FROM risk_ledger WHERE is_open = TRUE")
    open_risk = float(cur.fetchone()[0] or 0)
    open_risk_pct = (open_risk / bal * 100) if bal > 0 else 0

    cur.execute("SELECT COUNT(*) FROM paper_trades WHERE status = 'OPEN'")
    active_count = cur.fetchone()[0]

    today = now.date()
    cur.execute("SELECT COUNT(*) FROM paper_trades WHERE DATE(created_at) = %s", (today,))
    daily_count = cur.fetchone()[0]

    cur.execute("""
        INSERT INTO portfolio_snapshot (
            run_time, account_balance, account_equity, margin_used,
            total_open_risk, total_open_risk_pct, active_trades_count, daily_trades_count
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    """, (now, bal, eq, marg, open_risk, round(open_risk_pct, 2), active_count, daily_count))
    conn.commit()


def run_iteration(conn, now: Optional[datetime] = None):
    """
    Run one complete 15-minute pipeline iteration.
    Returns dict with summary stats.
    """
    now = now or datetime.now(timezone.utc)
    print(f"\n{'='*70}")
    print(f"  VLTHR Portfolio Orchestrator — Run at {now.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print(f"{'='*70}")

    # ── Phase 4: Trace + Phase 3: Brain ──
    tracer = PipelineTrace(conn)
    tracer.start_run(now)
    brain = PipelineBrain(conn) if RIVER_AVAILABLE else None

    t0 = __import__('time').time()
    safety = SafetyLayer(conn)
    if not safety.pre_flight_checks(now):
        print("  [SAFETY] Pre-flight checks failed — aborting iteration.")
        tracer.log("pre_flight", "FAIL", detail={"reason": "pre_flight_failed"})
        return {"status": "ABORTED", "reason": "pre_flight_failed"}
    tracer.log("pre_flight", "OK", duration_ms=int((__import__('time').time()-t0)*1000))

    # ── Step 0a: Fallback trade monitor (safety net behind dashboard backend) ──
    t0 = __import__('time').time()
    fb_closed = _fallback_monitor_trades(conn, now)
    if fb_closed > 0:
        print(f"  [FALLBACK] Closed {fb_closed} OPEN trade(s) that hit SL/TP/time-exit")
    tracer.log("fallback_monitor", "OK", duration_ms=int((__import__('time').time()-t0)*1000),
               out_count=fb_closed, detail={"closed": fb_closed})

    ssm = SignalStateManager(conn)
    ranker = SignalRanker(conn)
    ledger = RiskBudgetLedger(conn)
    guard = TradeCountGuard(conn)
    corr = CorrelationGuard(conn)

    # ── Step 0: Expire stale signals from previous runs ──
    t0 = __import__('time').time()
    expired_count = ssm.cleanup_expired(now)
    tracer.log("cleanup", "OK", duration_ms=int((__import__('time').time()-t0)*1000),
               in_count=0, out_count=expired_count, detail={"expired": expired_count})
    if expired_count > 0:
        print(f"  [CLEANUP] Expired {expired_count} stale signal_state row(s)")
        # Sync expiry to high_confidence_signals (symbol-based, not signal_id based,
        # because signal_id may be NULL for state-only tracked signals)
        cur = conn.cursor()
        cur.execute("""
            UPDATE high_confidence_signals hcs
            SET is_expired = TRUE,
                expiry_reason = COALESCE(hcs.expiry_reason, 'Auto-expired: age exceeded 2h limit')
            FROM signal_state ss
            WHERE ss.symbol = hcs.symbol
              AND ss.status = 'EXPIRED'
              AND (hcs.is_expired = FALSE OR hcs.is_expired IS NULL)
              AND hcs.scan_time_utc < NOW() - INTERVAL '2 hours'
        """)
        if cur.rowcount > 0:
            print(f"  [CLEANUP] Synced {cur.rowcount} high_confidence_signals to expired")
        conn.commit()

    # ── Step 0b: Purge old dead rows (prevent unbounded growth) ──
    cur = conn.cursor()
    # V3: Delete EXPIRED signal_state rows older than 24h
    cur.execute("""
        DELETE FROM signal_state
        WHERE status = 'EXPIRED' AND updated_at < NOW() - INTERVAL '24 hours'
    """)
    if cur.rowcount > 0:
        print(f"  [CLEANUP] Purged {cur.rowcount} old EXPIRED signal_state row(s)")
    # V5: Delete watchlist_current rows older than 24h
    cur.execute("""
        DELETE FROM watchlist_current
        WHERE scan_time_utc < NOW() - INTERVAL '24 hours'
    """)
    if cur.rowcount > 0:
        print(f"  [CLEANUP] Purged {cur.rowcount} old watchlist_current row(s)")
    conn.commit()

    # ── Step 0c: Reconcile risk_ledger with paper_trades ──
    cur = conn.cursor()
    # Close risk_ledger entries that have no matching OPEN paper trade
    cur.execute("""
        UPDATE risk_ledger
        SET is_open = FALSE, closed_at = NOW()
        WHERE is_open = TRUE
        AND trade_id NOT IN (SELECT id FROM paper_trades WHERE status = 'OPEN')
    """)
    if cur.rowcount > 0:
        print(f"  [RECONCILE] Closed {cur.rowcount} stale risk_ledger entry(s)")
    # Add missing risk_ledger entries for OPEN paper trades without ledger rows
    cur.execute("""
        INSERT INTO risk_ledger (symbol, trade_id, entry_price, sl_price, qty_contracts, dollar_risk, risk_pct_of_account, is_open)
        SELECT pt.symbol, pt.id, pt.entry_price_actual, pt.sl_price, pt.qty_contracts,
               ABS(pt.entry_price_actual - pt.sl_price) * pt.qty_contracts,
               0, TRUE
        FROM paper_trades pt
        LEFT JOIN risk_ledger rl ON rl.trade_id = pt.id
        WHERE pt.status = 'OPEN' AND rl.id IS NULL
    """)
    if cur.rowcount > 0:
        print(f"  [RECONCILE] Added {cur.rowcount} missing risk_ledger entry(s)")
    conn.commit()

    account_balance = _get_account_balance(conn)  # Phase 1: now returns (equity + margin_used)
    active_trades = _get_active_trades(conn)
    active_symbols = active_trades["symbol"].tolist() if len(active_trades) else []

    daily_breach, daily_breach_reason = _check_daily_loss_breaker(conn, account_balance)
    if daily_breach:
        print(f"  [DailyLossBreaker] TRIPPED — {daily_breach_reason}. New approvals halted until UTC midnight.")

    # TP/SL tuning from closed-trade history (adjusts RISK_TIERS in-place)
    tune_tp_from_history(conn)
    # Regime-aware SL/TP tuning from closed-trade outcomes per regime
    tune_regime_from_history(conn)

    print(f"  Account equity: ${account_balance:,.2f}")
    print(f"  Active trades: {len(active_trades)} | Symbols: {active_symbols}")

    # Phase 1: Scanner does NOT read open positions — gates fetch fresh state
    # active_symbols is only used by gates (Step 4), not by scanner (Step 1)
    _scan_active_symbols = []  # scanner gets empty list — decoupled from portfolio state

    # ── Step 0d: Resolve shadow decisions for recently closed trades ──
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT pt.symbol, pt.net_pnl_usd, pt.signal_bar_utc
            FROM paper_trades pt
            WHERE pt.status LIKE 'CLOSED%%'
              AND pt.exit_time_utc > NOW() - INTERVAL '20 minutes'
              AND pt.net_pnl_usd IS NOT NULL
        """)
        closed = cur.fetchall()
        if closed:
            shadow = ShadowDecisionEngine(conn)
            for row in closed:
                entry_time = row[2] if row[2] else None
                shadow.resolve_trade(row[0], float(row[1] or 0), trade_entry_time=entry_time)
            print(f"  [Shadow] Resolved {len(closed)} closed trade(s) with shadow decisions")
        cur.close()
    except Exception as e:
        print(f"  [Shadow] Resolve step error: {e}")
        conn.rollback()

    # ── Step 1: Scan all symbols ──
    print("\n  Step 1 — Scanning symbols...")
    t0 = __import__('time').time()
    new_signals = []
    disabled_symbols = _get_disabled_symbols(conn)
    run_id = str(now.isoformat())
    _enriched_cache = {}  # cache for shadow engine reuse

    # V2: Pre-load daily bias and BTC momentum for filters
    daily_bias_cache = {}
    for sym in SYMBOLS:
        daily_bias_cache[sym] = load_daily_bias(sym)
    btc_mom_6 = get_btc_momentum()
    print(f"  [V2] BTC momentum (6-bar): {btc_mom_6:.2f}%")

    for symbol in SYMBOLS:
        if symbol in disabled_symbols:
            _log_node_decision(conn, run_id, symbol, None, None, None, None,
                              "scan", 1, "SKIP", "SYMBOL_DISABLED")
            print(f"  [Scan] {symbol} skipped — SYMBOL_DISABLED")
            continue
        try:
            scan_id = str(uuid.uuid4())
            enriched = load_enriched(symbol)
            if enriched is None or len(enriched) == 0:
                continue
            _enriched_cache[symbol] = enriched  # cache for shadow engine
            ob = load_orderbook_features(symbol)
            session = enriched.iloc[-1].get("session", "")
            dqs, breakdown = score_signal_adaptive(symbol, enriched, ob, session)

            if dqs < DQS_THRESHOLDS["min_to_track"]:
                _log_node_decision(conn, run_id, symbol, round(dqs, 2),
                                  breakdown.get("direction"), breakdown.get("strategy"),
                                  session, "scan", 1, "REJECT", f"DQS {dqs:.0f} < {DQS_THRESHOLDS['min_to_track']}",
                                  scan_id=scan_id)
                continue  # Drop weak signals immediately

            # ── Pull full bar data ──
            last_bar = enriched.iloc[-1]
            close_price = float(last_bar["close"])
            atr = float(last_bar.get("atr", 0)) if not pd.isna(last_bar.get("atr")) else 0

            # ── Determine side from strategy-selected direction ──
            side = breakdown.get("direction", "LONG")
            strategy = breakdown.get("strategy", "trend_following")

            # V2: Apply strategy filters (daily bias, volume, RSI momentum, regime, BTC correlation)
            v2_passed, strategy, tp_mult_scale, v2_reason = apply_v2_filters(
                symbol, side, strategy, enriched, daily_bias_cache.get(symbol), btc_mom_6)
            if not v2_passed:
                print(f"  [Scan] {symbol} V2 filtered: {v2_reason}")
                _log_node_decision(conn, run_id, symbol, round(dqs, 2),
                                  side, strategy, session,
                                  "scan", 1, "REJECT", f"V2: {v2_reason}",
                                  scan_id=scan_id)
                continue

            # Regime-aware DQS filter: require higher DQS for certain strategy+regime combos
            regime_val_pre = breakdown.get("regime", "MIXED")
            strategy_dqs_min = REGIME_STRATEGY_MIN_DQS.get((strategy, regime_val_pre))
            if strategy_dqs_min and dqs < strategy_dqs_min:
                print(f"  [Scan] {symbol} regime DQS filtered: {strategy}/{regime_val_pre} DQS {dqs:.0f} < {strategy_dqs_min}")
                _log_node_decision(conn, run_id, symbol, round(dqs, 2),
                                  side, strategy, session,
                                  "scan", 1, "REJECT", f"REGIME_DQS: {strategy}/{regime_val_pre} DQS {dqs:.0f} < {strategy_dqs_min}",
                                  scan_id=scan_id)
                continue
            # Re-evaluate side if V2 changed the strategy (direction logic differs per strategy)
            original_strategy = breakdown.get("strategy", "trend_following")
            if strategy != original_strategy:
                log_ret_4h = float(last_bar.get("ctx_log_ret_4h", 0)) if not pd.isna(last_bar.get("ctx_log_ret_4h")) else 0
                rsi_val = float(last_bar.get("rsi", 50)) if not pd.isna(last_bar.get("rsi")) else 50
                if strategy == "trend_following":
                    side = "LONG" if log_ret_4h >= 0 else "SHORT"
                elif strategy == "mean_reversion":
                    sym_p = get_symbol_params(symbol, session)
                    rsi_ob = sym_p.get("rsi_overbought", 70)
                    rsi_os = sym_p.get("rsi_oversold", 30)
                    if rsi_val >= rsi_ob:
                        side = "SHORT"
                    elif rsi_os > 0 and rsi_val <= rsi_os:
                        side = "LONG"
                    else:
                        side = "LONG" if log_ret_4h >= 0 else "SHORT"
                breakdown["direction"] = side
            breakdown["strategy"] = strategy  # override strategy in breakdown

            # ── Entry / SL / TP (regime-aware) ──
            sym_params = get_symbol_params(symbol, session)
            regime_val = breakdown.get("regime", "MIXED")
            sl_mode = sym_params.get("sl_mode", "atr")
            if sl_mode == "pct":
                sl_price, tp_price, rr = get_sl_tp_pct(
                    close_price, sym_params["sl_pct"], sym_params["tp_pct"], side=side, regime=regime_val)
            else:
                sl_price, tp_price, rr = get_sl_tp(close_price, atr, int(dqs), side=side, regime=regime_val, symbol=symbol)
            if sl_price is None:
                print(f"  [Scan] {symbol} skipped: DQS {dqs:.0f} below executable threshold")
                continue

            # V2: TP scaling by ATR percentile
            if tp_mult_scale != 1.0 and sl_mode != "pct":
                tp_dist = abs(tp_price - close_price)
                new_tp_dist = tp_dist * tp_mult_scale
                if side.upper() == "SHORT":
                    tp_price = round(close_price - new_tp_dist, 6)
                else:
                    tp_price = round(close_price + new_tp_dist, 6)
                sl_dist = abs(close_price - sl_price)
                rr = round(new_tp_dist / sl_dist, 2) if sl_dist > 0 else 0
            # Phase 1: V7 veto removed from scanner — risk gate handles Kelly=0 veto
            # V7: Skip disabled symbols
            if symbol in DISABLED_SYMBOLS:
                print(f"  [Scan] {symbol} skipped: V7 disabled symbol")
                continue

            tier = get_risk_tier(int(dqs))
            kelly_mult = get_kelly_v7(dqs, strategy)
            risk_pct = tier["risk_pct"] * kelly_mult

            # ── Position sizing (V7 Kelly-band) ──
            sl_dist = abs(close_price - sl_price)
            sl_dist_pct = (sl_dist / close_price * 100) if close_price > 0 else 0
            tp_dist = abs(tp_price - close_price)
            tp_dist_pct = (tp_dist / close_price * 100) if close_price > 0 else 0
            dollar_risk = account_balance * (risk_pct / 100)
            qty_contracts = (dollar_risk / sl_dist) if sl_dist > 0 else 0
            position_size_1x = close_price * qty_contracts
            # ── Cap position size per symbol (V7: 25%) ──
            position_size_1x = cap_position_size(position_size_1x, account_balance)
            qty_contracts = position_size_1x / close_price if close_price > 0 else 0

            # v0.3.0 — Enforce qtyStep rounding and min notional
            step = QTY_STEP.get(symbol, 0.001)
            qty_contracts = (int(qty_contracts / step) * step) if step > 0 else qty_contracts
            position_size_1x = close_price * qty_contracts
            notional = position_size_1x
            # Phase 1: When Kelly=0 (DQS >= 85), notional is 0 — let signal through to risk gate
            if kelly_mult > 0 and notional < MIN_NOTIONAL:
                print(f"  [Scan] {symbol} skipped: notional ${notional:.2f} < min ${MIN_NOTIONAL}")
                continue

            dollar_risk = sl_dist * qty_contracts
            leverage = 4  # default
            leveraged_notional = position_size_1x * leverage
            actual_dollar_risk = sl_dist * qty_contracts

            # v0.3.0 — Correct Bybit margin + liquidation price
            fee_to_close = notional * TAKER_FEE
            margin_required = (notional / leverage) + fee_to_close if leverage > 0 else 0
            mm = notional * MMR
            liquidation_price = get_liquidation_price(close_price, qty_contracts, margin_required, mm, side)

            # ── Funding / OI from last bar ──
            funding_rate = float(last_bar.get("funding_rate", 0)) if not pd.isna(last_bar.get("funding_rate")) else 0
            oi_now = float(last_bar.get("open_interest", 0)) if not pd.isna(last_bar.get("open_interest")) else 0
            oi_delta_pct = 0.0
            if len(enriched) >= 97:
                oi_old = float(enriched.iloc[-97].get("open_interest", oi_now))
                if oi_old > 0:
                    oi_delta_pct = ((oi_now - oi_old) / oi_old) * 100

            # ── OB metrics ──
            ob_spread_pct = 0.0
            ob_imbalance_pct = 0.0
            if ob is not None:
                ob_spread_pct = float(ob.get("spread_pct", 0))
                ob_imbalance_pct = float(ob.get("imbalance_pct", 0))

            # ── Deduplicate: only 1 signal per symbol per bar ──
            signal_bar_utc = last_bar["timestamp"]
            sig = {
                "symbol": symbol,
                "scan_id": scan_id,
                "signal_bar_utc": signal_bar_utc,
                "dqs": dqs,
                "price": close_price,
                "entry": close_price,
                "sl": sl_price,
                "tp": tp_price,
                "rr": rr,
                "atr": atr,
                "risk_pct": risk_pct,
                "kelly_mult": kelly_mult,
                "sl_distance_pct": round(sl_dist_pct, 4),
                "tp_distance_pct": round(tp_dist_pct, 4),
                "dollar_risk": round(dollar_risk, 2),
                "actual_dollar_risk": round(actual_dollar_risk, 2),
                "qty_contracts": round(qty_contracts, 4),
                "position_size_1x": round(position_size_1x, 2),
                "leveraged_notional": round(leveraged_notional, 2),
                "margin_required": round(margin_required, 2),
                "liquidation_price": round(liquidation_price, 4) if liquidation_price > 0 else None,
                "maintenance_margin": round(mm, 4) if mm > 0 else None,
                "funding_rate": funding_rate,
                "oi_delta_pct": round(oi_delta_pct, 4),
                "ob_spread_pct": ob_spread_pct,
                "ob_imbalance_pct": ob_imbalance_pct,
                "side": side,
                **breakdown,
            }
            # Phase 3: Brain prediction (gated by BRAIN_ENABLED flag)
            if BRAIN_ENABLED and brain is not None:
                try:
                    pred = brain.predict(symbol, sig, int(dqs), now=now)
                    sig["brain_prediction"] = pred
                    if not pred.get("shadow_mode", True):
                        print(f"    [Brain] {symbol}: prob={pred['prob_favorable']:.2f} fused={pred['confidence_fused']:.2f} TRUSTED")
                    else:
                        print(f"    [Brain] {symbol}: prob={pred['prob_favorable']:.2f} fused={pred['confidence_fused']:.2f} shadow")
                except Exception as e:
                    print(f"    [Brain] {symbol}: prediction error: {e}")
            new_signals.append(sig)
        except Exception as e:
            safety.log_error("scan", str(e), symbol)
            continue

    print(f"  Signals found (DQS >= {DQS_THRESHOLDS['min_to_track']}): {len(new_signals)}")
    tracer.log("scan", "OK", duration_ms=int((__import__('time').time()-t0)*1000),
               in_count=len(SYMBOLS), out_count=len(new_signals),
               detail={"symbols_scanned": len(SYMBOLS), "signals_found": len(new_signals)})

    # ── Step 1a: Shadow-mode decision logging (Doc 22) ──
    if new_signals:
        try:
            shadow = ShadowDecisionEngine(conn)
            shadow.evaluate_signals(new_signals, run_id, now, _enriched_cache)
            # Log tournament if 2+ candidates
            shadow.log_tournament(run_id, new_signals, now)
        except Exception as e:
            print(f"  [Shadow] Error: {e}")
            conn.rollback()

    # ── Step 1b: Update watchlist from pipeline scans ──
    wl_count = _upsert_watchlist(conn, new_signals, now)
    if wl_count > 0:
        print(f"  [WATCHLIST] Updated {wl_count} symbol(s) in watchlist_current")

    # Clear watchlist entries for symbols not in current scan
    cleared = _clear_absent_watchlist(conn, [s["symbol"] for s in new_signals], now)
    if cleared > 0:
        print(f"  [WATCHLIST] Cleared {cleared} stale symbol(s) to NO_SIGNAL")

    # ── Step 1c: Re-price existing PENDING signals ──
    print("\n  Step 1c — Re-pricing PENDING signals...")
    reprice_updated, reprice_expired = _reprice_pending_signals(conn, now, account_balance)
    if reprice_updated > 0:
        print(f"  [Reprice] Refreshed {reprice_updated} PENDING signal(s)")
    if reprice_expired > 0:
        print(f"  [Reprice] Expired {reprice_expired} PENDING signal(s) (confidence < 50)")

    # Refresh active trades after reprice — PENDING→OPEN promotions change the set
    if reprice_updated > 0:
        active_trades = _get_active_trades(conn)
        active_symbols = active_trades["symbol"].tolist() if len(active_trades) else []
        print(f"  [Reprice] Active trades refreshed: {len(active_trades)} | Symbols: {active_symbols}")

    # ── Step 2: Track state + trajectory ──
    print("\n  Step 2 — Tracking signal state...")
    t0 = __import__('time').time()
    for sig in new_signals:
        try:
            ssm.evaluate(
                symbol=sig["symbol"],
                signal_id=None,  # not yet in high_confidence_signals at this stage
                signal_bar_utc=sig["signal_bar_utc"],
                dqs=int(sig["dqs"]),
                ranker_score=int(sig.get("adjusted_score", sig["dqs"])),
                entry=sig.get("entry", 0),
                sl=sig.get("sl", 0),
                tp=sig.get("tp", 0),
                risk_pct=sig.get("risk_pct", 0),
                now=now,
                side=sig.get("side", sig.get("direction", "LONG")),
                strategy=sig.get("strategy", "trend_following"),
                session=sig.get("session", ""),
            )
        except Exception as e:
            conn.rollback()  # clear aborted tx before logging
            safety.log_error("state_manager", str(e), sig["symbol"])

    tracer.log("state", "OK", duration_ms=int((__import__('time').time()-t0)*1000),
               in_count=len(new_signals), out_count=ssm.get_active_signals().shape[0])

    # Phase 3: Resolve previous predictions + learn (gated by BRAIN_ENABLED)
    if BRAIN_ENABLED and brain is not None:
        try:
            brain.resolve_and_learn(now)
        except Exception as e:
            print(f"  [Brain] resolve_and_learn error: {e}")

    # ── Step 3: Rank and filter ──
    print("\n  Step 3 — Ranking signals...")
    t0 = __import__('time').time()
    active_df = ssm.get_active_signals()
    ranked = ranker.rank_and_filter(active_df)
    print(f"  Signals passing ranker gate (>= {ranker.MIN_RANKER_SCORE}): {len(ranked)}")
    tracer.log("rank", "OK", duration_ms=int((__import__('time').time()-t0)*1000),
               in_count=active_df.shape[0], out_count=len(ranked))

    # ── Step 4: Apply portfolio gates ──
    print("\n  Step 4 — Portfolio gates...")
    approved_signals = []

    # Treatment B: Detect crowding across all ranked signals at this scan
    crowded_symbols = _detect_crowding(ranked)
    if crowded_symbols:
        print(f"  [Crowding] Detected crowded symbols: {crowded_symbols} — Kelly reduced {CROWDING_KELLY_REDUCTION*100:.0f}%")

    for sig in ranked:
        symbol = sig["symbol"]
        session = sig.get("session", "")
        dqs = sig.get("adjusted_score", 0)
        tier = get_risk_tier(int(dqs))

        # Gate -1: Daily loss circuit breaker (vlthr-scale-audit.html Priority 3)
        if daily_breach:
            _log_signal_audit(conn, None, symbol, int(dqs), 'GATE_DAILY_BREAKER', False,
                            daily_breach_reason, 'REJECTED')
            continue

        # Phase 1: V7 Kelly veto moved from scanner to risk gate
        # Kelly=0 means negative edge — risk gate blocks, scanner should have let it through
        v7_gate_strategy = sig.get("strategy", "trend_following")
        v7_gate_kelly = get_kelly_v7(dqs, v7_gate_strategy)
        if v7_gate_kelly == 0.0:
            print(f"    [{symbol}] REJECTED — V7 Kelly veto: Kelly=0 for DQS {dqs:.0f} (negative edge)")
            _log_signal_audit(conn, None, symbol, int(dqs), 'GATE_0_V7_KELLY_ZERO', False,
                            f'Kelly=0 for DQS {dqs:.0f}', 'REJECTED')
            _log_node_decision(conn, run_id, symbol, round(dqs, 2),
                              sig.get("side") or sig.get("direction", "LONG"), sig.get("strategy", "trend_following"), session,
                              "gate", 0, "REJECT", f"V7_KELLY_ZERO: Kelly=0 for DQS {dqs:.0f}",
                              scan_id=sig.get("scan_id"))
            continue

        # V7 Gate 0b: Disabled symbols
        if symbol in DISABLED_SYMBOLS:
            print(f"    [{symbol}] REJECTED — V7 disabled symbol")
            _log_signal_audit(conn, None, symbol, int(dqs), 'GATE_0_V7_DISABLED', False,
                            'Symbol disabled in V7', 'REJECTED')
            continue

        # Gate 0c: Regime-skip — require higher DQS in VOLATILE/MIXED regimes
        sig_regime = (sig.get("regime") or "MIXED").upper()
        min_dqs_for_regime = REGIME_MIN_DQS.get(sig_regime, 50)
        if dqs < min_dqs_for_regime:
            reason = f'RegimeGate: DQS {dqs:.0f} < {min_dqs_for_regime} required for {sig_regime}'
            print(f"    [{symbol}] REJECTED — {reason}")
            _log_signal_audit(conn, None, symbol, int(dqs), 'GATE_0C_REGIME', False, reason, 'REJECTED')
            _log_node_decision(conn, run_id, symbol, round(dqs, 2),
                              sig.get("side") or sig.get("direction", "LONG"), sig.get("strategy", "trend_following"), session,
                              "gate", 0, "REJECT", reason, scan_id=sig.get("scan_id"))
            continue

        # Gate 0d: Regime-strategy alignment (Phase B — regime_router)
        # Checks whether the signal's strategy aligns with the current market regime.
        # Uses ADX + efficiency ratio + ATR rank for richer classification than Gate 0c.
        _enriched_for_regime = _enriched_cache.get(symbol)
        if _enriched_for_regime is not None:
            _rg = evaluate_regime_gate(
                _enriched_for_regime,
                strategy=sig.get("strategy", "trend_following"),
                session=session,
                dqs=int(dqs),
                symbol=symbol,
            )
            if not _rg.allowed:
                reason = f'RegimeRouter: {_rg.reason} (regime={_rg.regime}, ER={_rg.efficiency_ratio:.2f}, ADX={_rg.adx:.1f})'
                print(f"    [{symbol}] REJECTED — {reason}")
                _log_signal_audit(conn, None, symbol, int(dqs), 'GATE_0D_REGIME_ROUTER', False, reason, 'REJECTED')
                _log_node_decision(conn, run_id, symbol, round(dqs, 2),
                                  sig.get("side") or sig.get("direction", "LONG"), sig.get("strategy", "trend_following"), session,
                                  "gate", 0, "REJECT", reason, scan_id=sig.get("scan_id"))
                continue
        else:
            print(f"    [{symbol}] WARNING — Regime router bypassed: no enriched data in cache")

        # Gate 1: Data Quality Gate (Treatment B — validated by backtest)
        # Reject signals with data quality score below DATA_QUALITY_MIN_SCORE (60)
        enriched_for_dq = _enriched_cache.get(symbol)
        if enriched_for_dq is not None:
            dq = _compute_data_quality(symbol, enriched_for_dq)
            if dq["gated"]:
                reason = f'DQGate: score={dq["score"]:.1f} < {DATA_QUALITY_MIN_SCORE} (fresh={dq["freshness"]}, comp={dq["completeness"]}, out={dq["outlier"]})'
                print(f"    [{symbol}] REJECTED — {reason}")
                _log_signal_audit(conn, None, symbol, int(dqs), 'GATE_1_DQ', False, reason, 'REJECTED')
                _log_node_decision(conn, run_id, symbol, round(dqs, 2),
                                  sig.get("side") or sig.get("direction", "LONG"), sig.get("strategy", "trend_following"), session,
                                  "gate", 1, "REJECT", reason, scan_id=sig.get("scan_id"))
                continue

        # Gate 4a: Correlation / stacking
        if not corr.can_add(active_symbols, symbol):
            if symbol in active_symbols:
                reason = f'CorrelationGuard: {symbol} already has open position'
            else:
                reason = f'CorrelationGuard: max {corr.MAX_OPEN} open positions'
            print(f"    [{symbol}] REJECTED — {reason}")
            _log_signal_audit(conn, None, symbol, int(dqs), 'GATE_4A_CORR', False, reason, 'REJECTED')
            continue

        # Gate 4b: Trade count
        if not guard.can_open(session):
            print(f"    [{symbol}] REJECTED — TradeCountGuard limit reached")
            _log_signal_audit(conn, None, symbol, int(dqs), 'GATE_4B_COUNT', False, 'TradeCountGuard limit', 'REJECTED')
            continue

        # Gate 4d: Directional balance — prevent all-same-side concentration
        sig_side = (sig.get("side") or sig.get("direction", "LONG")).upper()
        active_sides = [s.upper() for s in active_trades["side"].tolist()] if len(active_trades) else []
        same_side_count = active_sides.count(sig_side)
        if same_side_count >= PORTFOLIO.get("max_same_side", 3):
            reason = f'DirectionalGuard: {same_side_count} {sig_side} positions open (max {PORTFOLIO["max_same_side"]})'
            print(f"    [{symbol}] REJECTED — {reason}")
            _log_signal_audit(conn, None, symbol, int(dqs), 'GATE_4D_DIRECTION', False, reason, 'REJECTED')
            continue

        # Gate 4c: Risk budget
        # V7: Kelly-band sizing with MR boost
        v7_strategy = sig.get("strategy", "trend_following")
        v7_kelly = get_kelly_v7(dqs, v7_strategy)
        # Treatment B: Apply crowding Kelly reduction
        if symbol in crowded_symbols:
            v7_kelly = v7_kelly * CROWDING_KELLY_REDUCTION
            print(f"    [{symbol}] CROWDING — Kelly reduced to {v7_kelly:.2f}x ({CROWDING_KELLY_REDUCTION*100:.0f}% reduction)")
        estimated_risk_usd = account_balance * (tier["risk_pct"] / 100) * v7_kelly
        # Cap per-trade risk at portfolio max (Kelly can push 5% * 2.5 = 12.5% > 10% cap)
        max_per_trade = account_balance * (PORTFOLIO["max_portfolio_risk_pct"] / 100)
        estimated_risk_usd = min(estimated_risk_usd, max_per_trade)
        if not ledger.has_capacity(estimated_risk_usd, account_balance):
            print(f"    [{symbol}] REJECTED — RiskBudgetLedger full")
            _log_signal_audit(conn, None, symbol, int(dqs), 'GATE_4C_RISK', False, 'RiskBudgetLedger full', 'REJECTED')
            _log_node_decision(conn, run_id, symbol, round(dqs, 2),
                              sig.get("side") or sig.get("direction", "LONG"), sig.get("strategy", "trend_following"), session,
                              "gate", 4, "REJECT", "RiskBudgetLedger full",
                              scan_id=sig.get("scan_id"))
            continue

        # Gate 4d: Calibration gate (N6) — reject if wilson_lower < per-symbol threshold
        side = sig.get("side") or sig.get("direction", "LONG")
        strategy = sig.get("strategy", "trend_following")
        cal_prob, cal_samples, cal_fallback, cal_wilson = lookup_calibrated_prob(
            dqs, symbol, side, strategy, session, conn)
        sig["calibrated_prob"] = cal_prob
        min_prob = get_calibration_gate(symbol)

        # Use Wilson lower bound for gating when real matrix data exists
        gate_value = cal_wilson if cal_samples >= 5 else cal_prob

        if gate_value < min_prob and cal_samples >= 5:
            print(f"    [{symbol}] REJECTED — CAL_GATE: wilson_lower={cal_wilson:.3f} < {min_prob:.3f} (n={cal_samples}, level={cal_fallback})")
            _log_signal_audit(conn, None, symbol, int(dqs), 'GATE_4D_CAL', False,
                            f'wilson_lower={cal_wilson:.3f} < {min_prob:.3f}', 'REJECTED')
            _log_node_decision(conn, run_id, symbol, round(dqs, 2),
                              side, strategy, session,
                              "gate", 4, "REJECT", f"CAL_GATE: wilson_lower={cal_wilson:.3f} < {min_prob:.3f}",
                              cal_prob=cal_prob, threshold=min_prob,
                              scan_id=sig.get("scan_id"),
                              details={"fallback_level": cal_fallback, "sample_size": cal_samples, "wilson_lower": cal_wilson})
            continue

        gate_risk_pct = tier["risk_pct"] * v7_kelly
        approved_signals.append({**sig, "risk_tier": tier, "kelly_v7": v7_kelly})
        active_symbols.append(symbol)  # prevent double-counting in same iteration
        print(f"    [{symbol}] APPROVED — DQS={dqs}, Tier={tier['tier']}, Kelly={v7_kelly:.1f}x, Risk={gate_risk_pct:.1f}%, CalProb={cal_prob:.3f}")
        _log_signal_audit(conn, None, symbol, int(dqs), 'ALL_GATES', True, f"Tier={tier['tier']}, Kelly={v7_kelly:.1f}x", 'APPROVED')
        _log_node_decision(conn, run_id, symbol, round(dqs, 2),
                          side, strategy, session,
                          "gate", 4, "PASS", f"Tier={tier['tier']}, Kelly={v7_kelly:.1f}x, CalProb={cal_prob:.3f}",
                          cal_prob=cal_prob, threshold=min_prob,
                          scan_id=sig.get("scan_id"))

    tracer.log("gate", "OK", in_count=len(ranked), out_count=len(approved_signals),
               detail={"rejected_by_corr": sum(1 for s in ranked if s['symbol'] not in [a['symbol'] for a in approved_signals])})

    # ── Step 5: Upsert to high_confidence_signals ──
    print(f"\n  Step 5 — Upserting {len(approved_signals)} approved signals to DB...")
    t0 = __import__('time').time()
    # Build lookup from original new_signals to get full data (rsi, adx, price, etc.)
    new_signals_lookup = {s["symbol"]: s for s in new_signals}
    db_records = []
    for sig in approved_signals:
        symbol = sig["symbol"]
        full = new_signals_lookup.get(symbol, {})  # get full signal data
        # Fallback to signal_state data when scan produced no new signals (V2 filtered)
        ss_entry = float(sig.get("current_entry", 0) or 0)
        ss_sl = float(sig.get("current_sl", 0) or 0)
        ss_tp = float(sig.get("current_tp", 0) or 0)
        db_records.append({
            "scan_time_utc": now.strftime("%Y-%m-%d %H:%M:%S"),
            "signal_bar_utc": sig.get("signal_bar_utc", now),
            "symbol": symbol,
            "confidence": int(sig.get("adjusted_score", sig.get("dqs", 0))),
            "grade": full.get("grade", "UNKNOWN"),
            "signal_strength": full.get("strength", ""),
            "technical_score": full.get("technical", 0),
            "market_structure_score": full.get("structure", 0),
            "funding_oi_score": full.get("funding_oi", 0),
            "session_symbol_score": full.get("session_symbol", 0),
            "price_at_signal": full.get("price", 0) or ss_entry,
            "atr_15m": full.get("atr", 0),
            "sl_price": full.get("sl", 0) or ss_sl,
            "tp_price": full.get("tp", 0) or ss_tp,
            "sl_distance_pct": full.get("sl_distance_pct", 0),
            "tp_distance_pct": full.get("tp_distance_pct", 0),
            "risk_reward": full.get("rr", 0),
            "dollar_risk": full.get("dollar_risk", 0),
            "actual_dollar_risk": full.get("actual_dollar_risk", 0),
            "qty_contracts": full.get("qty_contracts", 0),
            "position_size_1x": full.get("position_size_1x", 0),
            "leveraged_notional": full.get("leveraged_notional", 0),
            "funding_rate": full.get("funding_rate", 0),
            "oi_delta_pct": full.get("oi_delta_pct", 0),
            "ob_spread_pct": full.get("ob_spread_pct", 0),
            "ob_imbalance_pct": full.get("ob_imbalance_pct", 0),
            "rsi_15m": full.get("rsi", 0),
            "adx_4h": full.get("adx", 0),
            "session": full.get("session", "") or sig.get("session", ""),
            "h4_direction": "UP" if full.get("log_ret_4h", 0) > 0 else "DOWN",
            "leverage": full.get("leverage", 4),
            "trade_decision": full.get("side", "LONG" if full.get("log_ret_4h", 0) > 0 else "SHORT"),
            "strategy": full.get("strategy", "trend_following"),
            "margin_required": full.get("margin_required", 0),
            "liquidation_price": full.get("liquidation_price"),
            "maintenance_margin": full.get("maintenance_margin"),
            "calibrated_confidence": round(float(sig.get("calibrated_prob", 0)), 4) if sig.get("calibrated_prob") else None,
            "brain_confidence_fused": round(float(full.get("brain_prediction", {}).get("confidence_fused", 0)), 4) if full.get("brain_prediction") else None,
            "brain_shadow_mode": full.get("brain_prediction", {}).get("shadow_mode", True) if full.get("brain_prediction") else True,
            "tech_raw": full.get("tech_raw"),
            "struct_raw": full.get("struct_raw"),
            "ctx_raw": full.get("ctx_raw"),
            "trend_mult": full.get("trend_mult"),
            "session_mult": full.get("session_mult"),
            "symbol_mult": full.get("symbol_mult"),
            "combined_mult": full.get("combined_mult"),
            "mult_cap_hit": full.get("mult_cap_hit", False),
            "rsi_overbought_long": full.get("rsi_overbought_long", False),
            "regime": full.get("regime"),
            "calibrated_prob": sig.get("calibrated_prob"),
            "scan_id": full.get("scan_id"),
        })
    n_upserted = _upsert_high_confidence(conn, db_records)

    # ── Step 5b: Backfill signal_id into signal_state for newly upserted HCS rows ──
    if n_upserted > 0:
        cur = conn.cursor()
        for rec in db_records:
            symbol = rec["symbol"]
            bar_ts = rec["signal_bar_utc"]
            try:
                cur.execute("""
                    SELECT id FROM high_confidence_signals
                    WHERE symbol = %s AND signal_bar_utc = %s
                    LIMIT 1
                """, (symbol, bar_ts))
                row = cur.fetchone()
                if row:
                    hcs_id = row[0]
                    cur.execute("""
                        UPDATE signal_state
                        SET signal_id = %s
                        WHERE symbol = %s AND signal_bar_utc = %s
                          AND signal_id IS NULL
                    """, (hcs_id, symbol, bar_ts))
            except Exception as e:
                print(f"  [SignalID Backfill] {symbol}: {e}")
        conn.commit()
        print(f"  [SignalID Backfill] Updated {n_upserted} signal_state row(s)")
    tracer.log("upsert", "OK", duration_ms=int((__import__('time').time()-t0)*1000),
               in_count=len(approved_signals), out_count=n_upserted)

    # ── Step 5c: Create PENDING paper_trades for approved signals ──
    print(f"\n  Step 5c — Creating PENDING paper_trades for {len(approved_signals)} approved signal(s)...")
    n_pending = 0
    cur = conn.cursor()
    for sig in approved_signals:
        symbol = sig["symbol"]
        full = new_signals_lookup.get(symbol, {})
        bar_ts = sig.get("signal_bar_utc", now)
        side = sig.get("side") or sig.get("direction", "LONG")
        # Fallback to signal_state data when scan produced no new signals
        ss_entry = float(sig.get("current_entry", 0) or 0)
        ss_sl = float(sig.get("current_sl", 0) or 0)
        ss_tp = float(sig.get("current_tp", 0) or 0)
        entry = float(full.get("price", 0) or full.get("entry", 0) or 0) or ss_entry
        sl = float(full.get("sl", 0) or 0) or ss_sl
        tp = float(full.get("tp", 0) or 0) or ss_tp
        qty = float(full.get("qty_contracts", 0) or 0)
        notional = float(full.get("leveraged_notional", 0) or full.get("position_size_1x", 0) or 0)
        margin_req = float(full.get("margin_required", 0) or 0)
        conf = int(sig.get("adjusted_score", sig.get("dqs", 0)))
        lev = int(full.get("leverage", 4))
        funding = float(full.get("funding_rate", 0) or 0)
        oi = float(full.get("oi_delta_pct", 0) or 0)
        liq_price = full.get("liquidation_price")
        mm = float(full.get("maintenance_margin", 0) or 0)
        atr_val = float(full.get("atr", 0) or 0)
        rsi_val = float(full.get("rsi", 0) or 0)
        adx_val = float(full.get("adx", 0) or 0)
        session_val = full.get("session", "") or sig.get("session", "")
        strategy_val = full.get("strategy", "trend_following") or sig.get("strategy", "trend_following")
        regime_val = full.get("regime")
        cal_prob_val = sig.get("calibrated_prob")
        entry_fee = notional * TAKER_FEE if notional > 0 else 0
        exit_fee = notional * TAKER_FEE if notional > 0 else 0
        fees_total = entry_fee + exit_fee
        funding_est = notional * abs(funding) if notional > 0 else 0
        slippage_pct = 0.10
        slippage_usd = notional * slippage_pct / 100 if notional > 0 else 0

        # Skip if PENDING already exists for this (symbol, signal_bar_utc)
        cur.execute("""
            SELECT 1 FROM paper_trades
            WHERE symbol = %s AND signal_bar_utc = %s AND status = 'PENDING'
            LIMIT 1
        """, (symbol, bar_ts))
        if cur.fetchone():
            continue
        # Also skip if an OPEN trade already exists for this symbol
        cur.execute("SELECT 1 FROM paper_trades WHERE symbol = %s AND status = 'OPEN' LIMIT 1", (symbol,))
        if cur.fetchone():
            continue

        # Get HCS id for signal_id
        cur.execute("""
            SELECT id FROM high_confidence_signals
            WHERE symbol = %s AND signal_bar_utc = %s LIMIT 1
        """, (symbol, bar_ts))
        hcs_row = cur.fetchone()
        signal_id = hcs_row[0] if hcs_row else None

        try:
            cur.execute("""
                INSERT INTO paper_trades (
                    signal_id, symbol, signal_bar_utc, scan_time_utc,
                    entry_price_planned, sl_price, tp_price,
                    qty_contracts, leveraged_notional, margin_required, confidence,
                    funding_rate_at_entry, oi_delta_at_entry,
                    entry_fee, exit_fee, fees_total, funding_events, funding_total,
                    slippage_pct, slippage_usd, status, side, leverage,
                    liquidation_price, maintenance_margin, atr_at_entry,
                    entry_rsi, entry_adx, entry_session, entry_strategy, entry_regime,
                    calibrated_prob, notes
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, 1, %s, %s, %s, 'PENDING', %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    'Auto-created by orchestrator Step 5c'
                )
            """, (signal_id, symbol, bar_ts, now.strftime("%Y-%m-%d %H:%M:%S"),
                  entry, sl, tp, round(qty, 4), round(notional, 2), round(margin_req, 2), conf,
                  funding, oi,
                  round(entry_fee, 4), round(exit_fee, 4), round(fees_total, 4),
                  round(funding_est, 4), round(slippage_pct, 4), round(slippage_usd, 4),
                  side, lev,
                  round(liq_price, 4) if liq_price and liq_price > 0 else None,
                  round(mm, 4) if mm > 0 else None,
                  round(atr_val, 4) if atr_val > 0 else None,
                  round(rsi_val, 2) if rsi_val > 0 else None,
                  round(adx_val, 2) if adx_val > 0 else None,
                  session_val, strategy_val, regime_val,
                  round(float(cal_prob_val), 4) if cal_prob_val else None))
            n_pending += 1
        except Exception as e:
            print(f"  [PaperTrade] {symbol} insert error: {e}")
    conn.commit()
    cur.close()
    if n_pending > 0:
        print(f"  [PaperTrade] Created {n_pending} PENDING paper_trade(s)")

    # ── Step 5d: Shadow validation (Phase C — independent gate re-check) ──
    if approved_signals:
        print(f"\n  Step 5d — Shadow validation ({len(approved_signals)} signal(s))...")
        try:
            val_summary = recheck_approved_signals(
                conn, approved_signals, account_balance,
                daily_breach=daily_breach)
            print(f"  [Shadow] Validation: {val_summary['passed']}/{val_summary['total_checks']} passed, {val_summary['failed']} failed")
            if val_summary["failed"] > 0:
                print(f"  [Shadow] WARNING — {val_summary['failed']} gate discrepancy(ies) detected — check validation_log table")
        except Exception as e:
            print(f"  [Shadow] Validation error: {e}")
            conn.rollback()

    # ── Step 6: Portfolio snapshot ──
    print("\n  Step 6 — Taking portfolio snapshot...")
    _take_snapshot(conn, now)

    tracer.log("snapshot", "OK", in_count=1, out_count=1)

    # ── Step 6b: Bybit reconciliation (Phase 2) ──
    if _bybit_executor is not None:
        print("\n  Step 6b — Bybit reconciliation...")
        try:
            cur = conn.cursor()
            # Get all paper_trades that have Bybit orders but bybit_status is OPEN
            cur.execute("""
                SELECT id, symbol, side, bybit_qty, bybit_order_id, bybit_entry_price,
                       bybit_account_balance, bybit_sl_price, bybit_tp_price,
                       status, exit_reason, created_at
                FROM paper_trades
                WHERE bybit_order_id IS NOT NULL AND bybit_status = 'OPEN'
            """)
            bybit_open_rows = cur.fetchall()

            # Fetch current Bybit positions
            bybit_positions = _bybit_executor.get_positions()
            bybit_pos_map = {p["symbol"]: p for p in bybit_positions if float(p.get("size", 0)) > 0}

            # ── Critical #2: Close orphaned Bybit positions (paper trade CLOSED via TIME_EXIT) ──
            for row in bybit_open_rows:
                trade_id, symbol, side, bybit_qty, order_id, entry_price, bybit_bal, sl_price, tp_price, pt_status, pt_exit_reason, created_at = row
                if pt_status and pt_status.startswith('CLOSED') and symbol in bybit_pos_map:
                    bybit_qty_f = float(bybit_qty or 0)
                    if bybit_qty_f > 0:
                        print(f"  [Bybit] {symbol} paper trade CLOSED ({pt_exit_reason}) — closing orphaned Bybit position (qty={bybit_qty_f})")
                        close_result = _bybit_executor.close_position(symbol, side, bybit_qty_f)
                        if close_result.get("retCode") == 0:
                            print(f"  [Bybit] {symbol} orphaned position closed successfully")
                            # Record close metadata inline so next cycle doesn't mislabel as SL/TP
                            bybit_pnl = 0.0
                            close_price = float(entry_price)
                            try:
                                closed_list = _bybit_executor.get_closed_pnl(symbol)
                                for item in closed_list:
                                    avg_entry = float(item.get("avgEntryPrice", 0))
                                    item_time = int(item.get("updatedTime", 0)) / 1000
                                    if (avg_entry > 0 and abs(avg_entry - float(entry_price)) < 0.01 * float(entry_price)
                                            and item_time >= created_at.timestamp()):
                                        close_price = float(item.get("avgExitPrice", entry_price))
                                        bybit_pnl = float(item.get("closedPnl", 0))
                                        break
                            except Exception:
                                pass
                            bybit_pnl_pct = (bybit_pnl / float(bybit_bal or 1)) * 100 if bybit_bal else 0
                            cur.execute("""
                                UPDATE paper_trades SET
                                    bybit_status = 'CLOSED',
                                    bybit_close_price = %s,
                                    bybit_pnl_usd = %s,
                                    bybit_pnl_pct = %s,
                                    bybit_close_time = NOW(),
                                    bybit_close_reason = 'TIME_EXIT'
                                WHERE id = %s
                            """, (round(close_price, 4), round(bybit_pnl, 4),
                                  round(bybit_pnl_pct, 4), trade_id))
                            print(f"  [Bybit] {symbol} TIME_EXIT recorded: close_price={close_price:.4f}, pnl=${bybit_pnl:.4f}")
                        else:
                            print(f"  [Bybit] {symbol} orphaned close FAILED: {close_result.get('retMsg')}")

            # Re-fetch positions after potential TIME_EXIT closes
            bybit_positions = _bybit_executor.get_positions()
            bybit_pos_map = {p["symbol"]: p for p in bybit_positions if float(p.get("size", 0)) > 0}

            # Re-fetch bybit_open_rows (TIME_EXIT closes are now bybit_status=CLOSED)
            cur.execute("""
                SELECT id, symbol, side, bybit_qty, bybit_order_id, bybit_entry_price,
                       bybit_account_balance, bybit_sl_price, bybit_tp_price,
                       status, exit_reason, created_at
                FROM paper_trades
                WHERE bybit_order_id IS NOT NULL AND bybit_status = 'OPEN'
            """)
            bybit_open_rows = cur.fetchall()

            # ── Critical #1: Detect Bybit SL/TP closes using closed-pnl endpoint ──
            for row in bybit_open_rows:
                trade_id, symbol, side, bybit_qty, order_id, entry_price, bybit_bal, sl_price, tp_price, pt_status, pt_exit_reason, created_at = row
                if symbol not in bybit_pos_map:
                    # Position closed on Bybit (SL/TP hit)
                    close_price = float(entry_price)  # fallback
                    close_reason = "SL/TP"
                    bybit_pnl = 0.0
                    try:
                        closed_list = _bybit_executor.get_closed_pnl(symbol)
                        # Match by entry price (1% tolerance) AND time (must be after our entry)
                        for item in closed_list:
                            avg_entry = float(item.get("avgEntryPrice", 0))
                            item_time = int(item.get("updatedTime", 0)) / 1000
                            if (avg_entry > 0 and abs(avg_entry - float(entry_price)) < 0.01 * float(entry_price)
                                    and item_time >= created_at.timestamp()):
                                close_price = float(item.get("avgExitPrice", entry_price))
                                bybit_pnl = float(item.get("closedPnl", 0))
                                break
                    except Exception:
                        pass

                    # Determine actual close reason from exit price vs SL/TP levels
                    is_long = side.upper() in ("LONG", "BUY")
                    sl_f = float(sl_price or 0)
                    tp_f = float(tp_price or 0)
                    if sl_f > 0 and tp_f > 0:
                        if is_long:
                            if abs(close_price - sl_f) < abs(close_price - tp_f):
                                close_reason = "SL_HIT"
                            else:
                                close_reason = "TP_HIT"
                        else:
                            if abs(close_price - sl_f) < abs(close_price - tp_f):
                                close_reason = "SL_HIT"
                            else:
                                close_reason = "TP_HIT"

                    # Fallback: calculate PnL manually if closed-pnl returned nothing
                    if bybit_pnl == 0.0 and close_price != float(entry_price):
                        bybit_qty_f = float(bybit_qty or 0)
                        bybit_pnl = (close_price - float(entry_price)) * bybit_qty_f if is_long else (float(entry_price) - close_price) * bybit_qty_f

                    bybit_pnl_pct = (bybit_pnl / float(bybit_bal or 1)) * 100 if bybit_bal else 0

                    cur.execute("""
                        UPDATE paper_trades SET
                            bybit_status = 'CLOSED',
                            bybit_close_price = %s,
                            bybit_pnl_usd = %s,
                            bybit_pnl_pct = %s,
                            bybit_close_time = NOW(),
                            bybit_close_reason = %s
                        WHERE id = %s
                    """, (round(close_price, 4), round(bybit_pnl, 4),
                          round(bybit_pnl_pct, 4), close_reason, trade_id))
                    print(f"  [Bybit] {symbol} closed on exchange: close_price={close_price:.4f}, pnl=${bybit_pnl:.4f} ({bybit_pnl_pct:.2f}%)")

            conn.commit()

            # Also run standard reconciliation for discrepancy reporting
            # Only compare paper_trades that HAVE bybit orders against Bybit positions
            cur.execute("""
                SELECT symbol, side, qty_contracts, status, id
                FROM paper_trades WHERE status = 'OPEN' AND bybit_order_id IS NOT NULL
            """)
            rows = cur.fetchall()
            local_trades = [
                {"symbol": r[0], "side": r[1], "qty_contracts": float(r[2] or 0), "status": r[3], "id": r[4]}
                for r in rows
            ]
            discrepancies = _bybit_executor.reconcile(local_trades)
            total_disc = sum(len(v) for v in discrepancies.values())
            if total_disc > 0:
                print(f"  [Reconcile] {total_disc} discrepancy(ies) found: {discrepancies}")
            else:
                print(f"  [Reconcile] Local state matches Bybit. {len(local_trades)} open position(s).")

            # Shadow validation: independent Bybit vs paper ledger comparison
            try:
                recon = reconcile_bybit_vs_paper(conn, _bybit_executor)
                if recon["discrepancies"]:
                    print(f"  [ShadowReconcile] {len(recon['discrepancies'])} discrepancy(ies): paper_only={recon['paper_only']}, bybit_only={recon['bybit_only']}")
                else:
                    print(f"  [ShadowReconcile] Paper and Bybit ledgers match. matched={recon['matched']}")
            except Exception as sr_e:
                print(f"  [ShadowReconcile] Error: {sr_e}")

            cur.close()
        except Exception as e:
            print(f"  [Reconcile] Error: {e}")

    # ── Step 7: Telegram alert for EXCELLENT signals ──
    excellent = [s for s in approved_signals if s.get("strength") == "EXCELLENT"]
    if excellent and send_telegram:
        for sig in excellent:
            msg = (
                f"<b>{TELEGRAM_BOT_NAME} — EXCELLENT Signal</b>\n"
                f"Symbol: {sig['symbol']}\n"
                f"Side: {sig.get('side', 'LONG')}\n"
                f"Strategy: {sig.get('strategy', 'trend_following')}\n"
                f"DQS: {sig.get('adjusted_score', 0)}/100\n"
                f"Tier: {sig['risk_tier']['tier']}\n"
                f"Time: {now.strftime('%H:%M')} UTC"
            )
            send_telegram(msg, chat_type="trading", parse_mode="HTML")

    # ── Step 8: Post-flight safety checks ──
    safety.post_flight_checks(now, approved_signals)

    # ── Step 8b: Symbol disable check ──
    _check_and_disable_symbols(conn)

    # ── Step 9: System-Wide Invariant Checks ──
    print("\n  Step 9 — Running invariant checks...")
    t0 = __import__('time').time()
    invariant_breaches = []
    cur = conn.cursor()

    try:
        # Inv 1: risk_ledger open count == paper_trades OPEN count
        cur.execute("SELECT COUNT(*) FROM paper_trades WHERE status = 'OPEN'")
        open_trades = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM risk_ledger WHERE is_open = TRUE")
        open_ledger = cur.fetchone()[0]
        if open_trades != open_ledger:
            invariant_breaches.append(f"INVARIANT BREACH: risk_ledger ({open_ledger}) != paper_trades ({open_trades})")
    except Exception as e:
        safety.log_error("invariant", f"Inv1 check failed: {e}")

    try:
        # Inv 2: No more than max_active_trades open trades
        max_active = PORTFOLIO["max_active_trades"]
        if open_trades > max_active:
            invariant_breaches.append(f"INVARIANT BREACH: active trades cap exceeded ({open_trades} > {max_active})")
    except Exception:
        pass

    try:
        # Inv 3: Total open risk <= 10% of balance
        cur.execute("SELECT COALESCE(SUM(dollar_risk), 0) FROM risk_ledger WHERE is_open = TRUE")
        total_risk = float(cur.fetchone()[0] or 0)
        cur.execute("SELECT balance FROM paper_account LIMIT 1")
        bal_row = cur.fetchone()
        bal = float(bal_row[0]) if bal_row else 0
        if bal > 0 and total_risk > bal * (PORTFOLIO["max_portfolio_risk_pct"] / 100):
            invariant_breaches.append(f"INVARIANT BREACH: portfolio risk {total_risk:.2f} > {PORTFOLIO['max_portfolio_risk_pct']}% of balance")
    except Exception as e:
        safety.log_error("invariant", f"Inv3 check failed: {e}")

    try:
        # Inv 4: signal_state has at most 6 ACTIVE/BOOSTED rows (one per symbol)
        cur.execute("SELECT COUNT(*) FROM signal_state WHERE status IN ('ACTIVE', 'BOOSTED')")
        state_count = cur.fetchone()[0]
        if state_count > 6:
            invariant_breaches.append(f"INVARIANT BREACH: signal_state has {state_count} active rows (max 6)")
    except Exception as e:
        safety.log_error("invariant", f"Inv4 check failed: {e}")

    try:
        # Inv 5: EXPIRED signals in signal_state also marked in high_confidence_signals
        cur.execute("""
            SELECT ss.symbol FROM signal_state ss
            JOIN high_confidence_signals hcs ON hcs.id = ss.signal_id
            WHERE ss.status = 'EXPIRED' AND (hcs.is_expired = FALSE OR hcs.is_expired IS NULL)
        """)
        mismatches = cur.fetchall()
        if mismatches:
            syms = ", ".join([r[0] for r in mismatches])
            invariant_breaches.append(f"INVARIANT BREACH: expiry mismatch for {syms}")
            # Auto-fix: sync expiry to high_confidence_signals
            cur.execute("""
                UPDATE high_confidence_signals hcs
                SET is_expired = TRUE,
                    expiry_reason = COALESCE(hcs.expiry_reason, ss.expiry_reason, 'Auto-synced from signal_state')
                FROM signal_state ss
                WHERE ss.signal_id = hcs.id
                  AND ss.status = 'EXPIRED'
                  AND (hcs.is_expired = FALSE OR hcs.is_expired IS NULL)
            """)
            conn.commit()
    except Exception:
        pass  # is_expired may not exist in older schema

    try:
        # Inv 6: DQS in range 0-100
        cur.execute("SELECT COUNT(*) FROM signal_state WHERE current_dqs < 0 OR current_dqs > 100")
        bad_dqs = cur.fetchone()[0]
        if bad_dqs > 0:
            invariant_breaches.append(f"INVARIANT BREACH: {bad_dqs} signals with DQS out of range")
    except Exception as e:
        safety.log_error("invariant", f"Inv6 check failed: {e}")

    if invariant_breaches:
        for b in invariant_breaches:
            print(f"  ! {b}")
            safety.log_error("invariant", b, is_critical=True)
        tracer.log("invariants", "WARN", duration_ms=int((__import__('time').time()-t0)*1000),
                   detail={"breaches": invariant_breaches})
    else:
        print("  All invariants OK")
        tracer.log("invariants", "OK", duration_ms=int((__import__('time').time()-t0)*1000))

    # Phase 4: Self-diagnostics — stuck runs_tracked alarm
    stuck_msg = tracer.check_stuck_runs_tracked()
    if stuck_msg:
        print(f"  [SelfDiag] {stuck_msg}")

    print(f"\n{'='*70}")
    print(f"  Iteration complete: {n_upserted} approved, {len(excellent)} excellent")
    print(f"{'='*70}")

    # Variance tracker: run once per day (check by date string)
    try:
        _today = now.strftime("%Y-%m-%d")
        if _today != getattr(run_iteration, "_last_variance_day", ""):
            from variance_tracker import run_variance_report
            run_variance_report(days=30)
            run_iteration._last_variance_day = _today
    except Exception as ve:
        print(f"  [VarianceTracker] Skipped: {ve}")

    return {
        "status": "OK",
        "run_time": now.isoformat(),
        "signals_scanned": len(new_signals),
        "signals_ranked": len(ranked),
        "signals_approved": len(approved_signals),
        "signals_excellent": len(excellent),
        "upserted": n_upserted,
        "invariant_breaches": len(invariant_breaches),
        "brain_available": brain is not None,
        "trace_nodes_logged": list(tracer._nodes_logged),
    }
