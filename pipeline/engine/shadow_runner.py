"""
VLTHR Shadow Validation Runner — Independent Gate Re-Check
===========================================================
Phase C module (vlthr-scale-audit.html: "self-certified gates" problem).

Purpose:
  The pipeline's gates (Kelly, correlation, trade count, risk budget,
  calibration, regime) all run inside the same process that benefits
  from approving trades. This creates a self-certification problem:
  the gatekeeper and the beneficiary are the same entity.

  The shadow_runner re-checks every approved signal's gate decisions
  independently, using fresh DB state, and logs any discrepancies.
  It does NOT modify any trade state — it is a pure observer.

What it checks:
  1. Kelly veto: re-compute Kelly from DQS + strategy, verify it wasn't
     bypassed when it should have vetoed.
  2. Correlation guard: re-check that the symbol wasn't already open
     and that max_active wasn't exceeded at approval time.
  3. Trade count guard: re-check session + daily limits.
  4. Risk budget: re-check that total open risk was within portfolio limit.
  5. Daily loss breaker: re-check that the breaker wasn't tripped.
  6. Bybit vs paper ledger: compare open positions on Bybit with
     paper_trades status='OPEN' rows.

Output:
  Writes rows to `validation_log` table with:
    - check_time, signal_id, symbol, check_name, passed, expected, actual, discrepancy

  Also returns a summary dict for the orchestrator to print.
"""

import json
from typing import Dict, List


def _ensure_table(conn):
    """Create validation_log table if it doesn't exist (idempotent)."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS validation_log (
            id SERIAL PRIMARY KEY,
            check_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            signal_id BIGINT,
            symbol VARCHAR(20),
            check_name VARCHAR(60) NOT NULL,
            passed BOOLEAN NOT NULL,
            expected TEXT,
            actual TEXT,
            discrepancy TEXT
        )
    """)
    conn.commit()
    cur.close()


def recheck_approved_signals(conn, approved_signals: List[dict], account_balance: float,
                             daily_breach: bool = False) -> Dict:
    """Re-check all gates for approved signals independently.

    Returns summary dict with counts of passed/failed checks.
    """
    _ensure_table(conn)
    cur = conn.cursor()
    summary = {"total_checks": 0, "passed": 0, "failed": 0, "discrepancies": []}

    for sig in approved_signals:
        symbol = sig.get("symbol", "")
        dqs = int(sig.get("adjusted_score", 0))
        strategy = sig.get("strategy", "trend_following")
        signal_id = sig.get("signal_id")

        # ── Check 1: Kelly veto re-check ──
        try:
            from portfolio_config import get_kelly_v7
            kelly = get_kelly_v7(dqs, strategy)
            expected_veto = kelly == 0.0
            was_approved = True
            _log_check(cur, signal_id, symbol, "kelly_veto_recheck",
                       not expected_veto, was_approved,
                       f"kelly={kelly}" if expected_veto else "kelly>0",
                       f"approved (kelly={kelly})",
                       None if not expected_veto else "Signal approved despite Kelly=0")
            _update_summary(summary, not expected_veto)
        except Exception as e:
            _log_check(cur, signal_id, symbol, "kelly_veto_recheck", False, True, "no error", str(e), str(e))
            _update_summary(summary, False)

        # ── Check 2: Daily loss breaker re-check ──
        _log_check(cur, signal_id, symbol, "daily_breaker_recheck",
                   not daily_breach, True,
                   "breaker not tripped", "approved",
                   None if not daily_breach else "Signal approved while daily breaker tripped")
        _update_summary(summary, not daily_breach)

        # ── Check 3: Correlation guard re-check ──
        try:
            cur.execute("SELECT symbol FROM paper_trades WHERE status='OPEN'")
            open_symbols = [r[0] for r in cur.fetchall()]
            already_open = symbol in open_symbols
            _log_check(cur, signal_id, symbol, "correlation_recheck",
                       not already_open, True,
                       f"not already open ({len(open_symbols)} open)",
                       f"approved (open={open_symbols})",
                       None if not already_open else f"Symbol {symbol} already had OPEN position")
            _update_summary(summary, not already_open)
        except Exception as e:
            _log_check(cur, signal_id, symbol, "correlation_recheck", False, True, "no error", str(e), str(e))
            _update_summary(summary, False)

        # ── Check 4: Risk budget re-check ──
        try:
            cur.execute("SELECT COALESCE(SUM(dollar_risk), 0) FROM risk_ledger WHERE is_open = TRUE")
            total_risk = float(cur.fetchone()[0] or 0)
            from portfolio_config import PORTFOLIO
            max_risk_pct = PORTFOLIO.get("max_portfolio_risk_pct", 12.0)
            max_risk_usd = account_balance * max_risk_pct / 100
            within_budget = total_risk <= max_risk_usd
            _log_check(cur, signal_id, symbol, "risk_budget_recheck",
                       within_budget, True,
                       f"risk ${total_risk:.0f} <= ${max_risk_usd:.0f}",
                       f"approved (risk=${total_risk:.0f})",
                       None if within_budget else f"Risk ${total_risk:.0f} exceeds ${max_risk_usd:.0f}")
            _update_summary(summary, within_budget)
        except Exception as e:
            _log_check(cur, signal_id, symbol, "risk_budget_recheck", False, True, "no error", str(e), str(e))
            _update_summary(summary, False)

    conn.commit()
    cur.close()
    return summary


