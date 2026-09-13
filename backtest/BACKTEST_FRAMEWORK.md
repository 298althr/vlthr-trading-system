# Backtesting Framework

**Version:** 1.0  
**Date:** 2026-05-23  
**Purpose:** Complete framework for researching, developing, and validating trading strategies

---

## Table of Contents

1. [Strategy Research Phase](#1-strategy-research-phase)
2. [Implementation Phase](#2-implementation-phase)
3. [Timeframe Structure Detection](#3-timeframe-structure-detection)
4. [Parameter Optimization Phase](#4-parameter-optimization-phase)
5. [Alpha Detection Phase](#5-alpha-detection-phase)
6. [Meta Detection Phase](#6-meta-detection-phase)
7. [Strategy Decoupling Phase](#7-strategy-decoupling-phase)
8. [Edge Validation Phase](#8-edge-validation-phase)
9. [Scenario Testing Phase](#9-scenario-testing-phase)
10. [Documentation Phase](#10-documentation-phase)
11. [Continuous Improvement](#11-continuous-improvement)

---

## 1. Strategy Research Phase

### 1.1 Strategy Discovery

**Sources:**
- **GitHub:** Search for trading strategies, algorithmic trading repos
- **TradingView:** Pine Script strategies (convert to Python)
- **Academic Papers:** SSRN, arXiv quantitative finance
- **Trading Forums:** Forex Factory, QuantConnect, Quantopian archives
- **Books:** "Advances in Financial Machine Learning", "Algorithmic Trading"

**Search Terms:**
- "trading strategy python"
- "backtest framework"
- "algorithmic trading github"
- "momentum strategy"
- "mean reversion strategy"
- "breakout strategy"

### 1.2 Strategy Evaluation Criteria

**Before Implementation, Answer:**
1. **Market Regime:** Trending vs ranging vs volatile
2. **Timeframe:** What timeframe is optimal? (1m, 5m, 15m, 1H, 4H, Daily)
3. **Asset Class:** Forex, crypto, stocks, commodities
4. **Complexity:** Can it be implemented with available data?
5. **Parameters:** How many tunable parameters?
6. **Risk:** What are the failure modes?

**Red Flags:**
- Requires order book data (not available)
- Requires fundamental data (not available)
- Too many parameters (>10)
- No clear exit logic
- Look-ahead bias in description

### 1.3 Strategy Documentation Template

Create a `STRATEGY_RESEARCH.md` for each strategy:

```markdown
# [Strategy Name]

## Description
[Brief description of strategy logic]

## Source
[GitHub link, paper, or forum post]

## Market Regime
- Trending/Ranging/Volatile
- Best timeframe: [timeframe]
- Best asset class: [asset]

## Entry Logic
[Detailed entry conditions]

## Exit Logic
[Detailed exit conditions]

## Risk Management
[Stop loss, take profit, position sizing]

## Parameters
- param1: [range]
- param2: [range]
- ...

## Potential Issues
[List known issues or concerns]

## Implementation Notes
[Any special considerations]
```

---

## 2. Implementation Phase

### 2.1 Project Structure

```
backtest/
├── strategies/
│   ├── STRATEGY_NAME/
│   │   ├── __init__.py
│   │   ├── strategy.py
│   │   └── STRATEGY_RESEARCH.md
├── data/
│   ├── XAUUSD/
│   │   ├── 1M/
│   │   ├── 5M/
│   │   ├── 15M/
│   │   ├── 1H/
│   │   ├── 4H/
│   │   └── D/
├── results/
│   └── STRATEGY_NAME/
│       ├── PARAM_SET_1/
│       ├── PARAM_SET_2/
│       └── ...
└── utils/
    ├── data_loader.py
    ├── metrics.py
    └── visualizer.py
```

### 2.2 Strategy Implementation Template

```python
import pandas as pd
import numpy as np
from typing import List, Dict, Optional

class Strategy:
    """Base class for all trading strategies."""
    
    def __init__(self, config: Dict):
        self.config = config
        self.signals = []
    
    def generate_signals(self, df: pd.DataFrame) -> List[Dict]:
        """
        Generate trading signals from OHLCV data.
        
        Args:
            df: DataFrame with columns [timestamp, open, high, low, close, volume]
        
        Returns:
            List of signal dicts: [{'timestamp': ..., 'direction': 'LONG/SHORT', 
                                   'entry_price': ..., 'sl': ..., 'tp': ...}]
        """
        raise NotImplementedError("Subclasses must implement generate_signals")
    
    def validate_parameters(self) -> bool:
        """Validate strategy parameters."""
        return True
```

### 2.3 Data Loading

```python
from pathlib import Path
import pandas as pd

def load_data(symbol: str, timeframe: str, year: int) -> pd.DataFrame:
    """
    Load historical data for backtesting.
    
    Args:
        symbol: e.g., 'XAUUSD'
        timeframe: e.g., '15M', '1H', 'D'
        year: e.g., 2024
    
    Returns:
        DataFrame with OHLCV data
    """
    data_path = Path(f'data/{symbol}/{timeframe}/{year}')
    df_list = []
    
    for month_file in sorted(data_path.glob('*.parquet')):
        df_month = pd.read_parquet(month_file)
        df_list.append(df_month)
    
    df = pd.concat(df_list, ignore_index=True)
    df = df.sort_values('timestamp').reset_index(drop=True)
    df.columns = df.columns.str.lower()
    
    return df
```

### 2.4 Cost Model

```python
class CostModel:
    def __init__(self, spread_pips=0.25, commission=0.40, slippage_pips=0.5):
        self.spread_pips = spread_pips
        self.commission = commission
        self.slippage_pips = slippage_pips
    
    def apply_to_entry(self, price: float, direction: str) -> float:
        if direction == 'LONG':
            return price + (self.spread_pips / 100) + (self.slippage_pips / 100)
        else:
            return price - (self.spread_pips / 100) - (self.slippage_pips / 100)
    
    def apply_to_exit(self, price: float, direction: str) -> float:
        if direction == 'LONG':
            return price - (self.slippage_pips / 100)
        else:
            return price + (self.slippage_pips / 100)
```

---

## 3. Timeframe Structure Detection

### 3.1 Auto-Search Across Timeframes

**Instead of Assuming a Timeframe, Brute-Force Candidate Horizons:**

```python
TIMEFRAMES = ['1M', '5M', '15M', '1H', '4H', 'D', 'W', 'M']

def detect_timeframe_structure(df):
    """
    Detect if timeframe has memory/structure via autocorrelation.
    Avoids testing on timeframes with no edge potential.
    """
    returns = df['close'].pct_change().dropna()
    
    # Autocorrelation at multiple lags
    lags = [1, 2, 5, 10, 20]
    autocorrs = [returns.autocorr(lag=lag) for lag in lags]
    
    # Variance ratio test (random walk vs trending)
    variance_ratio = returns.var() / returns.rolling(5).var().mean()
    
    # Structure detected if significant autocorrelation or variance ratio deviation
    has_structure = (
        any(abs(ac) > 0.05 for ac in autocorrs) or
        abs(variance_ratio - 1.0) > 0.1
    )
    
    return {
        'autocorrelations': dict(zip(lags, autocorrs)),
        'variance_ratio': variance_ratio,
        'has_structure': has_structure
    }

# Scan all timeframes
timeframe_results = {}
for tf in TIMEFRAMES:
    df = load_data('XAUUSD', tf, 2024)
    structure = detect_timeframe_structure(df)
    timeframe_results[tf] = structure
    
    if structure['has_structure']:
        print(f"{tf}: Has structure - pass to optimization")
    else:
        print(f"{tf}: No structure - skip")
```

### 3.2 Structure Detection Logic

**What We're Testing:**
- **Autocorrelation:** Does past return predict future return?
- **Variance Ratio:** Is price trending or random walking?
- **Lag Analysis:** At what lag does memory exist?

**Pass Criteria:**
- Autocorrelation > 0.05 at any lag
- Variance ratio deviation > 10% from 1.0
- If no structure detected, skip this timeframe

**Why This Matters:**
Testing a strategy on a timeframe with no memory is guaranteed to fail. This filter saves computation time and prevents false positives.

---

## 4. Parameter Optimization Phase

### 4.1 Parameter Space Definition

**Define Parameter Ranges:**
```python
PARAMETER_RANGES = {
    'fast_period': (4, 20),           # Tuple: (min, max)
    'slow_period': (10, 50),
    'risk_pct': (1.0, 3.0),
    'atr_multiplier': (0.5, 1.5),
    'min_grade': ['A', 'B', 'C'],     # Categorical
    'session': ['LONDON', 'NY', 'BOTH']
}
```

### 4.2 10,000 Combination Generation

**Method 1: Grid Search (Discrete Parameters)**
```python
from itertools import product

def generate_grid_combinations(param_ranges, target=10000):
    """Generate approximately target combinations using grid search."""
    
    # Calculate steps for each parameter
    n_params = len(param_ranges)
    steps_per_param = int(target ** (1/n_params))
    
    combinations = []
    
    for param_name, (min_val, max_val) in param_ranges.items():
        if isinstance(min_val, int):
            values = list(range(min_val, max_val + 1, 
                                max(1, (max_val - min_val) // steps_per_param)))
        else:
            values = np.linspace(min_val, max_val, steps_per_param).tolist()
        
        combinations.append(values)
    
    return list(product(*combinations))
```

**Method 2: Random Search (Continuous Parameters)**
```python
import random

def generate_random_combinations(param_ranges, n=10000):
    """Generate n random parameter combinations."""
    
    combinations = []
    
    for _ in range(n):
        combo = {}
        for param_name, value_range in param_ranges.items():
            if isinstance(value_range, tuple):
                min_val, max_val = value_range
                if isinstance(min_val, int):
                    combo[param_name] = random.randint(min_val, max_val)
                else:
                    combo[param_name] = random.uniform(min_val, max_val)
            else:  # Categorical
                combo[param_name] = random.choice(value_range)
        
        combinations.append(combo)
    
    return combinations
```

**Method 3: Latin Hypercube Sampling (Better Coverage)**
```python
from scipy.stats import qmc

def generate_lhs_combinations(param_ranges, n=10000):
    """Generate combinations using Latin Hypercube Sampling."""
    
    # Normalize parameter ranges to [0, 1]
    param_bounds = []
    param_types = []
    
    for param_name, value_range in param_ranges.items():
        if isinstance(value_range, tuple):
            min_val, max_val = value_range
            param_bounds.append([min_val, max_val])
            param_types.append('continuous')
        else:
            param_bounds.append([0, len(value_range) - 1])
            param_types.append('categorical')
    
    # Generate LHS samples
    sampler = qmc.LatinHypercube(d=len(param_bounds))
    samples = sampler.random(n=n)
    
    # Scale to parameter ranges
    combinations = []
    for sample in samples:
        combo = {}
        for i, (bound, ptype) in enumerate(zip(param_bounds, param_types)):
            if ptype == 'continuous':
                min_val, max_val = bound
                value = sample[i] * (max_val - min_val) + min_val
                if isinstance(min_val, int):
                    combo[list(param_ranges.keys())[i]] = int(round(value))
                else:
                    combo[list(param_ranges.keys())[i]] = value
            else:
                idx = int(sample[i] * (bound[1] - bound[0] + 0.5))
                combo[list(param_ranges.keys())[i]] = param_ranges[list(param_ranges.keys())[i]][idx]
        
        combinations.append(combo)
    
    return combinations
```

### 4.3 Multi-Timeframe Optimization

**Test Each Timeframe Separately:**
```python
TIMEFRAMES = ['1M', '5M', '15M', '1H', '4H', 'D']

for timeframe in TIMEFRAMES:
    print(f"\n{'='*80}")
    print(f"Optimizing for {timeframe} timeframe")
    print(f"{'='*80}")
    
    # Load data for this timeframe
    df = load_data('XAUUSD', timeframe, 2024)
    
    # Check for structure first
    structure = detect_timeframe_structure(df)
    if not structure['has_structure']:
        print(f"{timeframe}: No structure - skipping")
        continue
    
    # Generate combinations
    combinations = generate_lhs_combinations(PARAMETER_RANGES, n=10000)
    
    # Backtest each combination
    results = []
    for i, params in enumerate(combinations):
        if i % 1000 == 0:
            print(f"Progress: {i}/{len(combinations)}")
        
        result = backtest_strategy(df, params)
        results.append({
            'params': params,
            'metrics': result,
            'timeframe': timeframe
        })
    
    # Save results
    save_results(results, f'results/STRATEGY_NAME/{timeframe}/')
```

### 4.4 Parallel Processing

**Speed Up with Multiprocessing:**
```python
from multiprocessing import Pool, cpu_count

def backtest_wrapper(args):
    """Wrapper for multiprocessing."""
    df, params = args
    return backtest_strategy(df, params)

def parallel_optimization(df, combinations):
    """Run backtests in parallel."""
    n_workers = cpu_count() - 1  # Leave one core free
    
    with Pool(n_workers) as pool:
        args_list = [(df, params) for params in combinations]
        results = pool.map(backtest_wrapper, args_list)
    
    return results
```

### 4.5 Stopping Criteria

**When to Stop Optimization:**
1. **Edge Found:** Positive expectancy (>0.1R), Sharpe > 1.5, Max DD < 30%
2. **Convergence:** Top 10 results have similar parameters
3. **Diminishing Returns:** No improvement in top result after 5000 iterations
4. **Time Limit:** Maximum 48 hours of computation

**Edge Detection:**
```python
def has_edge(metrics):
    """Check if parameters show edge."""
    return (
        metrics['expectancy_r'] > 0.1 and
        metrics['sharpe_ratio'] > 1.5 and
        metrics['max_drawdown'] < 0.30 and
        metrics['profit_factor'] > 1.5
    )
```

---

## 5. Alpha Detection Phase

### 5.1 Statistical Significance Testing

**Alpha means: risk-adjusted, non-random, unexplained by known factors.**

**Bootstrap P-Value Test:**
```python
def bootstrap_p_value(returns, n_bootstrap=10000):
    """
    Bootstrap test: Is edge just lucky sequence?
    Returns p-value for null hypothesis that returns are random.
    """
    observed_mean = np.mean(returns)
    bootstrap_means = []
    
    for _ in range(n_bootstrap):
        shuffled = np.random.choice(returns, size=len(returns), replace=True)
        bootstrap_means.append(np.mean(shuffled))
    
    bootstrap_means = np.array(bootstrap_means)
    p_value = np.mean(np.abs(bootstrap_means) >= np.abs(observed_mean))
    
    return p_value
```

**Deflated Sharpe Ratio:**
```python
def deflated_sharpe(sharpe, n_trials, n_obs):
    """
    Deflated Sharpe Ratio: Corrects for multiple trials.
    Accounts for data snooping bias.
    """
    from scipy.stats import norm
    
    # Expected maximum Sharpe under random
    E_S_max = norm.ppf(1 - 1/n_trials) * np.sqrt(1/n_obs)
    
    # Deflated Sharpe
    DS = sharpe - E_S_max
    
    return DS
```

**Factor Regression:**
```python
def factor_regression(returns, market_returns, volatility_returns):
    """
    Factor regression: Remove market, sector, volatility exposure.
    Returns alpha (strategy-specific return).
    """
    import statsmodels.api as sm
    
    X = np.column_stack([market_returns, volatility_returns])
    X = sm.add_constant(X)
    
    model = sm.OLS(returns, X).fit()
    alpha = model.params[0]  # Intercept is alpha
    
    return alpha, model.summary()
```

### 5.2 Alpha Thresholds

**Minimum threshold for "alpha exists":**
- Out-of-sample p < 0.01
- Factor-adjusted alpha > 0
- Deflated Sharpe > 0
- Works in ≥ 3 disjoint years

### 5.3 Regime Sensitivity

**Test if alpha vanishes in high volatility:**
```python
def test_regime_sensitivity(df, params):
    """Test if alpha persists across market regimes."""
    
    # Split by volatility
    returns = df['close'].pct_change()
    vol_threshold = returns.std()
    
    high_vol_df = df[returns.rolling(20).std() > vol_threshold]
    low_vol_df = df[returns.rolling(20).std() <= vol_threshold]
    
    high_vol_result = backtest_strategy(high_vol_df, params)
    low_vol_result = backtest_strategy(low_vol_df, params)
    
    return {
        'high_vol_sharpe': high_vol_result['sharpe'],
        'low_vol_sharpe': low_vol_result['sharpe'],
        'regime_stable': abs(high_vol_result['sharpe'] - low_vol_result['sharpe']) < 1.0
    }
```

---

## 6. Meta Detection Phase

### 6.1 Cross-Asset Consistency

**A meta exists when the same structural logic produces edge across different instruments.**

```python
def test_meta_consistency(strategy_func, assets, timeframe):
    """
    Test if strategy logic produces edge across unrelated assets.
    Meta exists if >70% have same Sharpe sign.
    """
    results = {}
    
    for asset in assets:
        df = load_data(asset, timeframe, 2024)
        result = backtest_strategy(df, strategy_func)
        results[asset] = result['sharpe']
    
    # Count positive vs negative Sharpe
    positive = sum(1 for s in results.values() if s > 0)
    total = len(results)
    consistency = positive / total
    
    meta_exists = consistency > 0.7 or consistency < 0.3
    
    return {
        'results': results,
        'consistency': consistency,
        'meta_exists': meta_exists
    }
```

### 6.2 Meta Validation

**Test Assets:**
- XAUUSD (Gold)
- EURUSD (Forex)
- GBPUSD (Forex)
- USDJPY (Forex)
- BTCUSD (Crypto)
- SPX500 (Index)

**Meta Criteria:**
- >70% of assets have same Sharpe sign
- Consistent across timeframes
- Structural logic, not parameter fit

### 6.3 Meta vs Spurious Fit

**Spurious Fit Indicators:**
- Works on one asset only
- Fails on similar assets
- Parameters are asset-specific
- Cannot explain logic in 2 sentences

**Meta Indicators:**
- Works across unrelated assets
- Parameters are stable
- Logic is explainable simply
- Edge persists across regimes

---

## 7. Strategy Decoupling Phase

### 7.1 Decoupler Stress Tests

**After finding a working candidate, the decoupler tries to destroy it:**

```python
def decoupler_stress_test(df, strategy_func, params):
    """
    Stress test strategy to validate it's not overfitted.
    Strategy should degrade gracefully, not collapse.
    """
    baseline = backtest_strategy(df, params)
    baseline_sharpe = baseline['sharpe']
    
    stress_results = {}
    
    # Test 1: Change random seed (if applicable)
    stress_results['seed_change'] = backtest_strategy(df, params, seed=42)['sharpe']
    
    # Test 2: Shuffle trade labels (should go flat)
    shuffled_trades = baseline['trades'].copy()
    np.random.shuffle(shuffled_trades)
    stress_results['shuffled'] = 0.0  # Should be near zero
    
    # Test 3: Add synthetic noise to price
    noisy_df = df.copy()
    noise = np.random.normal(0, df['close'].std() * 0.01, len(df))
    noisy_df['close'] += noise
    stress_results['noisy_price'] = backtest_strategy(noisy_df, params)['sharpe']
    
    # Test 4: Remove one year from history
    if len(df) > 252:
        reduced_df = df.iloc[:-252]
        stress_results['reduced_history'] = backtest_strategy(reduced_df, params)['sharpe']
    
    # Test 5: Test on different asset
    other_asset_df = load_data('EURUSD', '15M', 2024)
    stress_results['different_asset'] = backtest_strategy(other_asset_df, params)['sharpe']
    
    # Evaluate degradation
    degradation = {}
    for test, sharpe in stress_results.items():
        if test == 'shuffled':
            degradation[test] = 'N/A'  # Should be zero
        else:
            pct_change = abs(sharpe - baseline_sharpe) / abs(baseline_sharpe) if baseline_sharpe != 0 else 0
            degradation[test] = pct_change
    
    return {
        'baseline_sharpe': baseline_sharpe,
        'stress_results': stress_results,
        'degradation': degradation,
        'decoupled': all(d < 0.5 for d in degradation.values() if isinstance(d, float))
    }
```

### 7.2 Decoupling Criteria

**Strategy is decoupled if:**
- Degradation < 50% on all stress tests
- Shuffled trades go flat (Sharpe ≈ 0)
- Noise doesn't collapse performance
- Works on different asset (may degrade but not collapse)
- Logic explainable in 2 sentences without parameters

**Example of Decoupled Strategy:**
"Buy when 20-day volatility drops below 5th percentile and price reclaims 10-day high; exit after 5 days."

**Example of Overfitted Strategy:**
"Buy when MA(5)>MA(13) and RSI<42.7 and ATR>0.03*close and volume>1.2x_avg..."

### 7.3 Decoupling as Destruction

**The decoupler's job is to break the strategy:**
- If it breaks → Overfitted, discard
- If it survives → Decoupled, proceed

**Only if the strategy degrades gracefully (not collapses) is it decoupled.**

---

## 8. Edge Validation Phase

### 8.1 Out-of-Sample Testing

**Split Data:**
- Training: 70% of data (e.g., 2021-2023)
- Validation: 15% of data (e.g., 2024 Q1-Q2)
- Test: 15% of data (e.g., 2024 Q3-Q4)

```python
def train_val_test_split(df):
    """Split data into train, validation, test sets."""
    n = len(df)
    train_end = int(n * 0.7)
    val_end = int(n * 0.85)
    
    return {
        'train': df.iloc[:train_end],
        'val': df.iloc[train_end:val_end],
        'test': df.iloc[val_end:]
    }
```

### 8.2 Walk-Forward Testing

**Rolling Window Validation:**
```python
def walk_forward_test(df, params, window_size=252, step=63):
    """
    Perform walk-forward testing.
    
    Args:
        df: Full dataset
        params: Strategy parameters
        window_size: Training window size (in bars)
        step: Step size for rolling window
    """
    results = []
    
    for i in range(0, len(df) - window_size - step, step):
        train_df = df.iloc[i:i+window_size]
        test_df = df.iloc[i+window_size:i+window_size+step]
        
        # Optimize on training data
        best_params = optimize(train_df)
        
        # Test on validation data
        test_result = backtest_strategy(test_df, best_params)
        
        results.append({
            'train_period': (i, i+window_size),
            'test_period': (i+window_size, i+window_size+step),
            'params': best_params,
            'test_metrics': test_result
        })
    
    return results
```

### 8.3 Year-by-Year Walk-Forward (Not Cumulative)

**Standard backtesters lie because they train/test across regimes.**
**Enforce chronological walk-forward with reset at each calendar year.**

```python
def year_by_year_walkforward(df, params, start_year=2018, end_year=2024):
    """
    Enforce chronological walk-forward with reset at each calendar year.
    Train on Y-2 to Y-1 only (not all prior history).
    Validate on Y (forward test).
    """
    results = []
    
    for year in range(start_year, end_year + 1):
        # Split data
        train_start = f"{year-2}-01-01"
        train_end = f"{year-1}-12-31"
        val_start = f"{year}-01-01"
        val_end = f"{year}-12-31"
        
        train_df = df[(df['timestamp'] >= train_start) & (df['timestamp'] <= train_end)]
        val_df = df[(df['timestamp'] >= val_start) & (df['timestamp'] <= val_end)]
        
        if len(train_df) == 0 or len(val_df) == 0:
            continue
        
        # Optimize on training data
        best_params = optimize_parameters(train_df, params)
        
        # Test on validation data
        val_result = backtest_strategy(val_df, best_params)
        
        results.append({
            'year': year,
            'train_period': (train_start, train_end),
            'val_period': (val_start, val_end),
            'params': best_params,
            'val_metrics': val_result
        })
    
    return results
```

**Why This Matters:**
If a strategy works in 2017–2018 but fails in 2020 (COVID), that's real information, not noise. Cumulative backtests hide regime changes.

### 8.4 Cross-Asset Testing

**Test on Multiple Assets:**
```python
ASSETS = ['XAUUSD', 'EURUSD', 'GBPUSD', 'USDJPY', 'BTCUSD']

for asset in ASSETS:
    df = load_data(asset, '15M', 2024)
    result = backtest_strategy(df, optimal_params)
    print(f"{asset}: Sharpe={result['sharpe']:.2f}, Return={result['return']:.2%}")
```

---

## 9. Scenario Testing Phase

### 9.1 Scenario Definitions

```python
SCENARIOS = {
    'normal': {
        'spread_pips': 0.25,
        'commission': 0.40,
        'slippage_pips': 0.5,
        'volatility_multiplier': 1.0,
        'description': 'Standard market conditions'
    },
    'high_volatility': {
        'spread_pips': 0.50,
        'commission': 0.40,
        'slippage_pips': 1.0,
        'volatility_multiplier': 1.5,
        'description': 'News events, economic releases'
    },
    'low_volatility': {
        'spread_pips': 0.15,
        'commission': 0.40,
        'slippage_pips': 0.3,
        'volatility_multiplier': 0.7,
        'description': 'Range-bound markets, low activity'
    },
    'crisis': {
        'spread_pips': 2.0,
        'commission': 0.40,
        'slippage_pips': 5.0,
        'volatility_multiplier': 3.0,
        'description': 'Market crash, liquidity crisis'
    },
    'gap_risk': {
        'spread_pips': 0.25,
        'commission': 0.40,
        'slippage_pips': 0.5,
        'volatility_multiplier': 1.0,
        'gap_probability': 0.1,
        'gap_size_pips': 50,
        'description': 'Weekend/overnight gap risk'
    }
}
```

### 9.2 Scenario Testing Implementation

```python
def test_scenarios(df, params, scenarios):
    """Test strategy across all scenarios."""
    results = {}
    
    for scenario_name, scenario_params in scenarios.items():
        print(f"\nTesting scenario: {scenario_name}")
        
        cost_model = CostModel(
            spread_pips=scenario_params['spread_pips'],
            commission=scenario_params['commission'],
            slippage_pips=scenario_params['slippage_pips']
        )
        
        result = backtest_strategy(df, params, cost_model)
        results[scenario_name] = result
        
        print(f"  Sharpe: {result['sharpe']:.2f}")
        print(f"  Return: {result['return']:.2%}")
        print(f"  Max DD: {result['max_drawdown']:.2%}")
    
    return results
```

### 9.3 Stress Testing

**Worst-Case Analysis:**
```python
def stress_test(df, params):
    """Perform extreme stress testing."""
    
    stress_scenarios = {
        'max_slippage': {'slippage_pips': 10.0},
        'max_spread': {'spread_pips': 5.0},
        'combo_crisis': {'spread_pips': 3.0, 'slippage_pips': 8.0}
    }
    
    results = {}
    for scenario_name, scenario_params in stress_scenarios.items():
        cost_model = CostModel(**scenario_params)
        result = backtest_strategy(df, params, cost_model)
        results[scenario_name] = result
    
    return results
```

---

## 10. Documentation Phase

### 10.1 Required Outputs

**Each Backtest Must Generate:**

1. **performance.json** - All metrics
2. **trades.json** - Individual trade details
3. **equity_curve.png** - Visual equity progression
4. **monthly_performance.png** - Monthly/yearly breakdown
5. **parameters.json** - Parameter values used
6. **backtest_log.txt** - Execution log

### 10.2 Folder Structure

```
results/
└── STRATEGY_NAME/
    ├── TIMEFRAME_1/
    │   ├── PARAM_SET_1/
    │   │   ├── performance.json
    │   │   ├── trades.json
    │   │   ├── equity_curve.png
    │   │   ├── monthly_performance.png
    │   │   ├── parameters.json
    │   │   └── backtest_log.txt
    │   ├── PARAM_SET_2/
    │   │   └── ...
    │   └── scenario_tests/
    │       ├── normal/
    │       ├── high_volatility/
    │       └── crisis/
    ├── TIMEFRAME_2/
    │   └── ...
    └── summary/
        ├── best_parameters.json
        └── comparison_chart.png
```

### 10.3 Performance JSON Template

```json
{
  "strategy_name": "STRATEGY_NAME",
  "timeframe": "15M",
  "period": "2024-01-01 to 2024-12-31",
  "parameters": {
    "param1": value1,
    "param2": value2
  },
  "metrics": {
    "initial_capital": 10000.0,
    "final_balance": 15000.0,
    "total_return": 0.5,
    "annualized_return": 0.5,
    "max_drawdown": 0.15,
    "sharpe_ratio": 2.5,
    "sortino_ratio": 3.0,
    "calmar_ratio": 3.33,
    "win_rate": 0.45,
    "profit_factor": 2.0,
    "expectancy_r": 0.15,
    "total_trades": 100,
    "avg_trade_pnl": 50.0,
    "avg_win_pnl": 150.0,
    "avg_loss_pnl": -100.0
  },
  "costs": {
    "spread_pips": 0.25,
    "commission": 0.40,
    "slippage_pips": 0.5,
    "total_costs": 80.0
  },
  "timestamp": "2026-05-23T10:00:00Z"
}
```

### 10.4 Trades JSON Template

```json
[
  {
    "trade_id": 1,
    "entry_time": "2024-01-01T10:00:00Z",
    "exit_time": "2024-01-01T14:00:00Z",
    "direction": "LONG",
    "entry_price": 2000.0,
    "exit_price": 2010.0,
    "pnl": 100.0,
    "pnl_r": 0.5,
    "bars_held": 4,
    "exit_reason": "TP"
  }
]
```

### 10.5 Visualization Functions

```python
import matplotlib.pyplot as plt
import seaborn as sns

def plot_equity_curve(equity_curve, save_path):
    """Plot equity curve."""
    plt.figure(figsize=(14, 8))
    plt.plot(equity_curve, linewidth=2)
    plt.title('Equity Curve', fontsize=16, fontweight='bold')
    plt.xlabel('Trade Number', fontsize=12)
    plt.ylabel('Equity ($)', fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()

def plot_monthly_performance(trades, save_path):
    """Plot monthly performance breakdown."""
    # Group trades by month
    trades_df = pd.DataFrame(trades)
    trades_df['month'] = pd.to_datetime(trades_df['entry_time']).dt.to_period('M')
    monthly_pnl = trades_df.groupby('month')['pnl'].sum()
    
    plt.figure(figsize=(14, 8))
    monthly_pnl.plot(kind='bar', color=['green' if x > 0 else 'red' for x in monthly_pnl])
    plt.title('Monthly Performance', fontsize=16, fontweight='bold')
    plt.xlabel('Month', fontsize=12)
    plt.ylabel('PnL ($)', fontsize=12)
    plt.grid(True, alpha=0.3)
    plt.savefig(save_path, dpi=150, bbox_inches='tight')
    plt.close()
```

---

## 11. Continuous Improvement

### 11.1 Iteration Process

**Don't Stop Until Edge is Found:**

1. **If No Edge After 10,000 Combinations:**
   - Review parameter ranges (expand or contract)
   - Check for look-ahead bias
   - Verify data quality
   - Consider different timeframe
   - Revisit strategy logic

2. **If Edge Found But Unstable:**
   - Increase regularization (simplify parameters)
   - Add filters (session, volatility, trend)
   - Implement adaptive parameters
   - Test on longer history

3. **If Edge Found and Stable:**
   - Proceed to scenario testing
   - Validate on out-of-sample data
   - Document thoroughly
   - Prepare for deployment

### 11.2 Performance Tracking

**Track All Iterations:**
```python
ITERATION_LOG = []

def log_iteration(params, metrics, iteration_num):
    """Log backtest iteration."""
    ITERATION_LOG.append({
        'iteration': iteration_num,
        'params': params,
        'metrics': metrics,
        'timestamp': datetime.now().isoformat()
    })
    
    # Save to file
    with open('results/STRATEGY_NAME/iteration_log.json', 'w') as f:
        json.dump(ITERATION_LOG, f, indent=2)
```

### 11.3 Comparison Analysis

**Compare Across Timeframes:**
```python
def compare_timeframes(results):
    """Compare performance across timeframes."""
    comparison = {}
    
    for timeframe, timeframe_results in results.items():
        best = max(timeframe_results, key=lambda x: x['metrics']['sharpe'])
        comparison[timeframe] = best
    
    # Generate comparison chart
    plot_comparison(comparison)
    
    return comparison
```

### 11.4 Final Validation Checklist

Before declaring edge found, verify:

**Performance Metrics:**
- [ ] Positive expectancy (>0.1R)
- [ ] Sharpe ratio > 1.5
- [ ] Max drawdown < 30%
- [ ] Profit factor > 1.5
- [ ] Sufficient trade count (>100)

**Statistical Validation:**
- [ ] Bootstrap p-value < 0.01
- [ ] Deflated Sharpe > 0 (corrected for data snooping)
- [ ] Factor-adjusted alpha > 0
- [ ] Works in ≥ 3 disjoint years

**Meta Validation:**
- [ ] Consistent across >70% of test assets
- [ ] Consistent across timeframes
- [ ] Structural logic, not parameter fit

**Decoupling Validation:**
- [ ] Degradation < 50% on all stress tests
- [ ] Shuffled trades go flat (Sharpe ≈ 0)
- [ ] Noise doesn't collapse performance
- [ ] Works on different asset (may degrade but not collapse)
- [ ] Logic explainable in 2 sentences without parameters

**Scenario Testing:**
- [ ] Stable in normal conditions
- [ ] Degrades gracefully in high volatility
- [ ] Survives crisis scenarios
- [ ] Handles gap risk

**Data Integrity:**
- [ ] No look-ahead bias
- [ ] Realistic cost model applied
- [ ] Validated on out-of-sample data
- [ ] Year-by-year walk-forward passed

---

## Appendix: Quick Start Template

```python
#!/usr/bin/env python3
"""
Quick start template for new strategy backtesting.
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime

# 1. Define parameter ranges
PARAMETER_RANGES = {
    'param1': (min_val, max_val),
    'param2': (min_val, max_val),
}

# 2. Generate combinations
combinations = generate_lhs_combinations(PARAMETER_RANGES, n=10000)

# 3. Load data
df = load_data('XAUUSD', '15M', 2024)

# 4. Optimize
results = []
for i, params in enumerate(combinations):
    if i % 1000 == 0:
        print(f"Progress: {i}/{len(combinations)}")
    
    result = backtest_strategy(df, params)
    results.append({'params': params, 'metrics': result})
    
    # Check for edge
    if has_edge(result):
        print(f"Edge found at iteration {i}!")
        break

# 5. Save results
save_results(results, 'results/STRATEGY_NAME/')

# 6. Scenario testing on best parameters
best_params = max(results, key=lambda x: x['metrics']['sharpe'])['params']
scenario_results = test_scenarios(df, best_params, SCENARIOS)

# 7. Generate reports
generate_reports(results, scenario_results)
```

---

*Backtesting Framework v1.0 — 2026-05-23*
