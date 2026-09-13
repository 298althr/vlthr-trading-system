"""
V3 Feature Registry — Lifecycle Management
===========================================
Manages the lifecycle of discovered features through 5 states:

    candidate → validated → calibrated → trusted → deprecated

Each feature is registered in the `feature_lifecycle` DB table with:
    - Information gain (IC)
    - Wilson lower bound (statistical significance)
    - Sample size
    - Drift score
    - Input features and probe types
    - Metadata

Promotion criteria:
    candidate → validated:    IC > 0.02, Wilson lower > 0.05, n > 500
    validated → calibrated:   IC > 0.03, Wilson lower > 0.08, drift < 0.15
    calibrated → trusted:     IC > 0.05, Wilson lower > 0.12, drift < 0.10

Demotion criteria:
    Any state → deprecated:   IC drops below 0.01 for 3 consecutive evaluations
                              OR drift > 0.30
"""
from __future__ import annotations

import os
import json
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field


# Lifecycle states
CANDIDATE = "candidate"
VALIDATED = "validated"
CALIBRATED = "calibrated"
TRUSTED = "trusted"
DEPRECATED = "deprecated"

LIFECYCLE_ORDER = [CANDIDATE, VALIDATED, CALIBRATED, TRUSTED, DEPRECATED]

# Promotion thresholds
PROMOTE_THRESHOLDS = {
    f"{CANDIDATE}->{VALIDATED}": {"ic_min": 0.02, "wilson_min": 0.05, "n_min": 500},
    f"{VALIDATED}->{CALIBRATED}": {"ic_min": 0.03, "wilson_min": 0.08, "n_min": 500, "drift_max": 0.15},
    f"{CALIBRATED}->{TRUSTED}": {"ic_min": 0.05, "wilson_min": 0.12, "n_min": 500, "drift_max": 0.10},
}

# Demotion thresholds
DEMOTE_IC_FLOOR = 0.01
DEMOTE_DRIFT_CEILING = 0.30
DEMOTE_CONSECUTIVE_FAILURES = 3


@dataclass
class FeatureRecord:
    feature_name: str
    lifecycle_state: str = CANDIDATE
    ic: float = 0.0
    ic_std: float = 0.0
    wilson_lower: float = 0.0
    sample_size: int = 0
    drift_score: float = 0.0
    probe_type: str = ""
    input_features: List[str] = field(default_factory=list)
    output_type: str = "continuous"
    metadata: dict = field(default_factory=dict)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    validated_at: Optional[str] = None
    calibrated_at: Optional[str] = None
    trusted_at: Optional[str] = None
    deprecated_at: Optional[str] = None
    consecutive_failures: int = 0

    def to_dict(self) -> dict:
        return {
            "feature_name": self.feature_name,
            "lifecycle_state": self.lifecycle_state,
            "ic": self.ic,
            "ic_std": self.ic_std,
            "wilson_lower": self.wilson_lower,
            "sample_size": self.sample_size,
            "drift_score": self.drift_score,
            "probe_type": self.probe_type,
            "input_features": self.input_features,
            "output_type": self.output_type,
            "metadata": self.metadata,
            "consecutive_failures": self.consecutive_failures,
        }


def _get_db_url() -> str:
    env = os.environ.get("DB_URL", "")
    if env:
        return env
    # Try loading from .env
    from pathlib import Path
    repo = Path(__file__).resolve().parents[5]
    env_file = repo / ".env"
    if env_file.exists():
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == "DB_URL":
                        return v.strip()
    return ""


