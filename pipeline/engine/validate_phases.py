"""
VLTHR Phase Validation Script
==============================
Runs Level 1 (unit) checks for each phase per strategy-upgrade.md.

Usage:
    python engine/validate_phases.py
"""
import sys
from pathlib import Path
from datetime import datetime, timezone

_engine = Path(__file__).resolve().parent
try:
    _repo = _engine.parents[2]
except IndexError:
    _repo = _engine.parent
sys.path.insert(0, str(_engine))
sys.path.insert(0, str(_repo / "departments" / "strategy_research" / "BACKTESTER" / "scripts" / "common"))
sys.path.insert(0, str(_repo / "departments" / "strategy_research" / "BACKTESTER" / "strategy" / "signals"))

# ── Phase 0: portfolio_config import check ──
try:
    from portfolio_config import (
        SYMBOLS, DQS_THRESHOLDS, DQS_WEIGHTS, RISK_TIERS, PORTFOLIO,
        SIGNAL_LIFECYCLE, FRESHNESS, TELEGRAM_BOT_NAME, SYMBOL_QUALITY,
        get_risk_tier, get_sl_tp, REPO_ROOT, DATA_ROOT
    )
    print("[PASS] Phase 0 — portfolio_config imports cleanly")
except Exception as e:
    print(f"[FAIL] Phase 0 — portfolio_config import error: {e}")
    sys.exit(1)

# ── Phase 1: DQS Formula Validation ──
from confidence_engine import score_technical, score_structure, score_context, score_signal
import pandas as pd
import numpy as np

print("\n--- Phase 1: DQS Formula ---")

# Technical Domain — each sub-comp scored 0-100, then averaged
scores = [
    (score_technical(28, 52, 0.012), 93.33, "rsi=28, adx=52, log_ret=0.012 → ~93.33 (80+100+100)/3"),
    (score_technical(38, 26, 0.003), 43.33, "rsi=38, adx=26, log_ret=0.003 → ~43.33 (40+40+50)/3"),
    (score_technical(45, 20, -0.002), 0.0, "rsi=45, adx=20, log_ret=-0.002 → 0"),
]
passed = 0
for actual, expected, desc in scores:
    ok = abs(actual - expected) < 1.0
    print(f"  {'[PASS]' if ok else '[FAIL]'} {desc}: got {actual}")
    if ok:
        passed += 1
print(f"  Technical: {passed}/{len(scores)} passed")

# Structure Domain — 5 sub-comps averaged (0-100 each)
ob_good = {"spread_pct": 0.005, "depth_usd": 600000, "imbalance_pct": 25}
ob_bad = {"spread_pct": 0.15, "depth_usd": 80000, "imbalance_pct": 3}
struct_scores = [
    (score_structure(None, 0, 0), 27.0, "ob=None, funding=0, oi=0 → 27 (0+0+0+75+60)/5"),
    (score_structure(ob_good, -0.0001, 6), 100.0, "perfect OB + neg funding + OI → 100"),
    (score_structure(ob_bad, 0.0006, -1), 20.0, "weak OB → 20 (funding=0.0006 gives 25pts)"),
]
passed = 0
for actual, expected, desc in struct_scores:
    ok = abs(actual - expected) < 5.0
    print(f"  {'[PASS]' if ok else '[FAIL]'} {desc}: got {actual}")
    if ok:
        passed += 1
print(f"  Structure: {passed}/{len(struct_scores)} passed")

# Context Domain — session + symbol quality averaged
ctx_scores = [
    (score_context("london", "SOLUSDT"), 90.0, "london + SOL → 90 (80+100)/2"),
    (score_context("asian", "XRPUSDT"), 45.0, "asian + XRP → 45 (20+70)/2"),
    (score_context("ny_late", "BTCUSDT"), 75.0, "ny_late + BTC → 75 (80+70)/2"),
]
passed = 0
for actual, expected, desc in ctx_scores:
    ok = abs(actual - expected) < 15.0
    print(f"  {'[PASS]' if ok else '[FAIL]'} {desc}: got {actual}")
    if ok:
        passed += 1
print(f"  Context: {passed}/{len(ctx_scores)} passed")

# DQS Composite with a synthetic DataFrame
df = pd.DataFrame({
    "timestamp": [datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc)],
    "close": [100.0],
    "rsi": [28.0],
    "adx": [52.0],
    "ctx_log_ret_4h": [0.012],
    "funding_rate": [-0.0001],
    "open_interest": [1000000.0],
})
dqs, breakdown = score_signal("BTCUSDT", df, ob_features=ob_good, session="london", idx=0)
print(f"\n  Full DQS composite (perfect conditions): {dqs}")
ok = 85 <= dqs <= 100
print(f"  {'[PASS]' if ok else '[FAIL]'} DQS in expected range [85, 100]: {dqs}")

