FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip wheel setuptools && \
    pip install requests psycopg2-binary python-dotenv urllib3

COPY . .

CMD ["python", "-u", "telegram_polling_service.py"]
