"""
Trend line pattern detection: wedges, triangles, channels, break/retest.

All functions use the shared pivot cache from pivot_detector.get_pivot_lists()
to avoid recomputing pivots. Linear regression is performed via scipy.stats.linregress
on pivot points. Only confirmed pivots (delayed by RIGHT bars) are used, preventing
look-ahead bias.

Output columns (prefixed pat_):
  pat_wedge_detected, pat_wedge_type, pat_wedge_strength, pat_wedge_bars_to_apex,
  pat_wedge_slope_ratio
  pat_asc_triangle_detected, pat_asc_triangle_strength, pat_asc_triangle_resistance_level,
  pat_asc_triangle_support_slope
  pat_desc_triangle_detected, pat_desc_triangle_strength, pat_desc_triangle_support_level,
  pat_desc_triangle_resistance_slope
  pat_channel_detected, pat_channel_type, pat_channel_slope, pat_channel_width,
  pat_channel_touches
  pat_trend_break_detected, pat_trend_break_direction, pat_trend_break_retest,
  pat_trend_break_strength
"""
import numpy as np
import pandas as pd

from .pivot_detector import get_pivot_lists, LEFT, RIGHT, WINDOW, _PIVOT_CACHE


def _safe_linregress(x, y):
    """Inline linear regression. Returns (slope, intercept, r_value) or zeros.
    Uses pure Python for small arrays (<=8 points) to avoid numpy overhead."""
    n = len(x)
    if n < 2 or len(y) < 2:
        return 0.0, 0.0, 0.0
    if n <= 8:
        # Pure Python fast path — avoids numpy array creation overhead
        x_mean = sum(x) / n
        y_mean = sum(y) / n
        ss_xx = sum((xi - x_mean) ** 2 for xi in x)
        if ss_xx == 0:
            return 0.0, 0.0, 0.0
        ss_xy = sum((x[i] - x_mean) * (y[i] - y_mean) for i in range(n))
        slope = ss_xy / ss_xx
        intercept = y_mean - slope * x_mean
        ss_yy = sum((yi - y_mean) ** 2 for yi in y)
        r_value = ss_xy / (ss_xx * ss_yy) ** 0.5 if ss_yy > 0 else 0.0
        return float(slope), float(intercept), float(r_value)
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    x_mean = x.mean()
    y_mean = y.mean()
    dx = x - x_mean
    dy = y - y_mean
    ss_xx = np.dot(dx, dx)
    if ss_xx == 0:
        return 0.0, 0.0, 0.0
    slope = np.dot(dx, dy) / ss_xx
    intercept = y_mean - slope * x_mean
    ss_yy = np.dot(dy, dy)
    r_value = np.dot(dx, dy) / np.sqrt(ss_xx * ss_yy) if ss_yy > 0 else 0.0
    return float(slope), float(intercept), float(r_value)


def _detect_wedge(pivot_highs, pivot_lows, close, atr):
    """Detect converging trend lines (wedge or symmetrical triangle).

    Returns (detected, wedge_type, strength, bars_to_apex, slope_ratio).
    """
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return False, "", 0.0, 0, 0.0

    x_max = np.array([p[0] for p in pivot_highs], dtype=float)
    y_max = np.array([p[1] for p in pivot_highs], dtype=float)
    x_min = np.array([p[0] for p in pivot_lows], dtype=float)
    y_min = np.array([p[1] for p in pivot_lows], dtype=float)

    slope_max, interc_max, r_max = _safe_linregress(x_max, y_max)
    slope_min, interc_min, r_min = _safe_linregress(x_min, y_min)

    # Need decent fit quality
    if r_max < 0.7 or r_min < 0.7:
        return False, "", 0.0, 0, 0.0

    # Convergence: lines must intersect ahead
    if abs(slope_max - slope_min) < 1e-10:
        return False, "", 0.0, 0, 0.0

    x_intersect = (interc_min - interc_max) / (slope_max - slope_min)
    max_pivot_x = max(x_max[-1], x_min[-1])

    if x_intersect <= max_pivot_x:
        return False, "", 0.0, 0, 0.0

    bars_to_apex = int(x_intersect - max_pivot_x)
    if bars_to_apex > 3 * WINDOW:
        return False, "", 0.0, 0, 0.0

    strength = min(abs(r_max), abs(r_min))

    # Slope ratio for classification
    if abs(slope_max) < 1e-10:
        slope_ratio = 0.0
    else:
        slope_ratio = slope_min / slope_max

    # Classification
    if slope_min > 0 and slope_max > 0 and 0.75 <= abs(slope_ratio) <= 1.25:
        wedge_type = "rising_wedge"
    elif slope_min < 0 and slope_max < 0 and 0.75 <= abs(slope_ratio) <= 1.25:
        wedge_type = "falling_wedge"
    elif slope_max < 0 and slope_min > 0:
        wedge_type = "symmetrical_triangle"
    else:
        return False, "", 0.0, 0, 0.0

    return True, wedge_type, strength, bars_to_apex, slope_ratio


