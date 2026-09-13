# Bybit Data Ingestion Workers

Folder: `data/bybit/workers/`

Scripts for fetching OHLCV klines, funding rate history, open interest history, and orderbook snapshots from Bybit V5 public REST API. Also includes enrichment pipeline that merges OHLCV + OI + funding into a single unified parquet.

---

## File Layout

```
data/bybit/workers/
├── fetch_bybit_data.py            # MASTER: OHLCV all symbols
├── fetch_bybit_market_data.py     # MASTER: funding + OI + orderbook all symbols
├── fetch_bybit_enrich.py          # MASTER: enrich OHLCV with OI + funding all symbols
├── bybit_ingest_core.py           # Core: OHLCV client + gap detection + parquet I/O
├── bybit_market_data_core.py      # Core: funding + OI + orderbook client
├── bybit_enrich_core.py           # Core: merge OHLCV + OI + funding via merge_asof
├── README.md
├── BTCUSDT/
│   ├── fetch_BTCUSDT.py           # OHLCV for BTCUSDT
│   ├── fetch_market_data.py       # Funding + OI + orderbook for BTCUSDT
│   └── enrich.py                  # Enrich BTCUSDT OHLCV with OI + funding
├── ETHUSDT/
│   ├── fetch_ETHUSDT.py
│   ├── fetch_market_data.py
│   └── enrich.py
├── SOLUSDT/
│   ├── fetch_SOLUSDT.py
│   ├── fetch_market_data.py
│   └── enrich.py
├── XRPUSDT/
│   ├── fetch_XRPUSDT.py
│   ├── fetch_market_data.py
│   └── enrich.py
├── BNBUSDT/
│   ├── fetch_BNBUSDT.py
│   ├── fetch_market_data.py
│   └── enrich.py
└── DOGEUSDT/
    ├── fetch_DOGEUSDT.py
    ├── fetch_market_data.py
    └── enrich.py
```

---

## 1. OHLCV Klines

Data: `open`, `high`, `low`, `close`, `volume`, `turnover` for 5 timeframes per symbol.

### Per-symbol

```bash
# BTCUSDT — all 5 timeframes (4h, 1h, 30m, 15m, 5m)
python data/bybit/workers/BTCUSDT/fetch_BTCUSDT.py

# Options
python data/bybit/workers/BTCUSDT/fetch_BTCUSDT.py --dry-run
python data/bybit/workers/BTCUSDT/fetch_BTCUSDT.py --delay 2.0
python data/bybit/workers/BTCUSDT/fetch_BTCUSDT.py --testnet
```

Same pattern for `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `BNBUSDT`, `DOGEUSDT`.

### All symbols (master)

```bash
python data/bybit/workers/fetch_bybit_data.py
python data/bybit/workers/fetch_bybit_data.py --dry-run
python data/bybit/workers/fetch_bybit_data.py --symbols BTCUSDT ETHUSDT
```

### Output

```
data/bybit/{SYMBOL}/{TF}/{YYYY}/{MM}.parquet
```

---

## 2. Market Data (Funding Rate, Open Interest, Orderbook)

| Data Type | Endpoint | Frequency | Rows/Symbol |
|---|---|---|---|
| Funding Rate | `/v5/market/funding/history` | 8h | ~3,800 |
| Open Interest | `/v5/market/open-interest` | 1h | ~30,200 |
| Orderbook L2 | `/v5/market/orderbook` | Snapshot | 100 levels |

### Per-symbol

```bash
# BTCUSDT — funding + OI + orderbook
python data/bybit/workers/BTCUSDT/fetch_market_data.py

# Fetch only funding + OI (skip orderbook)
python data/bybit/workers/BTCUSDT/fetch_market_data.py --data-types funding oi

# Dry run
python data/bybit/workers/BTCUSDT/fetch_market_data.py --dry-run
```

Same pattern for `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `BNBUSDT`, `DOGEUSDT`.

### All symbols (master)

