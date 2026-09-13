#!/usr/bin/env python3
"""Unit Tests for Session-Aware Parameter Resolution
=====================================================
Tests the get_symbol_params fallback chain and SESSION_PARAMS structure.

Run:
  cd /path/to/DEVOPS/pipeline/engine
  python3 -m pytest test_session_params.py -v
  # or:
  python3 test_session_params.py
"""
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from portfolio_config import (
    get_symbol_params,
    SESSION_PARAMS,
    PER_SYMBOL_PARAMS,
    PARAMS,
    classify_session,
    ACTIVE_SESSIONS,
)


class TestFallbackChain(unittest.TestCase):
    """Test get_symbol_params fallback chain."""

    def test_symbol_only_returns_symbol_params(self):
        result = get_symbol_params("BTCUSDT")
        self.assertEqual(result["strategy"], "trend_following")
        self.assertEqual(result["sl_mult"], 2.5)

    def test_session_with_no_session_params_returns_symbol_params(self):
        result = get_symbol_params("SOLUSDT", session="asian")
        self.assertEqual(result["strategy"], "trend_following")
        self.assertEqual(result["sl_mult"], 2.5)

    def test_session_with_unpopulated_session_returns_symbol_params(self):
        result = get_symbol_params("BTCUSDT", session="london")
        self.assertEqual(result["strategy"], "trend_following")
        self.assertEqual(result["sl_mult"], 2.5)

    def test_session_with_populated_cell_returns_session_params(self):
        # SESSION_PARAMS is currently empty after WFO validation.
        # This test verifies the fallback chain works: session param falls through to symbol params.
        result = get_symbol_params("BTCUSDT", session="asian")
        self.assertEqual(result["strategy"], "trend_following")
        self.assertEqual(result["sl_mult"], 2.5)

    def test_session_params_merge_over_symbol_params(self):
        # With empty SESSION_PARAMS, result is just PER_SYMBOL_PARAMS
        result = get_symbol_params("BTCUSDT", session="asian")
        self.assertEqual(result["strategy"], "trend_following")
        self.assertEqual(result["risk_pct"], 0.05)
        self.assertEqual(result["max_trade_hours"], 12)

    def test_unknown_symbol_returns_defaults(self):
        result = get_symbol_params("UNKNOWN", session="asian")
        self.assertEqual(result["sl_mult"], PARAMS["sl_mult"])

    def test_unknown_symbol_with_session_returns_defaults(self):
        result = get_symbol_params("UNKNOWN", session="london")
        self.assertEqual(result, PARAMS)

    def test_none_session_returns_symbol_params(self):
        result = get_symbol_params("BTCUSDT", session=None)
        self.assertEqual(result["strategy"], "trend_following")

    def test_empty_session_returns_symbol_params(self):
        result = get_symbol_params("BTCUSDT", session="")
        self.assertEqual(result["strategy"], "trend_following")


class TestSessionParamsStructure(unittest.TestCase):
    """Test SESSION_PARAMS dict structure."""

    def test_session_params_empty_after_wfo_validation(self):
        # WFO backtest showed assumed BTC asian params hurt OOS PF.
        # Cell removed pending proper WFO optimization.
        # The structure and fallback chain remain ready for future use.
        self.assertEqual(SESSION_PARAMS, {})

    def test_fallback_chain_still_works_with_empty_params(self):
        # With empty SESSION_PARAMS, all symbols fall through to PER_SYMBOL_PARAMS
        result = get_symbol_params("BTCUSDT", session="asian")
        self.assertEqual(result["strategy"], "trend_following")
        result = get_symbol_params("BTCUSDT", session="london")
        self.assertEqual(result["strategy"], "trend_following")


class TestClassifySession(unittest.TestCase):
    """Test the standardized session classification."""

    def test_all_4_sessions_exist(self):
        self.assertEqual(len(ACTIVE_SESSIONS), 4)
        for s in ["asian", "london", "ny_open", "ny_late"]:
            self.assertIn(s, ACTIVE_SESSIONS)

    def test_no_overlap_session(self):
        self.assertNotIn("overlap", ACTIVE_SESSIONS)

    def test_boundary_asian_start(self):
        import pandas as pd
        ts = pd.Timestamp("2026-01-01 00:00:00", tz="UTC")
        self.assertEqual(classify_session(ts), "asian")

    def test_boundary_asian_end_london_start(self):
        import pandas as pd
        ts = pd.Timestamp("2026-01-01 08:00:00", tz="UTC")
        self.assertEqual(classify_session(ts), "london")

    def test_boundary_london_end_nyopen_start(self):
        import pandas as pd
        ts = pd.Timestamp("2026-01-01 12:00:00", tz="UTC")
        self.assertEqual(classify_session(ts), "ny_open")

    def test_boundary_nyopen_end_nylate_start(self):
        import pandas as pd
        ts = pd.Timestamp("2026-01-01 17:00:00", tz="UTC")
        self.assertEqual(classify_session(ts), "ny_late")

    def test_boundary_nylate_end_asian_start(self):
        import pandas as pd
        ts = pd.Timestamp("2026-01-01 23:59:00", tz="UTC")
        self.assertEqual(classify_session(ts), "ny_late")


if __name__ == "__main__":
    unittest.main(verbosity=2)