def _detect_asc_triangle(pivot_highs, pivot_lows, close, atr):
    """Detect ascending triangle: flat resistance + rising support."""
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return False, 0.0, 0.0, 0.0

    x_max = np.array([p[0] for p in pivot_highs], dtype=float)
    y_max = np.array([p[1] for p in pivot_highs], dtype=float)
    x_min = np.array([p[0] for p in pivot_lows], dtype=float)
    y_min = np.array([p[1] for p in pivot_lows], dtype=float)

    slope_max, interc_max, r_max = _safe_linregress(x_max, y_max)
    slope_min, interc_min, r_min = _safe_linregress(x_min, y_min)

    # Flat top: slope near zero, low r-value (horizontal)
    tolerance = 0.5 * atr if atr > 0 else 0.0
    price_range = y_max.max() - y_max.min()
    is_flat_top = price_range < tolerance and abs(slope_max) < 1e-6 * (y_max.mean() if y_max.mean() > 0 else 1.0)

    # Rising bottom
    is_rising_bottom = slope_min > 0 and r_min > 0.8

    if is_flat_top and is_rising_bottom:
        resistance_level = float(y_max.mean())
        strength = min(abs(r_min), 1.0)
        return True, strength, resistance_level, float(slope_min)

    return False, 0.0, 0.0, 0.0


def _detect_desc_triangle(pivot_highs, pivot_lows, close, atr):
    """Detect descending triangle: flat support + falling resistance."""
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return False, 0.0, 0.0, 0.0

    x_max = np.array([p[0] for p in pivot_highs], dtype=float)
    y_max = np.array([p[1] for p in pivot_highs], dtype=float)
    x_min = np.array([p[0] for p in pivot_lows], dtype=float)
    y_min = np.array([p[1] for p in pivot_lows], dtype=float)

    slope_max, interc_max, r_max = _safe_linregress(x_max, y_max)
    slope_min, interc_min, r_min = _safe_linregress(x_min, y_min)

    # Flat bottom
    tolerance = 0.5 * atr if atr > 0 else 0.0
    price_range = y_min.max() - y_min.min()
    is_flat_bottom = price_range < tolerance and abs(slope_min) < 1e-6 * (y_min.mean() if y_min.mean() > 0 else 1.0)

    # Falling top
    is_falling_top = slope_max < 0 and r_max > 0.8

    if is_flat_bottom and is_falling_top:
        support_level = float(y_min.mean())
        strength = min(abs(r_max), 1.0)
        return True, strength, support_level, float(slope_max)

    return False, 0.0, 0.0, 0.0


