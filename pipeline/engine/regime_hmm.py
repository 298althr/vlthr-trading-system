"""
VLTHR HMM Regime Detection
===========================
Trains per-symbol Gaussian HMM over log returns and rolling realized volatility
to classify market regimes as trending or ranging.

The HMM is trained offline and serialized as pickle. The live pipeline loads
the model and calls predict() on the latest feature window, same pattern as
calibration.json.

States:
  2-state baseline: 0=trending, 1=ranging
  3-state optional: 0=trending, 1=ranging, 2=high-vol

Training features:
  - log returns (15m)
  - rolling realized vol (20-bar window)

Usage:
  # Train
  python3 regime_hmm.py --train --symbol BTCUSDT

  # Predict (programmatic)
  from regime_hmm import predict_regime
  regime = predict_regime("BTCUSDT", enriched_df)
"""
import pickle
import sys
import warnings
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

_ENGINE = Path(__file__).resolve().parent
_DEVOPS = _ENGINE.parent.parent
DATA_ROOT = _DEVOPS / "data" / "bybit"
MODEL_DIR = _ENGINE / "regime_models"

N_STATES = 2  # trending, ranging
VOL_WINDOW = 20  # 20 * 15m = 5h rolling vol window
MIN_TRAIN_BARS = 2000  # ~21 days of 15m bars
PREDICT_WINDOW = 200  # bars to use for Viterbi prediction (sliding window)

_MODEL_CACHE: dict = {}  # in-memory model cache to avoid repeated disk I/O
_REGIME_CACHE: dict = {}  # {symbol: {ts: (regime, confidence)}} pre-computed per symbol


def _compute_features(df: pd.DataFrame) -> np.ndarray:
    """Compute HMM features: log returns and rolling realized vol.

    Returns (n, 2) array. Drops NaN rows.
    """
    close = df["close"].astype(float)
    log_ret = np.log(close / close.shift(1))
    rolling_vol = log_ret.rolling(VOL_WINDOW).std()

    features = pd.DataFrame({
        "log_ret": log_ret,
        "rolling_vol": rolling_vol,
    }).dropna()

    return features.values


def train_hmm(symbol: str, df: pd.DataFrame, n_states: int = N_STATES) -> object:
    """Train a Gaussian HMM for the given symbol.

    Parameters:
        symbol: e.g. "BTCUSDT"
        df: enriched DataFrame with 'close' column
        n_states: 2 (trending/ranging) or 3 (trending/ranging/high-vol)

    Returns:
        Fitted GaussianHMM model with state_labels attribute mapping
        state indices to regime names.
    """
    from hmmlearn.hmm import GaussianHMM

    X = _compute_features(df)
    if len(X) < MIN_TRAIN_BARS:
        raise ValueError(f"Insufficient training data for {symbol}: {len(X)} bars (need {MIN_TRAIN_BARS})")

    model = GaussianHMM(
        n_components=n_states,
        covariance_type="full",
        n_iter=200,
        random_state=42,
        tol=1e-4,
    )
    model.fit(X)

    # Map state indices to regime names based on volatility characteristic.
    # Higher mean rolling_vol = ranging. Lower = trending.
    state_vols = model.means_[:, 1]  # second feature is rolling_vol
    sorted_states = np.argsort(state_vols)  # ascending vol

    if n_states == 2:
        labels = {int(sorted_states[0]): "TRENDING", int(sorted_states[1]): "RANGING"}
    else:
        labels = {
            int(sorted_states[0]): "TRENDING",
            int(sorted_states[1]): "RANGING",
            int(sorted_states[2]): "VOLATILE",
        }
    model.state_labels = labels

    return model