```bash
python data/bybit/workers/fetch_bybit_market_data.py
python data/bybit/workers/fetch_bybit_market_data.py --data-types funding oi --dry-run
```

### Output

```
data/bybit/{SYMBOL}/funding_rate/{YYYY}/{MM}.parquet
data/bybit/{SYMBOL}/open_interest/{YYYY}/{MM}.parquet
data/bybit/{SYMBOL}/orderbook/{YYYY}/{MM}/{DD}.parquet
```

---

## 3. Enriched OHLCV (Merged Parquet)

Merges OHLCV + open_interest + funding_rate into a single parquet per (symbol, timeframe, year, month) using `merge_asof` (backward fill by nearest timestamp — no future leakage).

Schema: `timestamp, open, high, low, close, volume, turnover, open_interest, funding_rate`

### Per-symbol

```bash
python data/bybit/workers/BTCUSDT/enrich.py
python data/bybit/workers/BTCUSDT/enrich.py --dry-run
python data/bybit/workers/BTCUSDT/enrich.py --tfs 1h 4h
```

Same pattern for `ETHUSDT`, `SOLUSDT`, `XRPUSDT`, `BNBUSDT`, `DOGEUSDT`.

### All symbols (master)

```bash
python data/bybit/workers/fetch_bybit_enrich.py
python data/bybit/workers/fetch_bybit_enrich.py --dry-run
python data/bybit/workers/fetch_bybit_enrich.py --symbols BTCUSDT ETHUSDT
```

### Output

```
data/bybit/{SYMBOL}/enriched/{TF}/{YYYY}/{MM}.parquet
```

---

## Symbols & Timeframes

| Symbols | OHLCV Timeframes (priority order) |
|---|---|
| BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, BNBUSDT, DOGEUSDT | **4h** → 1h → 30m → 15m → 5m |

| Data Type | API Endpoint | Auth Required |
|---|---|---|
| OHLCV | `GET /v5/market/kline` | No |
| Funding Rate | `GET /v5/market/funding/history` | No |
| Open Interest | `GET /v5/market/open-interest` | No |
| Orderbook | `GET /v5/market/orderbook` | No |

- **Category:** `linear` (USDT perpetuals)
- **Rate limit:** 600 req / 5s per IP
- **Script delay:** 1.0s between requests (safe default)
- **Retries:** 6 attempts with exponential backoff
- **Max records/request:** 1,000 (OHLCV), 200 (funding/OI)

---

## Quick Command Reference

```bash
# --- OHLCV ---
python data/bybit/workers/BTCUSDT/fetch_BTCUSDT.py
python data/bybit/workers/ETHUSDT/fetch_ETHUSDT.py
python data/bybit/workers/SOLUSDT/fetch_SOLUSDT.py
python data/bybit/workers/XRPUSDT/fetch_XRPUSDT.py
python data/bybit/workers/BNBUSDT/fetch_BNBUSDT.py
python data/bybit/workers/DOGEUSDT/fetch_DOGEUSDT.py
python data/bybit/workers/fetch_bybit_data.py              # master

# --- Market Data (funding + OI + orderbook) ---
python data/bybit/workers/BTCUSDT/fetch_market_data.py
python data/bybit/workers/ETHUSDT/fetch_market_data.py
python data/bybit/workers/SOLUSDT/fetch_market_data.py
python data/bybit/workers/XRPUSDT/fetch_market_data.py
python data/bybit/workers/BNBUSDT/fetch_market_data.py
python data/bybit/workers/DOGEUSDT/fetch_market_data.py
python data/bybit/workers/fetch_bybit_market_data.py       # master

# --- Enriched (OHLCV + OI + funding merged) ---
python data/bybit/workers/BTCUSDT/enrich.py
python data/bybit/workers/ETHUSDT/enrich.py
python data/bybit/workers/SOLUSDT/enrich.py
python data/bybit/workers/XRPUSDT/enrich.py
python data/bybit/workers/BNBUSDT/enrich.py
python data/bybit/workers/DOGEUSDT/enrich.py
python data/bybit/workers/fetch_bybit_enrich.py             # master
```