def upsert_feature(record: FeatureRecord) -> bool:
    """Insert or update a feature in the feature_lifecycle table."""
    db_url = _get_db_url()
    if not db_url:
        print("[feature_registry] No DB_URL, skipping upsert")
        return False
    try:
        import psycopg2
        from psycopg2.extras import Json
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        now = datetime.now(timezone.utc).isoformat()
        cur.execute("""
            INSERT INTO feature_lifecycle (
                feature_name, lifecycle_state, information_gain, wilson_lower,
                sample_size, drift_score, metadata, input_features, output_type,
                probe_types, created_at, updated_at,
                validated_at, calibrated_at, trusted_at, deprecated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (feature_name) DO UPDATE SET
                lifecycle_state = EXCLUDED.lifecycle_state,
                information_gain = EXCLUDED.information_gain,
                wilson_lower = EXCLUDED.wilson_lower,
                sample_size = EXCLUDED.sample_size,
                drift_score = EXCLUDED.drift_score,
                metadata = EXCLUDED.metadata,
                input_features = EXCLUDED.input_features,
                output_type = EXCLUDED.output_type,
                probe_types = EXCLUDED.probe_types,
                updated_at = EXCLUDED.updated_at,
                validated_at = COALESCE(EXCLUDED.validated_at, feature_lifecycle.validated_at),
                calibrated_at = COALESCE(EXCLUDED.calibrated_at, feature_lifecycle.calibrated_at),
                trusted_at = COALESCE(EXCLUDED.trusted_at, feature_lifecycle.trusted_at),
                deprecated_at = COALESCE(EXCLUDED.deprecated_at, feature_lifecycle.deprecated_at)
        """, (
            record.feature_name, record.lifecycle_state, record.ic,
            record.wilson_lower, record.sample_size, record.drift_score,
            Json(record.metadata), record.input_features, record.output_type,
            [record.probe_type] if record.probe_type else [],
            record.created_at or now, now,
            record.validated_at, record.calibrated_at,
            record.trusted_at, record.deprecated_at,
        ))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"[feature_registry] upsert failed: {e}")
        return False


