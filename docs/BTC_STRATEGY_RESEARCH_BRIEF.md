# BTC Strategy Research Brief

## Purpose

Request research into a BTC-specific trading strategy as a new ensemble leg. Current BTC performance under the shared VLTHR strategy is poor. We need a dedicated approach for BTC before adding similar symbol-specific legs for other underperforming symbols.

## Current System Overview

VLTHR is a multi-symbol crypto trading pipeline. It scans 6 symbols (BTC, ETH, SOL, XRP, BNB, DOGE) on 15m bars using a shared strategy framework with two modes:
- **Trend following** (TF): enters in direction of momentum, uses ADX and log returns
- **Mean reversion** (MR): enters against short-term extremes, uses RSI oversold/overbought

The pipeline applies a Dynamic Quality Score (DQS) that combines technical, structure, and context domains with bonuses and multipliers. Signals pass through 12 gates before execution.

## BTC Performance Problem

### Live Paper Trading Results (Jul 15-24, 2026)

| Metric | BTC LONG | BTC SHORT | All BTC |
|--------|----------|-----------|---------|
| Trades | 7 | 6 | 13 |
| Wins | 2 | 2 | 4 |
| Win rate | 28.6% | 33.3% | 30.8% |
| Net PnL | -$544.39 | -$19.07 | -$563.46 |
| Avg PnL/trade | -$77.77 | -$3.18 | -$43.34 |
| SL hits | 4 | 3 | 7 |
| TP hits | 2 | 2 | 3 |

BTC LONGs are the primary bleed: 7 trades, 28.6% win rate, -$544 net. Three consecutive SL hits on Jul 22-24 triggered the automated symbol disable.

### 1-Year Backtest Results (Jul 2025 - Jul 2026)

- BTCUSDT x MIXED regime: PF 1.32, 187 trades. Worst symbol-regime combo in the entire backtest.
- BTCUSDT x TRENDING: PF 2.10, 89 trades. Acceptable but below portfolio average.
- Overall portfolio PF: 2.20. BTC drags this down.
- SHORT signals across all symbols: PF 7.27 (only 16 trades vs 878 LONGs). SHORTs are underutilized but highly profitable.
- Mean reversion dominates: PF 4.1 vs TF 1.87.

### Key Observations

1. BTC LONGs in MIXED regime consistently lose. The strategy enters LONG on BTC when momentum is ambiguous, and BTC's lower volatility relative to smaller caps means the SL distance is tight but TP targets are too far.
2. BTC SHORTs perform better than BTC LONGs (-$19 vs -$544) but are rarely generated. The current RSI-based mean reversion logic produces mostly LONG entries for BTC.
3. BTC has the highest symbol multiplier (1.42x) in the DQS system, which inflates scores and pushes more BTC signals past the execution threshold. This may be counterproductive when the underlying signal quality is poor.
4. BTC's 4h log returns and ADX readings in MIXED regime produce frequent false positives for trend_following entries.

## What We Need Researched

### 1. BTC-specific entry conditions
- Should BTC use different indicators or timeframes than the rest of the portfolio?
- Is a shorter timeframe (5m or 1m) better for BTC given its lower per-bar volatility?
- Should BTC use a volume-profile or order-flow-based entry rather than RSI/ADX?

### 2. Side bias
- Should BTC be restricted to SHORT-only or SHORT-favored given that LONGs lose 3.5x more?
- What market structure conditions justify BTC LONGs? (e.g., only in confirmed TRENDING regime with ADX > 30?)
- Is there a regime filter that cleanly separates winning BTC LONGs from losing ones?

### 3. DQS multiplier adjustment
- The current 1.42x symbol multiplier for BTC inflates marginal signals past the execution threshold. Should this be lowered?
- Should BTC have a higher DQS execution threshold (e.g., 60 instead of 50) to filter out weak signals?

### 4. SL/TP optimization for BTC
- BTC's current SL multiplier is 1.5x ATR and TP multiplier is 3.0x ATR. Is this appropriate for BTC's volatility profile?
- Should BTC use a wider SL (2.0x ATR) to avoid premature stop-outs, or a tighter TP (2.0x ATR) to improve hit rate?
- The backtest showed TP hit rate of 29.9% across all symbols (below the 40% gate). Is this worse for BTC specifically?

### 5. Regime gating
- Should BTC be locked to trend_following only, with mean_reversion disabled?
- Should BTC trading be paused entirely in MIXED regime given PF 1.32?
- What regime detection method would better classify BTC-specific market states?

## Data Available for Research

- 1-year backtest CSV: 34,731 signals, 894 closed trades, 104 columns including DQS breakdown, regime, strategy, RSI, ADX, ATR, pattern detection columns
- Live paper trading database: 43 closed trades across 5 symbols with entry/exit prices, PnL, exit reasons
- Bybit demo execution data: parallel execution on Bybit demo account with real fills
- Calibration curves: per-symbol win probability estimates by DQS bucket

## Success Criteria for BTC Strategy

- PF > 2.0 on BTC-only backtest (currently 1.32 in MIXED, 2.10 in TRENDING)
- Win rate > 35% (currently 30.8%)
- Max drawdown < 5% on BTC-only leg
- At least 50 trades in the backtest for statistical significance
- Clear regime filter that distinguishes when to trade vs when to sit out

## Next Steps

1. Consultants research and propose BTC-specific strategy parameters
2. We backtest the proposed strategy on existing 1-year data
3. If backtest passes gates, implement as a BTC-only ensemble leg in the pipeline
4. Repeat the process for other symbols (XRP, SOL) if the BTC leg succeeds

## Constraints

- Must work within the existing pipeline architecture (Python, 15m bars, Bybit V5 API)
- Cannot introduce look-ahead bias in any signal generation
- Must be backtestable using the existing backtest runner
- Risk per trade cannot exceed 7.5% of account equity
- Max leverage: 4x