def save_model(model: object, symbol: str) -> Path:
    """Serialize HMM model to regime_models/{symbol}_hmm.pkl."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = MODEL_DIR / f"{symbol}_hmm.pkl"
    with open(path, "wb") as f:
        pickle.dump(model, f)
    return path


def load_model(symbol: str) -> Optional[object]:
    """Load serialized HMM model. Returns None if not found.
    Caches in memory after first load."""
    if symbol in _MODEL_CACHE:
        return _MODEL_CACHE[symbol]

    path = MODEL_DIR / f"{symbol}_hmm.pkl"
    if not path.exists():
        return None
    with open(path, "rb") as f:
        model = pickle.load(f)
    _MODEL_CACHE[symbol] = model
    return model


def precompute_regimes(symbol: str, df: pd.DataFrame):
    """Pre-compute HMM regime for all bars in df. Stores in _REGIME_CACHE.

    This runs one Viterbi pass on the full sequence instead of per-bar calls.
    Call this once per symbol before backtest/replay loops.
    """
    model = load_model(symbol)
    if model is None:
        _REGIME_CACHE[symbol] = {}
        return

    X = _compute_features(df)
    if len(X) < 5:
        _REGIME_CACHE[symbol] = {}
        return

    states = model.predict(X)
    posteriors = model.predict_proba(X)

    cache = {}
    pad_count = len(df) - len(states)
    for i, state in enumerate(states):
        ts = df.index[i + pad_count] if hasattr(df, 'index') and pad_count <= i else None
        if ts is not None:
            regime = model.state_labels.get(int(state), "MIXED")
            conf = float(posteriors[i, int(state)])
            cache[ts] = (regime, conf)

    _REGIME_CACHE[symbol] = cache


def predict_regime(symbol: str, df: pd.DataFrame) -> Tuple[str, float]:
    """Predict current regime for symbol using loaded HMM model.

    Parameters:
        symbol: e.g. "BTCUSDT"
        df: enriched DataFrame with 'close' column (needs at least VOL_WINDOW+1 rows)

    Returns:
        (regime_name, confidence) where regime_name is "TRENDING", "RANGING",
        "VOLATILE", or "MIXED" (fallback). Confidence is posterior probability
        of the predicted state, 0-1.
    """
    # Check pre-computed cache first
    if symbol in _REGIME_CACHE and len(_REGIME_CACHE[symbol]) > 0:
        # Look up by timestamp column (preferred) or index
        last_ts = None
        if "timestamp" in df.columns:
            last_ts = df.iloc[-1].get("timestamp")
        if last_ts is None and hasattr(df, 'index'):
            last_ts = df.index[-1]
        if last_ts is not None and last_ts in _REGIME_CACHE[symbol]:
            return _REGIME_CACHE[symbol][last_ts]

    model = load_model(symbol)
    if model is None:
        return "MIXED", 0.0

    X = _compute_features(df)
    if len(X) < 5:
        return "MIXED", 0.0

    # Use sliding window for prediction (last PREDICT_WINDOW bars)
    if len(X) > PREDICT_WINDOW:
        X = X[-PREDICT_WINDOW:]

    states = model.predict(X)
    last_state = int(states[-1])

    posteriors = model.predict_proba(X)
    confidence = float(posteriors[-1, last_state])

    regime = model.state_labels.get(last_state, "MIXED")
    return regime, confidence


def predict_regime_rolling(symbol: str, df: pd.DataFrame) -> pd.Series:
    """Predict regime for each bar in df. Returns series of regime names.

    Uses Viterbi on the full sequence for consistency.
    """
    model = load_model(symbol)
    if model is None:
        return pd.Series(["MIXED"] * len(df), index=df.index)

    X = _compute_features(df)
    if len(X) < 5:
        return pd.Series(["MIXED"] * len(df), index=df.index)

    states = model.predict(X)
    labels = [model.state_labels.get(int(s), "MIXED") for s in states]

    # Pad front with MIXED for dropped NaN rows
    pad_count = len(df) - len(labels)
    if pad_count > 0:
        labels = ["MIXED"] * pad_count + labels

    return pd.Series(labels, index=df.index)


def train_all_symbols(start: str = "2025-07-01", end: str = "2026-07-01"):
    """Train HMM models for all active symbols using enriched data."""
    sys.path.insert(0, str(_ENGINE))
    sys.path.insert(0, str(_DEVOPS / "backtest"))
    from backtest_runner import load_symbol_enriched
    from portfolio_config import SYMBOLS

    results = {}
    for symbol in SYMBOLS:
        print(f"\n  Training {symbol}...")
        try:
            df = load_symbol_enriched(symbol, start, end)
            if len(df) < MIN_TRAIN_BARS:
                print(f"  SKIP {symbol}: only {len(df)} bars (need {MIN_TRAIN_BARS})")
                results[symbol] = {"status": "skip", "bars": len(df)}
                continue

            model = train_hmm(symbol, df)
            path = save_model(model, symbol)

            # Evaluate state distribution
            X = _compute_features(df)
            states = model.predict(X)
            dist = {}
            for s in range(model.n_components):
                label = model.state_labels.get(s, f"state_{s}")
                dist[label] = int(np.sum(states == s))

            print(f"  OK {symbol}: {len(df)} bars, states={dist}, saved to {path.name}")
            results[symbol] = {"status": "ok", "bars": len(df), "state_dist": dist}
        except Exception as e:
            print(f"  FAIL {symbol}: {e}")
            results[symbol] = {"status": "fail", "error": str(e)}

    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="VLTHR HMM Regime Detection")
    parser.add_argument("--train", action="store_true", help="Train models for all symbols")
    parser.add_argument("--symbol", type=str, default=None, help="Train single symbol")
    parser.add_argument("--start", type=str, default="2025-07-01")
    parser.add_argument("--end", type=str, default="2026-07-01")
    args = parser.parse_args()

    if args.train:
        if args.symbol:
            sys.path.insert(0, str(_ENGINE))
            sys.path.insert(0, str(_DEVOPS / "backtest"))
            from backtest_runner import load_symbol_enriched
            df = load_symbol_enriched(args.symbol, args.start, args.end)
            model = train_hmm(args.symbol, df)
            path = save_model(model, args.symbol)
            print(f"Saved {args.symbol} HMM to {path}")
        else:
            results = train_all_symbols(args.start, args.end)
            print(f"\nSummary: {sum(1 for r in results.values() if r['status'] == 'ok')}/{len(results)} trained")
    else:
        parser.print_help()
