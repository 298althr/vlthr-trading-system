"""
Variance Tracker and Pattern Aggregator
=======================================
Tracks PnL variance across sessions, symbols, strategies, and regimes.
Aggregates recurring patterns from closed trades to inform calibration.

Reads from paper_trades + high_confidence_signals.
Writes summary to logs/variance_report.json for dashboard consumption.
"""

import json
import os
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple

import psycopg2
import numpy as np

DB_URL = os.environ.get("DB_URL", "")
LOG_DIR = Path(os.environ.get("VARIANCE_LOG_DIR", "/app/logs"))
REPORT_PATH = LOG_DIR / "variance_report.json"


def fetch_closed_trades(conn, days: int = 30) -> List[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            pt.symbol, pt.side, pt.confidence, pt.exit_reason,
            pt.hours_held, pt.net_pnl_pct, pt.net_pnl_usd,
            pt.entry_strategy, pt.entry_regime,
            pt.created_at, pt.exit_time_utc,
            hcs.session, hcs.rsi_15m, hcs.adx_4h,
            hcs.funding_rate, hcs.oi_delta_pct
        FROM paper_trades pt
        LEFT JOIN high_confidence_signals hcs ON hcs.id = pt.signal_id
        WHERE pt.status = 'CLOSED'
          AND pt.exit_time_utc >= NOW() - (%s || ' days')::interval
        ORDER BY pt.exit_time_utc DESC
        """,
        (str(days),)
    )
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    cur.close()
    return [dict(zip(cols, r)) for r in rows]


def compute_variance_metrics(trades: List[dict]) -> Dict:
    pnls = [float(t["net_pnl_pct"] or 0) for t in trades]
    if not pnls:
        return {"n_trades": 0}

    arr = np.array(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    return {
        "n_trades": len(pnls),
        "mean_pnl_pct": round(float(arr.mean()), 4),
        "std_pnl_pct": round(float(arr.std()), 4),
        "sharpe_approx": round(float(arr.mean() / arr.std()), 4) if arr.std() > 0 else 0,
        "win_rate": round(len(wins) / len(pnls), 4),
        "avg_win_pct": round(float(np.mean(wins)), 4) if wins else 0,
        "avg_loss_pct": round(float(np.mean(losses)), 4) if losses else 0,
        "max_win_pct": round(float(max(wins)), 4) if wins else 0,
        "max_loss_pct": round(float(min(losses)), 4) if losses else 0,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 4) if losses and sum(losses) != 0 else 0,
        "pnl_skew": round(float(((arr - arr.mean()) ** 3).mean() / (arr.std() ** 3)), 4) if arr.std() > 0 else 0,
    }


def aggregate_by_dimension(trades: List[dict], key: str) -> Dict[str, Dict]:
    groups = defaultdict(list)
    for t in trades:
        val = t.get(key)
        if val is None:
            val = "unknown"
        groups[str(val)].append(t)

    result = {}
    for group_name, group_trades in groups.items():
        result[group_name] = compute_variance_metrics(group_trades)
    return result


def detect_patterns(trades: List[dict]) -> List[Dict]:
    """Detect recurring patterns in winning vs losing trades."""
    patterns = []

    # Pattern 1: Session + Strategy combination
    session_strategy = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": []})
    for t in trades:
        sess = t.get("session") or "unknown"
        strat = t.get("entry_strategy") or "unknown"
        key = f"{sess}_{strat}"
        pnl = float(t["net_pnl_pct"] or 0)
        session_strategy[key]["pnl"].append(pnl)
        if pnl > 0:
            session_strategy[key]["wins"] += 1
        else:
            session_strategy[key]["losses"] += 1

    for key, stats in sorted(session_strategy.items(), key=lambda x: sum(x[1]["pnl"]), reverse=True):
        total = stats["wins"] + stats["losses"]
        if total < 3:
            continue
        avg_pnl = sum(stats["pnl"]) / total
        wr = stats["wins"] / total
        patterns.append({
            "pattern": f"session_strategy:{key}",
            "n_trades": total,
            "win_rate": round(wr, 3),
            "avg_pnl_pct": round(avg_pnl, 4),
            "edge": "positive" if avg_pnl > 0 else "negative",
        })

    # Pattern 2: Regime + Symbol
    regime_symbol = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": []})
    for t in trades:
        regime = t.get("entry_regime") or "unknown"
        sym = t.get("symbol") or "unknown"
        key = f"{regime}_{sym}"
        pnl = float(t["net_pnl_pct"] or 0)
        regime_symbol[key]["pnl"].append(pnl)
        if pnl > 0:
            regime_symbol[key]["wins"] += 1
        else:
            regime_symbol[key]["losses"] += 1

    for key, stats in sorted(regime_symbol.items(), key=lambda x: sum(x[1]["pnl"]), reverse=True):
        total = stats["wins"] + stats["losses"]
        if total < 3:
            continue
        avg_pnl = sum(stats["pnl"]) / total
        wr = stats["wins"] / total
        patterns.append({
            "pattern": f"regime_symbol:{key}",
            "n_trades": total,
            "win_rate": round(wr, 3),
            "avg_pnl_pct": round(avg_pnl, 4),
            "edge": "positive" if avg_pnl > 0 else "negative",
        })

    # Pattern 3: Exit reason analysis
    exit_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": []})
    for t in trades:
        reason = t.get("exit_reason") or "unknown"
        pnl = float(t["net_pnl_pct"] or 0)
        exit_stats[reason]["pnl"].append(pnl)
        if pnl > 0:
            exit_stats[reason]["wins"] += 1
        else:
            exit_stats[reason]["losses"] += 1

    for reason, stats in sorted(exit_stats.items(), key=lambda x: sum(x[1]["pnl"]), reverse=True):
        total = stats["wins"] + stats["losses"]
        if total < 3:
            continue
        avg_pnl = sum(stats["pnl"]) / total
        patterns.append({
            "pattern": f"exit_reason:{reason}",
            "n_trades": total,
            "win_rate": round(stats["wins"] / total, 3),
            "avg_pnl_pct": round(avg_pnl, 4),
            "edge": "positive" if avg_pnl > 0 else "negative",
        })

    # Pattern 4: DQS band analysis
    dqs_bands = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": []})
    for t in trades:
        conf = int(t.get("confidence") or 0)
        band = f"{(conf // 5) * 5}-{(conf // 5) * 5 + 4}"
        pnl = float(t["net_pnl_pct"] or 0)
        dqs_bands[band]["pnl"].append(pnl)
        if pnl > 0:
            dqs_bands[band]["wins"] += 1
        else:
            dqs_bands[band]["losses"] += 1

    for band, stats in sorted(dqs_bands.items(), key=lambda x: x[0]):
        total = stats["wins"] + stats["losses"]
        if total < 3:
            continue
        avg_pnl = sum(stats["pnl"]) / total
        patterns.append({
            "pattern": f"dqs_band:{band}",
            "n_trades": total,
            "win_rate": round(stats["wins"] / total, 3),
            "avg_pnl_pct": round(avg_pnl, 4),
            "edge": "positive" if avg_pnl > 0 else "negative",
        })

    return patterns


def run_variance_report(days: int = 30) -> dict:
    """Build variance report from last N days of closed trades."""
    if not DB_URL:
        print("[VarianceTracker] DB_URL not set, skipping")
        return {}

    conn = psycopg2.connect(DB_URL, sslmode="prefer")
    try:
        trades = fetch_closed_trades(conn, days)
        if not trades:
            print(f"[VarianceTracker] No closed trades in last {days} days")
            return {"n_trades": 0, "generated_at": datetime.now(timezone.utc).isoformat()}

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": days,
            "overall": compute_variance_metrics(trades),
            "by_symbol": aggregate_by_dimension(trades, "symbol"),
            "by_session": aggregate_by_dimension(trades, "session"),
            "by_strategy": aggregate_by_dimension(trades, "entry_strategy"),
            "by_regime": aggregate_by_dimension(trades, "entry_regime"),
            "by_exit_reason": aggregate_by_dimension(trades, "exit_reason"),
            "by_side": aggregate_by_dimension(trades, "side"),
            "patterns": detect_patterns(trades),
        }

        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(REPORT_PATH, "w") as f:
            json.dump(report, f, indent=2)

        print(f"[VarianceTracker] Report saved to {REPORT_PATH}")
        print(f"  Overall: {report['overall']['n_trades']} trades, "
              f"WR={report['overall']['win_rate']:.1%}, "
              f"PF={report['overall']['profit_factor']}, "
              f"Sharpe~={report['overall']['sharpe_approx']}")

        top_patterns = [p for p in report["patterns"] if p["edge"] == "positive"][:5]
        if top_patterns:
            print(f"  Top positive patterns:")
            for p in top_patterns:
                print(f"    {p['pattern']}: n={p['n_trades']}, WR={p['win_rate']:.1%}, avg={p['avg_pnl_pct']:.2f}%")

        return report
    finally:
        conn.close()


if __name__ == "__main__":
    run_variance_report()
