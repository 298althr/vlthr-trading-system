## Summary

<!-- One-line description of what this PR changes and why -->

## Problem
<!-- What problem does this solve? Link the issue: Fixes #123 -->

## Changes
<!-- List the files changed and what was modified in each -->

## Frozen Parameters Touched
<!-- List any frozen parameters modified. If none, write "None". See CONTRIBUTING.md -->

## Backtest Results

### Before
| Metric | Value |
|--------|-------|
| Win rate | |
| Profit factor | |
| Expectancy | |
| Max drawdown | |
| TP hit rate | |

### After
| Metric | Value |
|--------|-------|
| Win rate | |
| Profit factor | |
| Expectancy | |
| Max drawdown | |
| TP hit rate | |

### All 6 Gates Pass?
- [ ] Win rate >= 30%
- [ ] Profit factor >= 1.5
- [ ] Expectancy > $0
- [ ] Max drawdown < 15%
- [ ] Expired rate < 30%
- [ ] TP hit rate >= 40%

## Testing
- [ ] Existing tests pass (`python3 -m pytest pipeline/engine/test_risk_logic.py`)
- [ ] No new hardcoded secrets introduced
- [ ] No magic numbers added outside `portfolio_config.py`
- [ ] No file exceeds 1000 lines

## Checklist
- [ ] One problem per change (no unrelated fixes)
- [ ] Comments explain WHY, not WHAT
- [ ] No premature abstraction
- [ ] `.env.example` updated if new env vars added