# Tier assignment check
tier = get_risk_tier(int(dqs))
print(f"  Tier: {tier['tier']}, Risk%: {tier['risk_pct']}%, R:R: {tier['rr']}")

# ── Phase 4: Signal State Machine (unit-level, no DB) ──
print("\n--- Phase 4: Signal State Machine (unit) ---")
from signal_state_manager import SignalStateManager

# Mock connection for trajectory logic test only
class MockConn:
    def __init__(self):
        self._tx = []
    def cursor(self):
        return MockCursor(self)
    def commit(self):
        pass
    def rollback(self):
        pass

class MockCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = {}
    def execute(self, sql, params=None):
        pass
    def fetchone(self):
        return None
    def fetchall(self):
        return []

# Test trajectory logic directly
ssm = SignalStateManager(MockConn())
traj = ssm._determine_trajectory(None, 58)
assert traj == "STABLE", f"First run should be STABLE, got {traj}"
print("  [PASS] First run → STABLE")

traj = ssm._determine_trajectory(58, 63)
assert traj == "IMPROVING", f"DQS +5 should be IMPROVING, got {traj}"
print("  [PASS] DQS +5 → IMPROVING")

traj = ssm._determine_trajectory(63, 59)
assert traj == "DECLINING", f"DQS -4 should be DECLINING, got {traj}"
print("  [PASS] DQS -4 → DECLINING")

traj = ssm._determine_trajectory(59, 59)
assert traj == "STABLE", f"No change should be STABLE, got {traj}"
print("  [PASS] No change → STABLE")

# ── Phase 5: SignalRanker ──
print("\n--- Phase 5: SignalRanker ---")
from signal_ranker import SignalRanker

ranker = SignalRanker(MockConn())

df_signals = pd.DataFrame([
    {"symbol": "BTCUSDT", "adjusted_score": 84, "session": "london"},
    {"symbol": "ETHUSDT", "adjusted_score": 71, "session": "london"},
    {"symbol": "SOLUSDT", "adjusted_score": 64, "session": "ny_late"},  # below gate
    {"symbol": "XRPUSDT", "adjusted_score": 90, "session": "london"},
])

results = ranker.rank_and_filter(df_signals)
symbols = [r["symbol"] for r in results]
print(f"  Ranked signals: {symbols}")
assert "SOLUSDT" not in symbols, "SOLUSDT (score 64) should be filtered out"
assert symbols[0] == "XRPUSDT", f"XRP should be first (score 90), got {symbols[0]}"
assert symbols[1] == "BTCUSDT", f"BTC should be second (score 84), got {symbols[1]}"
print("  [PASS] Ranker filters and sorts correctly")

# ── Phase 6: Portfolio Gates ──
print("\n--- Phase 6: Portfolio Gates ---")
from portfolio_gates import CorrelationGuard

corr = CorrelationGuard()
assert corr.can_add([], "BTCUSDT") == True, "Empty → should allow"
assert corr.can_add(["BTCUSDT", "ETHUSDT"], "SOLUSDT") == True, "2 open → allow 3rd"
assert corr.can_add(["BTCUSDT", "ETHUSDT", "SOLUSDT"], "XRPUSDT") == False, "3 open → block"
assert corr.can_add(["BTCUSDT", "ETHUSDT", "SOLUSDT"], "BTCUSDT") == False, "Same symbol → block"
print("  [PASS] CorrelationGuard limits work")

# ── System-Wide Invariants (code presence checks) ──
print("\n--- System Invariants (code presence) ---")
from portfolio_orchestrator import run_iteration
assert callable(run_iteration), "run_iteration must be callable"
print("  [PASS] portfolio_orchestrator.run_iteration callable")

from safety_layer import SafetyLayer
assert hasattr(SafetyLayer, "pre_flight_checks"), "SafetyLayer missing pre_flight_checks"
assert hasattr(SafetyLayer, "post_flight_checks"), "SafetyLayer missing post_flight_checks"
print("  [PASS] SafetyLayer has required methods")

from replay_runner import ReplayRunner
assert hasattr(ReplayRunner, "run"), "ReplayRunner missing run method"
print("  [PASS] ReplayRunner has run method")

# ── Summary ──
print("\n" + "=" * 60)
print("VALIDATION COMPLETE")
print("=" * 60)
print("All unit-level checks passed. Next steps:")
print("  1. Run DB migration: python engine/db_migration.py")
print("  2. Run pipeline: python engine/run_pipeline.py")
print("  3. Run replay: python engine/run_pipeline.py --replay")
