#!/usr/bin/env python3
"""Unit tests for Phase 2: HMM regime detection and strategy resolution."""
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

_ENGINE = Path(__file__).resolve().parent
sys.path.insert(0, str(_ENGINE))

from portfolio_config import (
    get_strategy, resolve_params, SESSION_REGIME_PARAMS,
    PER_SYMBOL_PARAMS, SESSION_PARAMS,
)


class TestGetStrategy(unittest.TestCase):
    """Test get_strategy resolution with session + regime."""

    def test_default_returns_trend_following(self):
        result = get_strategy("UNKNOWN", session=None, regime=None)
        self.assertEqual(result, "trend_following")

    def test_symbol_strategy_from_per_symbol(self):
        # XRP is mean_reversion in PER_SYMBOL_PARAMS
        result = get_strategy("XRPUSDT", session=None, regime=None)
        self.assertEqual(result, "mean_reversion")

    def test_trending_does_not_override_when_disabled(self):
        # REGIME_STRATEGY_OVERRIDE_ENABLED = False
        # XRP is mean_reversion, TRENDING should NOT switch to trend_following
        result = get_strategy("XRPUSDT", session="london", regime="TRENDING")
        self.assertEqual(result, "mean_reversion")

    def test_ranging_does_not_override_when_disabled(self):
        # BTC is trend_following, RANGING should NOT switch to mean_reversion
        result = get_strategy("BTCUSDT", session="london", regime="RANGING")
        self.assertEqual(result, "trend_following")

    def test_mixed_keeps_symbol_strategy(self):
        # MIXED regime should not override
        result = get_strategy("BTCUSDT", session="london", regime="MIXED")
        self.assertEqual(result, "trend_following")
        result = get_strategy("XRPUSDT", session="london", regime="MIXED")
        self.assertEqual(result, "mean_reversion")

    def test_volatile_keeps_symbol_strategy(self):
        # VOLATILE regime should not override strategy
        result = get_strategy("BTCUSDT", session="london", regime="VOLATILE")
        self.assertEqual(result, "trend_following")

    def test_empty_session_uses_symbol_strategy(self):
        result = get_strategy("XRPUSDT", session="", regime="TRENDING")
        self.assertEqual(result, "mean_reversion")

    def test_none_session_uses_symbol_strategy(self):
        result = get_strategy("XRPUSDT", session=None, regime="TRENDING")
        self.assertEqual(result, "mean_reversion")

    def test_session_fallback_to_symbol_when_session_key_missing(self):
        # If SESSION_PARAMS[symbol] exists but doesn't have the requested session,
        # should fall back to PER_SYMBOL_PARAMS[symbol]["strategy"], not "trend_following"
        import portfolio_config as pc
        original = pc.SESSION_PARAMS.copy()
        try:
            pc.SESSION_PARAMS["XRPUSDT"] = {"london": {"strategy": "trend_following"}}
            result = get_strategy("XRPUSDT", session="asian", regime=None)
            self.assertEqual(result, "mean_reversion")
        finally:
            pc.SESSION_PARAMS.clear()
            pc.SESSION_PARAMS.update(original)


class TestV2FiltersFlagGating(unittest.TestCase):
    """Test that v2_filters Filter 3 respects REGIME_STRATEGY_OVERRIDE_ENABLED."""

    def test_filter3_does_not_override_when_disabled(self):
        import portfolio_config as pc
        from v2_filters import apply_v2_filters
        import pandas as pd
        import numpy as np

        # Create minimal enriched DataFrame
        n = 60
        dates = pd.date_range("2026-01-01", periods=n, freq="15min")
        df = pd.DataFrame({
            "close": np.linspace(100, 130, n),
            "high": np.linspace(100, 130, n) + 0.5,
            "low": np.linspace(100, 130, n) - 0.5,
            "atr": [2.0] * n,
            "adx": [30.0] * n,
            "volume": [1000.0] * n,
            "rsi": [55.0] * n,
        })

        # With override disabled, mean_reversion should pass through unchanged
        assert pc.REGIME_STRATEGY_OVERRIDE_ENABLED is False
        passed, strategy, tp_scale, reason = apply_v2_filters(
            "XRPUSDT", "SHORT", "mean_reversion", df, None, 0.0)
        self.assertEqual(strategy, "mean_reversion")


