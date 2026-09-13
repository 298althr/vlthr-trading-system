"""Unit tests for VLTHR risk logic — gates, safety layer, TP/SL tuning.

Run: python -m pytest test_risk_logic.py  (or python test_risk_logic.py)
Uses stdlib unittest only — no third-party test framework required.
"""
import unittest
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone, timedelta

_engine = Path(__file__).resolve().parent
sys.path.insert(0, str(_engine))

from portfolio_config import (
    RISK_TIERS, MIN_RR_FLOOR, PORTFOLIO, get_risk_tier, get_sl_tp,
    get_kelly_v7, KELLY_V7_BANDS, MR_BOOST, DQS_VETO_THRESHOLD, MAX_KELLY_FRACTION,
    cap_position_size, CAPITAL_PER_SYMBOL, SYMBOL_ALLOC_CAP, MAX_LEVERAGE,
    tune_tp_from_history, PER_SYMBOL_PARAMS, REGIME_SL_TP_MULTIPLIERS,
)
from portfolio_gates import RiskBudgetLedger, TradeCountGuard, CorrelationGuard
from regime_router import evaluate_regime_gate, compute_efficiency_ratio, classify_regime
import pandas as pd
import numpy as np


class TestRiskTiers(unittest.TestCase):
    def test_fair_tier(self):
        tier = get_risk_tier(55)
        self.assertEqual(tier["tier"], "fair")
        self.assertEqual(tier["risk_pct"], 2.0)

    def test_good_tier(self):
        tier = get_risk_tier(70)
        self.assertEqual(tier["tier"], "good")
        self.assertEqual(tier["risk_pct"], 3.0)

    def test_excellent_tier(self):
        tier = get_risk_tier(90)
        self.assertEqual(tier["tier"], "excellent")
        self.assertEqual(tier["risk_pct"], 5.0)

    def test_dqs_85_in_excellent(self):
        tier = get_risk_tier(85)
        self.assertEqual(tier["tier"], "excellent")
        self.assertLessEqual(85, tier["max_dqs"])

    def test_below_threshold_returns_none(self):
        tier = get_risk_tier(30)
        self.assertEqual(tier["tier"], "none")
        self.assertEqual(tier["risk_pct"], 0)


class TestSLTP(unittest.TestCase):
    def test_long_sl_below_entry(self):
        sl, tp, rr = get_sl_tp(100.0, 2.0, 60, "LONG")
        self.assertLess(sl, 100.0)
        self.assertGreater(tp, 100.0)
        self.assertGreaterEqual(rr, MIN_RR_FLOOR)

    def test_short_sl_above_entry(self):
        sl, tp, rr = get_sl_tp(100.0, 2.0, 60, "SHORT")
        self.assertGreater(sl, 100.0)
        self.assertLess(tp, 100.0)
        self.assertGreaterEqual(rr, MIN_RR_FLOOR)

    def test_below_50_returns_none(self):
        sl, tp, rr = get_sl_tp(100.0, 2.0, 40, "LONG")
        self.assertIsNone(sl)
        self.assertIsNone(tp)

    def test_rr_floor_enforced(self):
        sl, tp, rr = get_sl_tp(100.0, 1.0, 50, "LONG")
        self.assertGreaterEqual(rr, MIN_RR_FLOOR)

    def test_zero_atr_raises(self):
        with self.assertRaises(ValueError):
            get_sl_tp(100.0, 0, 60, "LONG")

    def test_zero_entry_raises(self):
        with self.assertRaises(ValueError):
            get_sl_tp(0, 2.0, 60, "LONG")


class TestKellyV7(unittest.TestCase):
    def test_dqs_85_vetoed(self):
        self.assertEqual(get_kelly_v7(85), 0.0)
        self.assertEqual(get_kelly_v7(90), 0.0)

    def test_dqs_80(self):
        # Band value is 2.5 but get_kelly_v7 caps at MAX_KELLY_FRACTION (1.0)
        self.assertEqual(KELLY_V7_BANDS["dqs_ge_80"], 2.5)
        self.assertEqual(get_kelly_v7(80), min(KELLY_V7_BANDS["dqs_ge_80"], MAX_KELLY_FRACTION))

    def test_mr_boost(self):
        # Use DQS 60 where cap does not apply: base=0.3, MR=0.3*3.0=0.9 (under 1.0 cap)
        base = get_kelly_v7(60, "trend_following")
        mr = get_kelly_v7(60, "mean_reversion")
        self.assertAlmostEqual(mr, base * MR_BOOST)

    def test_below_65(self):
        self.assertEqual(get_kelly_v7(50), KELLY_V7_BANDS["dqs_lt_65"])


