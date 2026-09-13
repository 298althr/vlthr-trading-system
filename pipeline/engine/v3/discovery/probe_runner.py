"""
V3 Probe Runner — Batch Execution & Feature Quality Scoring
=============================================================
Runs all probes across all symbols, detects redundant features,
and produces a ranked feature quality report.

Feature Quality Score (FQS) = 0.40 × |IC| + 0.25 × wilson_lower + 0.20 × uniqueness + 0.15 × stability

Where:
    - IC: Information Coefficient (predictive value)
    - wilson_lower: Statistical significance lower bound
    - uniqueness: 1 - max_correlation_with_other_features
    - stability: 1 - normalized IC std
"""
from __future__ import annotations

import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pandas as pd

from .probe_engine import (
    run_all_probes, ProbeResult, ALL_PROBES,
)
from .feature_registry import (
    FeatureRecord, upsert_feature, load_feature, load_all_features,
    evaluate_promotion, compute_drift_score, get_trusted_features,
    CANDIDATE, VALIDATED, CALIBRATED, TRUSTED, DEPRECATED,
)

# Ensure V2 enrichment is importable
_repo = Path(__file__).resolve().parents[5]
_engine = Path(__file__).resolve().parents[3]  # engine/
for p in [str(_repo / "data" / "bybit" / "workers"), str(_engine)]:
    if p not in sys.path:
        sys.path.insert(0, p)


def load_enriched_data(symbol: str, timeframe: str = "15m",
                       bars: int = 2000) -> pd.DataFrame:
    """Load enriched data for a symbol using V2's enrichment pipeline."""
    try:
        from bybit_enrich_core import run_symbol as _enrich
        df = _enrich(symbol, timeframe, bars=bars)
        if df is not None and len(df) > 0:
            return df
    except Exception:
        pass

    # Fallback: load raw OHLCV from parquet
    try:
        from ..ingestion.parquet_store import read_existing
    except ImportError:
        from ingestion.parquet_store import read_existing
    df = read_existing("ohlcv", symbol, timeframe)
    if df is not None and len(df) > 0:
        df = df.tail(bars).reset_index(drop=True)
    return df if df is not None else pd.DataFrame()


def load_btc_data(timeframe: str = "15m", bars: int = 2000) -> pd.DataFrame:
    """Load BTC data for correlation probes."""
    return load_enriched_data("BTCUSDT", timeframe, bars)


def compute_redundancy(results: List[ProbeResult]) -> Dict[str, float]:
    """
    Compute uniqueness score for each feature.
    uniqueness = 1 - max(|correlation|) with any other feature.
    Uses unique key per (feature_name, symbol) to avoid collisions.
    """
    if len(results) < 2:
        return {r.feature_name: 1.0 for r in results}

    # Align all feature series on the same index — use unique keys
    series_dict = {}
    key_map = {}  # unique_key -> feature_name
    for r in results:
        s = r.values.dropna()
        if len(s) > 50:
            key = f"{r.feature_name}::{r.symbol}"
            series_dict[key] = s
            key_map[key] = r.feature_name

    if len(series_dict) < 2:
        return {r.feature_name: 1.0 for r in results}

    # Build correlation matrix
    df = pd.DataFrame(series_dict)
    corr_matrix = df.corr(method="spearman").abs()

    # Compute uniqueness per unique key, then map back to feature_name
    # If same feature_name appears for multiple symbols, take the average uniqueness
    per_feature_uniqueness: Dict[str, List[float]] = {}
    for key in series_dict:
        others = corr_matrix[key].drop(key)
        max_corr = others.max() if len(others) > 0 else 0.0
        uniq = float(max(0.0, 1.0 - max_corr))
        fname = key_map[key]
        per_feature_uniqueness.setdefault(fname, []).append(uniq)

    uniqueness = {}
    for fname, vals in per_feature_uniqueness.items():
        uniqueness[fname] = float(np.mean(vals))

    return uniqueness


