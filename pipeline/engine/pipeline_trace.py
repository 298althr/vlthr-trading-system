"""
VLTHR Pipeline Trace — Per-Node Observability (Phase 4)
========================================================
Writes one row per node per run to pipeline_trace.
Nodes: pre_flight, scan, score, predict, state, rank, gate, upsert, snapshot, telegram, invariants

Usage (from portfolio_orchestrator):
    from pipeline_trace import PipelineTrace
    tracer = PipelineTrace(conn)
    tracer.start_run(now)
    tracer.log("scan", "OK", in_count=6, out_count=2, detail={"symbols": [...]})
    tracer.end_run()
"""
from datetime import datetime, timezone
from typing import Optional, Dict, Any
import json

VALID_NODES = {
    "pre_flight", "scan", "score", "predict", "state", "rank",
    "gate", "upsert", "snapshot", "telegram", "invariants", "cleanup",
}


class PipelineTrace:
    """Per-node trace logger for the pipeline."""

    def __init__(self, conn):
        self.conn = conn
        self._run_time: Optional[datetime] = None
        self._nodes_logged = set()

    def start_run(self, now: Optional[datetime] = None):
        """Mark the start of a pipeline run."""
        self._run_time = now or datetime.now(timezone.utc)
        self._nodes_logged = set()

    def log(self, node: str, status: str, duration_ms: int = 0,
            in_count: int = 0, out_count: int = 0, detail: Optional[Dict] = None,
            now: Optional[datetime] = None):
        """Write a single trace row for a node."""
        if node not in VALID_NODES:
            node = "unknown"
        now = now or datetime.now(timezone.utc)
        run_time = self._run_time or now

        cur = self.conn.cursor()
        try:
            cur.execute("""
                INSERT INTO pipeline_trace (
                    run_time, node, status, duration_ms, in_count, out_count, detail
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                run_time, node, status, duration_ms, in_count, out_count,
                json.dumps(detail) if detail else None,
            ))
            self.conn.commit()
            self._nodes_logged.add(node)
        except Exception as e:
            self.conn.rollback()
            print(f"[Trace] Failed to write trace for {node}: {e}")

    def log_error(self, node: str, error: str, detail: Optional[Dict] = None):
        """Convenience for logging a node failure."""
        self.log(node, "FAIL", detail={"error": error, **(detail or {})})

    def log_warn(self, node: str, message: str, detail: Optional[Dict] = None):
        """Convenience for logging a node warning."""
        self.log(node, "WARN", detail={"message": message, **(detail or {})})

    def end_run(self):
        """Optional end-of-run marker."""
        pass

    # ── Self-diagnostics ────────────────────────────────────────────────

    def check_stuck_runs_tracked(self, threshold_runs: int = 1, min_logs: int = 5) -> Optional[str]:
        """
        Diagnosis: if runs_tracked has been stuck at `threshold_runs` across
        the last `min_logs` runs for any symbol, return a warning message.
        """
        cur = self.conn.cursor()
        cur.execute("""
            SELECT symbol, runs_tracked, last_updated_utc
            FROM signal_state
            WHERE status IN ('ACTIVE', 'BOOSTED')
              AND runs_tracked <= %s
              AND last_updated_utc < NOW() - INTERVAL '2 hours'
        """, (threshold_runs,))
        rows = cur.fetchall()
        if rows:
            syms = ", ".join([r[0] for r in rows])
            msg = f"runs_tracked stuck at <= {threshold_runs} for {len(rows)} symbol(s): {syms}"
            self.log_warn("state", msg, detail={"symbols": [r[0] for r in rows], "max_runs": threshold_runs})
            return msg
        return None

    def get_trace_for_run(self, run_time: datetime, limit: int = 50) -> list:
        """Fetch trace rows for a specific run."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT node, status, duration_ms, in_count, out_count, detail
            FROM pipeline_trace
            WHERE run_time = %s
            ORDER BY id
            LIMIT %s
        """, (run_time, limit))
        return [
            {
                "node": r[0], "status": r[1], "duration_ms": r[2],
                "in_count": r[3], "out_count": r[4], "detail": r[5],
            }
            for r in cur.fetchall()
        ]

    def get_latest_trace(self, limit: int = 20) -> list:
        """Fetch trace rows for the most recent run."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT run_time, node, status, duration_ms, in_count, out_count, detail
            FROM pipeline_trace
            ORDER BY run_time DESC, id DESC
            LIMIT %s
        """, (limit,))
        return [
            {
                "run_time": r[0], "node": r[1], "status": r[2], "duration_ms": r[3],
                "in_count": r[4], "out_count": r[5], "detail": r[6],
            }
            for r in cur.fetchall()
        ]
