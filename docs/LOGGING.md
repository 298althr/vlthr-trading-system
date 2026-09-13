# VLTHR Logging Guide

## Log Sources

### 1. Docker Logs (stdout/stderr)
Each service writes to stdout/stderr, captured by Docker:

```bash
# Follow a specific service
docker compose logs -f pipeline

# Last N lines
docker compose logs --tail=100 backend

# All services
docker compose logs --tail=50

# Since a time
docker compose logs --since=1h pipeline
```

### 2. File Logs (mounted volumes)
Pipeline logs are written to `./logs/pipeline/` via the volume mount.

### 3. Database Logs
Structured logs stored in PostgreSQL for querying:

```sql
-- Recent pipeline errors
SELECT * FROM error_log 
WHERE created_at > NOW() - INTERVAL '3 hours' 
ORDER BY created_at DESC LIMIT 10;

-- Pipeline trace (last run)
SELECT node, status, duration_ms, detail 
FROM pipeline_trace 
WHERE run_time > NOW() - INTERVAL '1 hour' 
ORDER BY run_time, seq;

-- Ingestion log
SELECT symbol, timeframe, rows_added, gaps, completed_at 
FROM ingestion_log 
ORDER BY completed_at DESC LIMIT 10;

-- Signal audit trail
SELECT * FROM signal_audit_log 
WHERE created_at > NOW() - INTERVAL '1 hour' 
ORDER BY created_at DESC LIMIT 20;
```

## Log Levels

| Level | Usage |
|---|---|
| INFO | Normal operations (scan results, trade promotions, snapshots) |
| WARN | Non-critical issues (V2 filter rejections, PENDING expiries) |
| ERROR | Failures requiring attention (DB errors, ingestion failures) |
| CRITICAL | System-halting issues (drawdown halt, circuit breaker) |

## Log Rotation

Docker logs are managed by Docker's built-in log driver (json-file by default).
Configure rotation in `/etc/docker/daemon.json`:

```json
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "5"
  }
}
```

## Telegram Alerts

The system sends Telegram alerts for:
- **Signal alerts**: EXCELLENT signals (DQS >= 75)
- **Execution alerts**: Trade opened, trade closed (SL/TP/TIME)
- **Error alerts**: Critical errors, ingestion failures
- **Hourly reports**: Data ingestion status via `scheduler_monitor.py`

## Monitoring Checklist

Daily check:
1. `docker compose ps` — all services up
2. `docker compose logs --tail=20 pipeline` — no errors
3. `docker compose logs --tail=20 data-ingestion` — ingestion running
4. `docker compose logs --tail=20 backend` — trade monitor active
5. Database query for recent errors
6. Check open trades and account balance
