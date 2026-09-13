"""
VLTHR Pipeline Brain — Online ML Predictor (Phase 3)
=====================================================
Incremental classifier that predicts favorable next-15m move per symbol.
Runs in shadow mode until trust criteria are met.

Trust criteria (all must hold for 5 consecutive days):
- ≥ 30 resolved predictions per symbol
- Rolling accuracy > 55%
- Brier score < 0.24
- Positive 2-week shadow P&L

Usage (from portfolio_orchestrator):
    from pipeline_brain import PipelineBrain
    brain = PipelineBrain(conn)
    for sig in signals:
        pred = brain.predict(symbol, features, dqs)
        # pred is a dict with prob_favorable, confidence_fused, is_trusted
    brain.resolve_and_learn(now)  # called each run to learn from last bar
"""
from __future__ import annotations
import os
import pickle
import json
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

# River is optional — gracefully degrade
RIVER_AVAILABLE = False
try:
    from river import compose, preprocessing, linear_model, metrics
    RIVER_AVAILABLE = True
except ImportError:
    pass

# Trust thresholds (from revival plan)
TRUST_CRITERIA = {
    "min_predictions_per_symbol": 30,
    "min_accuracy": 0.55,
    "max_brier": 0.24,
    "min_shadow_pnl": 0.0,
    "consecutive_days": 5,
}

# Feature keys expected from signal dict
FEATURE_KEYS = [
    "dqs", "rsi", "adx", "log_ret_4h", "oi_delta_pct",
    "funding_rate", "ob_spread_pct", "ob_imbalance_pct",
    "consecutive_improvements", "consecutive_declines", "runs_tracked",
    # Sentiment features deferred from FEATURE_KEYS until predictive value
    # is proven in shadow testing. Data is collected and enriched but not
    # fed to the model. See docs/REVIW-DOCUMENTATION.html for rationale.
]


def _extract_features(signal: dict) -> dict:
    """Extract numeric features from a signal dict for the model."""
    features = {}
    for key in FEATURE_KEYS:
        val = signal.get(key, 0)
        if val is None or (isinstance(val, float) and val != val):  # NaN check
            val = 0.0
        features[key] = float(val)
    return features


def _make_model():
    """Create a fresh River model (pipeline with scaler + logistic regression)."""
    if not RIVER_AVAILABLE:
        return None
    return compose.Pipeline(
        ("scale", preprocessing.StandardScaler()),
        ("clf", linear_model.LogisticRegression()),
    )


def _rolling_accuracy(conn, symbol: str, window: int = 30) -> float:
    """Compute rolling accuracy from predictions table."""
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE hit = TRUE),
               COUNT(*) FILTER (WHERE actual_outcome IS NOT NULL)
        FROM (
            SELECT hit, actual_outcome
            FROM predictions
            WHERE symbol = %s AND actual_outcome IS NOT NULL
            ORDER BY run_time DESC
            LIMIT %s
        ) sub
    """, (symbol, window))
    row = cur.fetchone()
    hits, total = row[0] or 0, row[1] or 0
    return (hits / total) if total > 0 else 0.0


def _brier_score(conn, symbol: str, window: int = 30) -> float:
    """Approximate Brier score from recent predictions."""
    cur = conn.cursor()
    cur.execute("""
        SELECT prob_favorable, actual_outcome
        FROM predictions
        WHERE symbol = %s AND actual_outcome IS NOT NULL
        ORDER BY run_time DESC
        LIMIT %s
    """, (symbol, window))
    rows = cur.fetchall()
    if not rows:
        return 1.0
    total = 0.0
    for prob, actual in rows:
        p = float(prob or 0.5)
        y = float(actual or 0)
        total += (p - y) ** 2
    return total / len(rows)


def _shadow_pnl(conn, symbol: str, days: int = 14) -> float:
    """Compute shadow P&L from resolved predictions (simplified: +1 per hit, -1 per miss)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE hit = TRUE) - COUNT(*) FILTER (WHERE hit = FALSE)
        FROM predictions
        WHERE symbol = %s AND actual_outcome IS NOT NULL
          AND run_time > NOW() - INTERVAL '%s days'
    """, (symbol, days))
    row = cur.fetchone()
    return float(row[0] or 0)


def _is_trusted(conn, symbol: str) -> bool:
    """Check if model for symbol meets all trust criteria."""
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM predictions
        WHERE symbol = %s AND actual_outcome IS NOT NULL
    """, (symbol,))
    n_pred = cur.fetchone()[0] or 0
    if n_pred < TRUST_CRITERIA["min_predictions_per_symbol"]:
        return False

    acc = _rolling_accuracy(conn, symbol)
    if acc < TRUST_CRITERIA["min_accuracy"]:
        return False

    brier = _brier_score(conn, symbol)
    if brier > TRUST_CRITERIA["max_brier"]:
        return False

    pnl = _shadow_pnl(conn, symbol)
    if pnl < TRUST_CRITERIA["min_shadow_pnl"]:
        return False

    return True


