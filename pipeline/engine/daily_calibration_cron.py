"""
Daily Calibration Cron
======================
Runs calibration_engine.run_calibration() once per day at 00:00 UTC.
Designed to run as a long-lived process alongside the pipeline.

Usage:
  python daily_calibration_cron.py

Or as a Windows scheduled task:
  schtasks /create /tn "VLTHR_Calibration" /tr "py daily_calibration_cron.py" /sc daily /st 00:00
"""

import time
import logging
from datetime import datetime, timezone, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CalibrationCron] %(message)s",
)
log = logging.getLogger(__name__)

CALIBRATION_HOUR_UTC = 0
CHECK_INTERVAL_SECONDS = 3600


def _run_calibration():
    try:
        from calibration_engine import run_calibration
        run_calibration()
        log.info("Calibration completed successfully")
    except Exception as e:
        log.error(f"Calibration failed: {e}")


def _seconds_until_next_run() -> float:
    now = datetime.now(timezone.utc)
    next_run = now.replace(hour=CALIBRATION_HOUR_UTC, minute=0, second=0, microsecond=0)
    if next_run <= now:
        next_run += timedelta(days=1)
    return (next_run - now).total_seconds()


def main():
    log.info("Daily calibration cron started (runs at 00:00 UTC daily)")
    _run_calibration()

    while True:
        wait_seconds = _seconds_until_next_run()
        log.info(f"Next calibration in {wait_seconds / 3600:.1f} hours")
        time.sleep(min(wait_seconds, CHECK_INTERVAL_SECONDS))

        if _seconds_until_next_run() < CHECK_INTERVAL_SECONDS:
            _run_calibration()


if __name__ == "__main__":
    main()
