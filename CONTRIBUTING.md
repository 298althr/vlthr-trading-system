# Contributing to VLTHR Trading System

Thank you for your interest in contributing! This is a research-oriented open-source algorithmic trading system. We welcome contributions from quant researchers, software engineers, and trading practitioners.

## 🚨 Important: This Is a Live Trading System

Some parameters in this repository are **frozen** because they affect live trade execution. Changing them without proper validation can cause real financial losses. Before touching any parameter in `pipeline/engine/portfolio_config.py`, read the [Frozen Parameters](#-frozen-parameters) section below.

## Research Areas We Need Help With

We have identified specific gaps that need research. If you have expertise in any of these areas, please open a discussion:

### Strategy Research
- **BTC-specific strategy**: BTC LONGs are the biggest loser (28.6% WR, -$544 net). The current trend_following strategy does not adapt to BTC's ranging periods.
- **SHORT signal generation**: The V2 daily bias filter blocks most SHORT signals in bull markets. Backtest shows SHORT PF 4.41 (n=249) but the live pipeline generates almost no SHORTs.
- **Regime-adaptive strategy switching**: `REGIME_STRATEGY_OVERRIDE_ENABLED = False` because HMM-based switching hurt OOS PF by 20%. Need a better approach (e.g., ensemble, Bayesian regime probability).

### Risk Management Research
- **Correlation-adjusted position sizing**: 5 crypto symbols with 0.7-0.9 correlation are treated as independent. Need a portfolio VaR or risk-parity framework.
- **Dynamic Kelly**: Static Kelly bands are used. Need rolling Kelly computed from live trade history with proper sample size requirements.
- **Volatility targeting**: No mechanism to reduce position size when volatility spikes. ATR expansion widens SL but does not reduce quantity.

### Backtest Validation
- **Slippage modeling**: Backtest assumes zero slippage. Need a realistic slippage model for Bybit market orders.
- **Funding rate modeling**: Perpetual futures funding payments are not modeled. Trades held 7+ hours incur funding costs.
- **Walk-forward optimization**: Infrastructure exists (`SESSION_PARAMS`, `SESSION_REGIME_PARAMS`) but is empty. Need IS/OOS walk-forward validation.

### Engineering
- **Paper vs Bybit SL divergence**: Paper monitor uses parquet OHLCV while Bybit uses actual fills. Need a unified execution layer.
- **SL attachment failure recovery**: If `set_trading_stop` fails after market fill, the position is open with no SL. Need a rollback mechanism.
- **Daily loss breaker stale trade bug**: After container restart, stale CLOSED trades can trip the breaker. Need automatic cleanup.

## How to Contribute

### 1. Fork and Clone

```bash
git clone https://github.com/298althr/vlthr-trading-system.git
cd vlthr-trading-system
cp .env.example .env  # Fill in your values
```

### 2. Set Up the Environment

```bash
# Build and start all services
docker compose up -d --build

# Verify health
curl http://localhost:8201/health    # Pipeline
curl http://localhost:3003/api/health # Backend
curl http://localhost:5175/           # Frontend
```

### 3. Run the Backtest

```bash
cd backtest
python3 backtest_runner.py --all --start 2026-01-01 --end 2026-06-30 --output backtest_results.csv --quiet
python3 stress_test.py backtest_results.csv stress_test_results.csv
```

### 4. Make Your Changes

Follow these rules:
- **One problem per change.** Do not fix unrelated bugs in the same PR.
- **Report bugs before fixing them.** Open an issue first, then reference it in your PR.
- **No magic numbers.** Every parameter that affects signal generation, risk, or execution must live in `portfolio_config.py`.
- **No premature abstraction.** Abstract on the second or third real repetition, not the first anticipated one.

### 5. Validate Before Submitting

Your PR must pass all 6 validation gates:

| Gate | Threshold |
|------|-----------|
| Win rate | >= 30% |
| Profit factor | >= 1.5 |
| Expectancy | > $0 |
| Max drawdown | < 15% |
| Expired rate | < 30% |
| TP hit rate | >= 40% |

Run the backtest with your changes and include the results in your PR description.

### 6. Submit a Pull Request

Use the PR template. Include:
- What problem you are solving
- What files you changed and why
- Backtest results (before and after)
- Which frozen parameters you touched (if any)

## ❄️ Frozen Parameters

These parameters require explicit maintainer approval AND a new backtest before modification. Do not change them in a PR without opening a discussion first:

| Parameter | Current Value | Why It Is Frozen |
|-----------|---------------|------------------|
| Kelly V7 bands | dqs_ge_85=0.0, ge_80=2.5, ge_75=1.5, ge_70=1.0, ge_65=0.7, lt_65=0.3 | Validated by Kelly adjustment stress test |
| MR boost | 3.0x | Concentration balance between TF and MR strategies |
| Calibration gate | 0.40 | ML model probability threshold |
| Crowding reduction | 50% | Risk reduction for correlated signals |
| DQ gate | 60 | Data quality score minimum |
| Max active trades | 8 | Portfolio capacity limit |
| Scan interval | 300s | Signal evaluation frequency |
| Max leverage | 4x | Risk limit |
| Max portfolio risk | 12% | Total open risk cap |
| SL_TIME_DECAY_RATE | 0.15 | Dynamic SL tightening rate |
| REGIME_STRATEGY_MIN_DQS | TF/MIXED=65 | Strategy-regime quality filter |
| mult_cap_downgrade threshold | 70 | DQS inflation prevention |
| mult_cap_downgrade cap | 79 | Below V7 veto threshold |

## Code Style

- **Python**: Follow PEP 8. One responsibility per function. Comments explain WHY, not WHAT.
- **TypeScript/React**: One component per file, one hook per file, one utility per file.
- **Node.js**: `.cjs` extension for CommonJS modules (as the existing codebase uses).
- **No file exceeds 1000 lines.** If it approaches 800, plan the split.

## Testing

- Run existing tests before submitting: `python3 -m pytest pipeline/engine/test_risk_logic.py`
- Add tests only where behavior could regress. Do not write tests for trivial changes.
- For parameter changes, include a backtest comparison (before vs after).

## Communication

- **Bug reports**: Use the bug report issue template
- **Feature requests**: Use the feature request issue template
- **Research discussions**: Open a discussion with the `research` label
- **Security issues**: Email the maintainer directly. Do not open a public issue for security vulnerabilities.

## License

This project is licensed under the **Business Source License 1.1 (BSL 1.1)**. Contributions are welcome and free for research, education, and personal use. Production/commercial use requires a commercial license.

By contributing, you agree that your contributions will be licensed under the BSL 1.1 terms, and will convert to Apache 2.0 on the Change Date (2030-09-13).

You must include attribution to the VLTHR Trading System in any derivative work or publication. See [ADDITIONAL_USE_GRANT.txt](ADDITIONAL_USE_GRANT.txt) for details.