def load_feature(feature_name: str) -> Optional[FeatureRecord]:
    """Load a feature from the DB by name."""
    db_url = _get_db_url()
    if not db_url:
        return None
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("""
            SELECT feature_name, lifecycle_state, information_gain, wilson_lower,
                   sample_size, drift_score, metadata, input_features, output_type,
                   probe_types, created_at, updated_at,
                   validated_at, calibrated_at, trusted_at, deprecated_at
            FROM feature_lifecycle WHERE feature_name = %s
        """, (feature_name,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        if not row:
            return None
        return FeatureRecord(
            feature_name=row[0], lifecycle_state=row[1],
            ic=float(row[2] or 0), wilson_lower=float(row[3] or 0),
            sample_size=int(row[4] or 0), drift_score=float(row[5] or 0),
            metadata=row[6] or {}, input_features=row[7] or [],
            output_type=row[8] or "continuous", probe_type=(row[9] or [""])[0],
            created_at=str(row[10]) if row[10] else None,
            updated_at=str(row[11]) if row[11] else None,
            validated_at=str(row[12]) if row[12] else None,
            calibrated_at=str(row[13]) if row[13] else None,
            trusted_at=str(row[14]) if row[14] else None,
            deprecated_at=str(row[15]) if row[15] else None,
        )
    except Exception as e:
        print(f"[feature_registry] load failed: {e}")
        return None


def load_all_features() -> List[FeatureRecord]:
    """Load all features from the DB."""
    db_url = _get_db_url()
    if not db_url:
        return []
    try:
        import psycopg2
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        cur.execute("""
            SELECT feature_name, lifecycle_state, information_gain, wilson_lower,
                   sample_size, drift_score, metadata, input_features, output_type,
                   probe_types, created_at, updated_at,
                   validated_at, calibrated_at, trusted_at, deprecated_at
            FROM feature_lifecycle ORDER BY information_gain DESC
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        records = []
        for row in rows:
            records.append(FeatureRecord(
                feature_name=row[0], lifecycle_state=row[1],
                ic=float(row[2] or 0), wilson_lower=float(row[3] or 0),
                sample_size=int(row[4] or 0), drift_score=float(row[5] or 0),
                metadata=row[6] or {}, input_features=row[7] or [],
                output_type=row[8] or "continuous", probe_type=(row[9] or [""])[0],
                created_at=str(row[10]) if row[10] else None,
                updated_at=str(row[11]) if row[11] else None,
                validated_at=str(row[12]) if row[12] else None,
                calibrated_at=str(row[13]) if row[13] else None,
                trusted_at=str(row[14]) if row[14] else None,
                deprecated_at=str(row[15]) if row[15] else None,
            ))
        return records
    except Exception as e:
        print(f"[feature_registry] load_all failed: {e}")
        return []


def evaluate_promotion(record: FeatureRecord) -> tuple[str, str]:
    """
    Evaluate if a feature should be promoted or demoted.
    Returns (new_state, reason).
    """
    # Check demotion first — use abs(ic) since negative IC is equally predictive
    if abs(record.ic) < DEMOTE_IC_FLOOR:
        record.consecutive_failures += 1
        if record.consecutive_failures >= DEMOTE_CONSECUTIVE_FAILURES:
            if record.lifecycle_state != DEPRECATED:
                return DEPRECATED, f"IC below {DEMOTE_IC_FLOOR} for {record.consecutive_failures} consecutive evaluations"
    else:
        record.consecutive_failures = 0

    if record.drift_score > DEMOTE_DRIFT_CEILING:
        if record.lifecycle_state != DEPRECATED:
            return DEPRECATED, f"Drift score {record.drift_score:.3f} exceeds ceiling {DEMOTE_DRIFT_CEILING}"

    if record.lifecycle_state == DEPRECATED:
        return DEPRECATED, "already deprecated"

    # Check promotion
    current_idx = LIFECYCLE_ORDER.index(record.lifecycle_state)
    if current_idx >= 3:  # Already trusted
        return record.lifecycle_state, "already at max non-deprecated state"

    next_state = LIFECYCLE_ORDER[current_idx + 1]
    transition_key = f"{record.lifecycle_state}->{next_state}"
    thresholds = PROMOTE_THRESHOLDS.get(transition_key)

    if not thresholds:
        return record.lifecycle_state, f"no thresholds for {transition_key}"

    ic_ok = abs(record.ic) >= thresholds["ic_min"]
    wl_ok = record.wilson_lower >= thresholds["wilson_min"]
    n_ok = record.sample_size >= thresholds["n_min"]
    drift_ok = True
    if "drift_max" in thresholds:
        drift_ok = record.drift_score <= thresholds["drift_max"]

    if ic_ok and wl_ok and n_ok and drift_ok:
        now = datetime.now(timezone.utc).isoformat()
        if next_state == VALIDATED:
            record.validated_at = now
        elif next_state == CALIBRATED:
            record.calibrated_at = now
        elif next_state == TRUSTED:
            record.trusted_at = now
        return next_state, f"promoted: IC={record.ic:.4f} WL={record.wilson_lower:.4f} n={record.sample_size}"

    reasons = []
    if not ic_ok:
        reasons.append(f"IC {record.ic:.4f} < {thresholds['ic_min']}")
    if not wl_ok:
        reasons.append(f"WL {record.wilson_lower:.4f} < {thresholds['wilson_min']}")
    if not n_ok:
        reasons.append(f"n {record.sample_size} < {thresholds['n_min']}")
    if not drift_ok:
        reasons.append(f"drift {record.drift_score:.3f} > {thresholds['drift_max']}")
    return record.lifecycle_state, f"not promoted: {', '.join(reasons)}"


def get_trusted_features() -> List[FeatureRecord]:
    """Get all trusted features."""
    return [f for f in load_all_features() if f.lifecycle_state == TRUSTED]


def get_active_features() -> List[FeatureRecord]:
    """Get all non-deprecated features (candidate + validated + calibrated + trusted)."""
    return [f for f in load_all_features() if f.lifecycle_state != DEPRECATED]


def compute_drift_score(current_ic: float, historical_ic: float) -> float:
    """Compute drift score as normalized IC decay."""
    if historical_ic == 0:
        return 0.0
    return float(abs(historical_ic - current_ic) / max(abs(historical_ic), 1e-10))
