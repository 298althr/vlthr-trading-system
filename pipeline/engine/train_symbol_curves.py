"""
Train per-symbol calibration curves from paper_trades history.
Updates calibration.json with symbol_curves populated from actual trade outcomes.

Usage:
    python train_symbol_curves.py
"""
import json
import os
import sys
from pathlib import Path
from collections import defaultdict
import psycopg2

_ENGINE = Path(__file__).resolve().parent
_CALIBRATION_PATH = _ENGINE / "calibration.json"

# Database connection
DATABASE_URL = os.environ.get("DATABASE_URL", "")

DQS_BREAKPOINTS = [50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100]


def load_calibration():
    with open(_CALIBRATION_PATH) as f:
        return json.load(f)


def save_calibration(cal):
    with open(_CALIBRATION_PATH, "w") as f:
        json.dump(cal, f, indent=2)
    print(f"[Calibration] Saved to {_CALIBRATION_PATH}")


def fetch_symbol_trades(conn):
    """Fetch all closed trades with their DQS (confidence) and outcome."""
    cur = conn.cursor()
    cur.execute("""
        SELECT symbol, confidence, net_pnl_usd, net_pnl_pct, status
        FROM paper_trades
        WHERE status IN ('CLOSED', 'OPEN')
          AND confidence IS NOT NULL
          AND confidence > 0
        ORDER BY created_at
    """)
    rows = cur.fetchall()
    cur.close()
    return rows


def compute_symbol_curve(trades):
    """Compute win probability per DQS bucket for a set of trades."""
    buckets = defaultdict(lambda: {"wins": 0, "total": 0})

    for symbol, confidence, pnl_usd, pnl_pct, status in trades:
        dqs = int(confidence)
        bucket_idx = 0
        for i, bp in enumerate(DQS_BREAKPOINTS):
            if dqs >= bp:
                bucket_idx = i
            else:
                break

        is_win = False
        if status == 'CLOSED':
            pnl = float(pnl_usd or pnl_pct or 0)
            is_win = pnl > 0
        elif status == 'OPEN':
            # Skip open trades — outcome unknown
            continue

        buckets[bucket_idx]["total"] += 1
        if is_win:
            buckets[bucket_idx]["wins"] += 1

    # Build probability array with smoothing
    probs = []
    total_trades = sum(b["total"] for b in buckets.values())

    for i, bp in enumerate(DQS_BREAKPOINTS):
        b = buckets[i]
        if b["total"] >= 3:
            # Enough samples — use actual win rate
            prob = b["wins"] / b["total"]
        elif b["total"] > 0:
            # Some samples — blend with 0.5 prior (Laplace smoothing)
            prob = (b["wins"] + 1) / (b["total"] + 2)
        else:
            # No samples — use 0.5 (neutral)
            prob = 0.5
        probs.append(round(prob, 4))

    return {
        "dqs_breakpoints": DQS_BREAKPOINTS,
        "win_probabilities": probs,
        "n_samples_used": total_trades,
    }


def main():
    if not DATABASE_URL:
        print("[Error] DATABASE_URL not set. Export it first.")
        sys.exit(1)

    conn = psycopg2.connect(DATABASE_URL)

    # Fetch all trades grouped by symbol
    rows = fetch_symbol_trades(conn)
    conn.close()

    if not rows:
        print("[Calibration] No trades found — skipping.")
        return

    # Group by symbol
    symbol_trades = defaultdict(list)
    for row in rows:
        symbol_trades[row[0]].append(row)

    # Compute per-symbol curves
    symbol_curves = {}
    for symbol, trades in symbol_trades.items():
        curve = compute_symbol_curve(trades)
        n = curve["n_samples_used"]
        wr = sum(1 for t in trades if t[4] == 'CLOSED' and float(t[2] or t[3] or 0) > 0)
        total_closed = sum(1 for t in trades if t[4] == 'CLOSED')
        print(f"  {symbol}: {n} samples, {total_closed} closed, {wr} wins ({wr/total_closed*100:.1f}% WR if closed>0)")
        symbol_curves[symbol] = curve

    # Update calibration.json
    cal = load_calibration()
    cal["calibration_curve"]["symbol_curves"] = symbol_curves
    save_calibration(cal)

    print(f"\n[Calibration] Trained curves for {len(symbol_curves)} symbols")
    for sym, curve in symbol_curves.items():
        print(f"  {sym}: probs={curve['win_probabilities']}")


if __name__ == "__main__":
    main()