def _detect_channel(pivot_highs, pivot_lows, close, atr):
    """Detect parallel channel (ascending or descending)."""
    if len(pivot_highs) < 2 or len(pivot_lows) < 2:
        return False, "", 0.0, 0.0, 0

    x_max = np.array([p[0] for p in pivot_highs], dtype=float)
    y_max = np.array([p[1] for p in pivot_highs], dtype=float)
    x_min = np.array([p[0] for p in pivot_lows], dtype=float)
    y_min = np.array([p[1] for p in pivot_lows], dtype=float)

    slope_max, interc_max, r_max = _safe_linregress(x_max, y_max)
    slope_min, interc_min, r_min = _safe_linregress(x_min, y_min)

    if r_max < 0.7 or r_min < 0.7:
        return False, "", 0.0, 0.0, 0

    # Parallel test: slopes within 15% of each other
    if abs(slope_max) < 1e-10:
        slope_ratio = 0.0
    else:
        slope_ratio = slope_min / slope_max

    is_parallel = 0.85 <= abs(slope_ratio) <= 1.15

    if not is_parallel:
        return False, "", 0.0, 0.0, 0

    # Channel width consistency
    min_len = min(len(x_max), len(x_min))
    if min_len < 2:
        return False, "", 0.0, 0.0, 0

    # Compute widths at common x range
    x_common = np.linspace(max(x_max[0], x_min[0]), min(x_max[-1], x_min[-1]), min_len)
    y_max_fit = slope_max * x_common + interc_max
    y_min_fit = slope_min * x_common + interc_min
    widths = y_max_fit - y_min_fit
    mean_width = float(widths.mean())
    if mean_width <= 0:
        return False, "", 0.0, 0.0, 0

    width_cv = float(widths.std() / mean_width) if mean_width > 0 else 1.0
    is_consistent = width_cv < 0.3

    if not is_consistent:
        return False, "", 0.0, 0.0, 0

    touches = len(pivot_highs) + len(pivot_lows)
    channel_type = "ascending_channel" if slope_max > 0 else "descending_channel"
    strength = min(abs(r_max), abs(r_min))

    return True, channel_type, float(slope_max), mean_width, touches


def _detect_trend_break(pivot_highs, pivot_lows, close, atr, n):
    """Detect trend line break and retest.

    Uses the most recent established trend line (min 3 touches, r > 0.8),
    then checks if price has broken through and retested.
    """
    if len(pivot_highs) < 3 and len(pivot_lows) < 3:
        return False, "", False, 0.0

    close_val = float(close)
    tolerance = 0.5 * atr if atr > 0 else 0.0

    # Try resistance line (from pivot highs)
    if len(pivot_highs) >= 3:
        x = np.array([p[0] for p in pivot_highs], dtype=float)
        y = np.array([p[1] for p in pivot_highs], dtype=float)
        slope, interc, r = _safe_linregress(x, y)
        if r > 0.8:
            trend_line_at_end = slope * n + interc
            was_resistance = close_val > trend_line_at_end + tolerance
            if was_resistance:
                # Check retest: price within 0.5 ATR of trend line
                retest = abs(close_val - trend_line_at_end) < tolerance
                return True, "bullish_break", retest, float(abs(r))

    # Try support line (from pivot lows)
    if len(pivot_lows) >= 3:
        x = np.array([p[0] for p in pivot_lows], dtype=float)
        y = np.array([p[1] for p in pivot_lows], dtype=float)
        slope, interc, r = _safe_linregress(x, y)
        if r > 0.8:
            trend_line_at_end = slope * n + interc
            was_support = close_val < trend_line_at_end - tolerance
            if was_support:
                retest = abs(close_val - trend_line_at_end) < tolerance
                return True, "bearish_break", retest, float(abs(r))

    return False, "", False, 0.0


