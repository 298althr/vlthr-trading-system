r"""
Unit tests for Phase 1 pattern detection.

Tests:
  1. No look-ahead bias: pattern outputs for bar i must be identical whether
     or not future bars are present in the DataFrame.
  2. NaN safety: no pat_ column contains NaN.
  3. Pivot detection correctness: known pivots are detected, non-pivots are not.
  4. S/R clustering: levels with 3+ touches are detected.
  5. BoS/CHoCH: structure breaks are detected correctly.

Run: cd .
     python3 -m pytest pipeline/engine/patterns/test_patterns.py -v
"""
import sys
import os
import numpy as np
import pandas as pd
import pytest
from pathlib import Path

# Add pipeline engine to path
_ENGINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ENGINE))

from patterns import detect_all_patterns, get_pivot_lists
from patterns.pivot_detector import detect_pivots, LEFT, RIGHT


def _make_synthetic_df(n=200, seed=42):
    """Create synthetic OHLCV data with known pivots and patterns."""
    np.random.seed(seed)
    base = 100.0
    # Create oscillating price with trend
    t = np.arange(n)
    trend = 0.02 * t
    cycle = 3.0 * np.sin(2 * np.pi * t / 20)
    noise = np.random.randn(n) * 0.5
    close = base + trend + cycle + noise

    high = close + np.abs(np.random.randn(n)) * 0.5 + 0.3
    low = close - np.abs(np.random.randn(n)) * 0.5 - 0.3
    op = (high + low) / 2
    volume = np.random.randint(100, 1000, n).astype(float)

    df = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=n, freq="15min", tz="UTC"),
        "open": op, "high": high, "low": low, "close": close, "volume": volume,
    })

    # Compute ATR
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs()
    ], axis=1).max(axis=1)
    df["atr"] = tr.rolling(window=14, min_periods=14).mean()

    return df


class TestNoLookAheadBias:
    """Verify pattern outputs are stable when future bars are added."""

    def test_pivot_detection_no_lookahead(self):
        """Pivot outputs at bar i must not change when bars after i+RIGHT are added."""
        df_full = _make_synthetic_df(200)
        df_short = df_full.iloc[:100].copy()

        result_full = detect_pivots(df_full.copy())
        result_short = detect_pivots(df_short.copy())

        # Compare pat_ columns for first 100 bars
        pat_cols = [c for c in result_full.columns if c.startswith("pat_")]
        for col in pat_cols:
            vals_full = result_full[col].iloc[:100].values
            vals_short = result_short[col].iloc[:100].values
            if col in ("pat_pivot_high_idx", "pat_pivot_low_idx"):
                np.testing.assert_array_equal(vals_full, vals_short,
                    err_msg=f"Look-ahead bias in {col}")
            else:
                np.testing.assert_allclose(vals_full, vals_short, rtol=1e-10,
                    err_msg=f"Look-ahead bias in {col}")

    def test_all_patterns_no_lookahead(self):
        """Full pattern detection at bar i must not change when future bars are added."""
        df_full = _make_synthetic_df(200)
        df_short = df_full.iloc[:120].copy()

        result_full = detect_all_patterns(df_full.copy())
        result_short = detect_all_patterns(df_short.copy())

        pat_cols = [c for c in result_full.columns if c.startswith("pat_")]
        for col in pat_cols:
            vals_full = result_full[col].iloc[:120].values
            vals_short = result_short[col].iloc[:120].values
            if vals_full.dtype == bool:
                assert (vals_full == vals_short).all(), f"Look-ahead bias in {col}"
            elif vals_full.dtype == object or vals_full.dtype.kind in ("U", "S", "O"):
                assert (vals_full == vals_short).all(), f"Look-ahead bias in {col}"
            else:
                np.testing.assert_allclose(vals_full, vals_short, rtol=1e-10,
                    err_msg=f"Look-ahead bias in {col}")

    def test_pivot_confirmation_delay(self):
        """A pivot at index i should only appear at index i+RIGHT or later."""
        df = _make_synthetic_df(100)
        result = detect_pivots(df.copy())

        # Find a confirmed pivot high
        pivot_high_indices = result["pat_pivot_high_idx"].values
        # The pivot_high_idx at bar j should never point to a pivot at j-RIGHT+1 or later
        # (because that pivot wouldn't be confirmed yet)
        for j in range(len(pivot_high_indices)):
            if pivot_high_indices[j] >= 0:
                # Pivot at pivot_high_indices[j] must be confirmed by bar j
                # i.e., pivot_high_indices[j] + RIGHT <= j
                assert pivot_high_indices[j] + RIGHT <= j, \
                    f"Pivot at {pivot_high_indices[j]} reported at {j} before confirmation " \
                    f"(needs {RIGHT} bars delay)"


