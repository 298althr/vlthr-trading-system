---
description: Decision engine workflow — decision intelligence generation, timing assessment, evidence breakdown, execution planning, exit intelligence, ABSTAIN gate
---

# Decision Engine Workflow

## When To Use
- Generating trading decisions with full intelligence (timing, evidence, execution, exit)
- Determining position side (LONG/SHORT/FLAT) with DQS + ABSTAIN gate
- Planning execution (position sizing, order type, scaling)
- Assessing exit conditions (TP/SL, time exit, signal decay, regime change)

## Decision Components

### 1. TimingAssessment (WHEN to buy)
- Volatility windows: low_vol for trend, high_vol for mean reversion
- Session quality scoring: US/EU/Asia session quality
- ADX-based timing: rising ADX = immediate, falling ADX = wait
- Urgency: immediate / normal / wait

### 2. EvidenceBreakdown (WHY buy)
- Technical: RSI, ADX, EMA alignment, ATR percentile
- Sentiment: crowd LS ratio, funding rate
- Structural: volume ratio, OI delta
- Risk: drawdown level, volatility regime
- Hypothesis alignment: confirmed hypothesis support
- Regime alignment: does current regime match strategy?

### 3. ExecutionPlan (HOW to buy)
- Position size multiplier with DQRAE V7 Kelly sizing:
  - DQS 85+: 0x (VETO — negative Kelly)
  - DQS 80-85: 2.5x (best bucket)
  - DQS 75-80: 1.5x
  - DQS 70-75: 1.0x
  - DQS 65-70: 0.7x
  - DQS <65: 0.3x
  - Mean reversion: 3.0x BOOST (fixes strategy concentration)
  - Symbol allocation cap: 25%, PnL cap: 30%
  - DOGEUSDT: DISABLED
- Order type: market (immediate) or limit (normal/wait)
- Scaling: single, scale_in_2, or scale_in_3

### 4. ExitIntelligence (WHEN to sell)
- TP/SL hit detection
- Time exit: max 12 hours then TIME_EXIT
- Signal decay: RSI overextension
- Regime change: ADX collapse for trend, ADX surge for MR
- Partial exits: 50% at first target, 75% at second

## Decision Generation Flow

1. **Load enriched data** and score with DQS
2. **Detect current regime** via scenario_engine
3. **Assess timing** (volatility window, session, ADX trend)
4. **Build evidence** (technical, sentiment, structural, risk, hypothesis)
5. **Plan execution** (Kelly sizing, order type, scaling)
6. **Assess exit** (existing position check)
7. **Determine side:**
   - If DQS < 50: FLAT
   - If DQS >= 50: LONG or SHORT based on strategy + log_ret + RSI
8. **Apply vetoes:**
   - Position size <= 0: FLAT (DQS 85+ veto or DQS < 40)
   - **ABSTAIN gate: FLAT if should_abstain(symbol, regime)**
9. **Compute opportunity score:**
   ```
   score = DQS * 0.50 + timing * 0.20 + evidence * 0.15 + hypothesis * 0.10 + liquidity * 0.05
   ```

## ABSTAIN Gate
```python
def should_abstain(symbol, regime):
    gate = load_abstain_gate()  # from abstain_gate.json
    cell = gate.get("per_symbol_regime", {}).get(f"{symbol}|{regime}")
    if cell and cell.get("abstain"):  # direction accuracy < 52%
        return True
    sym = gate.get("per_symbol", {}).get(symbol)
    if sym and sym.get("abstain"):
        return True
    return False
```

## Side Determination Logic
```python
if dqs >= 50:
    if strategy == "mean_reversion":
        if rsi >= overbought: side = "SHORT"
        elif rsi <= oversold: side = "LONG"
        else: side = "LONG" if log_ret > 0 else "SHORT"
    else:  # trend_following
        side = "LONG" if log_ret > 0 else "SHORT"
else:
    side = "FLAT"
```

## VLTHR V7 Results (GOLD STANDARD 88/100)
- 1,201 trades (filtered from 9,403 base decisions)
- $13,401 net PnL, 54% WR, 1.80 PF, MaxDD -$1,288
- Capital: $10,000, 4x leverage
- Trading window: 12 months (Jun 2025 – May 2026)
- 5 symbols active (DOGEUSDT disabled)
- Entry filter: calibrated_win_prob > 0.48
- V3 scorer: tech 0.00, struct 0.00, ctx 1.00

## Files
- `engine/v3/discovery/decision_engine.py` — core engine
- `engine/v3/discovery/decision_runner.py` — batch runner
- `Backtest-Engine/backtest_phase8_decision.py` — PnL replay
- `Backtest-Engine/institutional_validation_v7.py` — V7 institutional validation
- `Backtest-Engine/results/institutional_validation_v7.json` — V7 scorecard
