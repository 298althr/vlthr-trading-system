"""
VLTHR Decision Quality System (DQS) — Confidence Engine
=======================================================
Scores every potential signal 0-100 using the MASTER_PROMPT DQS formula:

    DQS = (0.40 x Technical Score) + (0.30 x Structure Score) + (0.30 x Context Score)

Each domain score is independently normalised to 0-100.

Usage:
    from confidence_engine import score_signal, load_enriched, load_orderbook_features
    dqs, breakdown = score_signal(symbol, enriched_df, ob_features, session)

Thresholds (from portfolio_config):
    EXCELLENT (81+):  Up to 4.0% risk per trade
    GOOD    (66-80):  Up to 2.5% risk per trade
    FAIR    (50-65):  Up to 1.5% risk per trade
    WEAK    (< 50):   Not tracked
"""
import sys
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "common"))

from data_loader import load_symbol_tf, REPO_ROOT
from indicators import add_atr, add_rsi, add_adx, add_ema

# Import single source of truth
sys.path.insert(0, str(Path(__file__).resolve().parents[5] / "paper_trade_unzipped" / "vlthr-signal-dashboard" / "engine"))
from portfolio_config import SYMBOL_QUALITY, get_symbol_params

PARAMS = {"rsi_threshold": 40, "sl_mult": 1.5, "tp_mult": 2.0, "adx_min": 25.0}


# ── ENRICHED DATA LOADER (pre-computed parquet with on-the-fly fallback) ──

def load_enriched(symbol, timeframe="15m"):
    """
    Load enriched DataFrame from pre-computed enriched parquet (built by bybit_enrich_core).
    Adds indicators (RSI/ADX/ATR), session classification, and 4h context on top.
    Falls back to on-the-fly enrichment from raw parquet if pre-computed is missing.
    """
    df = _load_precomputed_enriched(symbol, timeframe)
    if df is None:
        print(f"[confidence_engine] No pre-computed enriched for {symbol}/{timeframe}, falling back to on-the-fly")
        df = _build_enriched_onthefly(symbol, timeframe)

    if df is None or len(df) == 0:
        return None

    # Compute indicators on top of enriched data
    df = add_atr(df, 14)
    df = add_rsi(df, 14)
    df = add_adx(df, 14)

    # Add session classification
    from indicators import classify_session
    df["session"] = df["timestamp"].apply(classify_session)

    # Merge 4h context (ADX, log return, bull flag)
    ctx_df = _load_4h_context(symbol)
    if ctx_df is not None and len(ctx_df):
        df["timestamp"] = df["timestamp"].astype("datetime64[ns, UTC]")
        ctx_df["timestamp"] = ctx_df["timestamp"].astype("datetime64[ns, UTC]")
        df = pd.merge_asof(
            df.sort_values("timestamp"),
            ctx_df.sort_values("timestamp"),
            on="timestamp",
            direction="backward"
        ).sort_values("timestamp").reset_index(drop=True)

    # Slice to last N bars for pipeline efficiency
    MAX_CACHED_BARS = 500
    if len(df) > MAX_CACHED_BARS:
        df = df.iloc[-MAX_CACHED_BARS:].reset_index(drop=True)

    return df


def _load_precomputed_enriched(symbol, timeframe="15m"):
    """Load pre-computed enriched parquet produced by bybit_enrich_core."""
    enriched_dir = REPO_ROOT / f"data/bybit/{symbol}/enriched/{timeframe}"
    if not enriched_dir.exists():
        return None
    files = sorted(enriched_dir.rglob("*.parquet"))
    if not files:
        return None
    dfs = []
    for f in files:
        try:
            df = pd.read_parquet(f)
            if not df.empty and "timestamp" in df.columns:
                dfs.append(df)
        except Exception:
            continue
    if not dfs:
        return None
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    # Input qualification
    REQUIRED_COLS = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        return None
    for c in ["open", "high", "low", "close"]:
        if df[c].isna().any():
            return None
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        return None

    # Guard: no future timestamps
    now_utc = pd.Timestamp.now(tz="UTC")
    if (df["timestamp"] > now_utc).any():
        return None

    return df


