"""
Support/resistance detection: horizontal S/R clustering, dynamic S/R,
Break of Structure (BoS) / Change of Character (CHoCH).

Uses shared pivot cache from pivot_detector.get_pivot_lists(). S/R levels
are clustered by price proximity (within 0.5 ATR). Recency decay is applied
to touch weights so stale levels carry less strength.

Output columns (prefixed pat_):
  pat_sr_level_count, pat_near_support, pat_near_resistance,
  pat_nearest_support_dist_atr, pat_nearest_resistance_dist_atr,
  pat_support_touches, pat_resistance_touches,
  pat_sr_recency_weighted_strength
  pat_rising_support_detected, pat_rising_support_slope,
  pat_rising_support_strength, pat_rising_support_dist_atr
  pat_falling_resistance_detected, pat_falling_resistance_slope,
  pat_falling_resistance_strength, pat_falling_resistance_dist_atr
  pat_bos_detected, pat_bos_direction, pat_choch_detected, pat_choch_direction
"""
import numpy as np
import pandas as pd

from .pivot_detector import get_pivot_lists, LEFT, RIGHT, WINDOW, _PIVOT_CACHE


def _safe_linregress(x, y):
    if len(x) < 2 or len(y) < 2:
        return 0.0, 0.0, 0.0
    n = len(x)
    if n <= 8:
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


def _cluster_sr_levels(pivots, atr, tolerance_factor=0.5):
    """Cluster pivot prices by proximity within tolerance_factor * ATR.

    Returns list of (center_price, touch_count, touch_indices) tuples.
    """
    if not pivots or atr <= 0:
        return []

    tolerance = tolerance_factor * atr
    clusters = []  # each: {"center": float, "touches": [(idx, price)]}

    for idx, price in pivots:
        placed = False
        for c in clusters:
            if abs(price - c["center"]) < tolerance:
                c["touches"].append((idx, price))
                # Update center as mean of all touch prices
                c["center"] = sum(t[1] for t in c["touches"]) / len(c["touches"])
                placed = True
                break
        if not placed:
            clusters.append({"center": price, "touches": [(idx, price)]})

    return [(c["center"], len(c["touches"]), [t[0] for t in c["touches"]]) for c in clusters]


def _compute_recency_weighted_strength(touch_indices, current_idx, max_bars=WINDOW):
    """Compute strength with recency decay: recent touches weighted higher.

    Uses inverse-age weighting: weight = 1 / (1 + age / max_bars).
    Returns float in [0, 1].
    """
    if not touch_indices:
        return 0.0

    total_weight = 0.0
    for idx in touch_indices:
        age = current_idx - idx
        if age < 0:
            age = 0
        weight = 1.0 / (1.0 + age / max_bars)
        total_weight += weight

    # Normalize: 3+ recent touches gives strength near 1.0
    normalized = min(total_weight / 3.0, 1.0)
    return normalized


def _detect_horizontal_sr(pivot_highs, pivot_lows, close, atr, current_idx):
    """Detect horizontal support/resistance levels.

    Returns dict with sr_level_count, near_support, near_resistance,
    nearest_support_dist_atr, nearest_resistance_dist_atr,
    support_touches, resistance_touches, sr_recency_weighted_strength.
    """
    all_pivots = [(idx, price) for idx, price in pivot_highs] + \
                 [(idx, price) for idx, price in pivot_lows]

    if not all_pivots or atr <= 0:
        return {
            "sr_level_count": 0,
            "near_support": False,
            "near_resistance": False,
            "nearest_support_dist_atr": 0.0,
            "nearest_resistance_dist_atr": 0.0,
            "support_touches": 0,
            "resistance_touches": 0,
            "sr_recency_weighted_strength": 0.0,
        }

    clusters = _cluster_sr_levels(all_pivots, atr)
    # Significant levels: 3+ touches
    sr_levels = [c for c in clusters if c[1] >= 3]

    tolerance = 0.5 * atr
    near_support = False
    near_resistance = False
    nearest_sup_dist = 0.0
    nearest_res_dist = 0.0
    sup_touches = 0
    res_touches = 0
    max_recency_strength = 0.0

    for center, touches, touch_indices in sr_levels:
        dist = abs(close - center)
        if center < close:
            # Support level
            if dist < tolerance:
                near_support = True
            if nearest_sup_dist == 0.0 or dist < nearest_sup_dist:
                nearest_sup_dist = dist
                sup_touches = touches
        elif center > close:
            # Resistance level
            if dist < tolerance:
                near_resistance = True
            if nearest_res_dist == 0.0 or dist < nearest_res_dist:
                nearest_res_dist = dist
                res_touches = touches

        # Recency-weighted strength for this level
        strength = _compute_recency_weighted_strength(touch_indices, current_idx)
        if strength > max_recency_strength:
            max_recency_strength = strength

    return {
        "sr_level_count": len(sr_levels),
        "near_support": near_support,
        "near_resistance": near_resistance,
        "nearest_support_dist_atr": nearest_sup_dist / atr if atr > 0 else 0.0,
        "nearest_resistance_dist_atr": nearest_res_dist / atr if atr > 0 else 0.0,
        "support_touches": sup_touches,
        "resistance_touches": res_touches,
        "sr_recency_weighted_strength": max_recency_strength,
    }