class TestCorrelationGuard(unittest.TestCase):
    def setUp(self):
        self.corr = CorrelationGuard()

    def test_can_add_new_symbol(self):
        self.assertTrue(self.corr.can_add([], "BTCUSDT"))

    def test_cannot_add_existing(self):
        self.assertFalse(self.corr.can_add(["BTCUSDT"], "BTCUSDT"))

    def test_max_open_reached(self):
        active = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "DOTUSDT"]
        self.assertFalse(self.corr.can_add(active, "LTCUSDT"))


class TestTradeCountGuard(unittest.TestCase):
    def setUp(self):
        self.mock_conn = MagicMock()
        self.mock_cur = MagicMock()
        self.mock_conn.cursor.return_value = self.mock_cur

    def test_can_open_under_limits(self):
        self.mock_cur.fetchone.side_effect = [(0,), (0,), (0,)]
        guard = TradeCountGuard(self.mock_conn)
        self.assertTrue(guard.can_open("london"))

    def test_cannot_open_at_max_active(self):
        self.mock_cur.fetchone.side_effect = [(8,)]
        guard = TradeCountGuard(self.mock_conn)
        self.assertFalse(guard.can_open("london"))


class TestRiskBudgetLedger(unittest.TestCase):
    def setUp(self):
        self.mock_conn = MagicMock()
        self.mock_cur = MagicMock()
        self.mock_conn.cursor.return_value = self.mock_cur

    def test_has_capacity_under_limit(self):
        self.mock_cur.fetchone.return_value = (50.0,)
        ledger = RiskBudgetLedger(self.mock_conn)
        self.assertTrue(ledger.has_capacity(100.0, 10000.0))

    def test_no_capacity_over_limit(self):
        self.mock_cur.fetchone.return_value = (1150.0,)
        ledger = RiskBudgetLedger(self.mock_conn)
        # 1150 + 100 = 1250 > 1200 (12% of 10000)
        self.assertFalse(ledger.has_capacity(100.0, 10000.0))

    def test_zero_balance_no_capacity(self):
        ledger = RiskBudgetLedger(self.mock_conn)
        self.assertFalse(ledger.has_capacity(10.0, 0.0))


class TestPositionCap(unittest.TestCase):
    def test_cap_at_max_leverage(self):
        # V7 fix: cap is now leverage-based, not arbitrary notional.
        # $10k account * 4x leverage = $40k max notional.
        result = cap_position_size(50000.0, 10000.0)
        self.assertEqual(result, 10000.0 * MAX_LEVERAGE)

    def test_cap_not_bounded_by_capital_per_symbol(self):
        # V7 fix: CAPITAL_PER_SYMBOL no longer caps notional.
        # With $100k account, 4x cap = $400k, not $2k.
        result = cap_position_size(50000.0, 100000.0)
        self.assertGreater(result, CAPITAL_PER_SYMBOL)

    def test_small_position_unchanged(self):
        result = cap_position_size(100.0, 10000.0)
        self.assertEqual(result, 100.0)


