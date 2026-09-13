"""P4 — DQS Calibration Layer.

Maps DQS score buckets to realized win-rate by analyzing closed paper trades.
This closes the feedback loop between predicted confidence and actual outcomes.
"""
from __future__ import annotations
import os
from typing import List, Dict, Any

try:
    import psycopg2
    _HAS_PSYCOPG2 = True
except ImportError:
    _HAS_PSYCOPG2 = False


def _get_connection():
    """Return a psycopg2 connection using env vars or DB_URL."""
    if not _HAS_PSYCOPG2:
        raise ImportError("psycopg2 is required for DQS calibration")
    db_url = os.environ.get("DB_URL", "")
    if db_url and db_url.startswith("postgresql://"):
        return psycopg2.connect(db_url)
    return psycopg2.connect(
        host=os.environ.get("SUPABASE_HOST", os.environ.get("PGHOST", "")),
        port=os.environ.get("SUPABASE_PORT", os.environ.get("PGPORT", "5432")),
        dbname=os.environ.get("SUPABASE_DBNAME", os.environ.get("PGDATABASE", "postgres")),
        user=os.environ.get("SUPABASE_USER", os.environ.get("PGUSER", "postgres")),
        password=os.environ.get("SUPABASE_PASSWORD", os.environ.get("PGPASSWORD", "")),
    )


def calibrate_dqs() -> Dict[str, Any]:
    """Analyze closed paper trades and compute realized win-rate per DQS bucket.

    Returns
    -------
    dict with keys:
        - total_closed: int
        - overall_win_rate: float
        - buckets: list[dict]
        - recommendation: str
    """
    if not _HAS_PSYCOPG2:
        return {
            "total_closed": 0,
            "overall_win_rate": 0.0,
            "buckets": [],
            "recommendation": "psycopg2 not installed; cannot query database.",
        }

    conn = _get_connection()
    cur = conn.cursor()

    # Pull closed trades with their originating signal confidence (DQS proxy)
    cur.execute("""
        SELECT
            pt.confidence,
            pt.exit_reason,
            pt.net_pnl_usd,
            pt.net_pnl_pct
        FROM paper_trades pt
        WHERE pt.status = 'CLOSED'
          AND pt.confidence IS NOT NULL
        ORDER BY pt.confidence
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        return {
            "total_closed": 0,
            "overall_win_rate": 0.0,
            "buckets": [],
            "recommendation": "No closed trades with confidence yet. Run more paper trades to build calibration.",
        }

    # Bucket every 5 DQS points
    buckets: Dict[int, Dict[str, Any]] = {}
    for conf, exit_reason, net_pnl_usd, net_pnl_pct in rows:
        conf = int(conf) if conf else 0
        bucket = (conf // 5) * 5  # 50, 55, 60, ...
        if bucket not in buckets:
            buckets[bucket] = {"count": 0, "wins": 0, "total_pnl": 0.0, "avg_pnl_pct": 0.0}
        buckets[bucket]["count"] += 1
        is_win = exit_reason == "TP_HIT" or (net_pnl_usd is not None and net_pnl_usd > 0)
        if is_win:
            buckets[bucket]["wins"] += 1
        if net_pnl_usd:
            buckets[bucket]["total_pnl"] += float(net_pnl_usd)
        if net_pnl_pct:
            buckets[bucket]["avg_pnl_pct"] += float(net_pnl_pct)

    # Compute win rates and averages
    total_closed = len(rows)
    total_wins = sum(1 for _, er, npu, _ in rows if er == "TP_HIT" or (npu is not None and npu > 0))
    overall_win_rate = round((total_wins / total_closed) * 100, 2) if total_closed > 0 else 0.0

    bucket_list: List[Dict[str, Any]] = []
    for b in sorted(buckets.keys()):
        data = buckets[b]
        count = data["count"]
        win_rate = round((data["wins"] / count) * 100, 2) if count > 0 else 0.0
        avg_pnl = round(data["total_pnl"] / count, 2) if count > 0 else 0.0
        avg_pnl_pct = round(data["avg_pnl_pct"] / count, 4) if count > 0 else 0.0
        bucket_list.append({
            "bucket": f"{b}-{b+4}",
            "count": count,
            "wins": data["wins"],
            "win_rate": win_rate,
            "avg_pnl_usd": avg_pnl,
            "avg_pnl_pct": avg_pnl_pct,
        })

    # Recommendation based on calibration
    recommendation = ""
    if overall_win_rate >= 60:
        recommendation = f"Strong calibration: {overall_win_rate}% win-rate across {total_closed} trades. Continue current thresholds."
    elif overall_win_rate >= 45:
        recommendation = f"Moderate calibration: {overall_win_rate}% win-rate. Consider raising min DQS to improve edge."
    else:
        recommendation = f"Weak calibration: {overall_win_rate}% win-rate. Review SL/TP geometry and DQS weights before scaling."

    return {
        "total_closed": total_closed,
        "overall_win_rate": overall_win_rate,
        "buckets": bucket_list,
        "recommendation": recommendation,
    }


def get_min_executable_dqs(calibration: Dict[str, Any], target_win_rate: float = 55.0) -> int:
    """Return the minimum DQS bucket that meets the target win-rate.
    If no bucket meets it, return the highest bucket seen + 5.
    """
    for b in calibration.get("buckets", []):
        if b["win_rate"] >= target_win_rate:
            return int(b["bucket"].split("-")[0])
    return 75  # fallback to EXCELLENT threshold
