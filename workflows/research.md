---
description: Research workflow — systematic research methodology for discovering, evaluating, and validating trading edges and features
---

# Research Workflow

## When To Use
- Researching new trading strategies or features
- Evaluating academic papers or open-source strategies for adaptation
- Conducting literature reviews on quantitative finance methods
- Discovering new data sources or feature engineering approaches

## Steps

1. **Define research question**
   - What specific edge are you looking for? (direction, timing, sizing, risk)
   - What is the null hypothesis? (e.g., "feature X has no predictive power")
   - What would falsify your hypothesis?

2. **Literature & source review**
   - Academic: SSRN, arXiv (quantitative finance)
   - Open-source: GitHub (freqtrade, backtrader, QuantConnect/Lean)
   - Trading forums: QuantConnect, Forex Factory
   - Books: Lopez de Prado "Advances in Financial Machine Learning", Ernie Chan "Algorithmic Trading"

3. **Identify candidate strategies/features**
   - What market regime does this work in?
   - What timeframe is optimal?
   - What are the key parameters?
   - What are the failure modes?

4. **Implement in Python**
   - Start with minimal implementation (no optimization)
   - Use existing data infrastructure (`load_enriched`, `data/bybit/`)
   - Follow the probe engine pattern for feature evaluation

5. **Run probe analysis**
   - Compute IC (Information Coefficient) against forward returns
   - Check Wilson lower bound for significance
   - Test IS/OOS persistence
   - Check for label leakage (correlate feature with forward returns — should be ~0 before any predictive logic)

6. **Backtest with realistic costs**
   - Include fees, slippage, funding
   - Use 3-scenario cost model (normal, 2x, maker)
   - Run IS/OOS walk-forward

7. **Statistical validation**
   - Bootstrap p-value
   - Deflated Sharpe ratio (correct for multiple trials)
   - Cross-asset consistency (meta detection)
   - Decoupler stress test

8. **Document findings**
   - What works, what doesn't, and why
   - Under what conditions does the edge exist?
   - What are the risks and failure modes?
   - Output: JSON results + markdown summary

## VLTHR Research History

### V7 Confirmed Edges (GOLD STANDARD 88/100)
- Isotonic calibration entry filter (`calibrated_win_prob > 0.48`) — filters 87% of base decisions, achieves 54% WR on 1,201 trades
- V3 adaptive scorer ctx-only (tech/struct zeroed out — IC was negative for both)
- DQS 80-85 bucket has best Kelly — 2.5x position size justified
- DQS 85+ has negative Kelly — veto justified
- Crowd LS ratio contrarian (AUC 0.538, OOS 0.520) — veto at > 3.0 improves Sharpe by 10%
- Mean reversion 3.0x BOOST — fixes strategy concentration (TF 52.1% / MR 47.9% PnL balance)
- Direction accuracy: 71.9% on V7 filtered trades (up from 48.9% pre-V7)

### V2 Disconfirmed (No Edge — Pre-V7, Deprecated)
- ctx_log_ret_4h (IC was 0.44 due to leakage, actually 0.07)
- DQS as standalone direction predictor (48.9% accuracy pre-filtering — V7 filtering solves this)
- BTC lead-lag to altcoins (P=0.443, near chance)
- Funding rate extremes as reversal signal (P=0.523, weak)
- Volume surge momentum (P=0.400, slight mean reversion)

### Open Questions
- Is there edge in orderbook/aggTrade data (currently NaN, need P1 data)?
- Does the system work in bear markets (untested)?
- Can robustness pillar improve from 11/15 (WF decay at 75% split = 45.7%)?
- Can calibration pillar improve from 8/10 (Brier 0.247 > 0.20 target)?

## Files
- `backtestsystem/SKILLS.md` — full skills reference
- `backtestsystem/BACKTEST_FRAMEWORK.md` — framework docs
- `Backtest-Engine/results/` — all research output JSONs
