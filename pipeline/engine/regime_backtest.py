"""
Regime Re-Orchestration Backtest
================================
Replays historical enriched data through the full pipeline logic
(with and without regime-aware changes) to produce a comparison.

Usage:
    docker exec vlthr-pipeline python3 /app/engine/regime_backtest.py --start 2026-05-01 --end 2026-07-09
"""
import sys, os, json, argparse, math
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

import pandas as pd
import numpy as np

_engine = Path(__file__).resolve().parent
sys.path.insert(0, str(_engine))
sys.path.insert(0, str(_engine.parents[1] / "departments" / "strategy_research" / "BACKTESTER" / "strategy" / "signals"))
sys.path.insert(0, str(_engine.parents[1] / "departments" / "strategy_research" / "BACKTESTER" / "scripts" / "common"))

from portfolio_config import (
    SYMBOLS, DISABLED_SYMBOLS, get_symbol_params, get_risk_tier, get_sl_tp, get_sl_tp_pct,
    get_kelly_v7, cap_position_size, QTY_STEP, TAKER_FEE, MIN_NOTIONAL,
    DQS_THRESHOLDS, DQS_VETO_THRESHOLD, REGIME_MIN_DQS, REGIME_SL_TP_MULTIPLIERS,
    PORTFOLIO, MIN_RR_FLOOR, SIGNAL_LIFECYCLE,
)
from adaptive_scorer import score_signal_adaptive
from v2_filters import apply_v2_filters

LEVERAGE = 4
INIT_CAPITAL = 10000.0
FEE_TAKER = 0.00055
MAX_HOLD_HOURS = 12.0


def load_full_enriched(symbol, data_root):
    enriched_dir = data_root / symbol / "enriched" / "15m"
    if not enriched_dir.exists():
        return None
    files = sorted(enriched_dir.rglob("*.parquet"))
    if not files:
        return None
    dfs = [pd.read_parquet(f) for f in files if not f.read_bytes()[:1] == b'']
    if not dfs:
        return None
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    # ATR
    hl = df["high"] - df["low"]
    hc = (df["high"] - df["close"].shift()).abs()
    lc = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14, min_periods=14).mean()
    # RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(upper=0)
    rs = gain.ewm(span=14, adjust=False).mean() / loss.ewm(span=14, adjust=False).mean().replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    # ADX
    hd, ld = df["high"].diff(), (-df["low"]).diff()
    dm_p = hd.where((hd > ld) & (hd > 0), 0)
    dm_m = ld.where((ld > hd) & (ld > 0), 0)
    tr2 = df["high"] - df["low"]
    atr2 = tr2.rolling(14, min_periods=14).mean()
    di_p = (dm_p.rolling(14).mean() / atr2) * 100
    di_m = (dm_m.rolling(14).mean() / atr2) * 100
    dx = (di_p - di_m).abs() / (di_p + di_m).replace(0, np.nan) * 100
    df["adx"] = dx.rolling(14).mean()
    # Session
    def sess(ts):
        h = ts.hour
        if 0 <= h < 8: return "asian"
        elif 8 <= h < 12: return "london"
        elif 12 <= h < 17: return "ny_open"
        else: return "ny_late"
    df["session"] = df["timestamp"].apply(sess)
    # 4h context merge
    ctx_dir = data_root / symbol / "4h"
    if ctx_dir.exists():
        cf = sorted(ctx_dir.rglob("*.parquet"))
        if cf:
            cd = [pd.read_parquet(f) for f in cf]
            ctx = pd.concat(cd, ignore_index=True)
            ctx["timestamp"] = pd.to_datetime(ctx["timestamp"], utc=True)
            ctx = ctx.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
            hd4, ld4 = ctx["high"].diff(), (-ctx["low"]).diff()
            dm_p4 = hd4.where((hd4 > ld4) & (hd4 > 0), 0)
            dm_m4 = ld4.where((ld4 > hd4) & (ld4 > 0), 0)
            tr4 = ctx["high"] - ctx["low"]
            atr4 = tr4.rolling(14, min_periods=14).mean()
            di_p4 = (dm_p4.rolling(14).mean() / atr4) * 100
            di_m4 = (dm_m4.rolling(14).mean() / atr4) * 100
            dx4 = (di_p4 - di_m4).abs() / (di_p4 + di_m4).replace(0, np.nan) * 100
            ctx["adx"] = dx4.rolling(14).mean()
            ctx["log_ret_4h"] = np.log(ctx["close"] / ctx["close"].shift(1))
            ctx["bull_4h"] = (ctx["close"] > ctx["close"].shift(1)).astype(int)
            ctx = ctx[["timestamp","adx","log_ret_4h","bull_4h","close"]].rename(columns={
                "adx":"ctx_adx","log_ret_4h":"ctx_log_ret_4h","bull_4h":"ctx_bull_4h","close":"ctx_close_4h"})
            df["timestamp"] = df["timestamp"].astype("datetime64[ns, UTC]")
            ctx["timestamp"] = ctx["timestamp"].astype("datetime64[ns, UTC]")
            df = pd.merge_asof(df.sort_values("timestamp"), ctx.sort_values("timestamp"),
                               on="timestamp", direction="backward").sort_values("timestamp").reset_index(drop=True)
    return df


