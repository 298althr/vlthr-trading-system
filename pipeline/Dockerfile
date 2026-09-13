# VLTHR Pipeline Daemon
# Runs the portfolio signal engine in continuous loop mode with embedded health server.
FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for layer caching
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Ensure core deps are present even if requirements.txt is minimal
RUN pip install --no-cache-dir \
    psycopg2-binary \
    pandas \
    numpy \
    pyarrow \
    fastapi \
    uvicorn \
    prefect \
    tenacity \
    structlog \
    pydantic \
    river \
    scikit-learn \
    redis \
    sqlalchemy

# Copy engine code
COPY engine/ /app/engine/

# Copy data root access (mounted at runtime)
VOLUME ["/app/data", "/app/.env", "/app/logs"]

# Health port
EXPOSE 8200

ENV PYTHONUNBUFFERED=1
ENV PIPELINE_HEALTH_PORT=8200

# Default: continuous 5-minute loop
CMD ["python", "-m", "engine.run_pipeline", "--loop", "--interval", "300"]