def compute_stability(results: List[ProbeResult]) -> Dict[str, float]:
    """Compute stability score: 1 - normalized IC std."""
    stability = {}
    for r in results:
        if r.ic_std > 0:
            stability[r.feature_name] = float(max(0.0, 1.0 - min(r.ic_std / max(abs(r.ic), 0.01), 1.0)))
        else:
            stability[r.feature_name] = 1.0
    return stability


def compute_fqs(ic: float, wilson_lower: float, uniqueness: float,
                stability: float) -> float:
    """Compute Feature Quality Score."""
    return float(
        0.40 * abs(ic) +
        0.25 * wilson_lower +
        0.20 * uniqueness +
        0.15 * stability
    )


def run_probe_cycle(symbols: List[str], timeframe: str = "15m",
                    bars: int = 2000, write_to_db: bool = True) -> Dict[str, Any]:
    """
    Run a complete probe cycle across all symbols.
    Returns a summary dict with all results and promotions/demotions.
    """
    print(f"[probe_runner] Starting probe cycle for {len(symbols)} symbols")
    print(f"[probe_runner] Timeframe: {timeframe}, Bars: {bars}")

    # Load BTC data once for correlation probes
    btc_df = None
    if "BTCUSDT" in symbols:
        btc_df = load_btc_data(timeframe, bars)
        if btc_df.empty:
            print("[probe_runner] WARNING: Could not load BTC data for correlation probes")

    all_results: List[ProbeResult] = []
    per_symbol: Dict[str, List[ProbeResult]] = {}
    errors: List[str] = []

    for symbol in symbols:
        print(f"[probe_runner] Loading data for {symbol}...")
        df = load_enriched_data(symbol, timeframe, bars)
        if df is None or df.empty:
            msg = f"No data for {symbol}"
            print(f"[probe_runner] {msg}")
            errors.append(msg)
            continue

        print(f"[probe_runner] Running probes for {symbol} ({len(df)} bars)...")
        results = run_all_probes(df, symbol, btc_df=btc_df if symbol != "BTCUSDT" else None)
        per_symbol[symbol] = results
        all_results.extend(results)
        print(f"[probe_runner] {symbol}: {len(results)} features probed")

    if not all_results:
        return {"status": "no_data", "errors": errors}

    # Compute redundancy and stability
    print("[probe_runner] Computing redundancy and stability...")
    uniqueness = compute_redundancy(all_results)
    stability = compute_stability(all_results)

    # Build feature records and evaluate promotions
    promotions = []
    demotions = []
    new_records = []
    fqs_scores = []

    for r in all_results:
        uniq = uniqueness.get(r.feature_name, 1.0)
        stab = stability.get(r.feature_name, 1.0)
        fqs = compute_fqs(r.ic, r.wilson_lower, uniq, stab)
        fqs_scores.append((r.feature_name, r.symbol, fqs, r.ic, r.wilson_lower, uniq, stab))

        # Check for existing record to compute drift
        existing = load_feature(r.feature_name) if write_to_db else None
        drift = 0.0
        if existing and abs(existing.ic) > 0.001:
            drift = compute_drift_score(r.ic, existing.ic)

        # Build record
        record = FeatureRecord(
            feature_name=r.feature_name,
            lifecycle_state=existing.lifecycle_state if existing else CANDIDATE,
            ic=r.ic,
            ic_std=r.ic_std,
            wilson_lower=r.wilson_lower,
            sample_size=r.sample_size,
            drift_score=drift,
            probe_type=r.probe_type,
            input_features=[r.probe_type],
            output_type="continuous",
            metadata={**r.metadata, "fqs": fqs, "uniqueness": uniq, "stability": stab},
            consecutive_failures=existing.consecutive_failures if existing else 0,
        )

        # Evaluate promotion/demotion
        new_state, reason = evaluate_promotion(record)
        old_state = record.lifecycle_state
        record.lifecycle_state = new_state

        if new_state != old_state:
            if new_state == DEPRECATED:
                demotions.append({
                    "feature": r.feature_name, "from": old_state,
                    "to": new_state, "reason": reason,
                })
                record.deprecated_at = datetime.now(timezone.utc).isoformat()
            else:
                promotions.append({
                    "feature": r.feature_name, "from": old_state,
                    "to": new_state, "reason": reason,
                })

        if write_to_db:
            upsert_feature(record)
        new_records.append(record)

    # Sort by FQS descending
    fqs_scores.sort(key=lambda x: x[2], reverse=True)

    # Summary
    summary = {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbols": symbols,
        "timeframe": timeframe,
        "total_features": len(all_results),
        "total_probes": len(ALL_PROBES),
        "promotions": promotions,
        "demotions": demotions,
        "errors": errors,
        "top_features": [
            {
                "feature": name, "symbol": sym, "fqs": round(fqs, 4),
                "ic": round(ic, 4), "wilson_lower": round(wl, 4),
                "uniqueness": round(u, 4), "stability": round(s, 4),
            }
            for name, sym, fqs, ic, wl, u, s in fqs_scores[:20]
        ],
        "state_distribution": {},
    }

    # State distribution
    for record in new_records:
        state = record.lifecycle_state
        summary["state_distribution"][state] = summary["state_distribution"].get(state, 0) + 1

    print(f"[probe_runner] Cycle complete: {len(all_results)} features, "
          f"{len(promotions)} promotions, {len(demotions)} demotions")

    return summary


