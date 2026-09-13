#!/usr/bin/env python3
"""
Populate confidence_matrix table from historical paper trade outcomes.
Computes win rates per DQS bucket with Wilson lower bound.
This is the bridge between calibration_engine.py (which writes calibration.json)
and calibration_lookup.py (which reads confidence_matrix table).
"""
import os
import sys
import math
from collections import defaultdict
from datetime import datetime, timezone

import psycopg2

DB_URL = os.environ.get("DB_URL", "")


def wilson_lower(wins, total, z=1.96):
    """Wilson score lower bound for a binomial proportion."""
    if total == 0:
        return 0.5
    p = wins / total
    denom = 1 + z * z / total
    center = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (center - margin) / denom)


def fetch_closed_trades(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT pt.confidence, pt.side, pt.symbol, pt.net_pnl_usd,
               pt.entry_strategy, pt.exit_reason
        FROM paper_trades pt
        WHERE pt.status = 'CLOSED'
          AND pt.confidence IS NOT NULL
          AND pt.net_pnl_usd IS NOT NULL
        ORDER BY pt.created_at
    """)
    rows = cur.fetchall()
    cur.close()
    return rows


def compute_matrix(trades):
    """
    Build confidence matrix entries at multiple fallback levels.
    Each entry: (dqs_bucket, symbol, side, strategy, session, win_rate, sample_size, wilson_lower)
    """
    # Group trades by DQS bucket and by specificity level
    levels = [
        ("full", lambda t: (t[0], t[2], t[1], t[4], None)),
        ("no_session", lambda t: (t[0], t[2], t[1], t[4], None)),
        ("no_strategy", lambda t: (t[0], t[2], t[1], None, None)),
        ("symbol_only", lambda t: (t[0], t[2], None, None, None)),
        ("global", lambda t: (t[0], None, None, None, None)),
    ]

    entries = []
    seen = set()

    for level_name, key_fn in levels:
        groups = defaultdict(lambda: {"wins": 0, "total": 0})

        for t in trades:
            dqs, symbol, side, strategy, session = key_fn(t)
            dqs_bucket = (int(dqs) // 5) * 5
            won = 1 if float(t[3]) > 0 else 0
            key = (dqs_bucket, symbol, side, strategy, session)
            groups[key]["wins"] += won
            groups[key]["total"] += 1

        for key, stats in groups.items():
            if stats["total"] < 3:
                continue
            dqs_bucket, symbol, side, strategy, session = key
            win_rate = stats["wins"] / stats["total"]
            wl = wilson_lower(stats["wins"], stats["total"])

            # Skip if we already have a more specific entry for this bucket
            dedup_key = (dqs_bucket, symbol, side, strategy, session)
            if dedup_key in seen:
                continue
            seen.add(dedup_key)

            entries.append({
                "dqs_bucket": dqs_bucket,
                "symbol": symbol,
                "side": side,
                "strategy": strategy,
                "session": session,
                "win_rate": round(win_rate, 4),
                "sample_size": stats["total"],
                "wilson_lower": round(wl, 4),
            })

    return entries


def insert_matrix(conn, entries, version):
    cur = conn.cursor()
    for e in entries:
        cur.execute("""
            INSERT INTO confidence_matrix
                (version, dqs_bucket, symbol, side, strategy, session,
                 win_rate, sample_size, wilson_lower, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (version, dqs_bucket,
                COALESCE(symbol,'_'), COALESCE(side,'_'),
                COALESCE(strategy,'_'), COALESCE(session,'_'))
            DO UPDATE SET
                win_rate = EXCLUDED.win_rate,
                sample_size = EXCLUDED.sample_size,
                wilson_lower = EXCLUDED.wilson_lower,
                updated_at = NOW()
        """, (version, e["dqs_bucket"], e["symbol"], e["side"],
              e["strategy"], e["session"],
              e["win_rate"], e["sample_size"], e["wilson_lower"]))
    conn.commit()
    cur.close()


def main():
    if not DB_URL:
        print("[PopulateMatrix] ERROR: DB_URL not set")
        sys.exit(1)

    conn = psycopg2.connect(DB_URL, sslmode="prefer")
    try:
        trades = fetch_closed_trades(conn)
        print(f"[PopulateMatrix] Fetched {len(trades)} closed trades with confidence")

        if len(trades) < 5:
            print("[PopulateMatrix] Not enough trades to build matrix")
            return

        entries = compute_matrix(trades)
        print(f"[PopulateMatrix] Computed {len(entries)} matrix entries")

        # Get next version
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM confidence_matrix")
        version = cur.fetchone()[0]
        cur.close()

        insert_matrix(conn, entries, version)
        print(f"[PopulateMatrix] Inserted version {version} with {len(entries)} entries")

        # Print summary
        print("\n[PopulateMatrix] Matrix summary:")
        print(f"{'DQS':>5} {'Symbol':>10} {'Side':>6} {'WR':>6} {'N':>4} {'Wilson':>7}")
        for e in sorted(entries, key=lambda x: (x["dqs_bucket"], str(x["symbol"]), str(x["side"]))):
            sym = e["symbol"] or "ALL"
            side = e["side"] or "ALL"
            print(f"{e['dqs_bucket']:>5} {sym:>10} {side:>6} {e['win_rate']:>6.2f} {e['sample_size']:>4} {e['wilson_lower']:>7.2f}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