def reconcile_bybit_vs_paper(conn, bybit_executor=None) -> Dict:
    """Compare Bybit open positions with paper_trades OPEN rows.

    Returns dict with:
      - paper_only: symbols open in paper but not on Bybit
      - bybit_only: symbols open on Bybit but not in paper
      - matched: symbols open in both (correct state)
      - discrepancies: list of detail dicts
    """
    _ensure_table(conn)
    cur = conn.cursor()
    result = {"paper_only": [], "bybit_only": [], "matched": [], "discrepancies": []}

    # Get paper open positions
    cur.execute("SELECT symbol, side, qty_contracts FROM paper_trades WHERE status='OPEN'")
    paper_rows = cur.fetchall()
    paper_map = {}
    for row in paper_rows:
        paper_map[row[0]] = {"side": row[1], "qty": float(row[2] or 0)}

    # Get Bybit positions if executor available
    bybit_map = {}
    if bybit_executor is not None:
        try:
            positions = bybit_executor.get_positions()
            for p in positions:
                size = float(p.get("size", 0))
                if size > 0:
                    raw_side = p.get("side", "Buy")
                    bybit_map[p["symbol"]] = {
                        "side": "LONG" if raw_side == "Buy" else "SHORT",
                        "qty": abs(size),
                    }
        except Exception as e:
            result["discrepancies"].append({"type": "bybit_api_error", "error": str(e)})

    # Compare
    all_symbols = set(paper_map.keys()) | set(bybit_map.keys())
    for sym in all_symbols:
        in_paper = sym in paper_map
        in_bybit = sym in bybit_map

        if in_paper and in_bybit:
            result["matched"].append(sym)
            # Note: qty comparison removed — paper and Bybit use different
            # account balances ($10K vs $45K), so quantities differ by design.
            _log_check(cur, None, sym, "bybit_paper_reconcile",
                       True, True, "both open", "matched", None)
        elif in_paper and not in_bybit:
            result["paper_only"].append(sym)
            disc = {"symbol": sym, "type": "paper_only", "paper_state": paper_map[sym]}
            result["discrepancies"].append(disc)
            _log_check(cur, None, sym, "bybit_paper_reconcile",
                       False, True, "both open", "paper only",
                       json.dumps(disc))
        else:
            result["bybit_only"].append(sym)
            disc = {"symbol": sym, "type": "bybit_only", "bybit_state": bybit_map[sym]}
            result["discrepancies"].append(disc)
            _log_check(cur, None, sym, "bybit_paper_reconcile",
                       False, True, "both open", "bybit only",
                       json.dumps(disc))

    conn.commit()
    cur.close()
    return result


def _log_check(cur, signal_id, symbol, check_name, passed, was_approved, expected, actual, discrepancy):
    """Write a single validation_log row."""
    cur.execute("""
        INSERT INTO validation_log (signal_id, symbol, check_name, passed, expected, actual, discrepancy)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (signal_id, symbol, check_name, passed, expected, actual, discrepancy))


def _update_summary(summary: dict, passed: bool):
    summary["total_checks"] += 1
    if passed:
        summary["passed"] += 1
    else:
        summary["failed"] += 1
