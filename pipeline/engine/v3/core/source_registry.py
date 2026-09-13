"""
V3 Source Registry
===================
Registers all data sources with metadata in the source_registry DB table.
Provides lookup functions for source confidence and reliability.

Sources registered:
    1. bybit_ohlcv         — Bybit V5 kline API (OHLCV candles)
    2. bybit_funding       — Bybit V5 funding rate API
    3. bybit_open_interest — Bybit V5 open interest API
    4. bybit_orderbook     — Bybit V5 orderbook API
    5. bybit_ls_ratio      — Bybit V5 account ratio (long/short sentiment)
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

# Ensure db module is importable
_engine = Path(__file__).resolve().parents[2]
_v3_root = Path(__file__).resolve().parents[1]
try:
    _repo = _engine.parents[2]
except IndexError:
    _repo = _engine.parent

# Load .env
_env = _repo / ".env"
if _env.exists():
    with open(_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)

sys.path.insert(0, str(_engine))

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]
TIMEFRAMES = ["4h", "1h", "30m", "15m", "5m"]

# Source definitions
SOURCES = [
    {
        "source_name": "bybit_ohlcv",
        "source_type": "market_data",
        "provider": "bybit",
        "endpoint": "https://api.bybit.com/v5/market/kline",
        "symbols": SYMBOLS,
        "timeframes": TIMEFRAMES,
        "update_frequency": "5m",
        "latency_ms": 200,
        "reliability_score": 0.95,
        "cost_per_request": 0.0,
        "api_key_required": False,
        "rate_limit_per_min": 600,
        "metadata": {"category": "price", "format": "parquet", "storage": "data/bybit/{SYMBOL}/{TF}/"},
        "lifecycle_state": "active",
    },
    {
        "source_name": "bybit_funding",
        "source_type": "market_data",
        "provider": "bybit",
        "endpoint": "https://api.bybit.com/v5/market/funding/history",
        "symbols": SYMBOLS,
        "timeframes": ["8h"],
        "update_frequency": "8h",
        "latency_ms": 200,
        "reliability_score": 0.95,
        "cost_per_request": 0.0,
        "api_key_required": False,
        "rate_limit_per_min": 600,
        "metadata": {"category": "funding", "format": "parquet", "storage": "data/bybit/{SYMBOL}/funding/"},
        "lifecycle_state": "active",
    },
    {
        "source_name": "bybit_open_interest",
        "source_type": "market_data",
        "provider": "bybit",
        "endpoint": "https://api.bybit.com/v5/market/open-interest",
        "symbols": SYMBOLS,
        "timeframes": ["1h"],
        "update_frequency": "1h",
        "latency_ms": 200,
        "reliability_score": 0.90,
        "cost_per_request": 0.0,
        "api_key_required": False,
        "rate_limit_per_min": 600,
        "metadata": {"category": "open_interest", "format": "parquet", "storage": "data/bybit/{SYMBOL}/oi/"},
        "lifecycle_state": "active",
    },
    {
        "source_name": "bybit_orderbook",
        "source_type": "market_data",
        "provider": "bybit",
        "endpoint": "https://api.bybit.com/v5/market/orderbook",
        "symbols": SYMBOLS,
        "timeframes": ["snapshot"],
        "update_frequency": "snapshot",
        "latency_ms": 100,
        "reliability_score": 0.85,
        "cost_per_request": 0.0,
        "api_key_required": False,
        "rate_limit_per_min": 600,
        "metadata": {"category": "orderbook", "format": "parquet", "storage": "data/bybit/{SYMBOL}/orderbook/", "depth": 50},
        "lifecycle_state": "active",
    },
    {
        "source_name": "bybit_ls_ratio",
        "source_type": "sentiment",
        "provider": "bybit",
        "endpoint": "https://api.bybit.com/v5/market/account-ratio",
        "symbols": SYMBOLS,
        "timeframes": ["1h"],
        "update_frequency": "1h",
        "latency_ms": 200,
        "reliability_score": 0.80,
        "cost_per_request": 0.0,
        "api_key_required": False,
        "rate_limit_per_min": 600,
        "metadata": {"category": "sentiment", "format": "parquet", "storage": "data/bybit/{SYMBOL}/ls_ratio/"},
        "lifecycle_state": "active",
    },
]


def register_sources():
    """Insert all sources into the source_registry table (idempotent upsert)."""
    import psycopg2
    from psycopg2.extras import Json

    db_url = os.environ.get("DB_URL")
    if not db_url:
        print("[source_registry] DB_URL not set, skipping registration")
        return

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    for src in SOURCES:
        cur.execute("""
            INSERT INTO source_registry
                (source_name, source_type, provider, endpoint, symbols, timeframes,
                 update_frequency, latency_ms, reliability_score, cost_per_request,
                 api_key_required, rate_limit_per_min, metadata, lifecycle_state)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_name) DO UPDATE SET
                source_type = EXCLUDED.source_type,
                provider = EXCLUDED.provider,
                endpoint = EXCLUDED.endpoint,
                symbols = EXCLUDED.symbols,
                timeframes = EXCLUDED.timeframes,
                update_frequency = EXCLUDED.update_frequency,
                latency_ms = EXCLUDED.latency_ms,
                reliability_score = EXCLUDED.reliability_score,
                cost_per_request = EXCLUDED.cost_per_request,
                api_key_required = EXCLUDED.api_key_required,
                rate_limit_per_min = EXCLUDED.rate_limit_per_min,
                metadata = EXCLUDED.metadata,
                lifecycle_state = EXCLUDED.lifecycle_state,
                updated_at = NOW()
        """, (
            src["source_name"], src["source_type"], src["provider"], src["endpoint"],
            src["symbols"], src["timeframes"], src["update_frequency"],
            src["latency_ms"], src["reliability_score"], src["cost_per_request"],
            src["api_key_required"], src["rate_limit_per_min"],
            Json(src["metadata"]),
            src["lifecycle_state"],
        ))

    conn.commit()
    cur.close()
    conn.close()
    print(f"[source_registry] Registered {len(SOURCES)} sources")


def get_source_confidence(source_name: str) -> float:
    """Get the current reliability score for a source."""
    import psycopg2

    db_url = os.environ.get("DB_URL")
    if not db_url:
        return 1.0  # Default full confidence if no DB

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("""
        SELECT reliability_score FROM source_registry
        WHERE source_name = %s AND lifecycle_state = 'active'
    """, (source_name,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return float(row[0]) if row else 1.0


def get_all_sources() -> list:
    """Get all active sources from the registry."""
    import psycopg2

    db_url = os.environ.get("DB_URL")
    if not db_url:
        return SOURCES

    conn = psycopg2.connect(db_url)
    cur = conn.cursor()
    cur.execute("""
        SELECT source_name, source_type, provider, reliability_score, lifecycle_state
        FROM source_registry
        WHERE lifecycle_state = 'active'
        ORDER BY source_name
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [{"source_name": r[0], "source_type": r[1], "provider": r[2],
             "reliability_score": float(r[3]), "lifecycle_state": r[4]} for r in rows]


if __name__ == "__main__":
    register_sources()
    sources = get_all_sources()
    for s in sources:
        print(f"  {s['source_name']:30s}  reliability={s['reliability_score']:.2f}  state={s['lifecycle_state']}")
