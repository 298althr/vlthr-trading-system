"""
VLTHR Pipeline Prefect Flow — Phase 1 Observability Wrapper
============================================================
Wraps run_iteration in a Prefect flow so every run appears in the Prefect UI
with task-level visibility into scan, score, state, rank, gate, and upsert.

This is a thin wrapper; the core logic remains in portfolio_orchestrator.
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

# Ensure imports resolve
_engine = Path(__file__).resolve().parent
try:
    _repo = _engine.parents[2]
except IndexError:
    _repo = _engine.parent
sys.path.insert(0, str(_engine))
sys.path.insert(0, str(_repo / "departments" / "strategy_research" / "BACKTESTER" / "scripts" / "common"))
sys.path.insert(0, str(_repo / "departments" / "strategy_research" / "BACKTESTER" / "strategy" / "signals"))

import psycopg2

# Prefect is optional — gracefully degrade if not installed or disabled
PREFECT_AVAILABLE = False
flow = lambda **kwargs: lambda f: f  # noqa
task = lambda **kwargs: lambda f: f  # noqa
get_run_logger = lambda: None

try:
    if os.environ.get("DISABLE_PREFECT", "").lower() not in ("true", "1", "yes"):
        from prefect import flow as _flow, task as _task, get_run_logger as _get_run_logger  # noqa
        from prefect.states import Completed, Failed  # noqa
        flow = _flow
        task = _task
        get_run_logger = _get_run_logger
        PREFECT_AVAILABLE = True
except ImportError:
    pass

from portfolio_orchestrator import run_iteration


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


@task(name="pre_flight", retries=0)
def _task_pre_flight():
    """Placeholder for explicit pre-flight task (currently inside run_iteration)."""
    return True


@task(name="run_iteration", retries=0)
def _task_run_iteration(conn):
    """Execute the core pipeline iteration."""
    return run_iteration(conn)


@flow(name="vlthr-pipeline-run", log_prints=True)
def pipeline_run_flow(now: Optional[datetime] = None) -> dict:
    """
    Prefect flow for a single pipeline iteration.
    Returns the same dict as run_iteration.
    """
    logger = get_run_logger()
    now = now or datetime.now(timezone.utc)
    if logger:
        logger.info(f"Pipeline run starting at {now.isoformat()}")

    conn = get_conn()
    try:
        _task_pre_flight()
        result = _task_run_iteration(conn)
        if logger:
            logger.info(f"Pipeline run complete: {result}")
        return result
    except Exception as e:
        if logger:
            logger.error(f"Pipeline run failed: {e}")
        raise
    finally:
        conn.close()


def run_without_prefect(now: Optional[datetime] = None) -> dict:
    """Fallback when Prefect is not installed."""
    now = now or datetime.now(timezone.utc)
    conn = get_conn()
    try:
        return run_iteration(conn, now=now)
    finally:
        conn.close()


def run_pipeline_iteration(now: Optional[datetime] = None) -> dict:
    """Entry point used by run_pipeline.py — chooses Prefect or plain."""
    if PREFECT_AVAILABLE:
        return pipeline_run_flow(now=now)
    return run_without_prefect(now=now)


if __name__ == "__main__":
    result = run_pipeline_iteration()
    print(result)