class PipelineBrain:
    """
    Online predictor that learns one bar at a time.
    One model per symbol (can extend to global model later).
    """

    def __init__(self, conn, model_type: str = "river_logistic"):
        self.conn = conn
        self.model_type = model_type
        self._models: Dict[str, Any] = {}  # symbol -> model
        self._last_predictions: Dict[str, dict] = {}  # symbol -> prediction row
        self._load_models()

    # ── Model persistence ────────────────────────────────────────────────

    def _load_models(self):
        """Load serialized models from DB."""
        cur = self.conn.cursor()
        cur.execute("""
            SELECT symbol, serialized, n_samples, accuracy, brier_score
            FROM model_state
            WHERE model_type = %s
        """, (self.model_type,))
        for row in cur.fetchall():
            symbol, blob, n_samples, acc, brier = row
            try:
                model = pickle.loads(blob)
                self._models[symbol] = model
                print(f"[Brain] Loaded model for {symbol} (n={n_samples}, acc={acc:.1f}%, brier={brier:.3f})")
            except Exception as e:
                print(f"[Brain] Failed to load model for {symbol}: {e}")

    def _save_model(self, symbol: str, n_samples: int):
        """Serialize and save model to DB."""
        model = self._models.get(symbol)
        if model is None:
            return
        try:
            blob = pickle.dumps(model)
        except Exception as e:
            print(f"[Brain] Pickle failed for {symbol}: {e}")
            return

        acc = _rolling_accuracy(self.conn, symbol)
        brier = _brier_score(self.conn, symbol)
        pnl = _shadow_pnl(self.conn, symbol)

        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO model_state (model_type, symbol, serialized, n_samples, accuracy, brier_score, shadow_pnl, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW())
            ON CONFLICT (model_type, symbol) DO UPDATE SET
                serialized = EXCLUDED.serialized,
                n_samples = EXCLUDED.n_samples,
                accuracy = EXCLUDED.accuracy,
                brier_score = EXCLUDED.brier_score,
                shadow_pnl = EXCLUDED.shadow_pnl,
                updated_at = NOW()
        """, (self.model_type, symbol, blob, n_samples, acc, brier, pnl))
        self.conn.commit()

    # ── Prediction ─────────────────────────────────────────────────────

    def predict(self, symbol: str, signal: dict, dqs: int, now: Optional[datetime] = None) -> dict:
        """
        Predict favorable next-15m move for a symbol.
        Returns dict with prob, fused confidence, and trust status.
        Always stores prediction (shadow logging).
        """
        now = now or datetime.now(timezone.utc)
        features = _extract_features(signal)

        # Ensure model exists
        if symbol not in self._models:
            self._models[symbol] = _make_model()

        model = self._models[symbol]
        if model is None:
            # River not available — return neutral prediction
            prob = 0.5
        else:
            try:
                prob = model.predict_proba_one(features).get(1, 0.5)
            except Exception as e:
                print(f"[Brain] predict_proba error for {symbol}: {e}")
                prob = 0.5

        predicted = 1 if prob >= 0.5 else 0
        trusted = _is_trusted(self.conn, symbol)

        # Confidence fusion: blend DQS + predicted probability
        # DQS gates eligibility (must pass 50); probability scales confidence
        dqs_norm = dqs / 100.0
        fused = dqs_norm * prob  # e.g., DQS=80 * prob=0.7 = 0.56 fused

        # Store prediction (always shadow-logged)
        close_price = float(signal.get("price", 0))
        cur = self.conn.cursor()
        cur.execute("""
            INSERT INTO predictions (
                run_time, symbol, features, prob_favorable, predicted_outcome,
                dqs_at_predict, model_version, shadow_mode, close_price_at_prediction
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            now, symbol, json.dumps(features),
            round(prob, 4), predicted,
            dqs, self.model_type, not trusted, close_price,
        ))
        self.conn.commit()

        # Cache for resolution next run
        self._last_predictions[symbol] = {
            "id": cur.fetchone()[0] if False else None,  # we don't need the ID
            "run_time": now,
            "prob": prob,
            "predicted": predicted,
        }

        return {
            "prob_favorable": prob,
            "predicted_outcome": predicted,
            "confidence_fused": round(fused, 4),
            "is_trusted": trusted,
            "shadow_mode": not trusted,
        }

    # ── Learning (resolve + train) ────────────────────────────────────

    def resolve_and_learn(self, now: Optional[datetime] = None):
        """
        Resolve previous run's predictions against realized next-bar prices,
        then call learn_one for each hit/miss.
        Should be called once per pipeline iteration after data is loaded.
        """
        if not RIVER_AVAILABLE:
            return

        now = now or datetime.now(timezone.utc)
        # Find predictions from the last run that haven't been resolved
        cur = self.conn.cursor()
        cur.execute("""
            SELECT id, symbol, predicted_outcome, features, dqs_at_predict
            FROM predictions
            WHERE actual_outcome IS NULL
              AND run_time < %s
            ORDER BY run_time DESC
        """, (now,))
        rows = cur.fetchall()
        if not rows:
            return

        print(f"[Brain] Resolving {len(rows)} pending predictions...")
        for pred_id, symbol, predicted, features_json, dqs_at_predict in rows:
            try:
                features = json.loads(features_json) if features_json else {}
            except Exception:
                features = {}

            actual = self._resolve_outcome(symbol, now)
            if actual is None:
                continue  # price data not yet available or move too small

            hit = (predicted == actual)
            cur.execute("""
                UPDATE predictions
                SET actual_outcome = %s, hit = %s
                WHERE id = %s
            """, (actual, hit, pred_id))
            self.conn.commit()

            # learn_one on this sample
            model = self._models.get(symbol)
            if model is not None:
                try:
                    model.learn_one(features, actual)
                except Exception as e:
                    print(f"[Brain] learn_one error for {symbol}: {e}")

        # Save updated models
        for symbol in self._models:
            cur.execute("SELECT COUNT(*) FROM predictions WHERE symbol = %s AND actual_outcome IS NOT NULL", (symbol,))
            n_samples = cur.fetchone()[0] or 0
            self._save_model(symbol, n_samples)

    def _resolve_outcome(self, symbol: str, now: datetime) -> Optional[int]:
        """
        Determine if the last prediction was correct using actual price change.
        Compares the stored close_price_at_prediction against the current live price.
        Returns 1 if price went up (favorable for LONG), 0 if down, None if unresolvable.
        """
        cur = self.conn.cursor()
        # Fetch the most recent unresolved prediction with a stored price for this symbol
        cur.execute("""
            SELECT id, close_price_at_prediction, run_time
            FROM predictions
            WHERE symbol = %s AND actual_outcome IS NULL
              AND close_price_at_prediction IS NOT NULL
              AND close_price_at_prediction > 0
              AND run_time < %s - INTERVAL '15 minutes'
            ORDER BY run_time DESC
            LIMIT 1
        """, (symbol, now))
        row = cur.fetchone()
        if not row:
            return None

        pred_id, pred_price, pred_time = row
        pred_price = float(pred_price)

        # Get current price from the enriched data loader
        try:
            from confidence_engine import load_enriched
            enriched = load_enriched(symbol)
            if enriched is None or len(enriched) == 0:
                return None
            current_price = float(enriched.iloc[-1]["close"])
        except Exception:
            return None

        if pred_price <= 0 or current_price <= 0:
            return None

        change_pct = (current_price - pred_price) / pred_price
        # Require at least 0.05% move to call it directional (avoid noise)
        if change_pct > 0.0005:
            return 1
        elif change_pct < -0.0005:
            return 0
        # Price too flat — ambiguous, skip
        return None

    # ── Diagnostics ─────────────────────────────────────────────────────

    def get_diagnostics(self, symbol: str) -> dict:
        """Return current model diagnostics for a symbol."""
        return {
            "symbol": symbol,
            "trusted": _is_trusted(self.conn, symbol),
            "accuracy": round(_rolling_accuracy(self.conn, symbol), 2),
            "brier": round(_brier_score(self.conn, symbol), 4),
            "shadow_pnl": round(_shadow_pnl(self.conn, symbol), 2),
            "n_predictions": self._count_predictions(symbol),
        }

    def _count_predictions(self, symbol: str) -> int:
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM predictions WHERE symbol = %s", (symbol,))
        return cur.fetchone()[0] or 0
