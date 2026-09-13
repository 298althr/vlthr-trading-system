"""
VLTHR Scheduler Monitor
=======================
Watches scheduler logs for errors, proxy bans, rate limits.
Sends Telegram alerts on issues.
Sends hourly data quality reports.
Runs as a background process inside the data-ingestion container.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests

# Ensure we can import sibling modules
sys.path.insert(0, str(Path(__file__).resolve().parent))

from telegram_alerts import (
    alert_error,
    alert_proxy_banned,
    alert_rate_limit,
    alert_data_quality,
    send_telegram,
)

# ── CONFIG ────────────────────────────────────────────────────────────────
SCHEDULER_LOG_DIR = Path("/tmp")  # Where run_ingestion.sh writes logs
DATA_ROOT = Path(os.getenv("DATA_ROOT", "/engine/data"))
CHECK_INTERVAL = 30  # seconds between log scans
HOURLY_REPORT_INTERVAL = 3600  # seconds between data quality reports

# Regex patterns for error detection in logs
# NOTE: These are matched against ANSI-stripped log lines.
# Use word boundaries (\b) to avoid matching substrings inside numbers/stats.
ERROR_PATTERNS = [
    (re.compile(r"\b403\b|CloudFront|blocked|access denied", re.I), "proxy_ban"),
    (re.compile(r"\b429\b|rate.?limit|too.?many.?requests", re.I), "rate_limit"),
    (re.compile(r"\b5\d\d\b|server.?error|internal.?error", re.I), "server_error"),
    (re.compile(r"connection.?refused|connection.?reset|timeout|timed out", re.I), "connection_error"),
    # Only match actual [ERR] tags or standalone 'error' word — avoid 'errors=0' in health stats
    (re.compile(r"\[ERR\]|\bfailed\b|\bfailure\b|\berror\b", re.I), "generic_error"),
]

# Last-seen timestamps per log file to avoid duplicate alerts
_last_alert_times: Dict[str, float] = {}
_last_hourly_report = 0.0


# ── LOG WATCHER ───────────────────────────────────────────────────────────

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)

def _read_log_tail(log_path: Path, lines: int = 100) -> str:
    if not log_path.exists():
        return ""
    try:
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            all_lines = f.readlines()
            return "".join(all_lines[-lines:])
    except Exception:
        return ""


def _check_log_for_issues(log_path: Path, service_name: str) -> List[Tuple[str, str]]:
    """
    Scan the tail of a log file for known error patterns.
    Returns list of (issue_type, matched_text).
    """
    raw_content = _read_log_tail(log_path, lines=50)
    if not raw_content:
        return []

    # Strip ANSI so [92m[OK]\x1b[0m doesn't trigger false positives
    content = _strip_ansi(raw_content)

    issues: List[Tuple[str, str]] = []
    for pattern, issue_type in ERROR_PATTERNS:
        for m in pattern.finditer(content):
            # Extract surrounding context from the CLEAN (ANSI-stripped) text
            start = max(0, m.start() - 80)
            end = min(len(content), m.end() + 80)
            context = content[start:end].strip().replace("\n", " ")
            issues.append((issue_type, context))

    return issues


def _should_alert(service_name: str, issue_type: str, cooldown: int = 300) -> bool:
    """Throttle repeated alerts for the same service+issue."""
    key = f"{service_name}:{issue_type}"
    now = time.time()
    last = _last_alert_times.get(key, 0)
    if now - last < cooldown:
        return False
    _last_alert_times[key] = now
    return True


def _check_scheduler_logs() -> None:
    """Scan data_scheduler.log and minute_scheduler.log for issues."""
    logs = {
        "data_scheduler": SCHEDULER_LOG_DIR / "data_scheduler.log",
        "minute_scheduler": SCHEDULER_LOG_DIR / "minute_scheduler.log",
    }

    for service_name, log_path in logs.items():
        issues = _check_log_for_issues(log_path, service_name)
        for issue_type, context in issues:
            if not _should_alert(service_name, issue_type):
                continue

            if issue_type == "proxy_ban":
                alert_proxy_banned(
                    proxy_url=os.getenv("PROXY_URL", "unknown"),
                    endpoint="Bybit API",
                    status_code=403,
                    response_body=context,
                )
            elif issue_type == "rate_limit":
                alert_rate_limit(source=service_name)
            else:
                alert_error(
                    source=service_name,
                    error_msg=issue_type.replace("_", " ").title(),
                    details=context,
                    chat_type="data",
                )


# ── DATA QUALITY CHECKER ────────────────────────────────────────────────

def _check_parquet_quality(symbol: str, tf: str) -> Tuple[bool, str]:
    """
    Check if parquet data exists and is not stale for a symbol+timeframe.
    Returns (ok, message).
    """
    import pandas as pd

    now = datetime.now(timezone.utc)
    base_path = DATA_ROOT / "bybit" / symbol / tf

    # Find latest year/month parquet
    if not base_path.exists():
        return False, f"NO DATA — directory missing"

    # Look for year/month parquet structure
    parquets = sorted(base_path.rglob("*.parquet"))
    if not parquets:
        return False, f"NO DATA — no parquet files"

    latest_file = parquets[-1]
    try:
        df = pd.read_parquet(latest_file)
        if df.empty:
            return False, f"EMPTY parquet"

        # Assume 'timestamp' or 'open_time' column
        ts_col = None
        for col in ["timestamp", "open_time", "datetime", "time"]:
            if col in df.columns:
                ts_col = col
                break

        if ts_col is None:
            return False, f"NO timestamp column"

        last_ts = pd.to_datetime(df[ts_col].max())
        if last_ts.tzinfo is None:
            last_ts = last_ts.replace(tzinfo=timezone.utc)

        # Stale threshold depends on timeframe
        stale_mins = {"1m": 5, "5m": 15, "15m": 30, "30m": 60, "1h": 120, "4h": 480}
        threshold = stale_mins.get(tf, 30)
        age_mins = (now - last_ts).total_seconds() / 60

        if age_mins > threshold:
            return False, f"STALE — last candle {age_mins:.0f}m ago"

        row_count = len(df)
        return True, f"OK — {row_count} rows, last {age_mins:.0f}m ago"

    except Exception as e:
        return False, f"READ ERROR — {str(e)[:80]}"


def _build_data_quality_report() -> List[str]:
    """Build hourly data quality report for all symbols and timeframes."""
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]
    timeframes = ["1m", "5m", "15m", "30m", "1h", "4h"]

    lines = []
    for sym in symbols:
        sym_ok = True
        sym_lines = [f"{sym}:"]
        for tf in timeframes:
            ok, msg = _check_parquet_quality(sym, tf)
            status = "✅" if ok else "❌"
            sym_lines.append(f"  {tf:>4} {status} {msg}")
            if not ok:
                sym_ok = False
        lines.append(f"{'✅' if sym_ok else '❌'} {sym}")
        lines.extend(sym_lines[1:])

    return lines


def _send_hourly_report() -> None:
    """Send the hourly data quality report via Telegram."""
    global _last_hourly_report
    now = time.time()
    if now - _last_hourly_report < HOURLY_REPORT_INTERVAL:
        return
    _last_hourly_report = now

    try:
        report = _build_data_quality_report()
        alert_data_quality(report, chat_type="data")
    except Exception as e:
        alert_error(
            source="scheduler_monitor",
            error_msg="Failed to build hourly report",
            details=str(e),
            chat_type="devops",
        )


# ── MAIN LOOP ─────────────────────────────────────────────────────────────

def main() -> None:
    print("[SchedulerMonitor] Starting monitor loop...")
    print(f"[SchedulerMonitor] Log dir: {SCHEDULER_LOG_DIR}")
    print(f"[SchedulerMonitor] Data root: {DATA_ROOT}")
    print(f"[SchedulerMonitor] Telegram alerts: {'ENABLED' if os.getenv('ENABLE_TELEGRAM_ALERTS') == 'true' else 'DISABLED'}")

    # Send startup alert
    send_telegram(
        "🟢 <b>VLTHR Scheduler Monitor</b> started\nWatching data_scheduler and minute_scheduler.",
        chat_type="data",
        parse_mode="HTML",
        silent=True,
    )

    while True:
        try:
            _check_scheduler_logs()
            _send_hourly_report()
        except Exception as e:
            print(f"[SchedulerMonitor] Exception in main loop: {e}")
            traceback.print_exc()

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