def load_daily(symbol, data_root):
    d = data_root / symbol / "1d"
    if not d.exists(): return None
    fs = sorted(d.rglob("*.parquet"))
    if not fs: return None
    df = pd.concat([pd.read_parquet(f) for f in fs], ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    delta = df["close"].diff()
    g, l = delta.clip(lower=0), (-delta).clip(upper=0)
    rs = g.ewm(span=14, adjust=False).mean() / l.ewm(span=14, adjust=False).mean().replace(0, np.nan)
    df["rsi_daily"] = 100 - (100 / (1 + rs))
    return df


def daily_bias_at(ddf, ts):
    if ddf is None or len(ddf) < 50: return None
    s = ddf[ddf["timestamp"] <= ts]
    if len(s) < 50: return None
    r = s.iloc[-1]
    return {"bias": "BULL" if float(r["close"]) > float(r["ema_50"]) else "BEAR",
            "rsi": float(r["rsi_daily"]) if not pd.isna(r["rsi_daily"]) else 50.0}


def btc_mom_at(all_data, ts, lb=6):
    b = all_data.get("BTCUSDT")
    if b is None: return 0.0
    s = b[b["timestamp"] <= ts]
    if len(s) < lb + 1: return 0.0
    m = float(s["close"].pct_change(lb).iloc[-1] * 100)
    return m if not pd.isna(m) else 0.0


def sim_exit(trade, bar):
    sl, tp = trade["sl"], trade["tp"]
    lo, hi = float(bar["low"]), float(bar["high"])
    if trade["side"] == "LONG":
        if lo <= sl: return "SL_HIT", sl
        if hi >= tp: return "TP_HIT", tp
    else:
        if hi >= sl: return "SL_HIT", sl
        if lo <= tp: return "TP_HIT", tp
    return None, None


def pnl(trade, exit_p):
    e, q = trade["entry"], trade["qty"]
    g = (exit_p - e) * q if trade["side"] == "LONG" else (e - exit_p) * q
    return g - (e + exit_p) * q * FEE_TAKER


def run_backtest(start_str, end_str, regime_aware=True):
    data_root = _engine.parents[1] / "data" / "bybit"
    if not data_root.exists():
        from portfolio_config import DATA_ROOT as CDR
        data_root = CDR

    start = pd.to_datetime(start_str, utc=True)
    end = pd.to_datetime(end_str, utc=True)
    mode = "REGIME-AWARE" if regime_aware else "BASELINE"
    print(f"\n{'='*70}\n  Backtest — {mode}\n  {start.date()} → {end.date()}\n  Init: ${INIT_CAPITAL:,.0f} | Lev: {LEVERAGE}x\n{'='*70}")

    all_data, daily = {}, {}
    for s in SYMBOLS:
        df = load_full_enriched(s, data_root)
        if df is not None and len(df):
            all_data[s] = df
            daily[s] = load_daily(s, data_root)
            print(f"  {s}: {len(df)} bars")

    if not all_data:
        print("  No data — aborting."); return None

    ts_list = sorted(set(pd.concat([d["timestamp"] for d in all_data.values()])))
    ts_list = [t for t in ts_list if start <= t <= end]
    print(f"  Timestamps: {len(ts_list)}")

    balance = INIT_CAPITAL
    open_t, pending_t, closed_t = [], [], []
    gate_rej = defaultdict(int)
    daily_cnt = defaultdict(int)
    cur_day = None

    rmin_dqs = REGIME_MIN_DQS if regime_aware else {k: 50 for k in REGIME_MIN_DQS}

    for i, ts in enumerate(ts_list):
        day = ts.date()
        if day != cur_day:
            cur_day = day
            daily_cnt = defaultdict(int)

        # 1. Check open trades
        for t in list(open_t):
            df = all_data.get(t["symbol"])
            if df is None: continue
            bar = df[df["timestamp"] == ts]
            if len(bar) == 0: continue
            bar = bar.iloc[0]
            hit, ep = sim_exit(t, bar)
            if hit:
                p = pnl(t, ep)
                balance += p
                t.update(exit_price=ep, exit_reason=hit, exit_time=ts, pnl=p,
                          hours_held=(ts - t["open_time"]).total_seconds()/3600)
                closed_t.append(t); open_t.remove(t); continue
            hh = (ts - t["open_time"]).total_seconds()/3600
            if hh >= MAX_HOLD_HOURS:
                cp = float(bar["close"]); p = pnl(t, cp)
                balance += p
                t.update(exit_price=cp, exit_reason="TIME_EXIT", exit_time=ts, pnl=p, hours_held=hh)
                closed_t.append(t); open_t.remove(t)

        # 2. Promote PENDING → OPEN (1 bar delay)
        for t in list(pending_t):
            if (ts - t["pending_time"]).total_seconds() < 900: continue
            df = all_data.get(t["symbol"])
            if df is None:
                t.update(exit_reason="EXPIRED", exit_time=ts); closed_t.append(t); pending_t.remove(t); continue
            sl = df[df["timestamp"] <= ts].tail(500)
            if len(sl) < 50:
                t.update(exit_reason="EXPIRED", exit_time=ts); closed_t.append(t); pending_t.remove(t); continue
            lb = sl.iloc[-1]
            sess = lb.get("session", "")
            dqs, brk = score_signal_adaptive(t["symbol"], sl, None, sess)
            if dqs < 50 or dqs >= DQS_VETO_THRESHOLD:
                t.update(exit_reason="EXPIRED", exit_time=ts); closed_t.append(t); pending_t.remove(t); continue
            cp = float(lb["close"])
            atr = float(lb.get("atr", 0)) if not pd.isna(lb.get("atr")) else 0
            sp = get_symbol_params(t["symbol"])
            rv = brk.get("regime", "MIXED") if regime_aware else None
            if sp.get("sl_mode") == "pct":
                sl_p, tp_p, _ = get_sl_tp_pct(cp, sp["sl_pct"], sp["tp_pct"], side=t["side"], regime=rv)
            else:
                if atr <= 0:
                    t.update(exit_reason="EXPIRED", exit_time=ts); closed_t.append(t); pending_t.remove(t); continue
                sl_p, tp_p, _ = get_sl_tp(cp, atr, int(dqs), side=t["side"], regime=rv)
            if sl_p is None:
                t.update(exit_reason="EXPIRED", exit_time=ts); closed_t.append(t); pending_t.remove(t); continue
            t.update(entry=cp, sl=sl_p, tp=tp_p, dqs=dqs, open_time=ts,
                     regime=rv or "MIXED", strategy=brk.get("strategy", sp.get("strategy","trend_following")))
            open_t.append(t); pending_t.remove(t)

        # 3. Scan for new signals
        act_syms = [t["symbol"] for t in open_t]
        act_sides = [t["side"] for t in open_t]
        if len(open_t) >= PORTFOLIO["max_active_trades"]: continue
        if daily_cnt[day] >= PORTFOLIO["max_daily_trades"]: continue

        bm = btc_mom_at(all_data, ts)

        for sym in SYMBOLS:
            if sym in DISABLED_SYMBOLS or sym in act_syms: continue
            if len(open_t) >= PORTFOLIO["max_active_trades"]: break
            df = all_data.get(sym)
            if df is None: continue
            sl = df[df["timestamp"] <= ts].tail(500)
            if len(sl) < 55: continue
            lb = sl.iloc[-1]
            if lb["timestamp"] != ts: continue

            sess = lb.get("session", "")
            dqs, brk = score_signal_adaptive(sym, sl, None, sess)
            if dqs < DQS_THRESHOLDS["min_to_track"]: continue
            if dqs >= DQS_VETO_THRESHOLD:
                gate_rej["GATE_0_V7_VETO"] += 1; continue

            side = brk.get("direction", "LONG")
            strat = brk.get("strategy", "trend_following")
            db = daily_bias_at(daily.get(sym), ts)
            v2p, strat, tpm, v2r = apply_v2_filters(sym, side, strat, sl, db, bm)
            if not v2p:
                gate_rej["V2_FILTER"] += 1; continue

            orig_strat = brk.get("strategy", "trend_following")
            if strat != orig_strat:
                lr = float(lb.get("ctx_log_ret_4h", 0)) if not pd.isna(lb.get("ctx_log_ret_4h")) else 0
                rv = float(lb.get("rsi", 50)) if not pd.isna(lb.get("rsi")) else 50
                if strat == "trend_following":
                    side = "LONG" if lr >= 0 else "SHORT"
                else:
                    sp = get_symbol_params(sym)
                    ob, os_ = sp.get("rsi_overbought", 70), sp.get("rsi_oversold", 30)
                    side = "SHORT" if rv >= ob else ("LONG" if os_ > 0 and rv <= os_ else ("LONG" if lr >= 0 else "SHORT"))
                brk["direction"] = side
            brk["strategy"] = strat

            rv = brk.get("regime", "MIXED")
            if regime_aware and dqs < rmin_dqs.get(rv, 50):
                gate_rej["GATE_0C_REGIME"] += 1; continue
            if sess not in ("london", "ny_open", "ny_late"):
                gate_rej["SESSION_SKIP"] += 1; continue
            if act_sides.count(side.upper()) >= PORTFOLIO.get("max_same_side", 3):
                gate_rej["GATE_4D_DIRECTION"] += 1; continue
            if len(open_t) + len(pending_t) >= PORTFOLIO["max_active_trades"]:
                gate_rej["GATE_4A_CORR"] += 1; continue

            cp = float(lb["close"])
            atr = float(lb.get("atr", 0)) if not pd.isna(lb.get("atr")) else 0
            sp = get_symbol_params(sym)
            if sp.get("sl_mode") == "pct":
                sl_p, tp_p, rr = get_sl_tp_pct(cp, sp["sl_pct"], sp["tp_pct"], side=side,
                                               regime=rv if regime_aware else None)
            else:
                if atr <= 0: continue
                sl_p, tp_p, rr = get_sl_tp(cp, atr, int(dqs), side=side, regime=rv if regime_aware else None)
            if sl_p is None: continue

            if tpm != 1.0 and sp.get("sl_mode") != "pct":
                td = abs(tp_p - cp); ntd = td * tpm
                tp_p = round(cp - ntd, 6) if side.upper() == "SHORT" else round(cp + ntd, 6)

            tier = get_risk_tier(int(dqs))
            km = get_kelly_v7(dqs, strat)
            rpct = tier["risk_pct"] * km
            sld = abs(cp - sl_p)
            dr = balance * (rpct / 100)
            qty = (dr / sld) if sld > 0 else 0
            ps = cap_position_size(cp * qty, balance)
            qty = ps / cp if cp > 0 else 0
            step = QTY_STEP.get(sym, 0.001)
            qty = int(qty / step) * step if step > 0 else qty
            if cp * qty < MIN_NOTIONAL: continue
            dr = sld * qty
            er = min(balance * (rpct/100) * km, balance * (PORTFOLIO["max_portfolio_risk_pct"]/100))
            tor = sum(x.get("dollar_risk", 0) for x in open_t)
            if tor + er > balance * (PORTFOLIO["max_portfolio_risk_pct"]/100):
                gate_rej["GATE_4C_RISK"] += 1; continue

            pending_t.append({
                "symbol": sym, "side": side, "entry": cp, "sl": sl_p, "tp": tp_p, "rr": rr,
                "qty": qty, "dollar_risk": dr, "dqs": dqs, "regime": rv, "strategy": strat,
                "session": sess, "atr": atr, "pending_time": ts, "open_time": None,
                "sl_dist_pct": round(sld/cp*100, 4) if cp > 0 else 0,
                "tp_dist_pct": round(abs(tp_p-cp)/cp*100, 4) if cp > 0 else 0,
            })
            act_syms.append(sym); act_sides.append(side); daily_cnt[day] += 1

        if i % 96 == 0 and i > 0:
            print(f"  Day {i//96}: Eq=${balance:,.0f} Open={len(open_t)} Pend={len(pending_t)} Closed={len(closed_t)}")

    # Close remaining
    for t in open_t:
        df = all_data.get(t["symbol"])
        if df is not None and ts_list:
            lb = df[df["timestamp"] <= ts_list[-1]]
            if len(lb):
                cp = float(lb.iloc[-1]["close"]); p = pnl(t, cp)
                balance += p
                t.update(exit_price=cp, exit_reason="TIME_EXIT", exit_time=ts_list[-1], pnl=p,
                          hours_held=(ts_list[-1]-t["open_time"]).total_seconds()/3600)
                closed_t.append(t)
    open_t.clear()
    for t in pending_t:
        t["exit_reason"] = "EXPIRED"; t["exit_time"] = ts_list[-1] if ts_list else end
        closed_t.append(t)
    pending_t.clear()

    # Metrics
    rt = [t for t in closed_t if t.get("exit_reason") in ("SL_HIT","TP_HIT","TIME_EXIT")]
    ex = [t for t in closed_t if t.get("exit_reason") == "EXPIRED"]
    wins = [t for t in rt if t["pnl"] > 0]
    losses = [t for t in rt if t["pnl"] <= 0]
    tpnl = sum(t["pnl"] for t in rt)
    wr = len(wins)/len(rt)*100 if rt else 0
    aw = np.mean([t["pnl"] for t in wins]) if wins else 0
    al = np.mean([t["pnl"] for t in losses]) if losses else 0
    pf = sum(t["pnl"] for t in wins)/abs(sum(t["pnl"] for t in losses)) if losses else 999
    ec = defaultdict(int)
    for t in rt: ec[t["exit_reason"]] += 1

    # Per-regime
    rs = {}
    for t in rt:
        r = t.get("regime","MIXED")
        if r not in rs: rs[r] = {"trades":0,"wins":0,"pnl":0,"sl_hit":0,"tp_hit":0,"time_exit":0,"sl_d":[],"tp_d":[]}
        rs[r]["trades"] += 1; rs[r]["pnl"] += t["pnl"]
        if t["pnl"] > 0: rs[r]["wins"] += 1
        rs[r][{"SL_HIT":"sl_hit","TP_HIT":"tp_hit","TIME_EXIT":"time_exit"}[t["exit_reason"]]] += 1
        rs[r]["sl_d"].append(t.get("sl_dist_pct",0)); rs[r]["tp_d"].append(t.get("tp_dist_pct",0))

    # Per-symbol
    ss = {}
    for t in rt:
        s = t["symbol"]
        if s not in ss: ss[s] = {"trades":0,"wins":0,"pnl":0,"sl_hit":0}
        ss[s]["trades"] += 1; ss[s]["pnl"] += t["pnl"]
        if t["pnl"] > 0: ss[s]["wins"] += 1
        if t["exit_reason"] == "SL_HIT": ss[s]["sl_hit"] += 1

    # Sharpe/Sortino
    if rt:
        pnls = [t["pnl"] for t in rt]
        ap, sp = np.mean(pnls), np.std(pnls)
        sharpe = ap/sp*np.sqrt(96*365/len(rt)) if sp > 0 else 0
        ds = [p for p in pnls if p < 0]
        sortino = ap/np.std(ds)*np.sqrt(96*365/len(rt)) if ds and np.std(ds) > 0 else 0
    else:
        sharpe = sortino = 0

    # Max DD
    eq = [INIT_CAPITAL]
    for t in sorted(rt, key=lambda x: x["exit_time"]): eq.append(eq[-1]+t["pnl"])
    pk = eq[0]; mdd = 0
    for e in eq:
        if e > pk: pk = e
        d = (pk-e)/pk*100 if pk > 0 else 0
        if d > mdd: mdd = d

    ah = np.mean([t["hours_held"] for t in rt]) if rt else 0

    res = {
        "mode": mode, "start": str(start.date()), "end": str(end.date()),
        "total_trades": len(rt), "expired": len(ex), "win_rate": round(wr,2),
        "avg_win": round(aw,2), "avg_loss": round(al,2), "net_pnl": round(tpnl,2),
        "final_balance": round(balance,2), "profit_factor": round(pf,2) if pf < 999 else 999,
        "max_drawdown": round(mdd,2), "sharpe": round(sharpe,2), "sortino": round(sortino,2),
        "avg_hold_hours": round(ah,2), "exit_counts": dict(ec), "gate_rejections": dict(gate_rej),
        "regime_stats": {k: {"trades":v["trades"],"win_rate":round(v["wins"]/v["trades"]*100,2) if v["trades"] else 0,
                             "avg_pnl":round(v["pnl"]/v["trades"],2) if v["trades"] else 0,
                             "sl_hit":v["sl_hit"],"tp_hit":v["tp_hit"],"time_exit":v["time_exit"],
                             "avg_sl_dist_pct":round(np.mean(v["sl_d"]),4) if v["sl_d"] else 0,
                             "avg_tp_dist_pct":round(np.mean(v["tp_d"]),4) if v["tp_d"] else 0} for k,v in rs.items()},
        "symbol_stats": {k: {"trades":v["trades"],"win_rate":round(v["wins"]/v["trades"]*100,2) if v["trades"] else 0,
                             "net_pnl":round(v["pnl"],2),"sl_hit":v["sl_hit"]} for k,v in ss.items()},
        "trades": rt + ex,
    }

    print(f"\n{'='*70}\n  Results — {mode}\n{'='*70}")
    print(f"  Trades: {len(rt)} (expired: {len(ex)})")
    print(f"  Win rate: {wr:.1f}% | Net PnL: ${tpnl:,.2f}")
    print(f"  PF: {pf:.2f} | MaxDD: {mdd:.2f}% | Sharpe: {sharpe:.2f}")
    print(f"  Exits: {dict(ec)}")
    print(f"  Gates: {dict(gate_rej)}")
    return res


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2026-05-01")
    p.add_argument("--end", default="2026-07-09")
    args = p.parse_args()

    baseline = run_backtest(args.start, args.end, regime_aware=False)
    regime = run_backtest(args.start, args.end, regime_aware=True)

    out = Path("/tmp/regime_backtest_results")
    out.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    results = {"baseline": baseline, "regime_aware": regime}
    with open(out / f"results_{ts}.json", "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n  Saved to {out / f'results_{ts}.json'}")
