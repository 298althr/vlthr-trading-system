"""
VLTHR Pipeline Health Server — Embedded FastAPI on port 8200
===========================================================
Provides /health, /last-run, /metrics for Docker healthchecks and dashboard.
Runs in a background thread so the main pipeline loop is unaffected.

Usage (from run_pipeline.py):
    from pipeline_health_server import start_health_server, update_last_run
    start_health_server(port=8200)
    update_last_run({"status": "OK", "signals_approved": 2, ...})
"""
import os
import json
import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from threading import Lock

# FastAPI is only imported when this module is actually used
try:
    from fastapi import FastAPI
    from uvicorn import Config, Server
except ImportError:
    FastAPI = None
    Config = None
    Server = None

# ── Shared state (thread-safe) ───────────────────────────────────────────
_last_run: Dict[str, Any] = {
    "status": "unknown",
    "run_time": None,
    "signals_scanned": 0,
    "signals_approved": 0,
    "duration_ms": 0,
    "abort_reason": None,
    "loop_interval_sec": 300,
}
_state_lock = Lock()
_startup_time = datetime.now(timezone.utc)


def update_last_run(
    status: str,
    run_time: Optional[datetime] = None,
    signals_scanned: int = 0,
    signals_approved: int = 0,
    duration_ms: int = 0,
    abort_reason: Optional[str] = None,
    loop_interval_sec: int = 300,
    node_summary: Optional[Dict] = None,
):
    """Call this after each pipeline iteration to update the health endpoint."""
    with _state_lock:
        _last_run.update({
            "status": status,
            "run_time": (run_time or datetime.now(timezone.utc)).isoformat(),
            "signals_scanned": signals_scanned,
            "signals_approved": signals_approved,
            "duration_ms": duration_ms,
            "abort_reason": abort_reason,
            "loop_interval_sec": loop_interval_sec,
            "node_summary": node_summary or {},
        })


def _get_last_run_age_sec() -> float:
    """Seconds since the last successful run."""
    with _state_lock:
        rt = _last_run.get("run_time")
    if not rt:
        return (datetime.now(timezone.utc) - _startup_time).total_seconds()
    try:
        last = datetime.fromisoformat(rt)
    except Exception:
        return 0.0
    return (datetime.now(timezone.utc) - last).total_seconds()


def _build_app() -> Optional["FastAPI"]:
    """Build the FastAPI app (returns None if fastapi/uvicorn missing)."""
    if FastAPI is None:
        print("[HealthServer] fastapi/uvicorn not installed; health endpoint disabled.")
        return None

    app = FastAPI(title="VLTHR Pipeline Health")

    @app.get("/health")
    def health():
        age = _get_last_run_age_sec()
        interval = _last_run.get("loop_interval_sec", 300)
        # Degrade to 503 if last run is older than 2× interval
        if age > interval * 2:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=503,
                content={
                    "status": "stale",
                    "last_run_age_sec": round(age, 1),
                    "loop_interval_sec": interval,
                    "message": "No pipeline run detected within expected window",
                },
            )
        return {
            "status": "alive",
            "last_run_age_sec": round(age, 1),
            "loop_interval_sec": interval,
            "startup_time": _startup_time.isoformat(),
        }

    @app.get("/last-run")
    def last_run():
        with _state_lock:
            payload = dict(_last_run)
        payload["last_run_age_sec"] = round(_get_last_run_age_sec(), 1)
        return payload

    @app.get("/metrics")
    def metrics():
        """Prometheus-compatible text format."""
        age = _get_last_run_age_sec()
        approved = _last_run.get("signals_approved", 0)
        lines = [
            "# HELP pipeline_last_run_age_seconds Seconds since last pipeline run",
            "# TYPE pipeline_last_run_age_seconds gauge",
            f"pipeline_last_run_age_seconds {age:.1f}",
            "",
            "# HELP pipeline_signals_approved Number of signals approved in last run",
            "# TYPE pipeline_signals_approved gauge",
            f"pipeline_signals_approved {approved}",
            "",
            "# HELP pipeline_up 1 if pipeline is considered healthy",
            "# TYPE pipeline_up gauge",
            f"pipeline_up {0 if age > (_last_run.get('loop_interval_sec', 300) * 2) else 1}",
        ]
        return {"content": "\n".join(lines)}

    return app


def start_health_server(port: int = 8200, host: str = "0.0.0.0") -> Optional[Any]:
    """Start the embedded health server in a background thread."""
    app = _build_app()
    if app is None:
        return None

    import threading

    def _run():
        config = Config(app=app, host=host, port=port, log_level="warning", access_log=False)
        server = Server(config)
        server.run()

    t = threading.Thread(target=_run, daemon=True, name="vlthr-health-server")
    t.start()
    print(f"[HealthServer] Started on http://{host}:{port}")
    return t


if __name__ == "__main__":
    # Standalone test
    start_health_server()
    update_last_run("OK", signals_scanned=6, signals_approved=2, duration_ms=1200)
    import time
    while True:
        time.sleep(10)
