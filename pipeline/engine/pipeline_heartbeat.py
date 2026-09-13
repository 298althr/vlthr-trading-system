"""
VLTHR Pipeline Heartbeat — DB writer + stats aggregation
=======================================================
Writes one row per pipeline iteration to pipeline_heartbeat.
Used by the health server and dashboard "engine alive" indicator.

Usage:
    from pipeline_heartbeat import write_heartbeat
    write_heartbeat(conn, result={"status":"OK", "signals_scanned":6, ...}, duration_ms=1500)
"""
from datetime import datetime, timezone
from typing import Optional, Dict, Any


def write_heartbeat(
    conn,
    status: str,
    duration_ms: int = 0,
    signals_scanned: int = 0,
    signals_approved: int = 0,
    abort_reason: Optional[str] = None,
    node_summary: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
):
    """Insert a single heartbeat row."""
    now = now or datetime.now(timezone.utc)
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO pipeline_heartbeat (
                run_time, status, abort_reason, duration_ms,
                signals_scanned, signals_approved, node_summary
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                now,
                status,
                abort_reason,
                duration_ms,
                signals_scanned,
                signals_approved,
                node_summary,
            ),
        )
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[Heartbeat] Failed to write heartbeat: {e}")


def get_latest_heartbeat(conn) -> Optional[Dict[str, Any]]:
    """Fetch the most recent heartbeat row."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT run_time, status, abort_reason, duration_ms,
               signals_scanned, signals_approved, node_summary, created_at
        FROM pipeline_heartbeat
        ORDER BY run_time DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "run_time": row[0],
        "status": row[1],
        "abort_reason": row[2],
        "duration_ms": row[3],
        "signals_scanned": row[4],
        "signals_approved": row[5],
        "node_summary": row[6],
        "created_at": row[7],
    }


def get_heartbeat_history(conn, limit: int = 20) -> list:
    """Fetch the last N heartbeat rows."""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT run_time, status, duration_ms, signals_scanned, signals_approved
        FROM pipeline_heartbeat
        ORDER BY run_time DESC
        LIMIT %s
        """,
        (limit,),
    )
    rows = cur.fetchall()
    return [
        {
            "run_time": r[0],
            "status": r[1],
            "duration_ms": r[2],
            "signals_scanned": r[3],
            "signals_approved": r[4],
        }
        for r in rows
    ]
