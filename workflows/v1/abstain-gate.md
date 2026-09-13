---
description: ABSTAIN gate workflow — calibrate direction accuracy per (symbol, regime) and block trading where accuracy < 52%
---

# ABSTAIN Gate Workflow

## When To Use
- Building or updating the ABSTAIN gate configuration
- Blocking trading in regimes/symbols where the system has no directional edge
- Re-calibrating after leakage fixes or strategy changes
- Determining which (symbol, regime) cells are safe for trading

## Philosophy
If the system's direction accuracy is below 52% (barely above chance) in a given (symbol, regime) cell, it should NOT trade in that cell. The ABSTAIN gate enforces this.

## Steps

1. **Run full replay** to generate decision data with `optimal_side` labels
   ```
   python replay_engine.py --symbols ALL --output results/replay_data.parquet
   ```

2. **Extract side and regime from replay data**
   ```python
   df["side"] = df["decision"].apply(lambda d: d.get("side", "FLAT"))
   df["regime"] = df["scenario"].apply(lambda d: d.get("regime", "normal"))
   ```

3. **Filter to active trades** (non-FLAT)
   ```python
   active = df[df["side"] != "FLAT"].copy()
   active["dir_correct"] = (active["side"] == active["optimal_side"]).astype(int)
   ```

4. **Compute accuracy per (symbol, regime) cell**
   ```python
   for sym in symbols:
       for regime in regimes:
           cell = active[(active["symbol"] == sym) & (active["regime"] == regime)]
           n = len(cell)
           acc = cell["dir_correct"].mean() if n > 0 else 0
           abstain = bool(acc < 0.52)
           cell_stats[f"{sym}|{regime}"] = {"n": int(n), "accuracy": float(acc), "abstain": abstain}
   ```

5. **Compute per-symbol and per-regime aggregates**
   ```python
   # Per-symbol
   sym_stats[sym] = {"n": int(n), "accuracy": float(acc), "abstain": bool(acc < 0.52)}
   # Per-regime
   regime_stats[reg] = {"n": int(n), "accuracy": float(acc), "abstain": bool(acc < 0.52)}
   ```

6. **Export to JSON**
   ```python
   gate = {
       "per_symbol": sym_stats,
       "per_regime": regime_stats,
       "per_symbol_regime": cell_stats,
       "threshold": 0.52,
       "total_active": int(len(active)),
       "overall_accuracy": float(active["dir_correct"].mean())
   }
   json.dump(gate, open("results/abstain_gate.json", "w"), indent=2)
   ```

7. **Integrate into decision pipeline**
   - V3 `decision_engine.py`: `should_abstain(symbol, regime)` after DQS veto
   - V2 `portfolio_orchestrator.py`: `_should_abstain(symbol, regime)` after V2 filters
   - V3 `orchestrator.py`: inline check in shadow mode

## V7 ABSTAIN Gate Status

In V7, the ABSTAIN gate is superseded by the isotonic calibration entry filter (`calibrated_win_prob > 0.48`), which filters 87% of base decisions. The combined effect of calibration + DQS 85+ veto + crowd LS veto + DOGEUSDT disable achieves 54% WR on the remaining 1,201 trades.

The original per-(symbol, regime) ABSTAIN gate (below) is still in the codebase but the V7 entry filter is the primary gate.

## VLTHR Calibration Results

**Per-symbol:**
- BTCUSDT: 47.2% [ABSTAIN]
- ETHUSDT: 50.1% [ABSTAIN]
- SOLUSDT: 52.0% [OK]
- XRPUSDT: 45.1% [ABSTAIN]
- BNBUSDT: 49.7% [ABSTAIN]
- DOGEUSDT: 47.2% [ABSTAIN]

**Per-regime:**
- sideways: 53.7% [OK]
- crisis: 53.8% [OK]
- liquidity_shock: 51.3% [ABSTAIN]
- normal: 49.3% [ABSTAIN]
- bull_trend: 48.2% [ABSTAIN]
- bear_trend: 47.3% [ABSTAIN]
- low_vol: 41.7% [ABSTAIN]
- high_vol: 44.3% [ABSTAIN]

**31 of 42 cells ABSTAIN.** Only SOLUSDT + sideways/crisis pass.

## Regime Detection (V2 Lightweight)
V2 pipeline doesn't have full scenario_engine. Uses:
- ADX > 25 + bull_4h -> bull_trend
- ADX > 25 + !bull_4h -> bear_trend
- ADX < 20 -> sideways
- else -> normal

## Recalibration Triggers
- After label leakage fixes (re-run replay, rebuild gate)
- After strategy parameter changes
- After new data sources added
- Monthly as part of calibration schedule

## Files
- `Backtest-Engine/build_abstain_gate.py` — calibration script
- `Backtest-Engine/results/abstain_gate.json` — gate config
- `Backtest-Engine/institutional_validation_v7.py` — V7 validation (primary gate)
- `engine/v3/discovery/decision_engine.py` — `should_abstain()` + check
- `engine/portfolio_orchestrator.py` — `_should_abstain()` + check
- `engine/v3/orchestrator.py` — inline check
