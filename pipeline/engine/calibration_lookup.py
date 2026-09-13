"""
Calibration Lookup — Multi-level fallback hierarchy for confidence matrix.
Returns calibrated win probability for a given (dqs, symbol, side, strategy, session).
"""
import json
import os
from pathlib import Path

CAL_PATH = Path(os.environ.get(
    "VLTHR_CAL_OVERRIDE",
    Path(__file__).parent / "calibration.json"
))


def _load_cal():
    return json.loads(CAL_PATH.read_text())


def get_calibration_gate(symbol: str) -> float:
    """Return per-symbol calibrated probability gate from calibration.json."""
    cal = _load_cal()
    gates = cal.get("calibration_gates", {})
    default = gates.get("min_calibrated_prob_to_open", 0.45)
    return gates.get("per_symbol_overrides", {}).get(symbol, default)


def get_calibration_gate_pending(symbol: str) -> float:
    """Return per-symbol calibrated probability gate for PENDING (lower threshold)."""
    cal = _load_cal()
    gates = cal.get("calibration_gates", {})
    default = gates.get("min_calibrated_prob_to_pending", 0.35)
    return gates.get("per_symbol_overrides_pending", {}).get(symbol, default)


def lookup_calibrated_prob(dqs, symbol, side, strategy, session, db_conn):
    """
    Multi-level lookup with fallback hierarchy.
    Returns (win_prob, weighted_sample_size, fallback_level, wilson_lower).

    Fallback levels:
      full → no_session → no_strategy → symbol_only → global → prior
    """
    dqs_bucket = (int(dqs) // 5) * 5

    fallback_levels = [
        {"symbol": symbol, "side": side, "strategy": strategy, "session": session, "label": "full"},
        {"symbol": symbol, "side": side, "strategy": strategy, "session": None, "label": "no_session"},
        {"symbol": symbol, "side": side, "strategy": None, "session": None, "label": "no_strategy"},
        {"symbol": symbol, "side": None, "strategy": None, "session": None, "label": "symbol_only"},
        {"symbol": None, "side": None, "strategy": None, "session": None, "label": "global"},
    ]

    cal = _load_cal()
    min_samples = cal.get("calibration_gates", {}).get("min_sample_size_to_enforce_gate", 5)

    for level in fallback_levels:
        row = _query_matrix(db_conn, dqs_bucket,
                            symbol=level["symbol"], side=level["side"],
                            strategy=level["strategy"], session=level["session"])
        if row and row["sample_size"] >= min_samples:
            return row["win_rate"], row["sample_size"], level["label"], row.get("wilson_lower", row["win_rate"])

    return 0.5, 0, "prior", 0.5


def _query_matrix(conn, dqs_bucket, symbol, side, strategy, session):
    """Query confidence_matrix for a specific cell. Returns dict or None."""
    cur = conn.cursor()
    filters = [
        "version = (SELECT MAX(version) FROM confidence_matrix)",
        "dqs_bucket = %s",
    ]
    params = [dqs_bucket]

    for col, val in [("symbol", symbol), ("side", side), ("strategy", strategy), ("session", session)]:
        if val is None:
            filters.append(f"{col} IS NULL")
        else:
            filters.append(f"{col} = %s")
            params.append(val)

    cur.execute(f"""
        SELECT win_rate, sample_size, wilson_lower
        FROM confidence_matrix
        WHERE {" AND ".join(filters)}
        LIMIT 1
    """, params)

    row = cur.fetchone()
    if row:
        return {"win_rate": float(row[0]), "sample_size": float(row[1]), "wilson_lower": float(row[2]) if row[2] else float(row[0])}
    return None
