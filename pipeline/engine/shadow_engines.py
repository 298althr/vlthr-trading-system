"""
shadow_engines.py — Shadow-Mode Decision System for VLTHR Pipeline
===================================================================
Runs alongside the live pipeline, logging alternative decisions without
affecting actual trades. Enables 2-week shadow validation of:

  1. Data quality scoring (S1-2)
  2. Crowding detection (S2-2)
  3. Cascade veto (S2-3)
  4. Empirical DQS weights (S3-1)
  5. Kelly position sizing (DQRAE-B1)
  6. Crowd veto (CRDS-B5)
  7. DQS 85+ veto

All outputs are logged to the `decision_events` table for later comparison
with actual trade outcomes.

Usage in portfolio_orchestrator.py:
    from shadow_engines import ShadowDecisionEngine
    shadow = ShadowDecisionEngine(conn)
    shadow.evaluate_signals(new_signals, run_id, now)
"""
import json
import os
import numpy as np
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path


def _json_default(obj):
    """Handle numpy types that json.dumps can't serialize."""
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    return str(obj)

# ── Empirical DQS weights (from fit_dqs_weights.py) ──
EMPIRICAL_WEIGHTS = {"technical": 0.73, "structure": 0.07, "context": 0.20}
CURRENT_WEIGHTS = {"technical": 0.40, "structure": 0.30, "context": 0.30}

# ── Per-symbol crowd veto thresholds ──
CROWD_VETO_THRESHOLDS = {
    "BTCUSDT": 3.0, "ETHUSDT": 4.0, "SOLUSDT": 5.0,
    "XRPUSDT": 3.0, "BNBUSDT": 3.0, "DOGEUSDT": 3.0,
}

# ── Kelly position sizing per DQS bucket (from DQRAE-B1) ──
KELLY_MULTIPLIERS = [
    (40, 50, 0.50), (50, 55, 0.50), (55, 60, 0.80),
    (60, 65, 1.00), (65, 70, 0.23), (70, 75, 1.50),
    (75, 80, 2.00), (80, 85, 2.50), (85, 200, 0.0),
]

# ── Correlation clusters for crowding detection ──
CORRELATION_CLUSTERS = [
    {"symbols": {"BTCUSDT", "ETHUSDT", "SOLUSDT"}, "min_fire": 2},
    {"symbols": {"BTCUSDT", "ETHUSDT", "XRPUSDT"}, "min_fire": 2},
]


def _get_kelly_mult(dqs: float) -> float:
    if dqs < 40:
        return 1.0
    for lo, hi, mult in KELLY_MULTIPLIERS:
        if lo <= dqs < hi:
            return mult
    return 0.0


_TABLES_READY = False


