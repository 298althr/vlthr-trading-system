# VLTHR Gate Specification

## Formal Preconditions
- Raw 15m OHLCV parquet must exist for symbol
- 4h context parquet must exist for symbol  
- Enriched DataFrame must contain: timestamp, open, high, low, close, volume, rsi, adx, atr, session, bull_4h
- `get_symbol_params(symbol)` must return valid per-symbol config

## Gate Definitions

| Gate | Label | Boolean Expression | Expected Pass Rate (live) |
|------|-------|-------------------|---------------------------|
| G1 Session | Session | `session in {"london", "ny_late"}` | ~33% (2 of 6 sessions) |
| G2 ADX | ADX ≥25 | `adx_4h >= params["adx_min"]` | ~60% |
| G3 Direction | 4h Direction | `log_ret_4h > 0` | ~50% |
| G4 RSI | RSI <40 | `rsi_15m < params["rsi_threshold"]` | ~30% |
| G5 EMA Trend | EMA8>EMA21 | `ema_8_4h > ema_21_4h` | ~55% |

## State Transition Rules

```
gates_passed = count(G1..G4 true)  // EMA is bonus, not counted
dqs = score_signal(...)  // 0-100

IF dqs >= 50 AND G1:
    signal_type = "CONFIRMED"
    quality = "HIGH"
ELIF dqs >= 50:
    signal_type = "OFF_SESSION"
    quality = "MONITOR"
ELIF dqs >= 50 AND G1:   // Note: identical to first branch — deprecated
    signal_type = "APPROACHING"
    quality = "WATCH"
ELSE:
    signal_type = "NO_SIGNAL"
    quality = "SKIP"
```

## Postconditions
- `gates_passed` ∈ {0, 1, 2, 3, 4}
- All gate booleans are actual `bool` (not None/NaN)
- DQS ∈ [0, 100]
- `signal_type` is one of: CONFIRMED, OFF_SESSION, APPROACHING, NO_SIGNAL
- `quality` is one of: HIGH, MONITOR, WATCH, SKIP

## Invariants
- `gate_bull_4h` does NOT affect `gates_passed` count
- `gate_bull_4h` = True → quality modifier = "Quality: HIGH"
- `gate_bull_4h` = False → quality modifier = "Quality: MEDIUM"
- SL < entry < TP (asserted in `get_sl_tp`)
