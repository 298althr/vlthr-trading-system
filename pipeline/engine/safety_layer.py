"""
VLTHR Safety Layer
==================
Protects the portfolio from bugs, bad data, and runaway logic.

Features:
1. Pre-flight checks — data freshness, circuit breaker, balance sanity
2. Shadow mode — logs what WOULD have happened without executing
3. Invariant checks — portfolio risk, drawdown, trade count bounds
4. Post-flight checks — DB consistency, signal count spikes
5. Circuit breaker — auto-halt on repeated errors
6. Error log — all anomalies written to error_log table

Usage:
    from safety_layer import SafetyLayer
    safety = SafetyLayer(conn)
    if not safety.pre_flight_checks(now):
        abort()
    safety.post_flight_checks(now, approved_signals)
"""
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import hashlib
import json
import warnings
import os
import glob
import pandas as pd

from portfolio_config import PORTFOLIO, FRESHNESS, SYMBOLS, COLD_START_GRACE_CYCLES, COLD_START_BAR_MAX_AGE_MIN

# Module-level counter: tracks how many pre_flight_checks calls since process start
_pre_flight_cycle_count = 0


class SafetyLayer:
    """Safety and monitoring layer for the pipeline."""

    ERROR_THRESHOLD = 3          # halt after 3 critical errors in 1h
    CIRCUIT_BREAKER_WINDOW = 3600  # 1 hour in seconds
    MAX_SIGNALS_PER_RUN = 20     # sanity cap

    def __init__(self, conn, shadow_mode: bool = False):
        self.conn = conn
        self.cur = conn.cursor()
        self.shadow_mode = shadow_mode

    # ── PRE-FLIGHT CHECKS ──────────────────────────────────────────────────

    def pre_flight_checks(self, now: datetime) -> bool:
        """Return False if any check fails — abort the iteration."""
        global _pre_flight_cycle_count
        _pre_flight_cycle_count += 1
        in_grace = _pre_flight_cycle_count <= COLD_START_GRACE_CYCLES
        if in_grace:
            print(f"  [SAFETY] Cold-start grace period: cycle {_pre_flight_cycle_count}/{COLD_START_GRACE_CYCLES} — relaxed freshness threshold ({COLD_START_BAR_MAX_AGE_MIN}m vs normal {FRESHNESS['bar_max_age_min']}m)")
        checks = {
            "circuit_breaker": self._check_circuit_breaker(now),
            "data_freshness": self._check_data_freshness(now, in_grace),
            "account_balance": self._check_account_balance(now),
            "calibration_age": self._check_calibration_age(now),
        }
        failed = [name for name, ok in checks.items() if not ok]
        if failed:
            print(f"  [SAFETY] Pre-flight FAILED: {failed}")
        return all(checks.values())

    def _check_circuit_breaker(self, now: datetime) -> bool:
        """Halt if too many critical errors recently. Auto-expires old errors."""
        window_start = now - timedelta(seconds=self.CIRCUIT_BREAKER_WINDOW)
        try:
            # Auto-expire critical errors older than the window
            self.cur.execute("""
                UPDATE error_log SET resolved = TRUE
                WHERE is_critical = TRUE AND resolved = FALSE AND run_time < %s
            """, (window_start,))
            self.conn.commit()
            # Count remaining unresolved critical errors in window
            self.cur.execute("""
                SELECT COUNT(*) FROM error_log
                WHERE is_critical = TRUE AND resolved = FALSE AND run_time >= %s
            """, (window_start,))
            count = self.cur.fetchone()[0]
        except Exception as e:
            print(f"[SafetyLayer] Circuit breaker query failed: {e}")
            return True

        if count >= self.ERROR_THRESHOLD:
            self.log_error("safety", f"CIRCUIT BREAKER TRIPPED — {count} critical errors in last hour", is_critical=False)
            return False
        return True

    def _check_data_freshness(self, now: datetime, in_grace: bool = False) -> bool:
        """Ensure latest data is not too old. Checks all active symbols."""
        data_root = os.environ.get("PARQUET_DATA_ROOT", "/data")
        stale_symbols = []
        any_data = False
        max_age = COLD_START_BAR_MAX_AGE_MIN if in_grace else FRESHNESS["bar_max_age_min"]

        for symbol in SYMBOLS:
            parquet_path = os.path.join(data_root, "bybit", symbol, "1m")
            latest_ts = None
            try:
                if os.path.isdir(parquet_path):
                    files = sorted(glob.glob(os.path.join(parquet_path, "*", "*.parquet")))
                    if files:
                        df = pd.read_parquet(files[-1])
                        if len(df) and "timestamp" in df.columns:
                            latest_ts = pd.to_datetime(df["timestamp"].max(), utc=True)
            except Exception:
                pass

            if latest_ts is None:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        latest = pd.read_sql("""
                            SELECT MAX(created_at) as latest_ts
                            FROM signals_historical WHERE symbol = %s
                        """, self.conn, params=(symbol,))
                    if not latest["latest_ts"].isna().all():
                        latest_ts = pd.to_datetime(latest["latest_ts"].iloc[0], utc=True)
                except Exception:
                    pass

            if latest_ts is None:
                continue

            any_data = True
            age_min = (now - latest_ts).total_seconds() / 60
            if age_min > max_age:
                stale_symbols.append(f"{symbol} ({age_min:.0f}m)")

        if not any_data:
            self.log_error("safety", "No data found (first run or missing data)", is_critical=False)
            return True

        if stale_symbols:
            self.log_error("safety", f"Data stale: {', '.join(stale_symbols)}", is_critical=not in_grace)
            if in_grace:
                print(f"  [SAFETY] Data stale (grace period, non-blocking): {', '.join(stale_symbols)}")
                return True
            return False
        return True

    def _check_account_balance(self, now: datetime) -> bool:
        """Ensure balance is positive and not in drawdown halt zone (mark-to-market)."""
        try:
            self.cur.execute("SELECT balance, equity, margin_used FROM paper_account LIMIT 1")
            row = self.cur.fetchone()
            if not row:
                self.log_error("safety", "paper_account missing", is_critical=True)
                return False
            balance, equity, margin_used = float(row[0]), float(row[1]), float(row[2] or 0)
            if balance <= 0:
                self.log_error("safety", f"Account balance {balance} — halted", is_critical=True)
                return False
            # True mark-to-market equity = wallet_balance + floating_pnl
            wallet = equity + margin_used
            floating_pnl = self._compute_floating_pnl()
            true_equity = wallet + floating_pnl
            if true_equity < balance * (1 - PORTFOLIO["account_drawdown_halt_pct"]):
                self.log_error("safety", f"Drawdown halt: true_equity={true_equity:.2f}, balance={balance}, floating_pnl={floating_pnl:.2f}", is_critical=True)
                return False
        except Exception as e:
            self.log_error("safety", f"Balance check failed: {e}", is_critical=True)
            return False
        return True

    def _compute_floating_pnl(self) -> float:
        """Compute unrealized PnL from open trades using latest parquet close prices."""
        try:
            self.cur.execute("""
                SELECT symbol, side, entry_price_actual, qty_contracts
                FROM paper_trades WHERE status = 'OPEN'
            """)
            rows = self.cur.fetchall()
            if not rows:
                return 0.0
            total = 0.0
            data_root = os.environ.get("PARQUET_DATA_ROOT", "/data")
            for symbol, side, entry, qty in rows:
                entry = float(entry or 0)
                qty = float(qty or 0)
                if entry <= 0 or qty <= 0:
                    continue
                parquet_path = os.path.join(data_root, "bybit", symbol, "1m")
                if not os.path.isdir(parquet_path):
                    continue
                files = sorted(glob.glob(os.path.join(parquet_path, "*", "*.parquet")))
                if not files:
                    continue
                df = pd.read_parquet(files[-1])
                if len(df) == 0 or "close" not in df.columns:
                    continue
                live_price = float(df.iloc[-1]["close"])
                if live_price <= 0:
                    continue
                if (side or "LONG").upper() == "LONG":
                    total += (live_price - entry) * qty
                else:
                    total += (entry - live_price) * qty
            return total
        except Exception:
            return 0.0

    def _check_calibration_age(self, now: datetime) -> bool:
        """Warn if calibration matrix is stale (>30 days). Warning only — does not abort."""
        try:
            self.cur.execute("""
                SELECT MAX(updated_at) FROM confidence_matrix
                WHERE version = (SELECT MAX(version) FROM confidence_matrix)
            """)
            row = self.cur.fetchone()
            if row and row[0]:
                age_days = (now - row[0]).days
                if age_days > 30:
                    self.log_error("safety", f"Calibration matrix is {age_days} days old — recalibration recommended", is_critical=False)
        except Exception:
            pass  # Table may not exist yet — pipeline runs without calibration gate
        return True

    # ── SHADOW MODE ────────────────────────────────────────────────────────

    def shadow_log(self, action: str, details: dict):
        """Log what WOULD have happened without executing."""
        if not self.shadow_mode:
            return
        print(f"[SHADOW] {action}: {details}")
        self.log_error("shadow", f"{action}: {details}", is_critical=False)

    # ── INVARIANT CHECKS ─────────────────────────────────────────────────────

    def invariant_checks(self, now: datetime, approved_signals: List[dict]) -> bool:
        """Run after ranking but before execution. Return False to block."""
        n_signals = len(approved_signals)
        if n_signals > self.MAX_SIGNALS_PER_RUN:
            self.log_error("safety", f"Signal spike: {n_signals} > {self.MAX_SIGNALS_PER_RUN}", is_critical=True)
            return False
        return True

    # ── POST-FLIGHT CHECKS ─────────────────────────────────────────────────

    def post_flight_checks(self, now: datetime, approved_signals: List[dict]):
        """Verify DB consistency after the run."""
        try:
            # Check: active trades count matches risk_ledger open count
            self.cur.execute("SELECT COUNT(*) FROM paper_trades WHERE status = 'OPEN'")
            n_open = self.cur.fetchone()[0]
            self.cur.execute("SELECT COUNT(*) FROM risk_ledger WHERE is_open = TRUE")
            n_ledger = self.cur.fetchone()[0]
            if n_open != n_ledger:
                self.log_error("safety", f"Mismatch: {n_open} open trades vs {n_ledger} ledger entries")
        except Exception as e:
            self.log_error("safety", f"Post-flight check failed: {e}")

    # ── ERROR LOGGING ──────────────────────────────────────────────────────

    def log_error(self, source: str, message: str, symbol: Optional[str] = None,
                  is_critical: bool = False, stack_trace: Optional[str] = None):
        """Write to error_log table with checksum for tamper detection."""
        now = datetime.now(timezone.utc)
        try:
            self.conn.rollback()
            checksum = hashlib.sha256(
                f"{source}|{message}|{symbol or ''}|{now.isoformat()}|{is_critical}".encode()
            ).hexdigest()[:16]
            self.cur.execute("""
                INSERT INTO error_log (source, error_type, message, stack_trace, symbol, run_time, is_critical, checksum)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (source, "CRITICAL" if is_critical else "WARNING", message, stack_trace, symbol, now, is_critical, checksum))
            self.conn.commit()
        except Exception as e:
            print(f"[SafetyLayer] Failed to log error: {e}")

