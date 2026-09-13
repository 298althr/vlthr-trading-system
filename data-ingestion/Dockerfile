# VLTHR Data Ingestion — runs data_scheduler + minute_scheduler side by side
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install runtime dependencies (procps provides pgrep for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev procps \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages used by data workers
RUN pip install --upgrade pip wheel setuptools && \
    pip install requests pandas pyarrow numpy python-dotenv urllib3 psycopg2-binary

# Workers and data are mounted as volumes at runtime:
#   ./workers → /app/workers (ro)
#   ./data    → /app/data (rw)
# Entrypoint is set in docker-compose.yml to /app/workers/run_ingestion.sh