def _build_enriched_onthefly(symbol, timeframe="15m"):
    """Fallback: build enriched DataFrame on-the-fly from raw parquet."""
    try:
        df = load_symbol_tf(symbol, timeframe)
    except Exception as e:
        print(f"[confidence_engine] Failed to load raw {timeframe} for {symbol}: {e}")
        return None

    REQUIRED_COLS = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        print(f"[confidence_engine] {symbol}: missing required columns {missing}")
        return None
    for c in ["open", "high", "low", "close"]:
        if df[c].isna().any():
            print(f"[confidence_engine] {symbol}: NaN found in {c}")
            return None
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        print(f"[confidence_engine] {symbol}: non-positive price values detected")
        return None

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    now_utc = pd.Timestamp.now(tz="UTC")
    if (df["timestamp"] > now_utc).any():
        print(f"[confidence_engine] {symbol}: future timestamps detected")
        return None

    # Merge funding rate
    funding_df = _load_funding(symbol)
    if funding_df is not None and len(funding_df):
        df = _merge_asof_nearest(df, funding_df, "timestamp", "funding_rate")

    # Merge open interest
    oi_df = _load_open_interest(symbol)
    if oi_df is not None and len(oi_df):
        df = _merge_asof_nearest(df, oi_df, "timestamp", "open_interest")

    return df


def _load_4h_context(symbol):
    """Load 4h data, compute ADX, EMA trend, and log returns, return ctx DataFrame."""
    try:
        df = load_symbol_tf(symbol, "4h")
    except Exception:
        return None
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    df = add_adx(df, 14)
    df = add_ema(df, periods=[8, 21])
    df["ctx_log_ret_4h"] = np.log(df["close"] / df["close"].shift(1))
    df["bull_4h"] = (df["ema_8"] > df["ema_21"]).astype(int)
    return df[["timestamp", "adx", "ctx_log_ret_4h", "bull_4h"]].rename(columns={"adx": "ctx_adx"})


