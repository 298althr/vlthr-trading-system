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

### Confirmed Edges
- Crowd LS ratio contrarian (AUC 0.538, OOS 0.520) — when crowd is long-heavy, price tends to go short
- DQS 80-85 bucket has best Kelly — 2.5x position size justified
- DQS 85+ has negative Kelly — veto justified
- Mean reversion in sideways regime (53.7% direction accuracy)
- Risk drawdown 96 has highest |IC| (-0.24, contrarian)

### Disconfirmed (No Edge)
- ctx_log_ret_4h (IC was 0.44 due to leakage, actually 0.07)
- DQS as direction predictor (48.9% accuracy, below chance)
- BTC lead-lag to altcoins (P=0.443, near chance)
- Funding rate extremes as reversal signal (P=0.523, weak)
- Volume surge momentum (P=0.400, slight mean reversion)

### Open Questions
- Can an ensemble of contrarian features (risk_drawdown_96 + trend_ret_96 + regime_trend_dir) achieve > 52% direction accuracy?
- Is there edge in orderbook/aggTrade data (currently NaN, need P1 data)?
- Does the system work in bear markets (untested)?

## Files
- `backtestsystem/SKILLS.md` — full skills reference
- `backtestsystem/BACKTEST_FRAMEWORK.md` — framework docs
- `Backtest-Engine/results/` — all research output JSONs