class TestResolveParams(unittest.TestCase):
    """Test resolve_params with hierarchical shrinkage."""

    def test_no_session_regime_returns_symbol_params(self):
        result = resolve_params("BTCUSDT")
        self.assertEqual(result["strategy"], "trend_following")
        self.assertEqual(result["sl_mult"], 2.5)

    def test_empty_session_regime_params_returns_symbol_params(self):
        # SESSION_REGIME_PARAMS is empty, so should fall back
        result = resolve_params("BTCUSDT", session="asian", regime="TRENDING")
        self.assertEqual(result["strategy"], "trend_following")

    def test_shrinkage_with_equal_trades(self):
        # With empty SESSION_REGIME_PARAMS, this just returns base params
        result = resolve_params("BTCUSDT", session="london", regime="RANGING",
                                cell_trades=30, parent_trades=30)
        self.assertEqual(result["sl_mult"], 2.5)


class TestHMMRegime(unittest.TestCase):
    """Test HMM regime prediction with trained models."""

    def _make_trending_df(self, n=100):
        dates = pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC")
        close = pd.Series(np.linspace(100, 130, n), index=dates)
        return pd.DataFrame({"close": close, "high": close + 0.5, "low": close - 0.5})

    def test_predict_regime_returns_tuple(self):
        from regime_hmm import predict_regime
        df = self._make_trending_df()
        regime, confidence = predict_regime("BTCUSDT", df)
        self.assertIn(regime, ("TRENDING", "RANGING", "MIXED"))
        self.assertIsInstance(confidence, float)
        self.assertGreaterEqual(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)

    def test_predict_regime_unknown_symbol_returns_mixed(self):
        from regime_hmm import predict_regime
        df = self._make_trending_df()
        regime, confidence = predict_regime("UNKOWNSYMBOL", df)
        self.assertEqual(regime, "MIXED")
        self.assertEqual(confidence, 0.0)

    def test_predict_regime_short_data_returns_mixed(self):
        from regime_hmm import predict_regime
        df = pd.DataFrame({"close": [100, 101]})
        regime, confidence = predict_regime("BTCUSDT", df)
        self.assertEqual(regime, "MIXED")


class TestRegimeRouterHMM(unittest.TestCase):
    """Test that regime_router uses HMM when available."""

    def _make_trending_df(self, n=60):
        dates = pd.date_range("2026-01-01", periods=n, freq="15min")
        close = pd.Series(np.linspace(100, 130, n), index=dates)
        high = close + 0.5
        low = close - 0.5
        atr = pd.Series([2.0] * n, index=dates)
        adx = pd.Series([30.0] * n, index=dates)
        return pd.DataFrame({"close": close, "high": high, "low": low, "atr": atr, "adx": adx})

    def test_classify_regime_with_symbol_uses_hmm(self):
        from regime_router import classify_regime
        df = self._make_trending_df()
        # With HMM model loaded, should return a valid regime
        regime, adx, er, atr_rank = classify_regime(df, symbol="BTCUSDT")
        self.assertIn(regime, ("TRENDING", "RANGING", "VOLATILE", "MIXED"))

    def test_classify_regime_without_symbol_falls_back(self):
        from regime_router import classify_regime
        df = self._make_trending_df()
        regime, adx, er, atr_rank = classify_regime(df, symbol=None)
        self.assertIn(regime, ("TRENDING", "RANGING", "VOLATILE", "MIXED"))

    def test_evaluate_regime_gate_with_symbol(self):
        from regime_router import evaluate_regime_gate
        df = self._make_trending_df()
        result = evaluate_regime_gate(df, strategy="trend_following",
                                       session="london", dqs=60, symbol="BTCUSDT")
        # Gate should return a valid result (allowed or vetoed) with a regime
        self.assertIsNotNone(result)
        self.assertIn(result.regime, ("TRENDING", "RANGING", "VOLATILE", "MIXED"))
        # If HMM says TRENDING, trend_following should be allowed
        # If HMM says RANGING, trend_following would be vetoed (correct behavior)
        if result.regime == "TRENDING":
            self.assertTrue(result.allowed)
        elif result.regime == "RANGING":
            self.assertFalse(result.allowed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
