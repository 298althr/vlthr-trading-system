# VLTHR Setup Guide

## Prerequisites

1. **Docker** 24+ and **Docker Compose** v2+
2. **Linux host** (tested on Ubuntu 22.04+)
3. **UFW** configured for Docker bridge networking (see below)
4. **Data** — Parquet OHLCV data included in `./data/bybit/`

## Step-by-Step Setup

### 1. Navigate to the DEVOPS folder
```bash
cd /path/to/DEVOPS
```

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env with your actual secrets:
# - POSTGRES_PASSWORD
# - Telegram bot tokens (3 bots)
# - NGROK_AUTHTOKEN and NGROK_DOMAIN
# - BYBIT API keys
# - External API keys (Deriv, TwelveData, etc.)
```

### 3. UFW Docker networking fix
If UFW is active, Docker's bridge networks get blocked by default. Fix:
```bash
# Find the Docker bridge ID for vlthr-dashboard-net
docker network inspect vlthr-dashboard-net --format '{{.Options}}' 2>/dev/null
# Or check: ip addr | grep br-

# Add rules to /etc/ufw/after.rules (replace br-XXXX with your bridge):
sudo tee -a /etc/ufw/after.rules << 'EOF'
*filter
:DOCKER-USER - [0:0]
-A DOCKER-USER -i br-a3d0f5808fba -j ACCEPT
-A DOCKER-USER -o br-a3d0f5808fba -j ACCEPT
COMMIT
EOF

sudo ufw reload
```

### 4. Build and start
```bash
docker compose up -d --build
```

### 5. Verify all services are healthy
```bash
docker compose ps
# All services should show "Up" or "Up (healthy)"
```

### 6. Check logs for errors
```bash
docker compose logs --tail=50 pipeline
docker compose logs --tail=50 backend
docker compose logs --tail=50 data-ingestion
```

### 7. Verify database schema
```bash
docker exec vlthr-postgres psql -U postgres -d postgres -c "
  SELECT table_name FROM information_schema.tables 
  WHERE table_schema='public' AND table_name IN ('paper_trades','high_confidence_signals','signal_state','risk_ledger')
  ORDER BY table_name;"
```

### 8. Access the dashboard
- **Local**: http://localhost:5174
  - The Google TOTP authentication layer has been removed; the dashboard opens directly.
- **Public (ngrok)**: https://your-domain.ngrok-free.dev (only if ngrok service is running)
- **ngrok inspector**: http://localhost:4040 (only if ngrok service is running)

Note: `ngrok` and `telegram` services are currently stopped by user request. Start them with `docker compose up -d ngrok telegram` if needed.

## Troubleshooting

### Container won't stop (permission denied / AppArmor)
On this host, `docker stop`/`docker kill` can fail with `permission denied` due to an AppArmor/Docker interaction. Use this workaround:

```bash
# 1. Disable auto-restart so Docker doesn't recreate the container
sudo docker update --restart=no <container>

# 2. Find the container PID and force-kill the process
PID=$(sudo docker inspect <container> --format '{{.State.Pid}}')
sudo kill -9 $PID

# 3. Remove the stopped container
sudo docker rm <container>
```

Then recreate the container with `docker compose up -d <service>`.

### Database connection timeout
Check UFW rules and Docker bridge networking (Step 3).

### Pipeline can't pull base image
Network issue — just restart from cached image:
```bash
docker compose up -d pipeline  # without --build
```

### No trades being taken
1. Check dashboard backend is running: `docker compose ps backend`
2. Check pipeline logs: `docker compose logs --tail=100 pipeline`
3. Verify data ingestion: `docker compose logs --tail=20 data-ingestion`
4. Check for circuit breaker: `docker exec vlthr-postgres psql -U postgres -d postgres -c "SELECT * FROM error_log WHERE created_at > NOW() - INTERVAL '3 hours' ORDER BY created_at DESC LIMIT 10;"`