---

## Run from repo root

All scripts must be executed from the repository root so relative paths resolve:
```bash
cd ./pipeline/engine
python data\bybit\workers\BTCUSDT\fetch_BTCUSDT.py
```

---

## 4. BACKTESTER Strategy Pipeline

Data ingested here feeds directly into the BACKTESTER research framework at:
`departments/strategy_research/BACKTESTER/`

### Strategy: PullbackToTrend

| Parameter | Value |
|-----------|-------|
| Execution TF | 15m |
| Context TF | 4h |
| Entry | RSI(14) < 40 on 15m + ADX(14) ≥ 25 on 4h + 4h bar bullish |
| Sessions | London (07:00–12:00 UTC), NY Late (20:00–00:00 UTC) |
| Stop Loss | 1.5 × ATR(14) on 15m |
| Take Profit | 2.0 × ATR(14) on 15m |
| Live Leverage | 4x |
| Live Fees | 0.055% taker |

### Validated Results (D1-D7 pipeline, 2023-2026)

| Symbol | Sharpe (live est.) | Profit Factor | MaxDD (4x) | WF Windows |
|--------|--------------------|---------------|------------|------------|
| BTCUSDT | 4.51 | 3.00 | 8.7% | 10/10 |
| ETHUSDT | 5.33 | 3.00 | 9.3% | 10/10 |
| SOLUSDT | 5.83 | 3.00 | 8.2% | 10/10 |
| XRPUSDT | 4.39 | 3.00 | 8.9% | 10/10 |
| BNBUSDT | 5.33 | 3.00 | 7.5% | 10/10 |
| DOGEUSDT | 4.98 | 3.00 | 21.8% | 10/10 |

### Run the full D1-D7 research pipeline

```bash
# All phases for all 6 symbols
python departments/strategy_research/BACKTESTER/scripts/run_all_symbols.py

# Individual phases
python departments/strategy_research/BACKTESTER/scripts/run_all_symbols.py --skip-d1 --skip-d5 --skip-d6   # D7 only
python departments/strategy_research/BACKTESTER/scripts/run_all_symbols.py --skip-d6 --skip-d7              # D1 + D5 only

# Individual symbol + phase
python departments/strategy_research/BACKTESTER/scripts/d5_alpha_extraction.py --symbol SOLUSDT
python departments/strategy_research/BACKTESTER/scripts/d6_dynamic_risk_probe.py --symbol ETHUSDT
python departments/strategy_research/BACKTESTER/scripts/d7_live_reality_probe.py --symbol BTCUSDT --skip-funding-fetch
```

### Scan for live signals

```bash
# Generates signals_latest.csv and signals_watchlist.csv
python departments/strategy_research/BACKTESTER/strategy/signals/scan_signals.py
```

Output files:
```
departments/strategy_research/BACKTESTER/strategy/signals/signals_latest.csv   # Confirmed + approaching signals (last 50h)
departments/strategy_research/BACKTESTER/strategy/signals/signals_watchlist.csv # Current bar status for all 6 symbols
```

Signal types:
- `CONFIRMED` — All 4 gates passed, inside session window → actionable
- `APPROACHING` — RSI 40-50, trend confirmed → set alert
- `OFF_SESSION` — Signal fired outside London/NY Late → monitor only
- `NO_SIGNAL` — Not ready

### Key documents

```
departments/strategy_research/BACKTESTER/docs/METHODOLOGY_6_SYMBOLS.md  # Full D1-D7 methodology + results
departments/strategy_research/BACKTESTER/docs/STRATEGY.md               # Manual trading playbook (7-gate process)
departments/strategy_research/BACKTESTER/strategy/BTCUSDT.yaml           # Per-symbol strategy configs
departments/strategy_research/BACKTESTER/results/D5_all_symbols_summary.csv
departments/strategy_research/BACKTESTER/results/D6_all_symbols_summary.csv
departments/strategy_research/BACKTESTER/results/D7_all_symbols_summary.csv
```