def main():
    """CLI entry point for the probe runner."""
    parser = argparse.ArgumentParser(description="V3 Feature Discovery Probe Runner")
    parser.add_argument("--symbols", nargs="+",
                        default=["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"],
                        help="Symbols to probe")
    parser.add_argument("--timeframe", default="15m", help="Timeframe to use")
    parser.add_argument("--bars", type=int, default=2000, help="Number of bars to analyze")
    parser.add_argument("--no-db", action="store_true", help="Skip DB writes")
    parser.add_argument("--json", action="store_true", help="Output JSON summary")
    args = parser.parse_args()

    summary = run_probe_cycle(
        symbols=args.symbols,
        timeframe=args.timeframe,
        bars=args.bars,
        write_to_db=not args.no_db,
    )

    if args.json:
        print(json.dumps(summary, indent=2, default=str))
    else:
        print(f"\n{'='*70}")
        print(f"  V3 PROBE CYCLE SUMMARY")
        print(f"{'='*70}")
        print(f"  Symbols:     {', '.join(args.symbols)}")
        print(f"  Timeframe:   {args.timeframe}")
        print(f"  Total features probed: {summary.get('total_features', 0)}")
        print(f"  Promotions:  {len(summary.get('promotions', []))}")
        print(f"  Demotions:   {len(summary.get('demotions', []))}")
        print(f"  Errors:      {len(summary.get('errors', []))}")
        print(f"\n  State Distribution:")
        for state, count in summary.get("state_distribution", {}).items():
            print(f"    {state:15s}: {count}")
        print(f"\n  Top 10 Features by FQS:")
        for i, f in enumerate(summary.get("top_features", [])[:10]):
            print(f"    {i+1:2d}. {f['feature']:30s} FQS={f['fqs']:.4f}  IC={f['ic']:.4f}  WL={f['wilson_lower']:.4f}")
        if summary.get("promotions"):
            print(f"\n  Promotions:")
            for p in summary["promotions"]:
                print(f"    {p['feature']:30s} {p['from']} -> {p['to']}: {p['reason']}")
        if summary.get("demotions"):
            print(f"\n  Demotions:")
            for d in summary["demotions"]:
                print(f"    {d['feature']:30s} {d['from']} -> {d['to']}: {d['reason']}")
        print(f"\n{'='*70}")


if __name__ == "__main__":
    main()
