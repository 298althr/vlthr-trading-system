FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install build deps for pyarrow / numpy wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip first, then install deps
RUN pip install --upgrade pip wheel setuptools
COPY requirements.txt .
RUN pip install --prefer-binary -r requirements.txt

# Copy application code
COPY . .

# Ensure data directory exists (will be overridden by volume mount)
RUN mkdir -p /app/data /app/logs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
