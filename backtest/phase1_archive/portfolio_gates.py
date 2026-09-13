"""
VLTHR Portfolio Gates
=====================
Three guardrails that protect the portfolio:

1. RiskBudgetLedger  — total open risk <= 10% of account balance
2. TradeCountGuard   — max 5 active trades, max 6/day, max 2 per session
3. CorrelationGuard  — all 6 crypto symbols treated as one cluster (max 5 open)

Usage:
    from portfolio_gates import RiskBudgetLedger, TradeCountGuard, CorrelationGuard
    ledger = RiskBudgetLedger(conn)
    guard  = TradeCountGuard(conn)
    corr   = CorrelationGuard()

    if ledger.has_capacity(new_risk_usd, account_balance) \
       and guard.can_open(session, date) \
       and corr.can_add(active_symbols, new_symbol):
        # Proceed with trade
"""
from datetime import datetime, timezone, date as dt_date
from typing import List, Optional, Set
import warnings
import pandas as pd

from portfolio_config import PORTFOLIO, get_risk_tier


# ── RISK BUDGET LEDGER ─────────────────────────────────────────────────────

class RiskBudgetLedger:
    """Tracks total open risk vs account balance."""

    MAX_PORTFOLIO_RISK_PCT = PORTFOLIO["max_portfolio_risk_pct"]  # 10.0%

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()

    def get_total_open_risk(self) -> float:
        """Sum of dollar_risk for all open positions."""
        self.cur.execute("""
            SELECT COALESCE(SUM(dollar_risk), 0)
            FROM risk_ledger WHERE is_open = TRUE
        """)
        return float(self.cur.fetchone()[0] or 0)

    def has_capacity(self, new_risk_usd: float, account_balance: float) -> bool:
        """Return True if adding new_risk_usd keeps total risk under cap."""
        if account_balance <= 0:
            return False
        current = self.get_total_open_risk()
        max_allowed = account_balance * (self.MAX_PORTFOLIO_RISK_PCT / 100)
        return (current + new_risk_usd) <= max_allowed

    def record_trade(self, symbol: str, trade_id: int, entry: float, sl: float,
                     qty: float, dollar_risk: float, risk_pct: float):
        """Record a new trade in the risk ledger."""
        self.cur.execute("""
            INSERT INTO risk_ledger (
                symbol, trade_id, entry_price, sl_price, qty_contracts,
                dollar_risk, risk_pct_of_account, is_open
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
        """, (symbol, trade_id, entry, sl, qty, dollar_risk, risk_pct))
        self.conn.commit()

    def close_trade(self, trade_id: int):
        """Mark a trade as closed in the risk ledger."""
        self.cur.execute("""
            UPDATE risk_ledger
            SET is_open = FALSE, closed_at = NOW()
            WHERE trade_id = %s
        """, (trade_id,))
        self.conn.commit()


# ── TRADE COUNT GUARD ─────────────────────────────────────────────────────

class TradeCountGuard:
    """Enforces session and daily trade count limits."""

    MAX_ACTIVE = PORTFOLIO["max_active_trades"]      # 5
    MAX_DAILY = PORTFOLIO["max_daily_trades"]         # 6
    SESSION_LIMITS = PORTFOLIO["session_limits"]        # {"london": 2, "ny_late": 2}

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()

    def _count_open(self) -> int:
        self.cur.execute("SELECT COUNT(*) FROM paper_trades WHERE status = 'OPEN'")
        return self.cur.fetchone()[0]

    def _count_daily(self, d: dt_date) -> int:
        self.cur.execute("""
            SELECT COUNT(*) FROM paper_trades
            WHERE DATE(created_at) = %s AND status IN ('OPEN', 'CLOSED')
        """, (d,))
        return self.cur.fetchone()[0]

    def _count_session(self, d: dt_date, session: str) -> int:
        self.cur.execute("""
            SELECT COUNT(*) FROM paper_trades
            WHERE DATE(created_at) = %s AND entry_session = %s AND status IN ('OPEN', 'CLOSED')
        """, (d, session))
        return self.cur.fetchone()[0]

    def can_open(self, session: str, d: Optional[dt_date] = None) -> bool:
        """Return True if all count limits allow opening a new trade."""
        d = d or datetime.now(timezone.utc).date()

        # Temporary daily limit override (auto-expires)
        max_daily = self.MAX_DAILY
        try:
            from pathlib import Path
            override_file = Path(__file__).resolve().parent / ".max_daily_override"
            if override_file.exists():
                expiry = datetime.fromisoformat(override_file.read_text().strip())
                if datetime.now(timezone.utc) < expiry:
                    max_daily = 6
        except Exception:
            pass

        if self._count_open() >= self.MAX_ACTIVE:
            return False
        if self._count_daily(d) >= max_daily:
            return False
        if session in self.SESSION_LIMITS:
            if self._count_session(d, session) >= self.SESSION_LIMITS[session]:
                return False
        return True

    def log_open(self, session: str, symbol: str, trade_id: int, d: Optional[dt_date] = None):
        """Log a trade open in the count log table."""
        d = d or datetime.now(timezone.utc).date()
        self.cur.execute("""
            INSERT INTO trade_count_log (date, session, symbol, trade_id, action, count_type, running_total)
            VALUES (%s, %s, %s, %s, 'OPENED', 'SESSION',
                (SELECT COALESCE(MAX(running_total), 0) + 1 FROM trade_count_log
                 WHERE date = %s AND session = %s AND count_type = 'SESSION'))
            ON CONFLICT (date, session, count_type) DO UPDATE SET
                running_total = EXCLUDED.running_total,
                trade_id = EXCLUDED.trade_id
        """, (d, session, symbol, trade_id, d, session))
        self.conn.commit()


# ── CORRELATION GUARD ─────────────────────────────────────────────────────

class CorrelationGuard:
    """
    All 6 crypto symbols treated as one correlated cluster.
    Max 5 open positions total (regardless of symbol).
    No stacking: only 1 position per symbol at a time.
    """

    MAX_OPEN = PORTFOLIO["max_active_trades"]  # 8 (raised from 5 for faster cycling)
    SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]  # V7: DOGE disabled

    def __init__(self, conn=None):
        self.conn = conn

    def can_add(self, active_symbols: List[str], new_symbol: str) -> bool:
        """
        Return True if:
        - Total open positions < MAX_OPEN
        - New symbol is not already in an open position
        """
        if len(active_symbols) >= self.MAX_OPEN:
            return False
        if new_symbol in active_symbols:
            return False
        return True

    def get_active_symbols(self) -> List[str]:
        """Fetch currently open symbols from DB."""
        if self.conn is None:
            return []
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            df = pd.read_sql("""
                SELECT DISTINCT symbol FROM paper_trades WHERE status = 'OPEN'
            """, self.conn)
        return df["symbol"].tolist() if len(df) else []
