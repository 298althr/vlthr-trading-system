"""
VLTHR Portfolio Pipeline — Entry Point
======================================
Runs one 5-minute iteration of the portfolio-centric DQS pipeline.

Usage:
    # Single run (for cron / manual)
    python engine/run_pipeline.py

    # Continuous loop (for daemon / Docker)
    python engine/run_pipeline.py --loop

    # Replay mode (30-day historical simulation)
    python engine/run_pipeline.py --replay --start 2026-05-01 --end 2026-05-31

Environment:
    DB_URL from .env
"""
import sys
from pathlib import Path
from datetime import datetime, timezone
import argparse
import os
import time

_engine = Path(__file__).resolve().parent
# Support both Docker (/app/engine/) and local deep-nested paths
try:
    _repo = _engine.parents[2]  # local dev: up to engine root
except IndexError:
    _repo = _engine.parent       # Docker: /app/engine/ -> /app/
sys.path.insert(0, str(_engine))
sys.path.insert(0, str(_repo / "departments" / "strategy_research" / "BACKTESTER" / "scripts" / "common"))
sys.path.insert(0, str(_repo / "departments" / "strategy_research" / "BACKTESTER" / "strategy" / "signals"))

# Load DB_URL
_env = _repo / ".env"
if _env.exists():
    with open(_env) as f:
        for line in f:
            if line.strip() and not line.startswith("#") and "=" in line:
                k, v = line.strip().split("=", 1)
                os.environ.setdefault(k, v)

import psycopg2

from db_migration import migrate
from replay_runner import ReplayRunner
from pipeline_health_server import start_health_server, update_last_run
from pipeline_heartbeat import write_heartbeat
from pipeline_flow import run_pipeline_iteration


# ── Health server startup ────────────────────────────────────────────────
HEALTH_PORT = int(os.environ.get("PIPELINE_HEALTH_PORT", "8200"))
start_health_server(port=HEALTH_PORT)


def get_conn():
    """Establish DB connection with fallback port."""
    db_url = os.environ.get("DB_URL")
    if not db_url:
        raise RuntimeError("DB_URL not found in .env")
    try:
        return psycopg2.connect(db_url, sslmode="prefer")
    except psycopg2.OperationalError:
        alt = db_url.replace(":5432/", ":6543/")
        return psycopg2.connect(alt, sslmode="prefer")


def _write_heartbeat(conn, result: dict, duration_ms: int, interval_sec: int):
    """Write DB heartbeat and update in-memory health state."""
    now = datetime.now(timezone.utc)
    status = result.get("status", "OK")
    abort_reason = result.get("reason") if status == "ABORTED" else None
    scanned = result.get("signals_scanned", 0)
    approved = result.get("signals_approved", 0)

    import json
    write_heartbeat(
        conn=conn,
        status=status,
        duration_ms=duration_ms,
        signals_scanned=scanned,
        signals_approved=approved,
        abort_reason=abort_reason,
        node_summary=json.dumps({"result": result}),
        now=now,
    )
    update_last_run(
        status=status,
        run_time=now,
        signals_scanned=scanned,
        signals_approved=approved,
        duration_ms=duration_ms,
        abort_reason=abort_reason,
        loop_interval_sec=interval_sec,
    )


def single_run(interval_sec: int = 900):
    """Execute one pipeline iteration with timing + heartbeat."""
    t0 = time.time()
    try:
        result = run_pipeline_iteration()
        print(f"\n  Result: {result}")
    except Exception as e:
        result = {"status": "ERROR", "reason": str(e)}
        print(f"\n  ERROR: {e}")
    duration_ms = int((time.time() - t0) * 1000)
    # Write heartbeat via fresh conn (prefect flow closed its own)
    try:
        conn = get_conn()
        try:
            _write_heartbeat(conn, result, duration_ms, interval_sec)
        except Exception as hb_err:
            print(f"[Pipeline] Heartbeat write failed: {hb_err}")
        finally:
            conn.close()
    except Exception as conn_err:
        print(f"[Pipeline] Heartbeat connection failed: {conn_err}")
    return result


def loop_mode(interval_sec: int = 300):
    """Run continuously every interval_sec seconds."""
    print(f"[Pipeline] Loop mode started. Interval: {interval_sec}s")
    while True:
        result = single_run(interval_sec=interval_sec)
        if result.get("status") == "ABORTED":
            print(f"[Pipeline] Run aborted: {result.get('reason')}. Retrying in {interval_sec}s...")
        print(f"[Pipeline] Sleeping {interval_sec}s...")
        time.sleep(interval_sec)


def replay_mode(start: str, end: str):
    """Run historical replay simulation."""
    conn = get_conn()
    try:
        runner = ReplayRunner(conn, start=start, end=end)
        results = runner.run()

        # Save equity curve
        out = _engine / "replay_results"
        out.mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        if "equity_curve" in results:
            results["equity_curve"].to_csv(out / f"equity_curve_{ts}.csv", index=False)
            print(f"  Saved equity curve to {out / f'equity_curve_{ts}.csv'}")

        # Save summary
        summary = {k: v for k, v in results.items() if k != "equity_curve" and k != "trades"}
        import json
        with open(out / f"summary_{ts}.json", "w") as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"  Saved summary to {out / f'summary_{ts}.json'}")

    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VLTHR Portfolio Pipeline")
    parser.add_argument("--loop", action="store_true", help="Run in continuous loop")
    parser.add_argument("--interval", type=int, default=300, help="Loop interval in seconds (default: 300 = 5m)")
    parser.add_argument("--replay", action="store_true", help="Run historical replay")
    parser.add_argument("--start", type=str, default="2026-05-01", help="Replay start date")
    parser.add_argument("--end", type=str, default="2026-05-31", help="Replay end date")
    parser.add_argument("--migrate", action="store_true", help="Run DB migration first")
    args = parser.parse_args()

    if args.migrate:
        print("[Pipeline] Running DB migration...")
        migrate()

    if args.replay:
        replay_mode(args.start, args.end)
    elif args.loop:
        loop_mode(args.interval)
    else:
        single_run()
