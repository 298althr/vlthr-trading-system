"""
V3 Probe Engine — Layer 4 of DISC
==================================
12 probe types that discover predictive features from market data.
Each probe computes a feature and evaluates its predictive quality (IC).

Probe Types:
  1. trend        — price momentum across lookback windows
  2. volatility   — ATR percentile, vol regime
  3. liquidity    — orderbook imbalance, spread
  4. orderflow    — volume delta, cumulative delta
  5. time         — session, day-of-week, hour patterns
  6. fractal      — self-similarity, Hurst exponent
  7. correlation  — cross-asset lead-lag
  8. regime       — trend/range/vol regime classification
  9. auction      — funding rate extremes, OI surges
 10. risk         — VaR, drawdown probability
 11. capital      — margin utilization, leverage pressure
 12. decision     — DQS feedback, calibration edge
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field


@dataclass
class ProbeResult:
    feature_name: str
    probe_type: str
    symbol: str
    values: pd.Series
    ic: float = 0.0
    ic_std: float = 0.0
    wilson_lower: float = 0.0
    sample_size: int = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "feature_name": self.feature_name,
            "probe_type": self.probe_type,
            "symbol": self.symbol,
            "ic": self.ic,
            "ic_std": self.ic_std,
            "wilson_lower": self.wilson_lower,
            "sample_size": self.sample_size,
            "metadata": self.metadata,
        }


def _rolling_ic(feature: pd.Series, forward_returns: pd.Series,
                window: int = 96) -> Tuple[float, float, int]:
    """Compute rolling Information Coefficient (Spearman rank correlation)."""
    aligned = pd.DataFrame({"f": feature, "r": forward_returns}).dropna()
    if len(aligned) < window:
        return 0.0, 0.0, 0
    ics = []
    for i in range(window, len(aligned), window // 2):
        chunk = aligned.iloc[i - window:i]
        if len(chunk) < 20:
            continue
        ic = chunk["f"].rank().corr(chunk["r"].rank())
        if not np.isnan(ic):
            ics.append(ic)
    if not ics:
        return 0.0, 0.0, 0
    mean_ic = float(np.mean(ics))
    std_ic = float(np.std(ics)) if len(ics) > 1 else 0.0
    return mean_ic, std_ic, len(aligned)


def _wilson_lower(successes: int, total: int, z: float = 1.96) -> float:
    """Wilson score lower bound."""
    if total == 0:
        return 0.0
    p = successes / total
    denom = 1 + z * z / total
    center = p + z * z / (2 * total)
    margin = z * np.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return float(max(0.0, (center - margin) / denom))


def _forward_returns(df: pd.DataFrame, periods: int = 4) -> pd.Series:
    """Compute forward returns (default 4 bars = 1 hour on 15m)."""
    return df["close"].shift(-periods) / df["close"] - 1


# ── PROBE BASE ────────────────────────────────────────────────────────────

class BaseProbe:
    """Base class for all probes."""
    probe_type: str = "base"

    def compute(self, df: pd.DataFrame, symbol: str) -> List[ProbeResult]:
        raise NotImplementedError


# ── 1. TREND PROBES ───────────────────────────────────────────────────────

class TrendProbe(BaseProbe):
    probe_type = "trend"

    def compute(self, df: pd.DataFrame, symbol: str) -> List[ProbeResult]:
        results = []
        fwd = _forward_returns(df)
        for window in [6, 12, 24, 48, 96]:
            feat = df["close"].pct_change(window)
            name = f"trend_ret_{window}"
            ic, ic_std, n = _rolling_ic(feat, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(
                feature_name=name, probe_type=self.probe_type, symbol=symbol,
                values=feat, ic=ic, ic_std=ic_std, wilson_lower=wl,
                sample_size=n, metadata={"window": window},
            ))
        # EMA slope
        for span in [20, 50]:
            ema = df["close"].ewm(span=span).mean()
            slope = ema.pct_change(4)
            name = f"trend_ema_slope_{span}"
            ic, ic_std, n = _rolling_ic(slope, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, slope,
                                       ic, ic_std, wl, n, {"span": span}))
        return results


# ── 2. VOLATILITY PROBES ──────────────────────────────────────────────────

class VolatilityProbe(BaseProbe):
    probe_type = "volatility"

    def compute(self, df: pd.DataFrame, symbol: str) -> List[ProbeResult]:
        results = []
        fwd = _forward_returns(df)
        high_low = df["high"] - df["low"]
        for window in [14, 28, 56]:
            atr = high_low.rolling(window).mean()
            atr_pct = atr / df["close"]
            name = f"vol_atr_pct_{window}"
            ic, ic_std, n = _rolling_ic(atr_pct, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, atr_pct,
                                       ic, ic_std, wl, n, {"window": window}))
        # Volatility percentile (regime)
        vol = high_low.rolling(20).std()
        vol_pctile = vol.rolling(96).rank(pct=True)
        name = "vol_pctile_96"
        ic, ic_std, n = _rolling_ic(vol_pctile, fwd)
        wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
        results.append(ProbeResult(name, self.probe_type, symbol, vol_pctile,
                                   ic, ic_std, wl, n, {}))
        return results


# ── 3. LIQUIDITY PROBES ───────────────────────────────────────────────────

class LiquidityProbe(BaseProbe):
    probe_type = "liquidity"

    def compute(self, df: pd.DataFrame, symbol: str) -> List[ProbeResult]:
        results = []
        fwd = _forward_returns(df)
        # Volume ratio
        vol_ma = df["volume"].rolling(20).mean()
        vol_ratio = df["volume"] / vol_ma
        name = "liq_vol_ratio_20"
        ic, ic_std, n = _rolling_ic(vol_ratio, fwd)
        wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
        results.append(ProbeResult(name, self.probe_type, symbol, vol_ratio,
                                   ic, ic_std, wl, n, {}))
        # Spread proxy (high-low / close)
        spread = (df["high"] - df["low"]) / df["close"]
        name = "liq_spread_proxy"
        ic, ic_std, n = _rolling_ic(spread, fwd)
        wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
        results.append(ProbeResult(name, self.probe_type, symbol, spread,
                                   ic, ic_std, wl, n, {}))
        return results


# ── 4. ORDERFLOW PROBES ───────────────────────────────────────────────────

class OrderflowProbe(BaseProbe):
    probe_type = "orderflow"

    def compute(self, df: pd.DataFrame, symbol: str) -> List[ProbeResult]:
        results = []
        fwd = _forward_returns(df)
        # Volume delta proxy: (close - open) / (high - low) * volume
        hl = (df["high"] - df["low"]).replace(0, np.nan)
        delta = ((df["close"] - df["open"]) / hl * df["volume"]).fillna(0)
        for window in [6, 24]:
            cum_delta = delta.rolling(window).sum()
            name = f"of_cum_delta_{window}"
            ic, ic_std, n = _rolling_ic(cum_delta, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, cum_delta,
                                       ic, ic_std, wl, n, {"window": window}))
        return results


# ── 5. TIME PROBES ────────────────────────────────────────────────────────

class TimeProbe(BaseProbe):
    probe_type = "time"

    def compute(self, df: pd.DataFrame, symbol: str) -> List[ProbeResult]:
        results = []
        fwd = _forward_returns(df)
        ts = df["timestamp"]
        # Hour of day
        hour = ts.dt.hour.astype(float)
        name = "time_hour"
        ic, ic_std, n = _rolling_ic(hour, fwd)
        wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
        results.append(ProbeResult(name, self.probe_type, symbol, hour,
                                   ic, ic_std, wl, n, {}))
        # Day of week
        dow = ts.dt.dayofweek.astype(float)
        name = "time_dow"
        ic, ic_std, n = _rolling_ic(dow, fwd)
        wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
        results.append(ProbeResult(name, self.probe_type, symbol, dow,
                                   ic, ic_std, wl, n, {}))
        return results


# ── 6. FRACTAL PROBES ─────────────────────────────────────────────────────

class FractalProbe(BaseProbe):
    probe_type = "fractal"

    def compute(self, df: pd.DataFrame, symbol: str) -> List[ProbeResult]:
        results = []
        fwd = _forward_returns(df)
        # Hurst exponent approximation via variance ratio
        ret_1 = df["close"].pct_change()
        for lag in [10, 20]:
            ret_lag = df["close"].pct_change(lag)
            window = lag * 10
            var_1 = ret_1.rolling(window).var()
            var_lag = ret_lag.rolling(window).var()
            # Hurst ~ 0.5 * log(var_lag / (var_1 * lag)) / log(lag) + 0.5
            vr = var_lag / (var_1 * lag + 1e-10)
            hurst_proxy = 0.5 * np.log(vr.clip(lower=1e-10)) / np.log(lag) + 0.5
            name = f"fractal_hurst_{lag}"
            ic, ic_std, n = _rolling_ic(hurst_proxy, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, hurst_proxy,
                                       ic, ic_std, wl, n, {"lag": lag}))
        return results


# ── 7. CORRELATION PROBES ─────────────────────────────────────────────────

class CorrelationProbe(BaseProbe):
    probe_type = "correlation"

    def compute(self, df: pd.DataFrame, symbol: str,
                btc_df: pd.DataFrame = None) -> List[ProbeResult]:
        results = []
        fwd = _forward_returns(df)
        if btc_df is not None and "close" in btc_df.columns:
            btc_ret = btc_df["close"].pct_change(4)
            sym_ret = df["close"].pct_change(4)
            # Rolling correlation
            for window in [48, 96]:
                corr = sym_ret.rolling(window).corr(btc_ret)
                name = f"corr_btc_{window}"
                ic, ic_std, n = _rolling_ic(corr, fwd)
                wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
                results.append(ProbeResult(name, self.probe_type, symbol, corr,
                                           ic, ic_std, wl, n, {"window": window}))
            # BTC lead-lag: BTC return t-1 vs symbol return t
            btc_lagged = btc_ret.shift(1)
            lead_lag = sym_ret.rolling(48).corr(btc_lagged)
            name = "corr_btc_leadlag_48"
            ic, ic_std, n = _rolling_ic(lead_lag, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, lead_lag,
                                       ic, ic_std, wl, n, {}))
        return results


# ── 8. REGIME PROBES ──────────────────────────────────────────────────────

class RegimeProbe(BaseProbe):
    probe_type = "regime"

    def compute(self, df: pd.DataFrame, symbol: str) -> List[ProbeResult]:
        results = []
        fwd = _forward_returns(df)
        # ADX-based regime: trending vs ranging
        high, low, close = df["high"], df["low"], df["close"]
        plus_dm = (high - high.shift(1)).clip(lower=0)
        minus_dm = (low.shift(1) - low).clip(lower=0)
        tr = (high - low).rolling(14).mean()
        plus_di = 100 * (plus_dm.rolling(14).mean() / (tr + 1e-10))
        minus_di = 100 * (minus_dm.rolling(14).mean() / (tr + 1e-10))
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
        adx = dx.rolling(14).mean()
        name = "regime_adx_14"
        ic, ic_std, n = _rolling_ic(adx, fwd)
        wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
        results.append(ProbeResult(name, self.probe_type, symbol, adx,
                                   ic, ic_std, wl, n, {}))
        # Trend direction via EMA50 vs EMA200
        if len(close) >= 200:
            ema50 = close.ewm(span=50).mean()
            ema200 = close.ewm(span=200).mean()
            trend_dir = (ema50 - ema200) / ema200
            name = "regime_trend_dir"
            ic, ic_std, n = _rolling_ic(trend_dir, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, trend_dir,
                                       ic, ic_std, wl, n, {}))
        return results


# ── 9. AUCTION PROBES ─────────────────────────────────────────────────────

class AuctionProbe(BaseProbe):
    probe_type = "auction"

    def compute(self, df: pd.DataFrame, symbol: str) -> List[ProbeResult]:
        results = []
        fwd = _forward_returns(df)
        # Funding rate (if available)
        if "funding_rate" in df.columns:
            fr = df["funding_rate"]
            name = "auc_funding_rate"
            ic, ic_std, n = _rolling_ic(fr, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, fr,
                                       ic, ic_std, wl, n, {}))
            # Funding rate z-score
            fr_z = (fr - fr.rolling(96).mean()) / (fr.rolling(96).std() + 1e-10)
            name = "auc_funding_zscore_96"
            ic, ic_std, n = _rolling_ic(fr_z, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, fr_z,
                                       ic, ic_std, wl, n, {}))
        # OI delta (if available)
        if "open_interest" in df.columns:
            oi = df["open_interest"]
            oi_delta = oi.pct_change(96)
            name = "auc_oi_delta_96"
            ic, ic_std, n = _rolling_ic(oi_delta, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, oi_delta,
                                       ic, ic_std, wl, n, {"window": 96}))
        # LS ratio (if available)
        if "buyRatio" in df.columns:
            ls = df["buyRatio"] / (df["sellRatio"] + 1e-10)
            name = "auc_ls_ratio"
            ic, ic_std, n = _rolling_ic(ls, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, ls,
                                       ic, ic_std, wl, n, {}))
        return results


# ── 10. RISK PROBES ───────────────────────────────────────────────────────

class RiskProbe(BaseProbe):
    probe_type = "risk"

    def compute(self, df: pd.DataFrame, symbol: str) -> List[ProbeResult]:
        results = []
        fwd = _forward_returns(df)
        ret = df["close"].pct_change()
        # Rolling VaR (95th percentile loss)
        for window in [48, 96]:
            var_95 = ret.rolling(window).quantile(0.05)
            name = f"risk_var95_{window}"
            ic, ic_std, n = _rolling_ic(var_95, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, var_95,
                                       ic, ic_std, wl, n, {"window": window}))
        # Max drawdown rolling
        rolling_max = df["close"].rolling(96).max()
        dd = (df["close"] - rolling_max) / rolling_max
        name = "risk_drawdown_96"
        ic, ic_std, n = _rolling_ic(dd, fwd)
        wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
        results.append(ProbeResult(name, self.probe_type, symbol, dd,
                                   ic, ic_std, wl, n, {}))
        return results


# ── 11. CAPITAL PROBES ────────────────────────────────────────────────────

class CapitalProbe(BaseProbe):
    probe_type = "capital"

    def compute(self, df: pd.DataFrame, symbol: str) -> List[ProbeResult]:
        results = []
        fwd = _forward_returns(df)
        # Turnover ratio (proxy for capital pressure)
        if "turnover" in df.columns:
            turn_ma = df["turnover"].rolling(20).mean()
            turn_ratio = df["turnover"] / (turn_ma + 1e-10)
            name = "cap_turnover_ratio_20"
            ic, ic_std, n = _rolling_ic(turn_ratio, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, turn_ratio,
                                       ic, ic_std, wl, n, {}))
        # Volume-weighted price deviation
        vwap = (df["close"] * df["volume"]).rolling(20).sum() / (df["volume"].rolling(20).sum() + 1e-10)
        vwap_dev = (df["close"] - vwap) / (vwap + 1e-10)
        name = "cap_vwap_dev_20"
        ic, ic_std, n = _rolling_ic(vwap_dev, fwd)
        wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
        results.append(ProbeResult(name, self.probe_type, symbol, vwap_dev,
                                   ic, ic_std, wl, n, {}))
        return results


# ── 12. DECISION PROBES ───────────────────────────────────────────────────

class DecisionProbe(BaseProbe):
    probe_type = "decision"

    def compute(self, df: pd.DataFrame, symbol: str) -> List[ProbeResult]:
        results = []
        fwd = _forward_returns(df)
        # RSI as decision quality proxy
        if "rsi" in df.columns:
            rsi = df["rsi"]
            name = "dec_rsi"
            ic, ic_std, n = _rolling_ic(rsi, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, rsi,
                                       ic, ic_std, wl, n, {}))
        # RSI momentum (3-bar change)
        if "rsi" in df.columns:
            rsi_mom = df["rsi"].diff(3)
            name = "dec_rsi_momentum_3"
            ic, ic_std, n = _rolling_ic(rsi_mom, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, rsi_mom,
                                       ic, ic_std, wl, n, {}))
        # Log return 4h (if available from enrichment)
        if "ctx_log_ret_4h" in df.columns:
            lr = df["ctx_log_ret_4h"]
            name = "dec_log_ret_4h"
            ic, ic_std, n = _rolling_ic(lr, fwd)
            wl = _wilson_lower(int(abs(ic) > 0.02), max(n // 96, 1)) if n > 0 else 0
            results.append(ProbeResult(name, self.probe_type, symbol, lr,
                                       ic, ic_std, wl, n, {}))
        return results


# ── PROBE REGISTRY ─────────────────────────────────────────────────────────

ALL_PROBES: Dict[str, BaseProbe] = {
    "trend": TrendProbe(),
    "volatility": VolatilityProbe(),
    "liquidity": LiquidityProbe(),
    "orderflow": OrderflowProbe(),
    "time": TimeProbe(),
    "fractal": FractalProbe(),
    "correlation": CorrelationProbe(),
    "regime": RegimeProbe(),
    "auction": AuctionProbe(),
    "risk": RiskProbe(),
    "capital": CapitalProbe(),
    "decision": DecisionProbe(),
}


def run_all_probes(df: pd.DataFrame, symbol: str,
                   btc_df: pd.DataFrame = None) -> List[ProbeResult]:
    """Run all 12 probe types on a DataFrame."""
    all_results = []
    for name, probe in ALL_PROBES.items():
        try:
            if name == "correlation" and btc_df is not None:
                results = probe.compute(df, symbol, btc_df=btc_df)
            else:
                results = probe.compute(df, symbol)
            all_results.extend(results)
        except Exception as e:
            print(f"[probe_engine] {name} probe failed for {symbol}: {e}")
    return all_results