def _detect_dynamic_support(pivot_lows, close, atr, n):
    """Detect rising dynamic support: positive slope on pivot lows, price above."""
    if len(pivot_lows) < 2:
        return False, 0.0, 0.0, 0.0

    x = np.array([p[0] for p in pivot_lows], dtype=float)
    y = np.array([p[1] for p in pivot_lows], dtype=float)

    slope, interc, r = _safe_linregress(x, y)

    if slope > 0 and r > 0.8:
        trend_line_at_n = slope * n + interc
        if close > trend_line_at_n:
            dist_atr = abs(close - trend_line_at_n) / atr if atr > 0 else 0.0
            strength = float(abs(r)) * min(len(pivot_lows) / 5.0, 1.0)
            return True, float(slope), strength, dist_atr

    return False, 0.0, 0.0, 0.0


def _detect_dynamic_resistance(pivot_highs, close, atr, n):
    """Detect falling dynamic resistance: negative slope on pivot highs, price below."""
    if len(pivot_highs) < 2:
        return False, 0.0, 0.0, 0.0

    x = np.array([p[0] for p in pivot_highs], dtype=float)
    y = np.array([p[1] for p in pivot_highs], dtype=float)

    slope, interc, r = _safe_linregress(x, y)

    if slope < 0 and r > 0.8:
        trend_line_at_n = slope * n + interc
        if close < trend_line_at_n:
            dist_atr = abs(close - trend_line_at_n) / atr if atr > 0 else 0.0
            strength = float(abs(r)) * min(len(pivot_highs) / 5.0, 1.0)
            return True, float(slope), strength, dist_atr

    return False, 0.0, 0.0, 0.0


def _detect_bos_choch(pivot_highs, pivot_lows, close, atr):
    """Detect Break of Structure and Change of Character.

    BoS: price breaks the most recent swing high (bullish) or low (bearish)
         in the same direction as the prior trend.
    CHoCH: price breaks against the prior trend direction.

    Returns (bos_detected, bos_direction, choch_detected, choch_direction).
    """
    if len(pivot_highs) < 2 or len(pivot_lows) < 2 or atr <= 0:
        return False, "", False, ""

    # Determine prior trend from pivot sequence
    # Higher highs + higher lows = uptrend; lower highs + lower lows = downtrend
    ph_prices = [p[1] for p in pivot_highs[-3:]]
    pl_prices = [p[1] for p in pivot_lows[-3:]]

    if len(ph_prices) >= 2 and len(pl_prices) >= 2:
        hh = ph_prices[-1] > ph_prices[-2]
        hl = pl_prices[-1] > pl_prices[-2]
        lh = ph_prices[-1] < ph_prices[-2]
        ll = pl_prices[-1] < pl_prices[-2]

        if hh and hl:
            prior_trend = "up"
        elif lh and ll:
            prior_trend = "down"
        else:
            prior_trend = "range"
    else:
        prior_trend = "range"

    # Check break of most recent pivot high / low
    last_ph_price = pivot_highs[-1][1]
    last_pl_price = pivot_lows[-1][1]

    tolerance = 0.1 * atr  # small buffer to avoid noise

    bos_detected = False
    bos_direction = ""
    choch_detected = False
    choch_direction = ""

    if close > last_ph_price + tolerance:
        # Broke above last swing high
        if prior_trend == "up":
            bos_detected = True
            bos_direction = "bullish"
        elif prior_trend == "down":
            choch_detected = True
            choch_direction = "bullish"

    if close < last_pl_price - tolerance:
        # Broke below last swing low
        if prior_trend == "down":
            bos_detected = True
            bos_direction = "bearish"
        elif prior_trend == "up":
            choch_detected = True
            choch_direction = "bearish"

    return bos_detected, bos_direction, choch_detected, choch_direction


