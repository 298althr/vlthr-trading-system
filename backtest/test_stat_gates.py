#!/usr/bin/env python3
"""Unit Tests for Statistical Gate Infrastructure
=================================================
Tests all 4 functions in stat_gates.py with synthetic data where the
expected result is known analytically.

Run:
  cd /path/to/DEVOPS/backtest
  python3 -m pytest test_stat_gates.py -v
  # or without pytest:
  python3 test_stat_gates.py
"""
import math
import unittest

import numpy as np

from stat_gates import (
    mann_whitney_test,
    validate_sample_size,
    deflated_sharpe_ratio,
    walk_forward_efficiency,
    evaluate_cell,
    _compute_sharpe,
)


class TestMannWhitney(unittest.TestCase):
    """Test Mann-Whitney U test wrapper."""

    def test_identical_distributions_not_significant(self):
        np.random.seed(42)
        a = np.random.normal(0, 1, 200)
        b = np.random.normal(0, 1, 200)
        result = mann_whitney_test(a, b, alpha=0.05)
        self.assertFalse(result.significant, "Identical distributions should not be significant")
        self.assertGreater(result.p_value, 0.05)
        self.assertLess(result.effect_size_r, 0.15)

    def test_different_distributions_significant(self):
        np.random.seed(42)
        a = np.random.normal(0.5, 1, 200)
        b = np.random.normal(0.0, 1, 200)
        result = mann_whitney_test(a, b, alpha=0.05)
        self.assertTrue(result.significant, "Different distributions should be significant")
        self.assertLess(result.p_value, 0.05)
        self.assertGreater(result.effect_size_r, 0.1)

    def test_small_samples_returns_not_significant(self):
        a = np.array([1.0, 2.0, 3.0])
        b = np.array([4.0, 5.0, 6.0])
        result = mann_whitney_test(a, b, alpha=0.05)
        self.assertFalse(result.significant)
        self.assertEqual(result.p_value, 1.0)

    def test_nan_handling(self):
        a = np.array([1.0, 2.0, np.nan, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0])
        b = np.array([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0])
        result = mann_whitney_test(a, b, alpha=0.05)
        self.assertFalse(math.isnan(result.u_statistic))
        self.assertFalse(math.isnan(result.p_value))

    def test_returns_correct_types(self):
        a = np.random.normal(0, 1, 50)
        b = np.random.normal(0.5, 1, 50)
        result = mann_whitney_test(a, b)
        self.assertIsInstance(result.u_statistic, float)
        self.assertIsInstance(result.p_value, float)
        self.assertIsInstance(result.effect_size_r, float)
        self.assertIsInstance(result.significant, bool)


class TestSampleSizeValidator(unittest.TestCase):
    """Test sample-size validation against 30 x N rule."""

    def test_passes_when_above_threshold(self):
        result = validate_sample_size(100, 3)
        self.assertTrue(result.passed)
        self.assertEqual(result.required_trades, 90)
        self.assertAlmostEqual(result.ratio, 100 / 90, places=2)

    def test_fails_when_below_threshold(self):
        result = validate_sample_size(45, 3)
        self.assertFalse(result.passed)
        self.assertEqual(result.required_trades, 90)

    def test_exact_boundary(self):
        result = validate_sample_size(90, 3)
        self.assertTrue(result.passed, "Exactly 90 trades with 3 params should pass")

    def test_one_below_boundary(self):
        result = validate_sample_size(89, 3)
        self.assertFalse(result.passed, "89 trades with 3 params should fail")

    def test_single_param(self):
        result = validate_sample_size(35, 1)
        self.assertTrue(result.passed)
        self.assertEqual(result.required_trades, 30)

    def test_zero_trades(self):
        result = validate_sample_size(0, 3)
        self.assertFalse(result.passed)
        self.assertEqual(result.ratio, 0.0)

    def test_invalid_params_raises(self):
        with self.assertRaises(ValueError):
            validate_sample_size(100, 0)
        with self.assertRaises(ValueError):
            validate_sample_size(100, -1)

    def test_negative_trades_raises(self):
        with self.assertRaises(ValueError):
            validate_sample_size(-1, 3)