def detect_trend_line_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """Detect all trend line patterns and add pat_ columns to df."""
    old_id = id(df)
    df = df.copy()
    # Propagate pivot cache to the copy
    if old_id in _PIVOT_CACHE:
        _PIVOT_CACHE[id(df)] = _PIVOT_CACHE[old_id]
    n = len(df)

    # Pre-allocate numpy arrays for output (much faster than df.iloc per bar)
    wedge_det = np.zeros(n, dtype=bool)
    wedge_type = np.array([""] * n, dtype=object)
    wedge_strength = np.zeros(n, dtype=np.float64)
    wedge_bars_apex = np.zeros(n, dtype=np.int64)
    wedge_slope_ratio = np.zeros(n, dtype=np.float64)

    asc_tri_det = np.zeros(n, dtype=bool)
    asc_tri_strength = np.zeros(n, dtype=np.float64)
    asc_tri_res_level = np.zeros(n, dtype=np.float64)
    asc_tri_sup_slope = np.zeros(n, dtype=np.float64)

    desc_tri_det = np.zeros(n, dtype=bool)
    desc_tri_strength = np.zeros(n, dtype=np.float64)
    desc_tri_sup_level = np.zeros(n, dtype=np.float64)
    desc_tri_res_slope = np.zeros(n, dtype=np.float64)

    channel_det = np.zeros(n, dtype=bool)
    channel_type = np.array([""] * n, dtype=object)
    channel_slope = np.zeros(n, dtype=np.float64)
    channel_width = np.zeros(n, dtype=np.float64)
    channel_touches = np.zeros(n, dtype=np.int64)

    trend_break_det = np.zeros(n, dtype=bool)
    trend_break_dir = np.array([""] * n, dtype=object)
    trend_break_retest = np.zeros(n, dtype=bool)
    trend_break_strength = np.zeros(n, dtype=np.float64)

    closes = df["close"].values
    atrs = df["atr"].values if "atr" in df.columns else np.zeros(n)

    for i in range(LEFT + RIGHT, n):
        pivot_highs, pivot_lows = get_pivot_lists(df, i)
        close_val = closes[i]
        atr_val = atrs[i] if i < len(atrs) else 0.0
        if np.isnan(atr_val):
            atr_val = 0.0

        det, wtype, strength, bars_apex, slope_ratio = _detect_wedge(
            pivot_highs, pivot_lows, close_val, atr_val)
        if det:
            wedge_det[i] = True
            wedge_type[i] = wtype
            wedge_strength[i] = strength
            wedge_bars_apex[i] = bars_apex
            wedge_slope_ratio[i] = slope_ratio

        det, strength, res_level, sup_slope = _detect_asc_triangle(
            pivot_highs, pivot_lows, close_val, atr_val)
        if det:
            asc_tri_det[i] = True
            asc_tri_strength[i] = strength
            asc_tri_res_level[i] = res_level
            asc_tri_sup_slope[i] = sup_slope

        det, strength, sup_level, res_slope = _detect_desc_triangle(
            pivot_highs, pivot_lows, close_val, atr_val)
        if det:
            desc_tri_det[i] = True
            desc_tri_strength[i] = strength
            desc_tri_sup_level[i] = sup_level
            desc_tri_res_slope[i] = res_slope

        det, ctype, slope, width, touches = _detect_channel(
            pivot_highs, pivot_lows, close_val, atr_val)
        if det:
            channel_det[i] = True
            channel_type[i] = ctype
            channel_slope[i] = slope
            channel_width[i] = width
            channel_touches[i] = touches

        det, direction, retest, strength = _detect_trend_break(
            pivot_highs, pivot_lows, close_val, atr_val, i)
        if det:
            trend_break_det[i] = True
            trend_break_dir[i] = direction
            trend_break_retest[i] = retest
            trend_break_strength[i] = strength

    df["pat_wedge_detected"] = wedge_det
    df["pat_wedge_type"] = wedge_type
    df["pat_wedge_strength"] = wedge_strength
    df["pat_wedge_bars_to_apex"] = wedge_bars_apex
    df["pat_wedge_slope_ratio"] = wedge_slope_ratio
    df["pat_asc_triangle_detected"] = asc_tri_det
    df["pat_asc_triangle_strength"] = asc_tri_strength
    df["pat_asc_triangle_resistance_level"] = asc_tri_res_level
    df["pat_asc_triangle_support_slope"] = asc_tri_sup_slope
    df["pat_desc_triangle_detected"] = desc_tri_det
    df["pat_desc_triangle_strength"] = desc_tri_strength
    df["pat_desc_triangle_support_level"] = desc_tri_sup_level
    df["pat_desc_triangle_resistance_slope"] = desc_tri_res_slope
    df["pat_channel_detected"] = channel_det
    df["pat_channel_type"] = channel_type
    df["pat_channel_slope"] = channel_slope
    df["pat_channel_width"] = channel_width
    df["pat_channel_touches"] = channel_touches
    df["pat_trend_break_detected"] = trend_break_det
    df["pat_trend_break_direction"] = trend_break_dir
    df["pat_trend_break_retest"] = trend_break_retest
    df["pat_trend_break_strength"] = trend_break_strength

    return df
