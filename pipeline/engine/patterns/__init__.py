"""
Pattern detection module — Phase 1: Foundation.

Entry point: detect_all_patterns(df) -> pd.DataFrame
Adds 38 pat_ columns to the DataFrame. No DQS modification, no live pipeline changes.

Patterns implemented (9):
  1. Converging trend lines (wedge, symmetrical triangle)
  2. Ascending triangle
  3. Descending triangle
  4. Parallel channel (ascending, descending)
  5. Trend line break / retest
  6. Horizontal support / resistance
  7. Dynamic support (rising)
  8. Dynamic resistance (falling)
  9. Break of Structure (BoS) / Change of Character (CHoCH)
"""
import pandas as pd

from .pivot_detector import detect_pivots, get_pivot_lists
from .trend_lines import detect_trend_line_patterns
from .support_resistance import detect_support_resistance_patterns


def detect_all_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """Run all Phase 1 pattern detectors on df.

    Args:
        df: Enriched DataFrame with columns: open, high, low, close, volume, atr.
            Must be sorted chronologically (oldest first).

    Returns:
        df with 38 pat_ columns added. Values are 0.0/False/"" for insufficient
        warmup data (never NaN).
    """
    if df is None or len(df) == 0:
        return df

    # Step 1: Detect pivots (adds pat_pivot_* columns, populates _PIVOT_CACHE)
    df = detect_pivots(df)

    # Step 2: Detect trend line patterns (adds 20 pat_ columns)
    df = detect_trend_line_patterns(df)

    # Step 3: Detect S/R and market structure (adds 18 pat_ columns)
    df = detect_support_resistance_patterns(df)

    # Ensure no NaN in pat_ columns
    for col in df.columns:
        if col.startswith("pat_"):
            if df[col].dtype == bool:
                df[col] = df[col].fillna(False)
            elif df[col].dtype == object:
                df[col] = df[col].fillna("")
            else:
                df[col] = df[col].fillna(0.0)

    return df


__all__ = [
    "detect_all_patterns",
    "detect_pivots",
    "get_pivot_lists",
    "detect_trend_line_patterns",
    "detect_support_resistance_patterns",
]
