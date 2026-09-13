---
description: Data management workflow — ingestion, validation, multi-timeframe merging, enrichment for crypto trading data
---

# Data Management Workflow

## When To Use
- Ingesting new data from exchanges (Bybit, Binance)
- Validating OHLCV data integrity
- Merging multi-timeframe data (15m execution + 4h context)
- Enriching bars with external metrics (LS ratio, liquidations, OI)

## Steps

1. **Ingest raw OHLCV data**
   - Source: Bybit API for perpetual futures (BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, BNBUSDT, DOGEUSDT)
   - Timeframes: 15m (execution), 4h (context), 1D (bias)
   - Store: `data/bybit/{SYMBOL}/{TF}/*.parquet`
   - Timestamps: UTC, Bybit convention (timestamp = candle OPEN)

2. **Validate data integrity (BLOCKING — Jul 2026 hardening)**
   - Validation is now **blocking**: if `validate_ohlcv` fails, data is **quarantined** (not written)
   - Previous behavior wrote bad data with `status='issues'`; now returns `status='quarantined'` and skips write
   ```python
   def validate_ohlcv(df):
       required = ['open', 'high', 'low', 'close', 'volume', 'timestamp']
       assert all(col in df.columns for col in required)
       assert df['timestamp'].is_monotonic_increasing
       assert (df['high'] >= df['low']).all()
       assert (df['high'] >= df['open']).all()
       assert (df['high'] >= df['close']).all()
       assert (df['low'] <= df['open']).all()
       assert (df['low'] <= df['close']).all()
       assert df['timestamp'].nunique() == len(df)
   ```
   - If validation fails: log warning, set `status='quarantined'`, return early (no write)

3. **Compute enriched features**
   - Technical: RSI, ADX, EMA (8/21/50/200), ATR, Bollinger Bands
   - Structure: volume ratios, ATR percentile, EMA alignment
   - Context (4h): ADX, log returns, bull/bear regime — **MUST shift 4h timestamps +4h before merge_asof**
   - External: Binance metrics (LS ratio, OI, funding), Chainticks liquidations

4. **Multi-timeframe merge (CRITICAL — label leakage prevention)**
   - 4h bar timestamps represent the OPEN of the candle
   - Close/ADX/EMA values are only known at the CLOSE (4h later)
   - **MUST shift 4h timestamps forward by 4h before merge_asof**
   - Use `direction="backward"` so 15m bars only see CLOSED 4h bars
   - Without this shift: 3h45m look-ahead leakage

5. **External data enrichment**
   - Binance futures metrics: 5min granularity, merge onto 15m bars
   - Chainticks liquidations: event-based, aggregate to 15m bars
   - Check coverage % and date range alignment

6. **Gap detection and handling**
   - Check for missing bars (gap in timestamp sequence)
   - Flag gaps > 1 bar for investigation
   - Forward-fill small gaps, interpolate if needed

## Critical Bug Prevention
The `ctx_log_ret_4h` label leakage bug was caused by NOT shifting 4h timestamps before merge. This caused:
- Phase 5: IC inflated from 0.07 to 0.44
- Phase 8: Direction accuracy inflated from 48.9% to 73.1%
- Always verify: `correlation(ctx_log_ret_4h, 1h_fwd_return) ~= 0` after merge

## Institutional Hardening (Jul 2026)

### Blocking Data Validation
- `scheduler_core.py`: `validate_ohlcv` failure now **quarantines** data (skips write) instead of writing with `status='issues'`
- Prevents bad OHLCV bars (nulls, invalid highs/lows, duplicate timestamps) from entering the pipeline

### TLS Verification
- All API calls now use TLS verification (`verify=True` by default)
- Removed `verify=False` from 6 call sites across 4 files:
  - `scheduler_core.py` (2 sites)
  - `bybit_ingest_core.py` (1 site + removed urllib3 warning suppression)
  - `bybit_market_data_core.py` (1 site)
  - `telegram_alerts.py` (1 site)

### Per-Symbol Data Freshness
- `safety_layer.py`: `_check_data_freshness` now checks **all 5 active symbols** (BTC, ETH, SOL, XRP, BNB)
- Previously only checked BTCUSDT — staleness in other symbols could go undetected
- Stale data in any symbol halts the pipeline

## Files
- `departments/strategy_research/BACKTESTER/strategy/signals/confidence_engine.py` — `load_enriched()`, `_load_4h_context()`
- `paper_trade_unzipped/vlthr-signal-dashboard/engine/replay_runner.py` — `_load_dual_tf()`
- `Backtest-Engine/verify_leakage.py` — leakage verification script
- `data/bybit/workers/scheduler_core.py` — blocking validation, TLS verification
- `data/bybit/workers/bybit_ingest_core.py` — TLS verification, `validate_ohlcv`
- `data/bybit/workers/bybit_market_data_core.py` — TLS verification
- `paper_trade_unzipped/vlthr-signal-dashboard/engine/safety_layer.py` — per-symbol freshness check
