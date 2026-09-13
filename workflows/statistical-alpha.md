---
description: Statistical alpha detection workflow — bootstrap tests, deflated Sharpe, factor regression, meta consistency, edge validation
---

# Statistical Alpha Detection Workflow

## When To Use
- Determining if a strategy's edge is real or just luck
- Correcting for multiple trials / data snooping
- Isolating strategy-specific alpha from market/sector exposure
- Testing cross-asset consistency (meta detection)

## Tests

### 1. Bootstrap P-Value
```python
def bootstrap_p_value(returns, n_bootstrap=10000):
    observed_mean = np.mean(returns)
    bootstrap_means = []
    for _ in range(n_bootstrap):
        shuffled = np.random.choice(returns, size=len(returns), replace=True)
        bootstrap_means.append(np.mean(shuffled))
    p_value = np.mean(np.abs(bootstrap_means) >= np.abs(observed_mean))
    return p_value
```
- p < 0.05: edge is likely real
- p > 0.10: edge could be random

### 2. Deflated Sharpe Ratio
```python
def deflated_sharpe(sharpe, n_trials, n_obs):
    from scipy.stats import norm
    E_S_max = norm.ppf(1 - 1/n_trials) * np.sqrt(1/n_obs)
    DS = sharpe - E_S_max
    return DS
```
- Corrects for data snooping bias
- DS > 0: Sharpe survives multiple-trial correction

### 3. Factor Regression
```python
def factor_regression(returns, market_returns, volatility_returns):
    import statsmodels.api as sm
    X = np.column_stack([market_returns, volatility_returns])
    X = sm.add_constant(X)
    model = sm.OLS(returns, X).fit()
    alpha = model.params[0]  # Intercept is strategy-specific alpha
    return alpha, model.summary()
```
- Positive alpha: strategy adds value beyond market exposure
- Near-zero alpha: strategy is just beta exposure

### 4. Meta Detection (Cross-Asset Consistency)
```python
def test_meta_consistency(strategy_func, assets, timeframe):
    results = {}
    for asset in assets:
        df = load_data(asset, timeframe)
        result = backtest_strategy(df, strategy_func)
        results[asset] = result['sharpe']
    positive = sum(1 for s in results.values() if s > 0)
    consistency = positive / len(results)
    meta_exists = consistency > 0.7 or consistency < 0.3
    return {'results': results, 'consistency': consistency, 'meta_exists': meta_exists}
```
- > 70% same Sharpe sign: meta exists (structural edge)
- < 30%: meta exists (structural contrarian edge)
- 30-70%: no meta (asset-specific, likely overfit)

### 5. Timeframe Structure Detection
```python
def detect_timeframe_structure(df):
    returns = df['close'].pct_change().dropna()
    lags = [1, 2, 5, 10, 20]
    autocorrs = [returns.autocorr(lag=lag) for lag in lags]
    variance_ratio = returns.var() / returns.rolling(5).var().mean()
    has_structure = any(abs(ac) > 0.05 for ac in autocorrs) or abs(variance_ratio - 1.0) > 0.1
    return {'autocorrelations': dict(zip(lags, autocorrs)), 'has_structure': has_structure}
```
- No structure = no edge potential on this timeframe

## VLTHR V7 Edge Assessment (GOLD STANDARD 88/100)

**V7 validated edge:**
- 1,201 trades, WR 54%, PF 1.80, $13,401 net PnL
- Edge existence: 15/15 (maxed), Statistical significance: 15/15 (maxed)
- Cost sensitivity: 15/15 (maxed — maker fee model gives 59.2% cost reduction)
- Direction accuracy: 71.9% on filtered trades (up from 48.9% pre-V7)

**V7 edge sources:**
- Isotonic calibration (entry filter > 0.48 filters 87% of base decisions)
- V3 adaptive scorer (ctx-only, tech/struct zeroed out)
- DQS 85+ veto (negative Kelly — counter-intuitive but confirmed)
- Crowd LS ratio veto (> 3.0)
- MR 3.0x boost (strategy concentration balance)

**Remaining edge concerns:**
- Robustness: 11/15 (WF decay at 75% split = 45.7%)
- Risk: 11/15 (CVaR/avg ratio > 5x)
- Calibration: 8/10 (Brier 0.247 > 0.20 target)
- Concentration: 8/10 (SOL PnL share = 30% at cap)

**Previous V2 edge assessment (deprecated):**
- Probe IC: 0/192 features statistically significant (Wilson > 0.05)
- Direction accuracy: 48.9% (below chance) — no directional edge
- Top features are contrarian (negative IC): risk_drawdown_96 (-0.24), trend_ret_96 (-0.24)
- DQS doesn't predict direction (DRVE-B2: dqs_diff coefficient -0.012)
- Edge is thin: $1.13/trade expectancy, costs consume 74% of gross profit

## Files
- `Backtest-Engine/institutional_validation_v7.py` — V7 institutional validation
- `Backtest-Engine/results/institutional_validation_v7.json` — V7 scorecard
- `backtestsystem/SKILLS.md` (Sections 10-13)