def _load_funding(symbol):
    """Load funding rate parquet and return DataFrame with timestamp, funding_rate."""
    p = REPO_ROOT / f"data/bybit/{symbol}/funding_rate"
    files = sorted(p.rglob("*.parquet"))
    if not files:
        return None
    dfs = [pd.read_parquet(f) for f in files]
    df = pd.concat(dfs, ignore_index=True)
    ts_col = "fundingTime" if "fundingTime" in df.columns else "timestamp"
    df["timestamp"] = pd.to_datetime(df[ts_col], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if "fundingRate" in df.columns:
        df = df.rename(columns={"fundingRate": "funding_rate"})
    return df[["timestamp", "funding_rate"]]


def _load_open_interest(symbol):
    """Load open interest parquet and return DataFrame with timestamp, open_interest."""
    p = REPO_ROOT / f"data/bybit/{symbol}/open_interest"
    files = sorted(p.rglob("*.parquet"))
    if not files:
        return None
    dfs = [pd.read_parquet(f) for f in files]
    df = pd.concat(dfs, ignore_index=True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if "openInterest" in df.columns:
        df = df.rename(columns={"openInterest": "open_interest"})
    return df[["timestamp", "open_interest"]]


def _merge_asof_nearest(left, right, on, right_col):
    """Merge right into left using backward-asof (nearest earlier timestamp)."""
    merged = pd.merge_asof(
        left.sort_values(on),
        right.sort_values(on),
        on=on,
        direction="backward"
    )
    return merged.sort_values(on).reset_index(drop=True)


# ── ORDERBOOK FEATURES ────────────────────────────────────────────────────

def load_orderbook_features(symbol):
    """Compute spread, depth, imbalance from latest orderbook snapshot."""
    p = REPO_ROOT / f"data/bybit/{symbol}/orderbook"
    files = sorted(p.rglob("*.parquet"))
    if not files:
        return None

    df = pd.read_parquet(files[-1])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

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


# ── DOMAIN SCORING (each returns 0-100) ───────────────────────────────────

def score_technical(rsi: float, adx: float, log_ret_4h: float, rsi_threshold: float = 40.0) -> float:
    """
    Technical Domain Score: 0-100
    Measures trend strength, pullback depth, and momentum quality.
    
    rsi_threshold: per-symbol gate threshold (e.g. ETH=50.1). 
                   RSI >= threshold gets 0 (failed gate).
    """
    # Scale RSI score relative to threshold: deeper pullback = higher score
    if rsi < 20:
        rsi_score = 100
    elif rsi < 30:
        rsi_score = 80
    elif rsi < 35:
        rsi_score = 60
    elif rsi < 40:
        rsi_score = 40
    elif rsi < rsi_threshold:
        rsi_score = 20  # passed gate but weak pullback depth
    else:
        rsi_score = 0   # failed gate

    if adx >= 50:
        adx_score = 100
    elif adx >= 40:
        adx_score = 80
    elif adx >= 30:
        adx_score = 60
    elif adx >= 25:
        adx_score = 40
    else:
        adx_score = 0

    if log_ret_4h >= 0.01:
        mom_score = 100
    elif log_ret_4h >= 0.005:
        mom_score = 75
    elif log_ret_4h >= 0.002:
        mom_score = 50
    elif log_ret_4h > 0:
        mom_score = 25
    else:
        mom_score = 0

    return round((rsi_score + adx_score + mom_score) / 3, 2)


def score_structure(ob_features, funding_rate: float, oi_delta_pct: float) -> float:
    """
    Structure Domain Score: 0-100
    Measures liquidity quality, funding direction, and open interest delta.
    """
    if ob_features is None:
        spread_score = depth_score = imb_score = 0
    else:
        spread = ob_features["spread_pct"]
        depth = ob_features["depth_usd"]
        imbalance = ob_features["imbalance_pct"]

        if spread < 0.01:
            spread_score = 100
        elif spread < 0.05:
            spread_score = 75
        elif spread < 0.10:
            spread_score = 50
        elif spread < 0.20:
            spread_score = 25
        else:
            spread_score = 0

        if depth >= 500_000:
            depth_score = 100
        elif depth >= 200_000:
            depth_score = 75
        elif depth >= 100_000:
            depth_score = 50
        elif depth >= 50_000:
            depth_score = 25
        else:
            depth_score = 0

        if imbalance >= 20:
            imb_score = 100
        elif imbalance >= 10:
            imb_score = 75
        elif imbalance >= 5:
            imb_score = 50
        elif imbalance >= 0:
            imb_score = 25
        else:
            imb_score = 0

    if funding_rate < 0:
        fund_score = 100
    elif funding_rate < 0.0001:
        fund_score = 75
    elif funding_rate < 0.0005:
        fund_score = 50
    elif funding_rate < 0.001:
        fund_score = 25
    else:
        fund_score = 0

    if oi_delta_pct >= 5:
        oi_score = 100
    elif oi_delta_pct >= 0:
        oi_score = 60
    else:
        oi_score = 0

    return round((spread_score + depth_score + imb_score + fund_score + oi_score) / 5, 2)


def score_context(session: str, symbol: str, macro_penalty: int = 0) -> float:
    """
    Context Domain Score: 0-100
    Measures session quality, symbol backtest quality, and macro risk.
    """
    session_scores = {
        "london": 80,
        "ny_open": 60,
        "ny_late": 80,
        "asian": 20,
    }
    sess_score = session_scores.get(session, 0)

    sharpe = SYMBOL_QUALITY.get(symbol, 0)
    if sharpe >= 5.5:
        sym_score = 100
    elif sharpe >= 4.5:
        sym_score = 70
    elif sharpe >= 4.0:
        sym_score = 50
    else:
        sym_score = 30

    raw = (sess_score + sym_score) / 2
    return round(max(0, raw - macro_penalty), 2)


# ── LEGACY SCORING HELPERS (backward-compatible DB keys) ──────────────────

def score_funding_oi_legacy(funding_rate: float, oi_delta_pct: float) -> int:
    """Old-style 0-20 score for funding + OI (kept for DB schema compat)."""
    if funding_rate < 0:
        fund_score = 10
    elif funding_rate < 0.0001:
        fund_score = 7
    elif funding_rate < 0.0005:
        fund_score = 4
    else:
        fund_score = 0

    if oi_delta_pct >= 5:
        oi_score = 10
    elif oi_delta_pct >= 0:
        oi_score = 6
    else:
        oi_score = 0

    return fund_score + oi_score


def score_session_symbol_legacy(session: str, symbol: str) -> int:
    """Old-style 0-10 score for session + symbol (kept for DB schema compat)."""
    sess_score = 5 if session in {"london", "ny_late"} else 0
    sharpe = SYMBOL_QUALITY.get(symbol, 0)
    if sharpe >= 5.5:
        sym_score = 5
    elif sharpe >= 4.5:
        sym_score = 3
    else:
        sym_score = 1
    return sess_score + sym_score


# ── CONTRADICTION ENGINE ────────────────────────────────────────────────────

def detect_contradictions(tech_score: float, struct_score: float, ctx_score: float,
                          ob_features, rsi: float, adx: float, oi_delta_pct: float) -> list:
    """
    Detect strong contradictions between domain scores.
    Returns list of contradiction strings.
    """
    contradictions = []

    if tech_score >= 70 and ob_features is not None:
        if ob_features.get("depth_usd", 0) < 100_000:
            contradictions.append("Strong trend but weak liquidity depth")

    if ob_features is not None:
        funding = ob_features.get("funding_rate", 0)
        if funding < 0 and oi_delta_pct < 0:
            contradictions.append("Negative funding but OI falling")

    if ob_features is not None and ob_features.get("imbalance_pct", 0) >= 20:
        if rsi >= 38:
            contradictions.append("Bid imbalance high but RSI barely in pullback zone")

    return contradictions


# ── MAIN DQS SCORING ──────────────────────────────────────────────────────

def score_signal(symbol, enriched_df, ob_features=None, session="", idx=-1,
                 rsi_override=None, adx_override=None, log_ret_override=None):
    """
    Compute DQS (Decision Quality Score) 0-100.
    Returns (dqs: float, breakdown: dict)
    """
    row = enriched_df.iloc[idx]

    rsi = float(rsi_override) if rsi_override is not None else float(row.get("rsi", 100) if not pd.isna(row.get("rsi")) else 100)
    adx = float(adx_override) if adx_override is not None else float(row.get("adx", 0) if not pd.isna(row.get("adx")) else 0)
    log_ret = float(log_ret_override) if log_ret_override is not None else float(row.get("ctx_log_ret_4h", 0) if not pd.isna(row.get("ctx_log_ret_4h")) else 0)

    funding_rate = float(row.get("funding_rate", 0)) if not pd.isna(row.get("funding_rate")) else 0

    # Funding rate z-score: how extreme is current funding vs recent history
    funding_zscore = 0.0
    if "funding_rate" in enriched_df.columns and len(enriched_df) >= 97:
        funding_window = enriched_df["funding_rate"].iloc[max(0, idx-96):idx+1].dropna()
        if len(funding_window) >= 10:
            mean_fr = funding_window.mean()
            std_fr = funding_window.std()
            if std_fr > 0:
                funding_zscore = (funding_rate - mean_fr) / std_fr

    oi_delta_pct = 0.0
    oi_now = float(row.get("open_interest", 0)) if not pd.isna(row.get("open_interest")) else 0
    if len(enriched_df) >= 97:
        oi_old = float(enriched_df.iloc[idx - 96].get("open_interest", oi_now))
        if oi_old > 0:
            oi_delta_pct = ((oi_now - oi_old) / oi_old) * 100

    # Per-symbol RSI threshold from proven D5 config
    sym_params = get_symbol_params(symbol)
    rsi_thresh = sym_params.get("rsi_threshold", 40.0)

    # Sentiment features from enriched data (data-only, no DQS impact)
    fear_greed = float(row.get("fear_greed", 0)) if not pd.isna(row.get("fear_greed")) else 0
    btc_dominance = float(row.get("btc_dominance", 0)) if not pd.isna(row.get("btc_dominance")) else 0
    taker_buy_ratio = float(row.get("taker_buy_ratio", 0)) if not pd.isna(row.get("taker_buy_ratio")) else 0
    binance_ls_ratio = float(row.get("longShortRatio", 0)) if not pd.isna(row.get("longShortRatio")) else 0

    tech_score = score_technical(rsi, adx, log_ret, rsi_threshold=rsi_thresh)
    struct_score = score_structure(ob_features, funding_rate, oi_delta_pct)
    ctx_score = score_context(session, symbol)

    contradictions = detect_contradictions(
        tech_score, struct_score, ctx_score, ob_features, rsi, adx, oi_delta_pct
    )
    n_contra = len(contradictions)
    contra_penalty = 10 if n_contra >= 2 else 0
    reject_for_contra = n_contra >= 3

    dqs = (0.40 * tech_score) + (0.30 * struct_score) + (0.30 * ctx_score)
    dqs -= contra_penalty
    dqs = max(0, min(100, dqs))

    if dqs >= 81:
        strength = "EXCELLENT"
    elif dqs >= 66:
        strength = "GOOD"
    elif dqs >= 50:
        strength = "FAIR"
    else:
        strength = "WEAK"

    # Backward-compatible sub-scores for DB schema
    funding_oi_score = score_funding_oi_legacy(funding_rate, oi_delta_pct)
    session_symbol_score = score_session_symbol_legacy(session, symbol)

    return dqs, {
        "dqs": round(dqs, 2),
        "total": dqs,  # backward compat for callers expecting 'total'
        "technical": tech_score,
        "structure": struct_score,
        "context": ctx_score,
        "market_structure": struct_score,  # backward compat key
        "funding_oi": funding_oi_score,     # backward compat key (0-20)
        "session_symbol": session_symbol_score,  # backward compat key (0-10)
        "rsi": round(rsi, 2),
        "adx": round(adx, 2),
        "bull_4h": int(row.get("bull_4h", 0)) if not pd.isna(row.get("bull_4h")) else 0,
        "log_ret_4h": round(log_ret, 6),
        "funding_rate": funding_rate,
        "oi_delta_pct": round(oi_delta_pct, 3),
        "oi_now": oi_now,
        "session": session,
        "symbol": symbol,
        "contradictions": contradictions,
        "contra_penalty": contra_penalty,
        "reject_for_contra": reject_for_contra,
        "strength": strength,
        "grade": strength,
        "tradeable": dqs >= 65,
        "trackable": dqs >= 50,
        "struct_meta": ob_features or {"note": "No orderbook data"},
        "funding_oi_meta": {
            "funding_rate": funding_rate,
            "oi_delta_pct": round(oi_delta_pct, 3),
            "oi_now": oi_now,
        },
        # Sentiment features (data-only passthrough, no DQS impact)
        "funding_zscore": round(funding_zscore, 4),
        "fear_greed": fear_greed,
        "btc_dominance": btc_dominance,
        "taker_buy_ratio": round(taker_buy_ratio, 6),
        "binance_ls_ratio": binance_ls_ratio,
        "session_meta": {
            "session_score": 5 if session in {"london", "ny_late"} else 0,
            "symbol_score": 5 if SYMBOL_QUALITY.get(symbol, 0) >= 5.5 else 3,
            "sharpe": SYMBOL_QUALITY.get(symbol, 0),
        },
    }


def classify_and_score(symbol, session=""):
    """Load data, score, return full report dict."""
    df = load_enriched(symbol)
    if df is None or len(df) == 0:
        return None

    ob = load_orderbook_features(symbol)

    dqs, breakdown = score_signal(symbol, df, ob, session)

    last = df.iloc[-1]
    price = float(last["close"])
    atr = float(last.get("atr", 0)) if not pd.isna(last.get("atr")) else 0
    if atr == 0:
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
        if r:
            print(f"\n{sym}: DQS = {r['dqs']}/100  ({r['strength']})")
            print(f"  Technical: {r['technical']:.1f} | Structure: {r['structure']:.1f} | Context: {r['context']:.1f}")
            if r["contradictions"]:
                print(f"  Contradictions: {r['contradictions']}")