class TestTPTuning(unittest.TestCase):
    def setUp(self):
        import portfolio_config as _pc
        self._saved_cache = dict(_pc._TP_TUNING_CACHE)
        self._saved_tiers = {k: dict(v) for k, v in _pc.RISK_TIERS.items()}

    def tearDown(self):
        import portfolio_config as _pc
        _pc._TP_TUNING_CACHE.clear()
        _pc._TP_TUNING_CACHE.update(self._saved_cache)
        for k, v in self._saved_tiers.items():
            _pc.RISK_TIERS[k].update(v)

    def test_no_tuning_under_20_trades(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = [("SL_HIT", 5), ("TIME_EXIT", 3)]
        original_tp = {k: v["tp_mult"] for k, v in RISK_TIERS.items()}
        tune_tp_from_history(mock_conn)
        for k, v in RISK_TIERS.items():
            self.assertEqual(v["tp_mult"], original_tp[k])

    def test_tuning_applies_with_0_tp_and_many_time_exits(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = [("SL_HIT", 10), ("TIME_EXIT", 10)]
        original_tp = {k: v["tp_mult"] for k, v in RISK_TIERS.items()}
        tune_tp_from_history(mock_conn)
        for k, v in RISK_TIERS.items():
            floor = v["sl_mult"] * MIN_RR_FLOOR
            expected = max(round(original_tp[k] * 0.80, 1), floor)
            self.assertEqual(v["tp_mult"], expected)
            self.assertGreaterEqual(v["tp_mult"], floor)

    def test_tuning_applies_only_once(self):
        import portfolio_config as _pc
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = [("SL_HIT", 10), ("TIME_EXIT", 10)]
        tune_tp_from_history(mock_conn)
        first_tps = {k: v["tp_mult"] for k, v in RISK_TIERS.items()}
        # Second call should be a no-op
        tune_tp_from_history(mock_conn)
        for k, v in RISK_TIERS.items():
            self.assertEqual(v["tp_mult"], first_tps[k])


class TestDirectionalGuard(unittest.TestCase):
    """Test that max_same_side config is present and enforced."""
    def test_max_same_side_exists(self):
        self.assertIn("max_same_side", PORTFOLIO)
        self.assertGreaterEqual(PORTFOLIO["max_same_side"], 1)

    def test_max_same_side_value(self):
        self.assertEqual(PORTFOLIO["max_same_side"], 5)


class TestDrawdownHaltConfig(unittest.TestCase):
    def test_drawdown_halt_pct_exists(self):
        self.assertIn("account_drawdown_halt_pct", PORTFOLIO)

    def test_drawdown_halt_pct_sane(self):
        pct = PORTFOLIO["account_drawdown_halt_pct"]
        self.assertGreater(pct, 0)
        self.assertLess(pct, 1.0)


class TestPerSymbolSLTP(unittest.TestCase):
    """Test that get_sl_tp uses per-symbol multipliers when symbol is provided."""

    def test_btcusdt_symbol_uses_per_symbol_mults(self):
        """BTCUSDT sl_mult=2.5, tp_mult=3.0 should produce different SL/TP than generic tier at DQS 70."""
        # At DQS 70, generic 'good' tier has sl_mult=3.0, tp_mult=4.0.
        # BTCUSDT per-symbol has sl_mult=2.5, tp_mult=3.0. Both should differ.
        sl_sym, tp_sym, rr_sym = get_sl_tp(100.0, 2.0, 70, "LONG", symbol="BTCUSDT")
        sl_gen, tp_gen, rr_gen = get_sl_tp(100.0, 2.0, 70, "LONG")
        self.assertNotEqual(sl_sym, sl_gen, "Per-symbol SL should differ from generic")
        self.assertNotEqual(tp_sym, tp_gen, "Per-symbol TP should differ from generic")

    def test_solusdt_symbol_uses_per_symbol_mults(self):
        sl, tp, rr = get_sl_tp(100.0, 2.0, 60, "LONG", symbol="SOLUSDT")
        self.assertIsNotNone(sl)
        self.assertIsNotNone(tp)
        self.assertGreaterEqual(rr, MIN_RR_FLOOR)

    def test_symbol_with_regime_combines_both(self):
        """Per-symbol mults should be further scaled by regime multipliers."""
        sl_base, tp_base, rr_base = get_sl_tp(100.0, 2.0, 60, "LONG", symbol="BTCUSDT")
        sl_trend, tp_trend, rr_trend = get_sl_tp(100.0, 2.0, 60, "LONG", symbol="BTCUSDT", regime="TRENDING")
        # TRENDING scales sl_mult by 1.3, so SL should be wider
        self.assertNotEqual(sl_base, sl_trend, "Regime scaling should change SL")

    def test_unknown_symbol_falls_back_to_tier(self):
        """Unknown symbol should fall back to generic tier multipliers."""
        sl, tp, rr = get_sl_tp(100.0, 2.0, 60, "LONG", symbol="UNKNOWNUSDT")
        sl_gen, tp_gen, rr_gen = get_sl_tp(100.0, 2.0, 60, "LONG")
        self.assertEqual(sl, sl_gen, "Unknown symbol should use generic tier")
        self.assertEqual(tp, tp_gen, "Unknown symbol should use generic tier")


class TestDailyLossBreakerConfig(unittest.TestCase):
    """Test that daily_loss_halt_pct is properly configured."""

    def test_daily_loss_halt_pct_exists(self):
        self.assertIn("daily_loss_halt_pct", PORTFOLIO)

    def test_daily_loss_halt_pct_sane(self):
        pct = PORTFOLIO["daily_loss_halt_pct"]
        self.assertGreater(pct, 0)
        self.assertLess(pct, 10, "Daily loss halt should be < 10%")


class TestRegimeRouter(unittest.TestCase):
    """Test the regime router gate logic."""

    def _make_trending_df(self, n=60):
        """Create a DataFrame with strong trend (high ADX, high ER)."""
        dates = pd.date_range("2026-01-01", periods=n, freq="15min")
        close = pd.Series(np.linspace(100, 130, n), index=dates)  # steady uptrend
        high = close + 0.5
        low = close - 0.5
        atr = pd.Series([2.0] * n, index=dates)
        adx = pd.Series([30.0] * n, index=dates)  # above 25 threshold
        return pd.DataFrame({"close": close, "high": high, "low": low, "atr": atr, "adx": adx})

    def _make_ranging_df(self, n=60):
        """Create a DataFrame with choppy/ranging market (low ADX, low ER).
        Uses a sine wave with period 20 so over the 20-bar ER lookback the
        start and end prices coincide, producing ER near 0."""
        dates = pd.date_range("2026-01-01", periods=n, freq="15min")
        i = np.arange(n)
        close = pd.Series(100 + 2 * np.sin(2 * np.pi * i / 20), index=dates)
        high = close + 0.5
        low = close - 0.5
        atr = pd.Series([1.0] * n, index=dates)
        adx = pd.Series([15.0] * n, index=dates)  # below 20 threshold
        return pd.DataFrame({"close": close, "high": high, "low": low, "atr": atr, "adx": adx})

    def _make_volatile_df(self, n=60):
        """Create a DataFrame with high volatility (high ATR rank).
        Constant close ensures atr_pct is directly proportional to ATR.
        ATR ramps up in the last 20 bars so the percentile rank is >= 0.80."""
        dates = pd.date_range("2026-01-01", periods=n, freq="15min")
        close = pd.Series([100.0] * n, index=dates)  # constant close
        high = close + 5
        low = close - 5
        # First 40 bars: low ATR. Last 20 bars: high ATR.
        atr_vals = [1.0] * 40 + [8.0] * 20
        atr = pd.Series(atr_vals, index=dates)
        adx = pd.Series([22.0] * n, index=dates)
        return pd.DataFrame({"close": close, "high": high, "low": low, "atr": atr, "adx": adx})

    def test_trending_allows_trend_following(self):
        df = self._make_trending_df()
        result = evaluate_regime_gate(df, strategy="trend_following", session="london", dqs=60)
        self.assertTrue(result.allowed, f"Trend_following should be allowed in TRENDING: {result.reason}")
        self.assertEqual(result.regime, "TRENDING")

    def test_trending_vetoes_mean_reversion(self):
        df = self._make_trending_df()
        result = evaluate_regime_gate(df, strategy="mean_reversion", session="london", dqs=60)
        self.assertFalse(result.allowed, f"Mean_reversion should be vetoed in TRENDING: {result.reason}")

    def test_ranging_allows_mean_reversion(self):
        df = self._make_ranging_df()
        result = evaluate_regime_gate(df, strategy="mean_reversion", session="london", dqs=60)
        self.assertTrue(result.allowed, f"Mean_reversion should be allowed in RANGING: {result.reason}")
        self.assertEqual(result.regime, "RANGING")

    def test_ranging_vetoes_trend_following(self):
        df = self._make_ranging_df()
        result = evaluate_regime_gate(df, strategy="trend_following", session="london", dqs=60)
        self.assertFalse(result.allowed, f"Trend_following should be vetoed in RANGING: {result.reason}")

    def test_mixed_allows_both(self):
        """MIXED regime should allow both strategies (safe default)."""
        dates = pd.date_range("2026-01-01", periods=60, freq="15min")
        close = pd.Series(np.linspace(100, 105, 60), index=dates)
        df = pd.DataFrame({
            "close": close, "high": close + 1, "low": close - 1,
            "atr": pd.Series([2.0] * 60), "adx": pd.Series([22.0] * 60),
        })
        result_tf = evaluate_regime_gate(df, strategy="trend_following", session="london", dqs=60)
        result_mr = evaluate_regime_gate(df, strategy="mean_reversion", session="london", dqs=60)
        self.assertTrue(result_tf.allowed, f"Trend_following should be allowed in MIXED: {result_tf.reason}")
        self.assertTrue(result_mr.allowed, f"Mean_reversion should be allowed in MIXED: {result_mr.reason}")

    def test_volatile_vetoes_low_dqs(self):
        df = self._make_volatile_df()
        result = evaluate_regime_gate(df, strategy="trend_following", session="london", dqs=55)
        self.assertFalse(result.allowed, f"Low DQS should be vetoed in VOLATILE: {result.reason}")
        self.assertEqual(result.regime, "VOLATILE")

    def test_volatile_allows_high_dqs(self):
        df = self._make_volatile_df()
        result = evaluate_regime_gate(df, strategy="trend_following", session="london", dqs=70)
        self.assertTrue(result.allowed, f"High DQS should be allowed in VOLATILE: {result.reason}")

    def test_efficiency_ratio_trending(self):
        df = self._make_trending_df()
        er = compute_efficiency_ratio(df)
        self.assertGreater(er, 0.5, f"ER should be high for trending data: {er}")

    def test_efficiency_ratio_ranging(self):
        df = self._make_ranging_df()
        er = compute_efficiency_ratio(df)
        self.assertLess(er, 0.3, f"ER should be low for ranging data: {er}")

    def test_short_data_returns_mixed(self):
        """Insufficient data should default to MIXED regime."""
        df = pd.DataFrame({"close": [100, 101], "high": [101, 102], "low": [99, 100],
                           "atr": [1.0, 1.0], "adx": [20, 20]})
        regime, adx, er, atr_rank = classify_regime(df)
        self.assertEqual(regime, "MIXED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
