---
description: Performance metrics workflow — calculate and report comprehensive trading performance metrics
---

# Performance Metrics Workflow

## When To Use
- Calculating backtest or live performance metrics
- Generating performance reports for strategy comparison
- Computing per-symbol, per-regime, per-session breakdowns

## Core Metrics

```python
def calculate_metrics(equity_curve, trades):
    equity = np.array(equity_curve)
    
    # Total return
    total_return = (equity[-1] - equity[0]) / equity[0]
    
    # Max drawdown
    peak = equity[0]
    max_dd = 0
    for val in equity:
        if val > peak: peak = val
        dd = (peak - val) / peak
        if dd > max_dd: max_dd = dd
    
    # Sharpe ratio
    returns = np.diff(equity) / equity[:-1]
    sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0
    
    # Win rate
    winning = [t for t in trades if t['pnl'] > 0]
    win_rate = len(winning) / len(trades) if trades else 0
    
    # Profit factor
    gross_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    return {
        'total_return': total_return,
        'max_drawdown': max_dd,
        'sharpe_ratio': sharpe,
        'win_rate': win_rate,
        'profit_factor': pf,
        'total_trades': len(trades),
        'avg_pnl_per_trade': np.mean([t['pnl'] for t in trades]) if trades else 0
    }
```

## Extended Metrics

- **Expectancy:** avg PnL per trade (win_rate * avg_win - loss_rate * avg_loss)
- **Cost ratio:** total costs / gross profit (target < 50%)
- **Exit type distribution:** SL hit %, TP hit %, TIME_EXIT %
- **Per-symbol PnL:** breakdown by symbol
- **Per-regime PnL:** breakdown by detected regime
- **Per-session PnL:** breakdown by trading session (US, EU, Asia)

## Bootstrap Analysis
```python
def bootstrap_pnl(trades, n_bootstrap=10000):
    pnls = [t['pnl'] for t in trades]
    bootstrap_totals = []
    for _ in range(n_bootstrap):
        sample = np.random.choice(pnls, size=len(pnls), replace=True)
        bootstrap_totals.append(sum(sample))
    return {
        'p5': np.percentile(bootstrap_totals, 5),
        'p50': np.percentile(bootstrap_totals, 50),
        'p95': np.percentile(bootstrap_totals, 95)
    }
```

## VLTHR V7 Benchmark Metrics (GOLD STANDARD 88/100)
- Starting balance: $10,000, 4x leverage
- Trading window: 12 months (Jun 2025 – May 2026)
- **V7 final:** 1,201 trades, WR 54%, +$13,401, PF 1.80, MaxDD -$1,288
- **V7 cost model:** Maker fees (40% of taker + 50% slippage reduction) → 59.2% cost reduction
- **V7 entry filter:** calibrated_win_prob > 0.48
- **V7 scorer:** tech 0.00, struct 0.00, ctx 1.00
- **Symbols:** 5 active (DOGEUSDT disabled)
- **Strategy balance:** TF 52.1% / MR 47.9%

**Previous V2 benchmark (deprecated):**
- IS: 15,111 trades, WR 41.5%, +$16,010, +160.1%
- OOS: 5,078 trades, WR 42.8%, +$5,733, +57.3%
- Bootstrap: P5=$17,237, P50=$22,917, P95=$28,476
- Overfitting check: WR decay -1.3%, expectancy decay -$0.07

## Output Format
Store as JSON in `Backtest-Engine/results/`:
```json
{
  "period": {"start": "...", "end": "..."},
  "metrics": {"sharpe": ..., "win_rate": ..., ...},
  "per_symbol": {"BTCUSDT": {...}, ...},
  "trades": [...]
}
```