def detect_support_resistance_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """Detect S/R and market structure patterns, add pat_ columns to df."""
    old_id = id(df)
    df = df.copy()
    # Propagate pivot cache to the copy
    if old_id in _PIVOT_CACHE:
        _PIVOT_CACHE[id(df)] = _PIVOT_CACHE[old_id]
    n = len(df)

    # Pre-allocate numpy arrays for output
    sr_level_count = np.zeros(n, dtype=np.int64)
    near_support = np.zeros(n, dtype=bool)
    near_resistance = np.zeros(n, dtype=bool)
    nearest_sup_dist = np.zeros(n, dtype=np.float64)
    nearest_res_dist = np.zeros(n, dtype=np.float64)
    support_touches = np.zeros(n, dtype=np.int64)
    resistance_touches = np.zeros(n, dtype=np.int64)
    sr_recency_strength = np.zeros(n, dtype=np.float64)

    rising_sup_det = np.zeros(n, dtype=bool)
    rising_sup_slope = np.zeros(n, dtype=np.float64)
    rising_sup_strength = np.zeros(n, dtype=np.float64)
    rising_sup_dist = np.zeros(n, dtype=np.float64)

    falling_res_det = np.zeros(n, dtype=bool)
    falling_res_slope = np.zeros(n, dtype=np.float64)
    falling_res_strength = np.zeros(n, dtype=np.float64)
    falling_res_dist = np.zeros(n, dtype=np.float64)

    bos_det = np.zeros(n, dtype=bool)
    bos_dir = np.array([""] * n, dtype=object)
    choch_det = np.zeros(n, dtype=bool)
    choch_dir = np.array([""] * n, dtype=object)

    closes = df["close"].values
    atrs = df["atr"].values if "atr" in df.columns else np.zeros(n)

    for i in range(LEFT + RIGHT, n):
        pivot_highs, pivot_lows = get_pivot_lists(df, i)
        close_val = float(closes[i])
        atr_val = float(atrs[i]) if i < len(atrs) else 0.0
        if np.isnan(atr_val):
            atr_val = 0.0

        # Horizontal S/R
        sr = _detect_horizontal_sr(pivot_highs, pivot_lows, close_val, atr_val, i)
        sr_level_count[i] = sr["sr_level_count"]
        near_support[i] = sr["near_support"]
        near_resistance[i] = sr["near_resistance"]
        nearest_sup_dist[i] = sr["nearest_support_dist_atr"]
        nearest_res_dist[i] = sr["nearest_resistance_dist_atr"]
        support_touches[i] = sr["support_touches"]
        resistance_touches[i] = sr["resistance_touches"]
        sr_recency_strength[i] = sr["sr_recency_weighted_strength"]

        # Dynamic support (rising)
        det, slope, strength, dist_atr = _detect_dynamic_support(
            pivot_lows, close_val, atr_val, i)
        if det:
            rising_sup_det[i] = True
            rising_sup_slope[i] = slope
            rising_sup_strength[i] = strength
            rising_sup_dist[i] = dist_atr

        # Dynamic resistance (falling)
        det, slope, strength, dist_atr = _detect_dynamic_resistance(
            pivot_highs, close_val, atr_val, i)
        if det:
            falling_res_det[i] = True
            falling_res_slope[i] = slope
            falling_res_strength[i] = strength
            falling_res_dist[i] = dist_atr

        # BoS / CHoCH
        b_det, b_dir, c_det, c_dir = _detect_bos_choch(
            pivot_highs, pivot_lows, close_val, atr_val)
        if b_det:
            bos_det[i] = True
            bos_dir[i] = b_dir
        if c_det:
            choch_det[i] = True
            choch_dir[i] = c_dir

    df["pat_sr_level_count"] = sr_level_count
    df["pat_near_support"] = near_support
    df["pat_near_resistance"] = near_resistance
    df["pat_nearest_support_dist_atr"] = nearest_sup_dist
    df["pat_nearest_resistance_dist_atr"] = nearest_res_dist
    df["pat_support_touches"] = support_touches
    df["pat_resistance_touches"] = resistance_touches
    df["pat_sr_recency_weighted_strength"] = sr_recency_strength
    df["pat_rising_support_detected"] = rising_sup_det
    df["pat_rising_support_slope"] = rising_sup_slope
    df["pat_rising_support_strength"] = rising_sup_strength
    df["pat_rising_support_dist_atr"] = rising_sup_dist
    df["pat_falling_resistance_detected"] = falling_res_det
    df["pat_falling_resistance_slope"] = falling_res_slope
    df["pat_falling_resistance_strength"] = falling_res_strength
    df["pat_falling_resistance_dist_atr"] = falling_res_dist
    df["pat_bos_detected"] = bos_det
    df["pat_bos_direction"] = bos_dir
    df["pat_choch_detected"] = choch_det
    df["pat_choch_direction"] = choch_dir

    return df
