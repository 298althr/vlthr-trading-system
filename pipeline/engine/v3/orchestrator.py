"""
V3 Orchestrator — Shadow Mode
==============================
Main V3 pipeline loop that runs alongside V2.

Each 15-minute cycle:
    1. Load evidence for all symbols (via evidence_store)
    2. Score each symbol (placeholder — V3 scoring comes in Phase 6)
    3. Log shadow decision to decision_lineage
    4. Compare with V2 decision if available

This is SHADOW MODE ONLY — no trades are executed.
V2 remains the live trading pipeline.
"""
import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

# Ensure imports resolve
_engine = Path(__file__).resolve().parents[1]
_v3_root = Path(__file__).resolve().parent
try:
    _repo = _engine.parents[2]
except IndexError:
    _repo = _engine.parent

sys.path.insert(0, str(_engine))
sys.path.insert(0, str(_v3_root))
sys.path.insert(0, str(_v3_root / "core"))
sys.path.insert(0, str(_repo / "departments" / "strategy_research" / "BACKTESTER" / "scripts" / "common"))
sys.path.insert(0, str(_repo / "departments" / "strategy_research" / "BACKTESTER" / "strategy" / "signals"))

# Load .env
_env = _repo / ".env"
if _env.exists():
    with open(_env) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v)

from source_registry import register_sources, get_all_sources
from evidence_store import load_evidence, write_lineage, create_evidence_snapshot

# Phase 5: Feature Discovery
try:
    from discovery.probe_runner import run_probe_cycle
    from discovery.feature_registry import get_trusted_features, get_active_features
    _probe_available = True
except ImportError:
    _probe_available = False
    print("[V3 Shadow] WARNING: probe_engine not available, feature discovery disabled")

# Phase 6: Hypothesis Generation
try:
    from discovery.hypothesis_runner import run_hypothesis_cycle
    from discovery.hypothesis_engine import get_confirmed_hypotheses, CONFIRMED
    _hypothesis_available = True
except ImportError:
    _hypothesis_available = False
    print("[V3 Shadow] WARNING: hypothesis_engine not available, hypothesis testing disabled")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]


