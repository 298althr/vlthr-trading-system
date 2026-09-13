#!/bin/bash
# VLTHR Data Ingestion — runs data_scheduler + minute_scheduler side by side
# Both write to the same local parquet store via mounted volume.

set -e

# Log with timestamp
log() {
  echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $1"
}

log "Starting VLTHR Data Ingestion Service"
log "  Working dir: $(pwd)"
log "  Data root:   ${DATA_ROOT:-/app/data}"
log "  Python:      $(python3 --version)"

# Verify .env is readable (Python scripts load it themselves via python-dotenv)
if [ -f "/app/.env" ]; then
  log ".env found at /app/.env (Python will load it)"
else
  log "[WARN] No .env file found — relying on injected env vars"
fi

# Verify data directory is writable
TEST_FILE="${DATA_ROOT:-/app/data}/.write_test"
if touch "$TEST_FILE" 2>/dev/null; then
  rm -f "$TEST_FILE"
  log "Data directory is WRITABLE"
else
  log "[ERROR] Data directory is NOT writable: ${DATA_ROOT:-/app/data}"
  log "[ERROR] Ensure the volume mount does NOT use :ro"
  exit 1
fi

# Function to handle child process exit
shutdown() {
  log "Shutdown signal received — stopping all processes..."
  if [ -n "$DATA_PID" ]; then kill "$DATA_PID" 2>/dev/null || true; fi
  if [ -n "$MINUTE_PID" ]; then kill "$MINUTE_PID" 2>/dev/null || true; fi
  if [ -n "$MONITOR_PID" ]; then kill "$MONITOR_PID" 2>/dev/null || true; fi
  wait
  log "All processes stopped."
  exit 0
}

trap shutdown SIGTERM SIGINT

# Start data_scheduler (5m/15m/30m/1h/4h + funding/OI/orderbook/enrich)
# Use 'tee' so output is visible in 'docker logs' AND written to /tmp for the monitor.
# -u = unbuffered Python stdout so logs appear in real time.
log "Launching data_scheduler.py..."
python3 -u workers/data_scheduler.py 2>&1 | tee /tmp/data_scheduler.log &
DATA_PID=$!
log "  data_scheduler PID=$DATA_PID"

# Give data_scheduler a moment to initialize
sleep 2

# Start minute_scheduler (1m for all symbols)
log "Launching minute_scheduler.py..."
python3 -u workers/minute_scheduler.py 2>&1 | tee /tmp/minute_scheduler.log &
MINUTE_PID=$!
log "  minute_scheduler PID=$MINUTE_PID"

# Start scheduler_monitor (watches logs, sends Telegram alerts, hourly reports)
log "Launching scheduler_monitor.py..."
python3 -u workers/scheduler_monitor.py 2>&1 | tee /tmp/scheduler_monitor.log &
MONITOR_PID=$!
log "  scheduler_monitor PID=$MONITOR_PID"

log "All processes running. Waiting for child processes..."

# Wait for any process to exit
wait -n
EXIT_CODE=$?

log "[WARN] A process exited with code $EXIT_CODE — shutting down others..."
if [ -n "$DATA_PID" ]; then kill "$DATA_PID" 2>/dev/null || true; fi
if [ -n "$MINUTE_PID" ]; then kill "$MINUTE_PID" 2>/dev/null || true; fi
if [ -n "$MONITOR_PID" ]; then kill "$MONITOR_PID" 2>/dev/null || true; fi
wait

exit $EXIT_CODE
