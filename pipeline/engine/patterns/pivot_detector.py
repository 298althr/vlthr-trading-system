"""
Pivot detection using fractal method (left=3, right=3).

A pivot high at index i is confirmed only when high[i] is the maximum of
high[i-3 : i+4]. This means the pivot is confirmed 3 bars after it forms.
During backtest, the detection function receives only df.iloc[:idx+1], so
a pivot at position i is only reported at position i+3 or later. This
prevents look-ahead bias by construction.

Output columns (prefixed pat_):
  pat_pivot_high_idx   - int, index of most recent confirmed pivot high, -1 if none
  pat_pivot_low_idx    - int, index of most recent confirmed pivot low, -1 if none
  pat_pivot_high_price - float, price of most recent confirmed pivot high, 0.0 if none
  pat_pivot_low_price  - float, price of most recent confirmed pivot low, 0.0 if none
  pat_pivot_highs      - list of (idx, price) tuples for all confirmed pivot highs in window
  pat_pivot_lows       - list of (idx, price) tuples for all confirmed pivot lows in window
"""
import bisect

import numpy as np
import pandas as pd

LEFT = 3
RIGHT = 3
WINDOW = 60  # max bars to look back for pivots

# Cache: {id(df): (all_pivot_highs, all_pivot_lows)} — set by detect_pivots
_PIVOT_CACHE: dict = {}


def detect_pivots(df: pd.DataFrame, left: int = LEFT, right: int = RIGHT,
                  window: int = WINDOW) -> pd.DataFrame:
    """Detect fractal pivot highs and lows using only confirmed pivots.

    A pivot is confirmed only after `right` bars have passed. This function
    operates on the full DataFrame but only reports pivots at indices where
    confirmation is possible (i.e., index + right < len(df)).

    Returns df with pat_pivot_* columns added.
    """
    df = df.copy()
    n = len(df)
    highs = df["high"].values
    lows = df["low"].values

    pivot_high_idx = np.full(n, -1, dtype=np.int64)
    pivot_low_idx = np.full(n, -1, dtype=np.int64)
    pivot_high_price = np.zeros(n, dtype=np.float64)
    pivot_low_price = np.zeros(n, dtype=np.float64)

    # Collect all confirmed pivots as flat lists: (confirmed_at_bar, pivot_idx, price)
    all_pivot_highs = []  # (pivot_idx, price) sorted by pivot_idx
    all_pivot_lows = []

    for i in range(left, n - right):
        # Pivot high: high[i] is max of window [i-left, i+right]
        window_high = highs[i - left : i + right + 1]
        if highs[i] == window_high.max() and highs[i] > 0:
            if (window_high == highs[i]).sum() == 1:
                all_pivot_highs.append((i, float(highs[i])))

        # Pivot low: low[i] is min of window [i-left, i+right]
        window_low = lows[i - left : i + right + 1]
        if lows[i] == window_low.min() and lows[i] > 0:
            if (window_low == lows[i]).sum() == 1:
                all_pivot_lows.append((i, float(lows[i])))

    # Cache for get_pivot_lists — O(log n) bisect lookup instead of O(n) scan
    # Store: (all_pivot_highs, all_pivot_lows, high_indices, low_indices)
    high_indices = [idx for idx, _ in all_pivot_highs]
    low_indices = [idx for idx, _ in all_pivot_lows]
    _PIVOT_CACHE[id(df)] = (all_pivot_highs, all_pivot_lows, high_indices, low_indices)

    # Forward-fill: at each bar, report the most recent confirmed pivot
    # A pivot at idx i is confirmed at i + right
    ph_ptr = 0
    pl_ptr = 0
    last_ph_idx = -1
    last_ph_price = 0.0
    last_pl_idx = -1
    last_pl_price = 0.0

    for i in range(n):
        # Advance pointers to include pivots confirmed by bar i
        while ph_ptr < len(all_pivot_highs) and all_pivot_highs[ph_ptr][0] + right <= i:
            last_ph_idx, last_ph_price = all_pivot_highs[ph_ptr]
            ph_ptr += 1
        while pl_ptr < len(all_pivot_lows) and all_pivot_lows[pl_ptr][0] + right <= i:
            last_pl_idx, last_pl_price = all_pivot_lows[pl_ptr]
            pl_ptr += 1

        pivot_high_idx[i] = last_ph_idx
        pivot_low_idx[i] = last_pl_idx
        pivot_high_price[i] = last_ph_price
        pivot_low_price[i] = last_pl_price

    df["pat_pivot_high_idx"] = pivot_high_idx
    df["pat_pivot_low_idx"] = pivot_low_idx
    df["pat_pivot_high_price"] = pivot_high_price
    df["pat_pivot_low_price"] = pivot_low_price

    return df


def get_pivot_lists(df: pd.DataFrame, end_idx: int, window: int = WINDOW,
                    left: int = LEFT, right: int = RIGHT):
    """Extract confirmed pivot highs and lows up to end_idx.

    Returns (pivot_highs, pivot_lows) where each is a list of (idx, price) tuples.
    Only pivots confirmed by end_idx (i.e., pivot formed at idx, confirmed at idx+right <= end_idx)
    are included. This is the look-ahead-safe accessor for downstream pattern functions.

    Uses precomputed cache from detect_pivots() with bisect for O(log n) lookup.
    """
    cache_key = id(df)
    if cache_key in _PIVOT_CACHE:
        all_highs, all_lows, high_indices, low_indices = _PIVOT_CACHE[cache_key]
        cutoff = end_idx + 1 - window
        max_pivot_idx = end_idx - right  # pivot at idx is confirmed when idx + right <= end_idx

        # bisect to find range [cutoff, max_pivot_idx] in sorted index arrays
        lo_h = bisect.bisect_left(high_indices, cutoff)
        hi_h = bisect.bisect_right(high_indices, max_pivot_idx)
        pivot_highs = all_highs[lo_h:hi_h]

        lo_l = bisect.bisect_left(low_indices, cutoff)
        hi_l = bisect.bisect_right(low_indices, max_pivot_idx)
        pivot_lows = all_lows[lo_l:hi_l]

        return pivot_highs, pivot_lows

    # Fallback: compute from scratch (used when detect_pivots wasn't called first)
    n = min(end_idx + 1, len(df))
    highs = df["high"].values[:n]
    lows = df["low"].values[:n]

    pivot_highs = []
    pivot_lows = []

    for i in range(left, n - right):
        window_high = highs[i - left : i + right + 1]
        if highs[i] == window_high.max() and highs[i] > 0:
            if (window_high == highs[i]).sum() == 1:
                pivot_highs.append((i, float(highs[i])))

        window_low = lows[i - left : i + right + 1]
        if lows[i] == window_low.min() and lows[i] > 0:
            if (window_low == lows[i]).sum() == 1:
                pivot_lows.append((i, float(lows[i])))

    cutoff = n - window
    pivot_highs = [(idx, p) for idx, p in pivot_highs if idx >= cutoff]
    pivot_lows = [(idx, p) for idx, p in pivot_lows if idx >= cutoff]

    return pivot_highs, pivot_lows