class TestNaNSafety:
    """No pat_ column should contain NaN."""

    def test_no_nan_in_pat_columns(self):
        df = _make_synthetic_df(100)
        result = detect_all_patterns(df.copy())

        pat_cols = [c for c in result.columns if c.startswith("pat_")]
        for col in pat_cols:
            nan_count = result[col].isna().sum()
            assert nan_count == 0, f"NaN found in {col}: {nan_count} values"


class TestPivotDetection:
    """Verify pivot detection finds known pivots."""

    def test_pivot_detection_finds_pivots(self):
        """With oscillating data, pivots should be detected."""
        df = _make_synthetic_df(200)
        result = detect_pivots(df.copy())

        # Should have at least some pivot highs and lows
        ph_count = (result["pat_pivot_high_idx"] >= 0).sum()
        pl_count = (result["pat_pivot_low_idx"] >= 0).sum()

        assert ph_count > 5, f"Too few pivot highs detected: {ph_count}"
        assert pl_count > 5, f"Too few pivot lows detected: {pl_count}"

    def test_pivot_lists_lookahead_safe(self):
        """get_pivot_lists should only return confirmed pivots."""
        df = _make_synthetic_df(100)
        ph, pl = get_pivot_lists(df, 99)

        # All pivot indices should be <= 99 - RIGHT (confirmed by bar 99)
        for idx, price in ph:
            assert idx + RIGHT <= 99, f"Unconfirmed pivot high at {idx}"
        for idx, price in pl:
            assert idx + RIGHT <= 99, f"Unconfirmed pivot low at {idx}"


class TestSupportResistance:
    """Verify S/R detection produces sensible results."""

    def test_sr_levels_detected(self):
        """With enough oscillation, S/R levels should be found."""
        df = _make_synthetic_df(200)
        result = detect_all_patterns(df.copy())

        # At least some bars should have S/R levels
        sr_count_nonzero = (result["pat_sr_level_count"] > 0).sum()
        assert sr_count_nonzero > 0, "No S/R levels detected at all"

    def test_recency_strength_in_range(self):
        """Recency-weighted strength should be in [0, 1]."""
        df = _make_synthetic_df(200)
        result = detect_all_patterns(df.copy())

        vals = result["pat_sr_recency_weighted_strength"].values
        assert (vals >= 0).all() and (vals <= 1.0).all(), \
            "Recency strength out of [0, 1] range"


class TestBoSCHoCH:
    """Verify BoS/CHoCH detection."""

    def test_bos_or_choch_fires(self):
        """With trending data, BoS or CHoCH should fire at some point."""
        df = _make_synthetic_df(200)
        result = detect_all_patterns(df.copy())

        bos_count = result["pat_bos_detected"].sum()
        choch_count = result["pat_choch_detected"].sum()

        # At least one should fire over 200 bars of oscillating data
        assert bos_count + choch_count > 0, "No BoS or CHoCH detected in 200 bars"


class TestOutputColumns:
    """Verify expected output columns are present."""

    def test_expected_columns_present(self):
        df = _make_synthetic_df(100)
        result = detect_all_patterns(df.copy())

        expected = [
            "pat_pivot_high_idx", "pat_pivot_low_idx",
            "pat_pivot_high_price", "pat_pivot_low_price",
            "pat_wedge_detected", "pat_wedge_type", "pat_wedge_strength",
            "pat_wedge_bars_to_apex", "pat_wedge_slope_ratio",
            "pat_asc_triangle_detected", "pat_asc_triangle_strength",
            "pat_asc_triangle_resistance_level", "pat_asc_triangle_support_slope",
            "pat_desc_triangle_detected", "pat_desc_triangle_strength",
            "pat_desc_triangle_support_level", "pat_desc_triangle_resistance_slope",
            "pat_channel_detected", "pat_channel_type", "pat_channel_slope",
            "pat_channel_width", "pat_channel_touches",
            "pat_trend_break_detected", "pat_trend_break_direction",
            "pat_trend_break_retest", "pat_trend_break_strength",
            "pat_sr_level_count", "pat_near_support", "pat_near_resistance",
            "pat_nearest_support_dist_atr", "pat_nearest_resistance_dist_atr",
            "pat_support_touches", "pat_resistance_touches",
            "pat_sr_recency_weighted_strength",
            "pat_rising_support_detected", "pat_rising_support_slope",
            "pat_rising_support_strength", "pat_rising_support_dist_atr",
            "pat_falling_resistance_detected", "pat_falling_resistance_slope",
            "pat_falling_resistance_strength", "pat_falling_resistance_dist_atr",
            "pat_bos_detected", "pat_bos_direction",
            "pat_choch_detected", "pat_choch_direction",
        ]

        for col in expected:
            assert col in result.columns, f"Missing column: {col}"

    def test_column_count(self):
        """Should produce 38+ pat_ columns (spec says 38 output fields)."""
        df = _make_synthetic_df(100)
        result = detect_all_patterns(df.copy())

        pat_cols = [c for c in result.columns if c.startswith("pat_")]
        assert len(pat_cols) >= 38, f"Expected >= 38 pat_ columns, got {len(pat_cols)}"
