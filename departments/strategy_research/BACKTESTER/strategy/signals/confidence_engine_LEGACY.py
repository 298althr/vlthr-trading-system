"""
PullbackToTrend — Confidence Engine (LEGACY BACKUP)
Scores every potential signal 0-100 using OHLCV + Orderbook + OI + Funding.

Usage:
    from confidence_engine import score_signal
    score = score_signal(symbol, enriched_row, ob_features)

Thresholds:
    >= 80 : HIGH CONFIDENCE — record to DB, trade it
    60-79 : MEDIUM — set alert, monitor, do not trade
    < 60  : NO SIGNAL — ignore
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "common"))

from data_loader import load_symbol_tf, REPO_ROOT

# D5 Sharpe scores per symbol (from backtest, used as quality multiplier)
SYMBOL_QUALITY = {
    "BTCUSDT": 5.18,
    "ETHUSDT": 5.81,
    "SOLUSDT": 6.16,
    "XRPUSDT": 4.72,
    "BNBUSDT": 5.89,
    "DOGEUSDT": 5.28,
}

PARAMS = {"rsi_threshold": 40, "sl_mult": 1.5, "tp_mult": 2.0, "adx_min": 25.0}


def load_enriched(symbol, timeframe="15m"):
    """Load enriched parquet with OI and funding merged."""
    p = REPO_ROOT / f"data/bybit/{symbol}/enriched/{timeframe}"
    files = sorted(p.rglob("*.parquet"))
    if not files:
        return None
    dfs = [pd.read_parquet(f) for f in files]
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return df


def load_orderbook_features(symbol):
    """Compute spread, depth, imbalance from latest orderbook snapshot."""
    p = REPO_ROOT / f"data/bybit/{symbol}/orderbook"
    files = sorted(p.rglob("*.parquet"))
    if not files:
        return None

    df = pd.read_parquet(files[-1])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

    # Take the most recent snapshot
    latest_ts = df["timestamp"].max()
    snap = df[df["timestamp"] == latest_ts]

    if len(snap) == 0:
        return None

    bids = snap[snap["side"] == "bid"].sort_values("level")
    asks = snap[snap["side"] == "ask"].sort_values("level")

    if len(bids) == 0 or len(asks) == 0:
        return None

    best_bid = bids["price"].iloc[0]
    best_ask = asks["price"].iloc[0]
    mid = (best_bid + best_ask) / 2
    spread = best_ask - best_bid
    spread_pct = (spread / mid) * 100

    # Top 5 depth in notional USD
    bids["notional"] = bids["price"] * bids["size"]
    asks["notional"] = asks["price"] * asks["size"]
    bid_depth = bids.head(5)["notional"].sum()
    ask_depth = asks.head(5)["notional"].sum()
    total_depth = bid_depth + ask_depth

    imbalance = ((bid_depth - ask_depth) / total_depth * 100) if total_depth > 0 else 0

    return {
        "spread_pct": spread_pct,
        "depth_usd": total_depth,
        "bid_depth_usd": bid_depth,
        "ask_depth_usd": ask_depth,
        "imbalance_pct": imbalance,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "timestamp": latest_ts,
    }


def score_technical(rsi, adx, log_ret_4h):
    """Technical score: 0-40 points."""
    # RSI depth (max 15)
    if rsi < 30:
        rsi_score = 15
    elif rsi < 35:
        rsi_score = 10
    elif rsi < 40:
        rsi_score = 5
    else:
        rsi_score = 0

    # ADX strength (max 15)
    if adx >= 50:
        adx_score = 15
    elif adx >= 40:
        adx_score = 12
    elif adx >= 30:
        adx_score = 8
    elif adx >= 25:
        adx_score = 4
    else:
        adx_score = 0

    # 4h momentum (max 10)
    if log_ret_4h >= 0.01:
        mom_score = 10
    elif log_ret_4h >= 0.005:
        mom_score = 7
    elif log_ret_4h >= 0.002:
        mom_score = 4
    elif log_ret_4h > 0:
        mom_score = 1
    else:
        mom_score = 0

    return rsi_score + adx_score + mom_score


def score_market_structure(ob_features):
    """Orderbook score: 0-30 points."""
    if ob_features is None:
        return 0, {"note": "No orderbook data"}

    spread = ob_features["spread_pct"]
    depth = ob_features["depth_usd"]
    imbalance = ob_features["imbalance_pct"]

    # Spread (max 10) — tighter = better
    if spread < 0.01:
        spread_score = 10
    elif spread < 0.05:
        spread_score = 7
    elif spread < 0.1:
        spread_score = 3
    else:
        spread_score = 0

    # Depth (max 10) — deeper = better
    if depth >= 500_000:
        depth_score = 10
    elif depth >= 200_000:
        depth_score = 7
    elif depth >= 100_000:
        depth_score = 4
    else:
        depth_score = 0

    # Imbalance (max 10) — more bids than asks = bullish
    if imbalance >= 20:
        imb_score = 10
    elif imbalance >= 10:
        imb_score = 6
    elif imbalance >= 5:
        imb_score = 3
    else:
        imb_score = 0

    return spread_score + depth_score + imb_score, {
        "spread_pct": round(spread, 4),
        "depth_usd": round(depth, 2),
        "imbalance_pct": round(imbalance, 2),
        "spread_score": spread_score,
        "depth_score": depth_score,
        "imb_score": imb_score,
    }


def score_funding_oi(symbol, enriched_df, idx=-1):
    """Funding + OI score: 0-20 points."""
    row = enriched_df.iloc[idx]

    funding = float(row.get("funding_rate", 0)) if not pd.isna(row.get("funding_rate")) else 0
    oi_now = float(row.get("open_interest", 0)) if not pd.isna(row.get("open_interest")) else 0

    # OI delta vs 96 bars ago (24h of 15m data)
    oi_delta = 0
    oi_score = 0
    if len(enriched_df) >= 97:
        oi_old = float(enriched_df.iloc[idx - 96].get("open_interest", oi_now))
        if oi_old > 0:
            oi_delta = ((oi_now - oi_old) / oi_old) * 100
            if oi_delta >= 5:
                oi_score = 10
            elif oi_delta >= 0:
                oi_score = 6
            else:
                oi_score = 0

    # Funding score (max 10) — negative = longs pay shorts = bullish
    if funding < 0:
        fund_score = 10  # Very bullish for long entry
    elif funding < 0.0001:
        fund_score = 7
    elif funding < 0.0005:
        fund_score = 4
    else:
        fund_score = 0

    return oi_score + fund_score, {
        "funding_rate": funding,
        "oi_now": oi_now,
        "oi_delta_pct": round(oi_delta, 3) if isinstance(oi_delta, (int, float, np.floating)) else 0,
        "oi_score": oi_score,
        "fund_score": fund_score,
    }


def score_session_symbol(session, symbol):
    """Session + symbol quality: 0-10 points."""
    # Session (max 5)
    if session in {"london", "ny_late"}:
        sess_score = 5
    else:
        sess_score = 0

    # Symbol quality (max 5)
    sharpe = SYMBOL_QUALITY.get(symbol, 0)
    if sharpe >= 5.5:
        sym_score = 5
    elif sharpe >= 4.5:
        sym_score = 3
    else:
        sym_score = 1

    return sess_score + sym_score, {"session_score": sess_score, "symbol_score": sym_score, "sharpe": sharpe}


def score_signal(symbol, enriched_df, ob_features=None, session="", idx=-1, rsi_override=None, adx_override=None, log_ret_override=None):
    """
    Main scoring function. Returns (total_score, breakdown_dict).
    total_score: 0-100
    
    Use *_override params when enriched_df lacks rsi/adx columns
    (e.g. raw enriched parquet only has OHLCV + OI + funding).
    """
    row = enriched_df.iloc[idx]
    if rsi_override is not None:
        rsi = float(rsi_override)
    else:
        rsi = float(row.get("rsi", 100)) if not pd.isna(row.get("rsi")) else 100
    if adx_override is not None:
        adx = float(adx_override)
    else:
        adx = float(row.get("adx", 0)) if not pd.isna(row.get("adx")) else 0
    if log_ret_override is not None:
        log_ret = float(log_ret_override)
    else:
        log_ret = float(row.get("ctx_log_ret_4h", 0)) if not pd.isna(row.get("ctx_log_ret_4h")) else 0

    tech = score_technical(rsi, adx, log_ret)
    struct, struct_meta = score_market_structure(ob_features)
    f_oi, f_oi_meta = score_funding_oi(symbol, enriched_df, idx)
    sess, sess_meta = score_session_symbol(session, symbol)

    total = tech + struct + f_oi + sess

    return total, {
        "total": total,
        "technical": tech,
        "market_structure": struct,
        "funding_oi": f_oi,
        "session_symbol": sess,
        "rsi": round(rsi, 2),
        "adx": round(adx, 2),
        "log_ret_4h": round(log_ret, 6),
        "struct_meta": struct_meta,
        "funding_oi_meta": f_oi_meta,
        "session_meta": sess_meta,
        "grade": "HIGH" if total >= 75 else ("MEDIUM" if total >= 50 else "LOW"),
        "tradeable": total >= 75,
    }


def classify_and_score(symbol, session=""):
    """Load data, score, return full report dict."""
    df = load_enriched(symbol)
    if df is None or len(df) == 0:
        return None

    ob = load_orderbook_features(symbol)

    total, breakdown = score_signal(symbol, df, ob, session)

    last = df.iloc[-1]
    price = float(last["close"])
    atr = float(last.get("atr", 0)) if not pd.isna(last.get("atr")) else 0
    if atr == 0:
        # compute simple atr if not present
        tr = max(last["high"] - last["low"],
                 abs(last["high"] - df.iloc[-2]["close"]),
                 abs(last["low"] - df.iloc[-2]["close"]))
        atr = tr

    return {
        "symbol": symbol,
        "timestamp": last["timestamp"],
        "price": round(price, 4),
        "atr": round(atr, 4),
        "session": session,
        **breakdown,
    }


if __name__ == "__main__":
    for sym in ["BTCUSDT", "SOLUSDT", "ETHUSDT"]:
        r = classify_and_score(sym, session="london")
        print(f"\n{sym}: {r['total']}/100  ({r['grade']})")
        print(f"  Technical: {r['technical']}/40 | Market Structure: {r['market_structure']}/30 | Funding/OI: {r['funding_oi']}/20 | Session/Symbol: {r['session_symbol']}/10")