class ShadowDecisionEngine:
    """
    Shadow-mode engine that logs alternative decisions without affecting live trades.
    """

    def __init__(self, conn):
        self.conn = conn
        if not _TABLES_READY:
            self._ensure_table()

    def _ensure_table(self):
        """Create decision_events table if it doesn't exist."""
        global _TABLES_READY
        cur = self.conn.cursor()
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS decision_events (
                    id SERIAL PRIMARY KEY,
                    run_time TIMESTAMP NOT NULL DEFAULT NOW(),
                    pipeline_run_id TEXT,
                    symbol VARCHAR(16) NOT NULL,
                    scan_id UUID,

                    -- Live system decision
                    live_dqs NUMERIC(6,2),
                    live_side TEXT,
                    live_strategy TEXT,
                    live_session TEXT,
                    live_tech_raw NUMERIC(6,2),
                    live_struct_raw NUMERIC(6,2),
                    live_ctx_raw NUMERIC(6,2),
                    live_decision TEXT,

                    -- Shadow: empirical weights
                    shadow_dqs_empirical NUMERIC(6,2),
                    shadow_dqs_delta NUMERIC(6,2),

                    -- Shadow: Kelly sizing
                    shadow_kelly_mult NUMERIC(5,2),
                    shadow_kelly_vetoed BOOLEAN DEFAULT FALSE,

                    -- Shadow: crowding
                    shadow_crowded BOOLEAN DEFAULT FALSE,
                    shadow_crowd_penalty NUMERIC(6,2) DEFAULT 0,

                    -- Shadow: cascade veto
                    shadow_cascade_vetoed BOOLEAN DEFAULT FALSE,
                    shadow_cascade_reason TEXT,

                    -- Shadow: crowd veto (ls_ratio)
                    shadow_crowd_vetoed BOOLEAN DEFAULT FALSE,
                    shadow_crowd_ls_ratio NUMERIC(8,4),

                    -- Shadow: DQS 85+ veto
                    shadow_dqs85_vetoed BOOLEAN DEFAULT FALSE,

                    -- Shadow: combined decision
                    shadow_decision TEXT,
                    shadow_dqs_final NUMERIC(6,2),
                    shadow_position_mult NUMERIC(5,2),

                    -- Data quality
                    data_quality_score NUMERIC(6,2),
                    data_quality_gated BOOLEAN DEFAULT FALSE,

                    -- Full breakdown JSON
                    breakdown JSONB,

                    -- Resolution (filled on trade close)
                    actual_pnl NUMERIC(18,2),
                    actual_outcome BOOLEAN,
                    resolved_at TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS crds_state (
                    id SERIAL PRIMARY KEY,
                    run_time TIMESTAMP NOT NULL DEFAULT NOW(),
                    symbol VARCHAR(16) NOT NULL,
                    crowd_ls_ratio NUMERIC(8,4),
                    crowd_taker_buy NUMERIC(8,4),
                    crowd_vetoed BOOLEAN DEFAULT FALSE,
                    crowd_veto_reason TEXT,
                    cascade_count INT DEFAULT 0,
                    cascade_usd NUMERIC(18,2) DEFAULT 0,
                    cascade_vetoed BOOLEAN DEFAULT FALSE,
                    cascade_veto_reason TEXT,
                    crowded_symbols TEXT[],
                    UNIQUE (symbol, run_time)
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS drve_tournament_log (
                    id SERIAL PRIMARY KEY,
                    run_time TIMESTAMP NOT NULL DEFAULT NOW(),
                    pipeline_run_id TEXT,
                    candidate_a_symbol VARCHAR(16),
                    candidate_a_dqs NUMERIC(6,2),
                    candidate_b_symbol VARCHAR(16),
                    candidate_b_dqs NUMERIC(6,2),
                    verifier_prob NUMERIC(6,4),
                    tournament_winner TEXT,
                    dqs_winner TEXT,
                    override BOOLEAN DEFAULT FALSE,
                    features JSONB
                )
            """)
            self.conn.commit()
            _TABLES_READY = True
        except Exception as e:
            print(f"  [Shadow] Table creation error: {e}")
            self.conn.rollback()
        finally:
            cur.close()

    def compute_data_quality(self, symbol: str, enriched: pd.DataFrame) -> dict:
        """Compute per-symbol data quality score."""
        if enriched is None or len(enriched) == 0:
            return {"score": 0.0, "gated": True, "freshness": 0, "completeness": 0, "outlier": 0}

        # Freshness: check last bar timestamp
        if "timestamp" in enriched.columns:
            last_ts = pd.to_datetime(enriched["timestamp"].iloc[-1], utc=True)
            now = datetime.now(timezone.utc)
            staleness_min = (now - last_ts).total_seconds() / 60
            freshness = max(0, 100 - (staleness_min / 60 * 100))
        else:
            freshness = 100.0

        # Completeness: gap count in last 96 bars
        if "timestamp" in enriched.columns and len(enriched) >= 2:
            ts = pd.to_datetime(enriched["timestamp"], utc=True)
            gaps = ts.diff().dt.total_seconds() / 60
            gap_count = (gaps > 20).sum()
            completeness = max(0, 100 - gap_count * 5)
        else:
            completeness = 100.0

        # Outlier: bars where |return| > 5 sigma
        if "close" in enriched.columns and len(enriched) >= 20:
            rets = enriched["close"].pct_change()
            rolling_std = rets.rolling(96, min_periods=20).std()
            z = (rets - rets.rolling(96, min_periods=20).mean()) / rolling_std.replace(0, np.nan)
            outlier_count = (z.abs() > 5).sum()
            outlier_score = max(0, 100 - outlier_count * 10)
        else:
            outlier_score = 100.0

        score = float(freshness) * 0.40 + float(completeness) * 0.40 + float(outlier_score) * 0.20
        return {
            "score": round(score, 2),
            "gated": bool(score < 60),
            "freshness": round(float(freshness), 2),
            "completeness": round(float(completeness), 2),
            "outlier": round(float(outlier_score), 2),
        }

    def detect_crowding(self, signals: list) -> dict:
        """Detect crowded symbols from a list of signals at the same scan."""
        crowded = set()
        trackable = [s for s in signals if s.get("dqs", 0) >= 50]
        if len(trackable) < 2:
            return {"crowded_symbols": [], "detail": {}}

        for cluster in CORRELATION_CLUSTERS:
            cluster_sigs = [s for s in trackable if s["symbol"] in cluster["symbols"]]
            if len(cluster_sigs) < cluster["min_fire"]:
                continue

            for side in ["LONG", "SHORT"]:
                side_sigs = [s for s in cluster_sigs if s.get("side", s.get("direction", "LONG")) == side]
                if len(side_sigs) >= cluster["min_fire"]:
                    dqs_vals = [s.get("dqs", 0) for s in side_sigs]
                    dqs_range = max(dqs_vals) - min(dqs_vals) if dqs_vals else 0
                    if dqs_range <= 5:
                        for s in side_sigs:
                            crowded.add(s["symbol"])

        return {"crowded_symbols": list(crowded), "detail": {"cluster_size": len(crowded)}}

    def compute_empirical_dqs(self, tech_raw: float, struct_raw: float, ctx_raw: float,
                               pullback_bonus: float = 0, liquidity_bonus: float = 0,
                               combined_mult: float = 1.0, contra_penalty: float = 0,
                               regime_penalty: float = 0) -> float:
        """Recompute DQS with empirical weights (0.73/0.07/0.20)."""
        base = (tech_raw * EMPIRICAL_WEIGHTS["technical"]
                + struct_raw * EMPIRICAL_WEIGHTS["structure"]
                + ctx_raw * EMPIRICAL_WEIGHTS["context"])
        dqs = (base + pullback_bonus + liquidity_bonus) * combined_mult - contra_penalty - regime_penalty
        return max(0, min(100, dqs))

    def evaluate_signals(self, signals: list, run_id: str, now: datetime,
                         enriched_cache: dict = None) -> None:
        """
        Main entry point: evaluate all signals from a scan in shadow mode.
        Logs to decision_events table. Does NOT modify any signals or trades.
        """
        if not signals:
            return

        # Detect crowding across all signals at this scan
        crowding_result = self.detect_crowding(signals)
        crowded_syms = set(crowding_result["crowded_symbols"])

        cur = self.conn.cursor()
        logged = 0

        for sig in signals:
            try:
                symbol = sig.get("symbol", "")
                live_dqs = float(sig.get("dqs", 0))
                side = sig.get("side", sig.get("direction", "LONG"))
                strategy = sig.get("strategy", "trend_following")
                session = sig.get("session", "")
                scan_id = sig.get("scan_id")

                tech_raw = float(sig.get("technical", sig.get("tech_raw", 0)))
                struct_raw = float(sig.get("structure", sig.get("struct_raw", 0)))
                ctx_raw = float(sig.get("context", sig.get("ctx_raw", 0)))

                # Data quality
                dq = {"score": 100.0, "gated": False}
                if enriched_cache and symbol in enriched_cache:
                    dq = self.compute_data_quality(symbol, enriched_cache[symbol])

                # Empirical DQS
                pb = float(sig.get("pullback_bonus", 0))
                lb = float(sig.get("liquidity_bonus", 0))
                cm = float(sig.get("combined_mult", 1.0))
                cp = float(sig.get("contra_penalty", 0))
                rp = 0  # regime penalty not easily accessible
                emp_dqs = self.compute_empirical_dqs(tech_raw, struct_raw, ctx_raw, pb, lb, cm, cp, rp)
                dqs_delta = emp_dqs - live_dqs

                # Kelly sizing
                kelly_mult = _get_kelly_mult(live_dqs)
                if strategy == "mean_reversion":
                    kelly_mult *= 0.5
                kelly_vetoed = bool(live_dqs >= 85)

                # Crowding
                is_crowded = bool(symbol in crowded_syms)
                crowd_penalty = -10 if is_crowded else 0

                # DQS 85+ veto
                dqs85_vetoed = bool(live_dqs >= 85)

                # Cascade veto (would need liquidation data — log as False for now)
                cascade_vetoed = False
                cascade_reason = None

                # Crowd veto (would need ls_ratio — log as False for now)
                crowd_vetoed = False
                crowd_ls_ratio = None

                # Combined shadow decision
                shadow_vetoed = bool(dq["gated"] or cascade_vetoed or crowd_vetoed or dqs85_vetoed)
                shadow_dqs_final = float(live_dqs + crowd_penalty)
                shadow_position_mult = float(kelly_mult) if not shadow_vetoed else 0.0
                shadow_decision = "VETOED" if shadow_vetoed else ("REDUCED" if kelly_mult < 1.0 else ("BOOSTED" if kelly_mult > 1.0 else "SAME"))

                # Live decision
                live_decision = "APPROVED" if live_dqs >= 50 else "TRACK_ONLY"

                # Log to decision_events
                cur.execute("""
                    INSERT INTO decision_events (
                        run_time, pipeline_run_id, symbol, scan_id,
                        live_dqs, live_side, live_strategy, live_session,
                        live_tech_raw, live_struct_raw, live_ctx_raw, live_decision,
                        shadow_dqs_empirical, shadow_dqs_delta,
                        shadow_kelly_mult, shadow_kelly_vetoed,
                        shadow_crowded, shadow_crowd_penalty,
                        shadow_cascade_vetoed, shadow_cascade_reason,
                        shadow_crowd_vetoed, shadow_crowd_ls_ratio,
                        shadow_dqs85_vetoed,
                        shadow_decision, shadow_dqs_final, shadow_position_mult,
                        data_quality_score, data_quality_gated,
                        breakdown
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s,
                        %s, %s,
                        %s,
                        %s, %s, %s,
                        %s, %s,
                        %s
                    )
                """, (
                    now, run_id, symbol, scan_id,
                    live_dqs, side, strategy, session,
                    tech_raw, struct_raw, ctx_raw, live_decision,
                    round(emp_dqs, 2), round(dqs_delta, 2),
                    kelly_mult, kelly_vetoed,
                    is_crowded, crowd_penalty,
                    cascade_vetoed, cascade_reason,
                    crowd_vetoed, crowd_ls_ratio,
                    dqs85_vetoed,
                    shadow_decision, round(shadow_dqs_final, 2), shadow_position_mult,
                    dq["score"], dq["gated"],
                    json.dumps({
                        "empirical_weights": EMPIRICAL_WEIGHTS,
                        "current_weights": CURRENT_WEIGHTS,
                        "crowding": crowding_result,
                        "data_quality": dq,
                        "kelly": {"mult": kelly_mult, "vetoed": kelly_vetoed},
                    }, default=_json_default)
                ))
                logged += 1
            except Exception as e:
                self.conn.rollback()
                print(f"  [Shadow] Error logging {sig.get('symbol', '?')}: {e}")
                continue

        # Log crowding to crds_state
        if crowded_syms:
            try:
                cur.execute("""
                    INSERT INTO crds_state (run_time, symbol, crowded_symbols)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (symbol, run_time) DO NOTHING
                """, (now, "ALL", list(crowded_syms)))
            except Exception:
                self.conn.rollback()
                # Re-create cursor after rollback so main commit works
                cur = self.conn.cursor()

        self.conn.commit()
        cur.close()

        if logged > 0:
            print(f"  [Shadow] Logged {logged} decision_events (crowded: {len(crowded_syms)}, dqs85_veto: {sum(1 for s in signals if s.get('dqs',0)>=85)})")

    def log_tournament(self, run_id: str, candidates: list, now: datetime) -> None:
        """
        Log a pairwise tournament between top 2 candidates.
        In shadow mode — no actual pick change.
        """
        if len(candidates) < 2:
            return

        # Sort by DQS descending
        sorted_cands = sorted(candidates, key=lambda s: s.get("dqs", 0), reverse=True)
        cand_a = sorted_cands[0]  # DQS winner
        cand_b = sorted_cands[1]  # Challenger

        # Simple heuristic: if challenger has higher volume, flag as potential override
        vol_a = cand_a.get("volume", 0) or 0
        vol_b = cand_b.get("volume", 0) or 0
        vol_diff = vol_b - vol_a if vol_a > 0 else 0

        # Verifier probability (simplified — real model would use drve_b2_model_weights.json)
        # For now, just log the comparison
        verifier_prob = 0.5  # neutral — model weights not loaded in live pipeline
        override = False
        tournament_winner = cand_a["symbol"]  # default to DQS winner
        dqs_winner = cand_a["symbol"]

        if vol_diff > 0 and cand_b.get("dqs", 0) >= cand_a.get("dqs", 0) - 10:
            # Challenger has more volume and similar DQS — potential override
            verifier_prob = 0.55
            override = True
            tournament_winner = cand_b["symbol"]

        cur = self.conn.cursor()
        try:
            cur.execute("""
                INSERT INTO drve_tournament_log (
                    run_time, pipeline_run_id,
                    candidate_a_symbol, candidate_a_dqs,
                    candidate_b_symbol, candidate_b_dqs,
                    verifier_prob, tournament_winner, dqs_winner, override,
                    features
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                now, run_id,
                cand_a.get("symbol"), cand_a.get("dqs", 0),
                cand_b.get("symbol"), cand_b.get("dqs", 0),
                verifier_prob, tournament_winner, dqs_winner, override,
                json.dumps({"vol_diff": vol_diff, "vol_a": vol_a, "vol_b": vol_b}, default=_json_default)
            ))
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"  [Shadow] Tournament log error: {e}")
        finally:
            cur.close()

    def resolve_trade(self, symbol: str, actual_pnl: float, run_id: str = None,
                       trade_entry_time: datetime = None) -> None:
        """
        Called when a trade closes — resolves the shadow decision with actual outcome.
        This enables comparison: "would the shadow decision have been better?"

        If trade_entry_time is provided, only resolves events within 30 min of that time
        to avoid matching the wrong scan iteration.
        """
        outcome = actual_pnl > 0
        cur = self.conn.cursor()
        try:
            if run_id:
                cur.execute("""
                    UPDATE decision_events
                    SET actual_pnl = %s,
                        actual_outcome = %s,
                        resolved_at = NOW()
                    WHERE symbol = %s
                      AND resolved_at IS NULL
                      AND pipeline_run_id = %s
                """, (actual_pnl, outcome, symbol, run_id))
            elif trade_entry_time:
                # Match unresolved event closest to trade entry time (within 30 min)
                cur.execute("""
                    UPDATE decision_events
                    SET actual_pnl = %s,
                        actual_outcome = %s,
                        resolved_at = NOW()
                    WHERE id = (
                        SELECT id FROM decision_events
                        WHERE symbol = %s
                          AND resolved_at IS NULL
                          AND live_dqs >= 50
                          AND run_time BETWEEN %s - INTERVAL '30 minutes'
                                           AND %s + INTERVAL '30 minutes'
                        ORDER BY ABS(EXTRACT(EPOCH FROM (run_time - %s)))
                        LIMIT 1
                    )
                """, (actual_pnl, outcome, symbol,
                       trade_entry_time, trade_entry_time, trade_entry_time))
            else:
                # Fallback: match most recent unresolved APPROVED (DQS >= 50) decision for this symbol
                cur.execute("""
                    UPDATE decision_events
                    SET actual_pnl = %s,
                        actual_outcome = %s,
                        resolved_at = NOW()
                    WHERE id = (
                        SELECT id FROM decision_events
                        WHERE symbol = %s
                          AND resolved_at IS NULL
                          AND live_dqs >= 50
                        ORDER BY run_time DESC
                        LIMIT 1
                    )
                """, (actual_pnl, outcome, symbol))
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"  [Shadow] Resolve error: {e}")
        finally:
            cur.close()
