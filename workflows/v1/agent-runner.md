---
description: Agent runner workflow — run batch analysis agents across symbols, timeframes, and configurations with parallel execution and result aggregation
---

# Agent Runner Workflow

## When To Use
- Running batch analysis across all 6 symbols
- Executing multiple engine cycles (probe, hypothesis, scenario, decision) in sequence
- Running parallel backtests with different parameters
- Aggregating results from multiple agent runs

## Agent Types

| Agent | Runner | CLI Flag | Interval |
|---|---|---|---|
| Probe | `probe_runner.py` | `--probes` | 4 (once/hour) |
| Hypothesis | `hypothesis_runner.py` | `--hypothesis` | 4 (once/hour) |
| Scenario | `scenario_runner.py` | `--scenarios` | 8 (once/2h) |
| Decision | `decision_runner.py` | `--decision` | 1 (every cycle) |

## Batch Execution Pattern

```python
# Run all agents across all symbols
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT"]

for symbol in SYMBOLS:
    # 1. Load enriched data
    df = load_enriched(symbol, max_bars=2000)
    
    # 2. Run probe cycle
    probe_results = run_probe_cycle(df, symbol)
    
    # 3. Run hypothesis cycle
    hyp_results = run_hypothesis_cycle([symbol], bars=2000)
    
    # 4. Run scenario cycle
    scenario_results = run_scenario_cycle([symbol], bars=2000)
    
    # 5. Run decision cycle
    decision_results = run_decision_cycle([symbol], bars=2000)
```

## CLI Usage

```bash
# Run individual agents
python -m engine.v3.discovery.probe_runner --symbols BTCUSDT ETHUSDT --bars 2000
python -m engine.v3.discovery.hypothesis_runner --symbols BTCUSDT ETHUSDT --bars 2000
python -m engine.v3.discovery.scenario_runner --symbols BTCUSDT ETHUSDT --bars 2000
python -m engine.v3.discovery.decision_runner --symbols BTCUSDT ETHUSDT --bars 2000

# Run all agents via orchestrator
python -m engine.v3.orchestrator --probes --hypotheses --scenarios --decisions
```

## Loop Mode
```bash
# Continuous loop mode (runs every 15 minutes)
python -m engine.v3.orchestrator --loop --probes --hypotheses --scenarios --decisions \
    --probe-interval 4 --hypothesis-interval 4 --scenario-interval 8 --decision-interval 1
```

## Result Aggregation
Each agent produces:
- Per-symbol results (list of result objects)
- Summary statistics (counts, rates, averages)
- DB entries (hypothesis_registry, experiment_runs, etc.)
- JSON output files in `Backtest-Engine/results/`

## Error Handling
- Each symbol is processed independently — one failure doesn't stop others
- Errors are logged with symbol, agent type, and traceback
- Failed runs are reported in summary but don't crash the orchestrator

## Files
- `engine/v3/orchestrator.py` — main orchestrator with all agent flags
- `engine/v3/discovery/probe_runner.py`
- `engine/v3/discovery/hypothesis_runner.py`
- `engine/v3/discovery/scenario_runner.py`
- `engine/v3/discovery/decision_runner.py`