def run_v3_shadow_iteration(now: Optional[datetime] = None,
                            run_probes: bool = False,
                            run_hypotheses: bool = False) -> dict:
    """
    Run one V3 shadow mode iteration.

    Args:
        now: Override timestamp (for testing)
        run_probes: If True, run feature discovery probe cycle before scoring
        run_hypotheses: If True, run hypothesis testing cycle before scoring

    Returns:
        dict with status, signals_scanned, shadow_decisions, lineage_events
    """
    now = now or datetime.now(timezone.utc)
    run_id = f"v3_{now.strftime('%Y%m%d_%H%M%S')}"

    print(f"\n[V3 Shadow] === Run {run_id} ===")

    # Ensure sources are registered
    try:
        register_sources()
    except Exception as e:
        print(f"[V3 Shadow] Source registration failed: {e}")

    # Phase 5: Run feature discovery probes if requested
    probe_summary = None
    trusted_features = []
    if run_probes and _probe_available:
        print("[V3 Shadow] Running feature discovery probes...")
        try:
            probe_summary = run_probe_cycle(
                symbols=SYMBOLS, timeframe="15m", bars=2000, write_to_db=True,
            )
            print(f"[V3 Shadow] Probes: {probe_summary.get('total_features', 0)} features, "
                  f"{len(probe_summary.get('promotions', []))} promotions")
        except Exception as e:
            print(f"[V3 Shadow] Probe cycle failed: {e}")
            probe_summary = {"status": "error", "error": str(e)}

    # Load trusted features for scoring context
    if _probe_available:
        try:
            trusted_features = get_trusted_features()
            if trusted_features:
                print(f"[V3 Shadow] {len(trusted_features)} trusted features loaded")
        except Exception:
            pass

    # Phase 6: Run hypothesis testing if requested
    hypothesis_summary = None
    confirmed_hypotheses = []
    if run_hypotheses and _hypothesis_available:
        print("[V3 Shadow] Running hypothesis testing...")
        try:
            hypothesis_summary = run_hypothesis_cycle(
                symbols=SYMBOLS, timeframe="15m", bars=2000, write_to_db=True,
            )
            print(f"[V3 Shadow] Hypotheses: {hypothesis_summary.get('total_hypotheses', 0)} tested, "
                  f"{len(hypothesis_summary.get('promotions', []))} confirmed, "
                  f"{len(hypothesis_summary.get('refutations', []))} refuted")
        except Exception as e:
            print(f"[V3 Shadow] Hypothesis cycle failed: {e}")
            hypothesis_summary = {"status": "error", "error": str(e)}

    # Load confirmed hypotheses for scoring context
    if _hypothesis_available:
        try:
            confirmed_hypotheses = get_confirmed_hypotheses()
            if confirmed_hypotheses:
                print(f"[V3 Shadow] {len(confirmed_hypotheses)} confirmed hypotheses loaded")
        except Exception:
            pass

    # Load evidence for all symbols
    evidence_map = {}
    errors = []

    for symbol in SYMBOLS:
        try:
            ev = load_evidence(symbol, "15m")
            if ev:
                evidence_map[symbol] = ev
                # Write market_event to lineage
                snap = create_evidence_snapshot(ev)
                lineage_id = write_lineage("market_event", snap)
                ev["lineage_id"] = lineage_id or ev["lineage_id"]
                print(f"  {symbol}: {len(ev['df'])} bars, lineage={lineage_id}")
            else:
                print(f"  {symbol}: no evidence")
                errors.append(f"{symbol}: no evidence loaded")
        except Exception as e:
            print(f"  {symbol}: ERROR — {e}")
            errors.append(f"{symbol}: {e}")

    # Score each symbol (placeholder — Phase 6 will implement V3 scoring)
    shadow_decisions = []
    for symbol, ev in evidence_map.items():
        df = ev["df"]
        last_bar = df.iloc[-1]

        # Placeholder: simple DQS proxy from V2 confidence_engine
        # Phase 6 will replace this with opportunity_score + hierarchical confidence
        try:
            from confidence_engine import score_signal
            dqs, breakdown = score_signal(symbol, df, None, last_bar.get("session", "UNKNOWN"))
        except Exception:
            dqs = 0
            breakdown = {}

        # Side determined by 4h log return (same as V2 portfolio_orchestrator)
        log_ret = float(last_bar.get("ctx_log_ret_4h", 0)) if not pd.isna(last_bar.get("ctx_log_ret_4h")) else 0.0
        if dqs >= 50:
            side = "LONG" if log_ret > 0 else "SHORT"
        else:
            side = "FLAT"

        decision = {
            "symbol": symbol,
            "dqs": dqs,
            "side": side,
            "sources": ev["sources"],
            "lineage_id": ev["lineage_id"],
            "shadow_mode": True,
            "timestamp": now.isoformat(),
            "trusted_feature_count": len(trusted_features),
            "confirmed_hypothesis_count": len(confirmed_hypotheses),
        }
        shadow_decisions.append(decision)

        # Write signal event to lineage
        write_lineage("signal", {
            "run_id": run_id,
            "symbol": symbol,
            "dqs": dqs,
            "breakdown": breakdown,
            "sources": ev["sources"],
            "shadow_mode": True,
        }, parent_id=ev["lineage_id"])

    # Summary
    result = {
        "status": "OK",
        "run_id": run_id,
        "timestamp": now.isoformat(),
        "signals_scanned": len(evidence_map),
        "shadow_decisions": len(shadow_decisions),
        "lineage_events": len(evidence_map) + len(shadow_decisions),
        "errors": errors,
        "decisions": shadow_decisions,
        "probe_summary": probe_summary,
        "trusted_features": len(trusted_features),
        "hypothesis_summary": hypothesis_summary,
        "confirmed_hypotheses": len(confirmed_hypotheses),
    }

    print(f"[V3 Shadow] Scanned={len(evidence_map)} Decisions={len(shadow_decisions)} Errors={len(errors)}")
    return result


def run_v3_shadow_loop(interval_sec: int = 900, probe_interval: int = 4,
                        hypothesis_interval: int = 4):
    """
    Run V3 shadow mode continuously.

    Args:
        interval_sec: Main loop interval in seconds (default 900 = 15min)
        probe_interval: Run probes every N iterations (default 4 = once per hour)
        hypothesis_interval: Run hypotheses every N iterations (default 4 = once per hour)
    """
    print(f"[V3 Shadow] Loop mode started. Interval: {interval_sec}s, "
          f"probe every {probe_interval} cycles, hypothesis every {hypothesis_interval} cycles")
    iteration = 0
    while True:
        try:
            run_probes = (iteration % probe_interval == 0) and _probe_available
            run_hyps = (iteration % hypothesis_interval == 0) and _hypothesis_available
            result = run_v3_shadow_iteration(run_probes=run_probes, run_hypotheses=run_hyps)
        except Exception as e:
            print(f"[V3 Shadow] Iteration error: {e}")
        iteration += 1
        print(f"[V3 Shadow] Sleeping {interval_sec}s...")
        time.sleep(interval_sec)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="V3 Shadow Mode Pipeline")
    parser.add_argument("--loop", action="store_true", help="Run in continuous loop")
    parser.add_argument("--interval", type=int, default=900, help="Loop interval in seconds")
    parser.add_argument("--probe", action="store_true", help="Run feature discovery probes")
    parser.add_argument("--probe-interval", type=int, default=4, help="Run probes every N cycles")
    parser.add_argument("--hypothesis", action="store_true", help="Run hypothesis testing")
    parser.add_argument("--hypothesis-interval", type=int, default=4, help="Run hypotheses every N cycles")
    args = parser.parse_args()

    if args.loop:
        run_v3_shadow_loop(args.interval, probe_interval=args.probe_interval,
                           hypothesis_interval=args.hypothesis_interval)
    else:
        result = run_v3_shadow_iteration(run_probes=args.probe, run_hypotheses=args.hypothesis)
        print(f"\nResult: {json.dumps(result, indent=2, default=str)}")
