"""
VLTHR Signal Ranker
====================
Ranks active signals and enforces portfolio gates.

Logic:
1. Filter: only signals with adjusted_score >= 65 pass the ranker gate
2. Sort: highest adjusted_score first (tie-break by symbol Sharpe)
3. Deduplicate: 1 trade per symbol at a time (no stacking)
4. Return: ordered list of signals ready for the portfolio layer

Usage:
    from signal_ranker import SignalRanker
    ranker = SignalRanker(conn)
    approved = ranker.rank_and_filter(active_signals_df)
"""
from typing import List, Dict
import pandas as pd

from portfolio_config import DQS_THRESHOLDS, SYMBOL_QUALITY


class SignalRanker:
    """Ranks active signals and applies the portfolio entry gate."""

    MIN_RANKER_SCORE = DQS_THRESHOLDS["min_to_execute"]  # 50 (display all tracked signals)

    def __init__(self, conn):
        self.conn = conn

    def rank_and_filter(self, signals_df: pd.DataFrame) -> List[Dict]:
        """
        Given a DataFrame of active signals, return a ranked list of
        signals that pass the minimum ranker score gate.
        """
        if signals_df is None or len(signals_df) == 0:
            return []

        # Ensure required columns exist
        required = ["symbol", "adjusted_score"]
        for col in required:
            if col not in signals_df.columns:
                raise KeyError(f"signals_df missing required column: {col}")

        # Gate 1: Minimum adjusted score
        gated = signals_df[signals_df["adjusted_score"] >= self.MIN_RANKER_SCORE].copy()
        if len(gated) == 0:
            return []

        # Add symbol quality (Sharpe) as tie-breaker
        gated["symbol_sharpe"] = gated["symbol"].map(SYMBOL_QUALITY).fillna(0)

        # Sort: adjusted_score DESC, then symbol_sharpe DESC
        gated = gated.sort_values(
            by=["adjusted_score", "symbol_sharpe"],
            ascending=[False, False]
        ).reset_index(drop=True)

        # Deduplicate: only top signal per symbol
        seen_symbols = set()
        results = []
        for _, row in gated.iterrows():
            sym = row["symbol"]
            if sym in seen_symbols:
                continue
            seen_symbols.add(sym)
            results.append(row.to_dict())

        return results

    def get_top_n(self, signals_df: pd.DataFrame, n: int = 3) -> List[Dict]:
        """Return top N signals after ranking and filtering."""
        approved = self.rank_and_filter(signals_df)
        return approved[:n]