class TestDeflatedSharpeRatio(unittest.TestCase):
    """Test DSR calculation with known synthetic data."""

    def test_high_sharpe_few_trials_passes(self):
        np.random.seed(42)
        returns = np.random.normal(0.002, 0.01, 252)
        sharpe = _compute_sharpe(returns)
        result = deflated_sharpe_ratio(sharpe, n_trials=1, returns=returns, threshold=0.80)
        self.assertGreater(result.dsr, 0.80, "High Sharpe with 1 trial should pass DSR")

    def test_moderate_sharpe_many_trials_fails(self):
        np.random.seed(42)
        returns = np.random.normal(0.0005, 0.015, 252)
        sharpe = _compute_sharpe(returns)
        result = deflated_sharpe_ratio(sharpe, n_trials=100, returns=returns, threshold=0.80)
        self.assertLess(result.dsr, 0.50, "Low Sharpe with 100 trials should fail DSR")

    def test_dsr_decreases_with_more_trials(self):
        np.random.seed(42)
        returns = np.random.normal(0.001, 0.01, 252)
        sharpe = _compute_sharpe(returns)
        dsr_1 = deflated_sharpe_ratio(sharpe, 1, returns)
        dsr_10 = deflated_sharpe_ratio(sharpe, 10, returns)
        dsr_50 = deflated_sharpe_ratio(sharpe, 50, returns)
        self.assertGreater(dsr_1.dsr, dsr_10.dsr, "DSR should decrease with more trials")
        self.assertGreater(dsr_10.dsr, dsr_50.dsr, "DSR should decrease with more trials")

    def test_dsr_increases_with_higher_sharpe(self):
        np.random.seed(42)
        returns = np.random.normal(0.001, 0.01, 252)
        dsr_low = deflated_sharpe_ratio(0.5, 10, returns)
        dsr_high = deflated_sharpe_ratio(3.0, 10, returns)
        self.assertGreater(dsr_high.dsr, dsr_low.dsr, "Higher Sharpe should give higher DSR")

    def test_threshold_respected(self):
        np.random.seed(42)
        returns = np.random.normal(0.001, 0.01, 252)
        result = deflated_sharpe_ratio(2.0, 5, returns, threshold=0.95)
        self.assertEqual(result.threshold, 0.95)

    def test_insufficient_data_returns_zero(self):
        returns = np.array([0.01])
        result = deflated_sharpe_ratio(1.0, 5, returns)
        self.assertEqual(result.dsr, 0.0)
        self.assertFalse(result.passed)

    def test_dsr_bounded_0_to_1(self):
        np.random.seed(42)
        returns = np.random.normal(0.001, 0.01, 252)
        for sr in [0.0, 0.5, 1.0, 2.0, 5.0]:
            for trials in [1, 5, 20, 100]:
                result = deflated_sharpe_ratio(sr, trials, returns)
                self.assertGreaterEqual(result.dsr, 0.0, f"DSR < 0 for SR={sr}, trials={trials}")
                self.assertLessEqual(result.dsr, 1.0, f"DSR > 1 for SR={sr}, trials={trials}")


