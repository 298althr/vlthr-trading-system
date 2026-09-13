"""
VLTHR Signal State Machine
============================
Tracks live signal state per symbol with 15-minute refresh.

Logic:
- 1 row per symbol in signal_state (updated each run)
- Append-only log in signal_state_log (every run snapshot)
- Trajectory: IMPROVING / DECLINING / STABLE based on DQS delta
- 3 consecutive improvements  -> BOOSTED (+5 bonus)
- 2 consecutive declines     -> EXPIRED (-10 penalty, then inactive)
- Signals expire after 2 hours from first_seen_utc

Usage:
    from signal_state_manager import SignalStateManager
    ssm = SignalStateManager(conn)
    state = ssm.evaluate(symbol, dqs, ranker_score, entry, sl, tp, risk_pct, now)
"""
from datetime import datetime, timezone, timedelta
from typing import Optional, Tuple
import warnings
import pandas as pd

from portfolio_config import SIGNAL_LIFECYCLE


class SignalStateManager:
    """Manages signal_state and signal_state_log tables."""

    IMPROVEMENT_THRESHOLD = SIGNAL_LIFECYCLE["trajectory_improvement_threshold"]
    DECLINE_THRESHOLD = SIGNAL_LIFECYCLE["trajectory_decline_threshold"]
    BOOST_BONUS = SIGNAL_LIFECYCLE["boost_bonus"]
    DECLINE_PENALTY = SIGNAL_LIFECYCLE["decline_penalty"]
    MAX_AGE_HOURS = SIGNAL_LIFECYCLE["max_age_hours"]
    GRACE_PERIOD_MINUTES = 45  # tolerate ~3 missed scans before episode reset

    def __init__(self, conn):
        self.conn = conn
        self.cur = conn.cursor()

    def _get_current_state(self, symbol: str):
        """Fetch latest signal_state row for a symbol (most recent signal_bar_utc)."""
        self.conn.rollback()  # clear any prior aborted tx
        self.cur.execute("""
            SELECT id, signal_id, current_dqs, current_ranker_score,
                   consecutive_improvements, consecutive_declines, runs_tracked,
                   trajectory_bonus, first_seen_utc, last_updated_utc, status
            FROM signal_state WHERE symbol = %s
            ORDER BY signal_bar_utc DESC LIMIT 1
        """, (symbol,))
        row = self.cur.fetchone()
        if row:
            return {
                "id": row[0], "signal_id": row[1], "current_dqs": row[2],
                "current_ranker_score": row[3],
                "consecutive_improvements": row[4], "consecutive_declines": row[5],
                "runs_tracked": row[6], "trajectory_bonus": row[7],
                "first_seen_utc": row[8].replace(tzinfo=timezone.utc) if row[8] and row[8].tzinfo is None else row[8],
                "last_updated_utc": row[9].replace(tzinfo=timezone.utc) if row[9] and row[9].tzinfo is None else row[9],
                "status": row[10],
            }
        return None

    def _determine_trajectory(self, prev_dqs: Optional[int], current_dqs: int) -> str:
        """Return trajectory direction based on DQS delta."""
        if prev_dqs is None:
            return "STABLE"
        delta = current_dqs - prev_dqs
        if delta > 0:
            return "IMPROVING"
        elif delta < 0:
            return "DECLINING"
        return "STABLE"

    def evaluate(self, symbol: str, signal_id: int, signal_bar_utc: datetime,
                 dqs: int, ranker_score: int, entry: float, sl: float, tp: float,
                 risk_pct: float, now: Optional[datetime] = None,
                 side: str = None, strategy: str = None, session: str = None) -> dict:
        """
        Evaluate signal state for a single symbol on this run.
        Returns the updated state dict.
        """
        now = now or datetime.now(timezone.utc)
        current = self._get_current_state(symbol)

        if current is None:
            # First time seeing this signal — initialise
            state = {
                "signal_id": signal_id,
                "symbol": symbol,
                "signal_bar_utc": signal_bar_utc,
                "first_seen_utc": now,
                "last_updated_utc": now,
                "current_dqs": dqs,
                "current_ranker_score": ranker_score,
                "current_entry": entry,
                "current_sl": sl,
                "current_tp": tp,
                "risk_pct": risk_pct,
                "prev_dqs": None,
                "prev_ranker_score": None,
                "dqs_delta": 0,
                "consecutive_improvements": 0,
                "consecutive_declines": 0,
                "runs_tracked": 1,
                "trajectory_bonus": 0,
                "adjusted_score": dqs,
                "status": "ACTIVE",
                "expiry_reason": None,
                "side": side,
                "strategy": strategy,
                "session": session,
            }
            self._upsert_state(state)
            self._append_log(state, now, run_number=1, trajectory="STABLE")
            return state

        # ── Existing signal — compute trajectory ──

        # If previously EXPIRED, check grace period before reset vs continuation
        if current["status"] == "EXPIRED":
            minutes_since = (now - current["last_updated_utc"]).total_seconds() / 60
            if minutes_since <= self.GRACE_PERIOD_MINUTES:
                # Within grace — revive this episode, carry counters forward
                current["status"] = "ACTIVE"
                print(f"  [StateTrace] {symbol}: Reviving EXPIRED within grace "
                      f"({minutes_since:.0f}m <= {self.GRACE_PERIOD_MINUTES}m)")
            else:
                # Outside grace — start a fresh episode
                state = {
                    "signal_id": signal_id,
                    "symbol": symbol,
                    "signal_bar_utc": signal_bar_utc,
                    "first_seen_utc": now,
                    "last_updated_utc": now,
                    "current_dqs": dqs,
                    "current_ranker_score": ranker_score,
                    "current_entry": entry,
                    "current_sl": sl,
                    "current_tp": tp,
                    "risk_pct": risk_pct,
                    "prev_dqs": None,
                    "prev_ranker_score": None,
                    "dqs_delta": 0,
                    "consecutive_improvements": 0,
                    "consecutive_declines": 0,
                    "runs_tracked": 1,
                    "trajectory_bonus": 0,
                    "adjusted_score": dqs,
                    "status": "ACTIVE",
                    "expiry_reason": None,
                    "side": side,
                    "strategy": strategy,
                    "session": session,
                }
                self._upsert_state(state)
                self._append_log(state, now, run_number=1, trajectory="STABLE")
                return state

        prev_dqs = current["current_dqs"]
        trajectory = self._determine_trajectory(prev_dqs, dqs)
        dqs_delta = dqs - prev_dqs if prev_dqs else 0

        # Update consecutive counters
        improvements = current["consecutive_improvements"]
        declines = current["consecutive_declines"]
        runs = current["runs_tracked"] + 1
        bonus = current["trajectory_bonus"]

        if trajectory == "IMPROVING":
            improvements += 1
            declines = 0
        elif trajectory == "DECLINING":
            declines += 1
            improvements = 0
        else:
            # STABLE — no change to counters
            pass

        # Apply trajectory rules
        status = current["status"]
        expiry_reason = None

        if improvements >= self.IMPROVEMENT_THRESHOLD:
            bonus = self.BOOST_BONUS
            status = "BOOSTED"
        elif declines >= self.DECLINE_THRESHOLD:
            bonus = self.DECLINE_PENALTY
            status = "EXPIRED"
            expiry_reason = f"DQS declined {declines} consecutive runs"

        # Check freshness-based expiration (uses last update time, not first seen)
        minutes_since_update = (now - current["last_updated_utc"]).total_seconds() / 60
        if minutes_since_update > (self.MAX_AGE_HOURS * 60) and status not in ("EXPIRED",):
            status = "EXPIRED"
            expiry_reason = (f"Signal stale {minutes_since_update:.0f}m exceeds "
                             f"{self.MAX_AGE_HOURS}h limit")

        adjusted_score = max(0, min(100, dqs + bonus))

        state = {
            "signal_id": signal_id,
            "symbol": symbol,
            "signal_bar_utc": signal_bar_utc,
            "first_seen_utc": current["first_seen_utc"],
            "last_updated_utc": now,
            "current_dqs": dqs,
            "current_ranker_score": ranker_score,
            "current_entry": entry,
            "current_sl": sl,
            "current_tp": tp,
            "risk_pct": risk_pct,
            "prev_dqs": prev_dqs,
            "prev_ranker_score": current["current_ranker_score"],
            "dqs_delta": dqs_delta,
            "consecutive_improvements": improvements,
            "consecutive_declines": declines,
            "runs_tracked": runs,
            "trajectory_bonus": bonus,
            "adjusted_score": adjusted_score,
            "status": status,
            "expiry_reason": expiry_reason,
            "side": side,
            "strategy": strategy,
            "session": session,
        }

        # Trace-first logging: state transition snapshot
        print(f"  [StateTrace] {symbol}: prev_dqs={prev_dqs} new_dqs={dqs} delta={dqs_delta:+d} "
              f"trajectory={trajectory} improvements={improvements} declines={declines} "
              f"bonus={bonus} adjusted_score={adjusted_score} status={status} "
              f"stale={minutes_since_update:.0f}m runs={runs}")

        self._upsert_state(state)
        self._append_log(state, now, run_number=runs, trajectory=trajectory)
        return state

    def _expire_previous_symbol_state(self, state: dict):
        """Expire any old ACTIVE/BOOSTED/TRADING rows for this symbol."""
        self.cur.execute("""
            UPDATE signal_state
            SET status = 'EXPIRED',
                expiry_reason = 'Replaced by newer signal_bar',
                updated_at = NOW()
            WHERE symbol = %s
              AND status IN ('ACTIVE', 'BOOSTED', 'TRADING')
              AND signal_bar_utc != %s
        """, (state["symbol"], state["signal_bar_utc"]))

    def _upsert_state(self, state: dict):
        """Upsert signal_state table."""
        # Prevent invariant breach: expire old rows for this symbol first
        self._expire_previous_symbol_state(state)
        sql = """
            INSERT INTO signal_state (
                signal_id, symbol, signal_bar_utc, first_seen_utc, last_updated_utc,
                current_dqs, current_ranker_score, current_entry, current_sl, current_tp, risk_pct,
                prev_dqs, prev_ranker_score, dqs_delta,
                consecutive_improvements, consecutive_declines, runs_tracked, trajectory_bonus,
                adjusted_score, status, expiry_reason,
                side, strategy, session
            ) VALUES (
                %(signal_id)s, %(symbol)s, %(signal_bar_utc)s, %(first_seen_utc)s, %(last_updated_utc)s,
                %(current_dqs)s, %(current_ranker_score)s, %(current_entry)s, %(current_sl)s, %(current_tp)s, %(risk_pct)s,
                %(prev_dqs)s, %(prev_ranker_score)s, %(dqs_delta)s,
                %(consecutive_improvements)s, %(consecutive_declines)s, %(runs_tracked)s, %(trajectory_bonus)s,
                %(adjusted_score)s, %(status)s, %(expiry_reason)s,
                %(side)s, %(strategy)s, %(session)s
            )
            ON CONFLICT (symbol, signal_bar_utc) DO UPDATE SET
                signal_id = EXCLUDED.signal_id,
                signal_bar_utc = EXCLUDED.signal_bar_utc,
                last_updated_utc = EXCLUDED.last_updated_utc,
                current_dqs = EXCLUDED.current_dqs,
                current_ranker_score = EXCLUDED.current_ranker_score,
                current_entry = EXCLUDED.current_entry,
                current_sl = EXCLUDED.current_sl,
                current_tp = EXCLUDED.current_tp,
                risk_pct = EXCLUDED.risk_pct,
                prev_dqs = EXCLUDED.prev_dqs,
                prev_ranker_score = EXCLUDED.prev_ranker_score,
                dqs_delta = EXCLUDED.dqs_delta,
                consecutive_improvements = EXCLUDED.consecutive_improvements,
                consecutive_declines = EXCLUDED.consecutive_declines,
                runs_tracked = EXCLUDED.runs_tracked,
                trajectory_bonus = EXCLUDED.trajectory_bonus,
                adjusted_score = EXCLUDED.adjusted_score,
                status = EXCLUDED.status,
                expiry_reason = EXCLUDED.expiry_reason,
                side = EXCLUDED.side,
                strategy = EXCLUDED.strategy,
                session = EXCLUDED.session,
                updated_at = NOW()
        """
        self.cur.execute(sql, state)
        self.conn.commit()

    def _append_log(self, state: dict, run_time: datetime, run_number: int, trajectory: str):
        """Append a row to signal_state_log."""
        sql = """
            INSERT INTO signal_state_log (
                signal_id, symbol, run_time, run_number,
                dqs, ranker_score, entry, sl, tp, risk_pct,
                status, trajectory, delta
            ) VALUES (
                %(signal_id)s, %(symbol)s, %(run_time)s, %(run_number)s,
                %(current_dqs)s, %(current_ranker_score)s, %(current_entry)s, %(current_sl)s, %(current_tp)s, %(risk_pct)s,
                %(status)s, %(trajectory)s, %(dqs_delta)s
            )
        """
        self.cur.execute(sql, {
            **state,
            "run_time": run_time,
            "run_number": run_number,
            "trajectory": trajectory,
        })
        self.conn.commit()

    def get_active_signals(self) -> pd.DataFrame:
        """Return latest active signal per symbol."""
        self.conn.rollback()  # clear any prior aborted tx
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            return pd.read_sql("""
                SELECT DISTINCT ON (symbol) *
                FROM signal_state
                WHERE status IN ('ACTIVE', 'BOOSTED', 'TRADING')
                ORDER BY symbol, signal_bar_utc DESC
            """, self.conn)

    def cleanup_expired(self, now: Optional[datetime] = None):
        """Mark any aged signals as EXPIRED and log."""
        now = now or datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=self.MAX_AGE_HOURS)
        self.cur.execute("""
            UPDATE signal_state
            SET status = 'EXPIRED',
                expiry_reason = 'Auto-expired by cleanup job',
                updated_at = NOW()
            WHERE status IN ('ACTIVE', 'BOOSTED')
              AND last_updated_utc < %s
        """, (cutoff,))
        n = self.cur.rowcount
        self.conn.commit()
        return n
