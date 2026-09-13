"""
V3 Evidence Store
==================
Loads enriched data from pre-computed parquet, attaches source confidence
scores from the source_registry, and writes decision lineage events to the
decision_lineage DB table.

This is the V3 replacement for confidence_engine.load_enriched — it wraps
the same data loading but adds:
    - Source confidence metadata (from source_registry)
    - Decision lineage tracking (to decision_lineage table)
    - Evidence snapshot for later replay/analysis
"""
import os
import sys
import json
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

# Ensure imports resolve
_engine = Path(__file__).resolve().parents[2]
_v3_root = Path(__file__).resolve().parents[1]
try:
    _repo = _engine.parents[2]
except IndexError:
    _repo = _engine.parent

sys.path.insert(0, str(_engine))
sys.path.insert(0, str(_repo / "departments" / "strategy_research" / "BACKTESTER" / "strategy" / "signals"))

# Import V2 load_enriched as fallback
from confidence_engine import load_enriched as _v2_load_enriched

# Import source registry for confidence lookup
sys.path.insert(0, str(_v3_root / "core"))
from source_registry import get_source_confidence


def load_evidence(symbol: str, timeframe: str = "15m") -> Optional[dict]:
    """
    Load enriched data for a symbol/timeframe and attach source confidence.

    Returns:
        dict with keys:
            - df: enriched DataFrame (indicators + session + 4h context)
            - sources: dict of source_name -> confidence_score
            - timestamp: when this evidence was loaded
            - lineage_id: UUID for decision lineage tracking
    """
    # Load enriched data using V2 path (which now uses pre-computed parquet)
    df = _v2_load_enriched(symbol, timeframe)
    if df is None or len(df) == 0:
        return None

    # Attach source confidence
    sources = {
        "bybit_ohlcv": get_source_confidence("bybit_ohlcv"),
        "bybit_funding": get_source_confidence("bybit_funding"),
        "bybit_open_interest": get_source_confidence("bybit_open_interest"),
        "bybit_orderbook": get_source_confidence("bybit_orderbook"),
        "bybit_ls_ratio": get_source_confidence("bybit_ls_ratio"),
    }

    # Check which sources actually contributed data
    if "funding_rate" not in df.columns or df["funding_rate"].isna().all():
        sources["bybit_funding"] = 0.0
    if "open_interest" not in df.columns or df["open_interest"].isna().all():
        sources["bybit_open_interest"] = 0.0

    lineage_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    return {
        "df": df,
        "sources": sources,
        "timestamp": now,
        "lineage_id": lineage_id,
        "symbol": symbol,
        "timeframe": timeframe,
    }


def write_lineage(event_type: str, payload: dict, parent_id: str = None) -> str:
    """
    Write a decision lineage event to the decision_lineage table.

    Args:
        event_type: One of market_event, signal, feature, prediction, decision, execution, outcome
        payload: Event data (will be stored as JSONB)
        parent_id: Parent event UUID (for chaining)

    Returns:
        The UUID of the inserted event
    """
    import psycopg2

    db_url = os.environ.get("DB_URL")
    if not db_url:
        return None

    event_id = str(uuid.uuid4())
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO decision_lineage (id, event_type, parent_id, payload, timestamp)
            VALUES (%s, %s, %s, %s, NOW())
        """, (
            event_id,
            event_type,
            parent_id,
            json.dumps(payload, default=str),
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[evidence_store] Lineage write failed: {e}")
        return None

    return event_id


def create_evidence_snapshot(evidence: dict) -> dict:
    """
    Create a serializable snapshot of the evidence for lineage tracking.
    Excludes the DataFrame itself (too large for JSONB).
    """
    df = evidence["df"]
    last_bar = df.iloc[-1].to_dict() if len(df) > 0 else {}

    # Convert numpy types to native Python
    for k, v in last_bar.items():
        if hasattr(v, "item"):
            last_bar[k] = v.item()
        elif pd.isna(v):
            last_bar[k] = None

    return {
        "symbol": evidence["symbol"],
        "timeframe": evidence["timeframe"],
        "timestamp": evidence["timestamp"].isoformat(),
        "lineage_id": evidence["lineage_id"],
        "sources": evidence["sources"],
        "bar_count": len(df),
        "last_bar": last_bar,
    }


if __name__ == "__main__":
    # Quick test
    for sym in ["BTCUSDT", "ETHUSDT"]:
        ev = load_evidence(sym, "15m")
        if ev:
            snap = create_evidence_snapshot(ev)
            print(f"  {sym}: {ev['bar_count']} bars, sources={ev['sources']}")
            lineage_id = write_lineage("market_event", snap)
            print(f"    lineage: {lineage_id}")
        else:
            print(f"  {sym}: no evidence loaded")