class TestWalkForwardEfficiency(unittest.TestCase):
    """Test WFE calculator."""

    def test_equal_returns_passes(self):
        is_ret = np.array([0.001] * 180)
        oos_ret = np.array([0.001] * 60)
        result = walk_forward_efficiency(is_ret, oos_ret, 180, 60, threshold=0.5)
        self.assertAlmostEqual(result.wfe, 1.0, places=2)
        self.assertTrue(result.passed)

    def test_oos_half_of_is_passes_at_threshold(self):
        # IS: 0.002/bar for 180 bars. OOS: 0.002/bar for 60 bars (same per-bar return).
        # Annualized returns will be equal, so WFE = 1.0.
        is_ret = np.array([0.002] * 180)
        oos_ret = np.array([0.002] * 60)
        result = walk_forward_efficiency(is_ret, oos_ret, 180, 60, threshold=0.5)
        self.assertGreaterEqual(result.wfe, 0.5)
        self.assertTrue(result.passed)

    def test_oos_near_zero_fails(self):
        is_ret = np.array([0.002] * 180)
        oos_ret = np.array([0.0001] * 60)
        result = walk_forward_efficiency(is_ret, oos_ret, 180, 60, threshold=0.5)
        self.assertLess(result.wfe, 0.5)
        self.assertFalse(result.passed)

    def test_negative_oos_fails(self):
        is_ret = np.array([0.002] * 180)
        oos_ret = np.array([-0.001] * 60)
        result = walk_forward_efficiency(is_ret, oos_ret, 180, 60, threshold=0.5)
        self.assertLess(result.wfe, 0.0)
        self.assertFalse(result.passed)

    def test_empty_returns_fails(self):
        result = walk_forward_efficiency(np.array([]), np.array([0.01] * 60), 0, 60)
        self.assertFalse(result.passed)
        self.assertEqual(result.wfe, 0.0)

    def test_nan_handling(self):
        is_ret = np.array([0.001, np.nan, 0.002, 0.001, 0.001] * 36)
        oos_ret = np.array([0.001, np.nan, 0.001] * 20)
        result = walk_forward_efficiency(is_ret, oos_ret, 180, 60, threshold=0.5)
        self.assertFalse(math.isnan(result.wfe))


class TestEvaluateCell(unittest.TestCase):
    """Test the combined cell evaluation function."""

    def test_all_gates_pass(self):
        np.random.seed(42)
        cell_returns = np.random.normal(0.002, 0.01, 252)
        parent_returns = np.random.normal(0.0005, 0.01, 252)
        is_ret = np.random.normal(0.002, 0.01, 180)
        oos_ret = np.random.normal(0.0015, 0.01, 60)
        sharpe = _compute_sharpe(cell_returns)

        result = evaluate_cell(
            cell_trades=100,
            free_params=3,
            cell_returns=cell_returns,
            parent_returns=parent_returns,
            n_trials=5,
            is_returns=is_ret,
            oos_returns=oos_ret,
            is_periods=180,
            oos_periods=60,
        )
        self.assertTrue(result.sample_size.passed)
        self.assertIsNotNone(result.mann_whitney)
        self.assertIsNotNone(result.dsr)
        self.assertIsNotNone(result.wfe)

    def test_sample_size_only(self):
        result = evaluate_cell(cell_trades=50, free_params=3)
        self.assertFalse(result.sample_size.passed)
        self.assertIsNone(result.mann_whitney)
        self.assertIsNone(result.dsr)
        self.assertIsNone(result.wfe)
        self.assertFalse(result.overall_pass)

    def test_sample_size_passes_but_no_other_gates(self):
        result = evaluate_cell(cell_trades=100, free_params=3)
        self.assertTrue(result.sample_size.passed)
        self.assertTrue(result.overall_pass, "Only gate checked is sample_size, which passes")


class TestSharpeHelper(unittest.TestCase):
    """Test the internal _compute_sharpe function."""

    def test_zero_returns(self):
        result = _compute_sharpe(np.array([0.0] * 100))
        self.assertEqual(result, 0.0)

    def test_positive_returns(self):
        np.random.seed(42)
        returns = np.random.normal(0.001, 0.005, 252)
        result = _compute_sharpe(returns)
        self.assertGreater(result, 0.0)

    def test_insufficient_data(self):
        result = _compute_sharpe(np.array([0.01]))
        self.assertEqual(result, 0.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
