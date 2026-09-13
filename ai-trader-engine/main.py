"""VLTHR AI Trader Engine — FastAPI backend.
Exposes D1-D7 pipeline stages, strategy management, and NVIDIA AI proxy.
"""
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
import uuid
import httpx
import os
import asyncio
from pathlib import Path
import json
import math

# Explicitly load .env from the same directory as this file
_env_path = Path(__file__).parent / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            _k = _k.strip()
            _v = _v.strip()
            # Override env var if missing or empty so .env always wins
            if not os.environ.get(_k):
                os.environ[_k] = _v

# ── NaN-safe JSON serialization helper ───────────────────────────────────
def _sanitize_nan(obj):
    """Recursively replace NaN/Inf/-Inf with None for JSON safety."""
    if isinstance(obj, dict):
        return {k: _sanitize_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_nan(v) for v in obj]
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
    return obj

app = FastAPI(title="VLTHR Backtest Engine", version="1.0.0")

_ALLOWED_ORIGINS = [
    "http://localhost:5174",
    "http://localhost:3002",
    os.environ.get("NGROK_DOMAIN", ""),
]
_ALLOWED_ORIGINS = [o for o in _ALLOWED_ORIGINS if o]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

def get_nvidia_key() -> str:
    return os.environ.get("NVIDIA_API_KEY", "")

# ============================================================================
# Pydantic Models
# ============================================================================

class CandleRequest(BaseModel):
    symbol: str
    start: str
    end: str
    interval: str

class OrderBookRequest(BaseModel):
    symbol: str
    depth: int = 10

class FeatureCalculateRequest(BaseModel):
    symbol: str
    start: str
    end: str
    interval: str
    features: List[str]

class StrategyCreateRequest(BaseModel):
    name: str
    type: str
    parameters: Dict[str, Any]
    rules: List[str]

class BacktestStartRequest(BaseModel):
    strategy_id: str
    symbol: str
    start: str
    end: str
    interval: str
    initial_capital: float = 10000

class ValidationRequest(BaseModel):
    backtest_id: Optional[str] = None
    strategyId: Optional[str] = None
    strategy_id: Optional[str] = None
    symbol: Optional[str] = None
    timeframe: Optional[str] = None
    params: Optional[Dict[str, Any]] = None

class DecisionCreateRequest(BaseModel):
    context: Dict[str, Any]
    hypothesis: Dict[str, Any]
    backtest: Dict[str, Any]
    validation: Dict[str, Any]
    belief: Dict[str, Any]
    recommendation: Dict[str, Any]

class MemoryStoreRequest(BaseModel):
    key: str
    value: Any
    metadata: Optional[Dict[str, Any]] = None

class TokenUpdateRequest(BaseModel):
    symbol: str
    price: float
    volume: float

class ChatMessage(BaseModel):
    role: str
    content: str
    tool_call_id: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None

class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    model: str
    tools: Optional[List[Dict[str, Any]]] = None
    stream: bool = False

# ============================================================================
# Pipeline import (deferred so missing deps don't crash on import)
# ============================================================================

try:
    from pipeline.stages import (
        d1_audit, d4_features,
        d5a_mechanical, d5b_optimised, d5c_dqs,
        d6_risk, d7_reality, run_full_pipeline,
    )
    from pipeline.data import load_ohlcv, load_funding, load_open_interest, data_root
    from pipeline.indicators import add_all
    from pipeline.probe import probe_symbol, probe_compare, probe_suggest
    _PIPELINE_AVAILABLE = True
except ImportError as _pip_err:
    _PIPELINE_AVAILABLE = False
    _pip_err_msg = str(_pip_err)

# ============================================================================
# In-Memory Storage
# ============================================================================

strategies: Dict[str, Dict] = {}
backtests: Dict[str, Dict] = {}
decisions: Dict[str, Dict] = {}
memory: Dict[str, Dict] = {}

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()

def _require_pipeline():
    if not _PIPELINE_AVAILABLE:
        raise HTTPException(status_code=503, detail=f"Pipeline unavailable — install pandas/numpy: {_pip_err_msg}")

# ============================================================================
# Market Data Endpoints
# ============================================================================

@app.get("/candles")
async def get_candles(symbol: str, start: str, end: str, interval: str, limit: int = 500):
    """Fetch OHLCV candle data from Parquet store."""
    _require_pipeline()
    try:
        df = await asyncio.get_event_loop().run_in_executor(
            None, load_ohlcv, symbol, interval, start, end
        )
        df = df.tail(limit)
        df["timestamp"] = df["timestamp"].astype(str)
        return {"symbol": symbol, "interval": interval, "count": len(df),
                "data": df[["timestamp", "open", "high", "low", "close", "volume"]].to_dict(orient="records")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/funding")
async def get_funding(symbol: str, start: str, end: str):
    """Fetch funding rate history from Parquet store."""
    _require_pipeline()
    try:
        df = await asyncio.get_event_loop().run_in_executor(
            None, load_funding, symbol, start, end
        )
        df["timestamp"] = df["timestamp"].astype(str)
        return {"symbol": symbol, "count": len(df), "data": df.to_dict(orient="records")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/open-interest")
async def get_open_interest(symbol: str, start: str, end: str):
    """Fetch open interest history from Parquet store."""
    _require_pipeline()
    try:
        df = await asyncio.get_event_loop().run_in_executor(
            None, load_open_interest, symbol, start, end
        )
        df["timestamp"] = df["timestamp"].astype(str)
        return {"symbol": symbol, "count": len(df), "data": df.to_dict(orient="records")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/data/range")
async def get_data_range(symbol: str, interval: str):
    """Return the earliest and latest available candle timestamps for a symbol/interval."""
    _require_pipeline()
    try:
        base = data_root()
        # Try bybit/ first, then capital.com/
        sym_dir = None
        for subdir in ["bybit", "capital.com"]:
            candidate = base / subdir / symbol / interval
            if candidate.exists():
                sym_dir = candidate
                break
        if not sym_dir:
            sym_dir = base / symbol / interval
        if not sym_dir.exists():
            raise HTTPException(status_code=404, detail=f"No data directory for {symbol}/{interval}")
        # Read all parquet files in year subdirectories
        timestamps = []
        for year_dir in sorted(sym_dir.iterdir()):
            if not year_dir.is_dir():
                continue
            for pq in year_dir.glob("*.parquet"):
                df = pd.read_parquet(pq)
                ts_col = next((c for c in df.columns if "time" in c.lower()), df.columns[0])
                timestamps.extend(pd.to_datetime(df[ts_col], utc=True).tolist())
        if not timestamps:
            raise HTTPException(status_code=404, detail=f"No parquet files found for {symbol}/{interval}")
        timestamps = sorted(set(timestamps))
        return {
            "symbol": symbol,
            "interval": interval,
            "earliest": timestamps[0].isoformat(),
            "latest": timestamps[-1].isoformat(),
            "total_bars": len(timestamps),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Data range error: {str(e)}")

@app.get("/data/symbols")
async def list_symbols():
    """List all symbols with available data."""
    _require_pipeline()
    root = data_root()
    symbols = []
    # Crypto symbols from bybit/
    bybit_dir = root / "bybit"
    if bybit_dir.exists():
        symbols.extend([d.name for d in bybit_dir.iterdir() if d.is_dir() and not d.name.startswith(".")])
    # Forex/metal symbols from capital.com/
    capital_dir = root / "capital.com"
    if capital_dir.exists():
        symbols.extend([d.name for d in capital_dir.iterdir() if d.is_dir() and not d.name.startswith(".")])
    return {"symbols": sorted(set(symbols))}

@app.get("/data/intervals")
async def list_intervals(symbol: str):
    """List available intervals for a symbol."""
    _require_pipeline()
    root = data_root()
    # Try bybit/ first, then capital.com/
    for subdir in ["bybit", "capital.com"]:
        sym_dir = root / subdir / symbol
        if sym_dir.exists():
            intervals = [d.name for d in sym_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
            return {"symbol": symbol, "intervals": sorted(intervals)}
    raise HTTPException(status_code=404, detail="Symbol not found")

# ============================================================================
# Feature Endpoints
# ============================================================================

@app.get("/features")
async def get_features(symbol: str, interval: str, start: str, end: str):
    """Compute technical indicators for a symbol/interval range."""
    _require_pipeline()
    try:
        df = await asyncio.get_event_loop().run_in_executor(
            None, load_ohlcv, symbol, interval, start, end
        )
        df = add_all(df)
        df["timestamp"] = df["timestamp"].astype(str)
        cols = ["timestamp", "rsi", "atr", "adx", "ema_fast", "ema_slow", "ema_200",
                "bb_lower", "bb_mid", "bb_upper", "vwap", "log_ret", "ctx_bull"]
        sample = df[cols].tail(100).round(4)
        return {"symbol": symbol, "interval": interval, "count": len(df),
                "sample": sample.to_dict(orient="records")}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ============================================================================
# Strategy Endpoints
# ============================================================================

@app.post("/strategy/create")
async def create_strategy(req: StrategyCreateRequest):
    """Register a strategy and return its ID."""
    strategy_id = str(uuid.uuid4())
    strategies[strategy_id] = {
        "id": strategy_id,
        "name": req.name,
        "type": req.type,
        "parameters": req.parameters,
        "rules": req.rules,
        "created_at": _utcnow(),
    }
    return {"id": strategy_id, "status": "created"}

@app.get("/strategy/{strategy_id}")
async def get_strategy(strategy_id: str):
    if strategy_id not in strategies:
        raise HTTPException(status_code=404, detail="Strategy not found")
    return strategies[strategy_id]

@app.post("/strategy/mutate")
async def mutate_strategy(req: Dict[str, Any]):
    """Merge new parameters into an existing strategy."""
    sid = req.get("id")
    if not sid or sid not in strategies:
        raise HTTPException(status_code=404, detail="Strategy not found")
    strategies[sid]["parameters"].update(req.get("params", {}))
    strategies[sid]["updated_at"] = _utcnow()
    return {"id": sid, "status": "mutated"}

@app.get("/strategy")
async def list_strategies():
    return {"strategies": list(strategies.values())}

# ============================================================================
# Backtest Endpoints — real D5A execution
# ============================================================================

def _resolve_strategy_params(strategy_id: str) -> dict:
    """Get merged parameters from stored strategy."""
    s = strategies.get(strategy_id, {})
    return s.get("parameters", {})


async def _run_backtest_async(backtest_id: str):
    """Execute backtest in thread pool and store result."""
    bt = backtests[backtest_id]
    try:
        params = _resolve_strategy_params(bt["strategy_id"])
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            d5a_mechanical,
            bt["symbol"], bt["interval"], bt["start"], bt["end"],
            params, bt["initial_capital"],
        )
        bt["status"] = "completed"
        bt["result"] = result
    except Exception as e:
        bt["status"] = "failed"
        bt["error"] = str(e)


@app.post("/backtest/start")
async def start_backtest(req: BacktestStartRequest, background_tasks: BackgroundTasks):
    """Queue a backtest and return its ID immediately."""
    _require_pipeline()
    if req.strategy_id not in strategies:
        raise HTTPException(status_code=404, detail="Strategy not found — call create_strategy first")
    backtest_id = str(uuid.uuid4())
    backtests[backtest_id] = {
        "id": backtest_id,
        "strategy_id": req.strategy_id,
        "symbol": req.symbol,
        "start": req.start,
        "end": req.end,
        "interval": req.interval,
        "initial_capital": req.initial_capital,
        "status": "running",
        "created_at": _utcnow(),
    }
    background_tasks.add_task(_run_backtest_async, backtest_id)
    return {"id": backtest_id, "status": "running"}


@app.get("/backtest/status/{backtest_id}")
@app.get("/backtest/status")
async def get_backtest_status(backtest_id: str = None, id: str = None):
    bid = backtest_id or id
    if not bid or bid not in backtests:
        raise HTTPException(status_code=404, detail="Backtest not found")
    bt = backtests[bid]
    progress = 100 if bt["status"] == "completed" else (0 if bt["status"] == "failed" else 50)
    return {"id": bid, "status": bt["status"], "progress": progress}


@app.get("/backtest/result/{backtest_id}")
@app.get("/backtest/result")
async def get_backtest_result(backtest_id: str = None, id: str = None):
    bid = backtest_id or id
    if not bid or bid not in backtests:
        raise HTTPException(status_code=404, detail="Backtest not found")
    bt = backtests[bid]
    if bt["status"] == "running":
        return {"id": bid, "status": "running", "message": "Still running — poll /backtest/status"}
    if bt["status"] == "failed":
        raise HTTPException(status_code=500, detail=bt.get("error", "Backtest failed"))
    return {"id": bid, "status": "completed", **bt.get("result", {})}


@app.post("/backtest/cancel/{backtest_id}")
@app.post("/backtest/cancel")
async def cancel_backtest(backtest_id: str = None, id: str = None):
    bid = backtest_id or id
    if not bid or bid not in backtests:
        raise HTTPException(status_code=404, detail="Backtest not found")
    backtests[bid]["status"] = "cancelled"
    return {"id": bid, "status": "cancelled"}

# ============================================================================
# D1-D7 Pipeline Stage Endpoints
# ============================================================================

class PipelineRequest(BaseModel):
    symbol: str
    interval: str
    start: str
    end: str
    strategy_id: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    initial_capital: float = 10000
    leverage: float = 4.0


def _get_params(req: PipelineRequest) -> dict:
    if req.strategy_id and req.strategy_id in strategies:
        return {**strategies[req.strategy_id]["parameters"], **(req.params or {})}
    return req.params or {}


@app.post("/pipeline/d1")
async def run_d1(req: PipelineRequest):
    """D1 — Data quality audit."""
    _require_pipeline()
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, d1_audit, req.symbol, req.interval, req.start, req.end
        )
        return _sanitize_nan(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/pipeline/d4")
async def run_d4(req: PipelineRequest):
    """D4 — Feature discovery & domain weights."""
    _require_pipeline()
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, d4_features, req.symbol, req.interval, req.start, req.end, _get_params(req)
        )
        return _sanitize_nan(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/pipeline/d5a")
async def run_d5a(req: PipelineRequest):
    """D5A — Mechanical base backtest."""
    _require_pipeline()
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, d5a_mechanical, req.symbol, req.interval, req.start, req.end,
            _get_params(req), req.initial_capital
        )
        return _sanitize_nan(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/pipeline/d5b")
async def run_d5b(req: PipelineRequest):
    """D5B — Parameter optimisation sweep (IS/OOS split)."""
    _require_pipeline()
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, d5b_optimised, req.symbol, req.interval, req.start, req.end,
            _get_params(req), req.initial_capital
        )
        return _sanitize_nan(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/pipeline/d5c")
async def run_d5c(req: PipelineRequest):
    """D5C — DQS-scored backtest."""
    _require_pipeline()
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, d5c_dqs, req.symbol, req.interval, req.start, req.end,
            _get_params(req), req.initial_capital
        )
        return _sanitize_nan(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/pipeline/d6")
async def run_d6(req: PipelineRequest):
    """D6 — Risk sizing with leverage and funding costs."""
    _require_pipeline()
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, d6_risk, req.symbol, req.interval, req.start, req.end,
            _get_params(req), req.initial_capital, req.leverage
        )
        return _sanitize_nan(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/pipeline/d7")
async def run_d7(req: PipelineRequest):
    """D7 — Reality probe across stress scenarios."""
    _require_pipeline()
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, d7_reality, req.symbol, req.interval, req.start, req.end,
            _get_params(req), req.initial_capital
        )
        return _sanitize_nan(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/pipeline/full")
async def run_full(req: PipelineRequest):
    """Run complete D1→D7 pipeline. Returns all stage results."""
    _require_pipeline()
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, run_full_pipeline, req.symbol, req.interval, req.start, req.end,
            _get_params(req), req.initial_capital
        )
        return _sanitize_nan(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# Probe Endpoints — Data Discovery & Pattern Detection
# ============================================================================

@app.post("/probe/symbol")
async def probe_symbol_endpoint(req: PipelineRequest):
    """Probe a single symbol/timeframe: regimes, patterns, feature ranks, summary."""
    _require_pipeline()
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, probe_symbol, req.symbol, req.interval, req.start, req.end
        )
        return _sanitize_nan(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/probe/compare")
async def probe_compare_endpoint(req: PipelineRequest):
    """Compare multiple symbols on the same timeframe. Pass symbols as comma-separated in req.symbol."""
    _require_pipeline()
    symbols = [s.strip() for s in req.symbol.split(",")]
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, probe_compare, symbols, req.interval, req.start, req.end
        )
        return _sanitize_nan(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/probe/suggest")
async def probe_suggest_endpoint(req: PipelineRequest):
    """After probing, suggest concrete strategy parameters for the detected regime."""
    _require_pipeline()
    archetype = req.params.get("archetype") if req.params else None
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, probe_suggest, req.symbol, req.interval, req.start, req.end, archetype
        )
        return _sanitize_nan(result)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Legacy validation aliases (AI tools use these names) ─────────────────────

@app.post("/validate/walkforward")
async def validate_walkforward(req: ValidationRequest):
    """Walk-forward = D5B IS/OOS split."""
    _require_pipeline()
    sid = req.strategy_id or req.strategyId or req.backtest_id
    symbol = req.symbol or "BTCUSDT"
    timeframe = req.timeframe or "15m"
    bt = backtests.get(sid or "", {})
    params = _resolve_strategy_params(bt.get("strategy_id", "")) if bt else {}
    start = bt.get("start", "")
    end = bt.get("end", "")
    if not start or not end:
        raise HTTPException(status_code=422, detail="Missing date range. Call /data/range first to discover available dates.")
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, d5b_optimised, symbol, timeframe, start, end, params
        )
        return _sanitize_nan({"result": "PASS" if result["passed"] else "FAIL", **result})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/validate/montecarlo")
async def validate_montecarlo(req: ValidationRequest):
    """Monte Carlo = D7 reality probe stress scenarios."""
    _require_pipeline()
    sid = req.strategy_id or req.strategyId or req.backtest_id
    bt = backtests.get(sid or "", {})
    params = _resolve_strategy_params(bt.get("strategy_id", "")) if bt else {}
    symbol = req.symbol or bt.get("symbol", "BTCUSDT")
    timeframe = req.timeframe or bt.get("interval", "15m")
    start = bt.get("start", "")
    end = bt.get("end", "")
    if not start or not end:
        raise HTTPException(status_code=422, detail="Missing date range. Call /data/range first to discover available dates.")
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, d7_reality, symbol, timeframe, start, end, params
        )
        return _sanitize_nan({"result": "PASS" if result["passed"] else "FAIL", **result})
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/validate/oos")
async def validate_oos(req: ValidationRequest):
    """Out-of-sample = D5B OOS metrics."""
    _require_pipeline()
    sid = req.strategy_id or req.strategyId or req.backtest_id
    bt = backtests.get(sid or "", {})
    params = _resolve_strategy_params(bt.get("strategy_id", "")) if bt else {}
    symbol = req.symbol or bt.get("symbol", "BTCUSDT")
    timeframe = req.timeframe or bt.get("interval", "15m")
    start = bt.get("start", "")
    end = bt.get("end", "")
    if not start or not end:
        raise HTTPException(status_code=422, detail="Missing date range. Call /data/range first to discover available dates.")
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None, d5b_optimised, symbol, timeframe, start, end, params
        )
        return _sanitize_nan({
            "result": "PASS" if result["passed"] else "FAIL",
            "oos_sharpe": result.get("oos_sharpe"),
            "is_sharpe": result.get("is_sharpe"),
            "best_params": result.get("best_params"),
        })
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

# ============================================================================
# Decision Endpoints
# ============================================================================

@app.post("/decision/create")
async def create_decision(req: DecisionCreateRequest):
    """Create a trading decision."""
    decision_id = str(uuid.uuid4())
    decisions[decision_id] = {
        "id": decision_id,
        "context": req.context,
        "hypothesis": req.hypothesis,
        "backtest": req.backtest,
        "validation": req.validation,
        "belief": req.belief,
        "recommendation": req.recommendation,
        "outcome": None,
        "created_at": datetime.utcnow().isoformat()
    }
    return {"id": decision_id}

@app.get("/decision/{decision_id}")
async def get_decision(decision_id: str):
    """Get decision by ID."""
    if decision_id not in decisions:
        raise HTTPException(status_code=404, detail="Decision not found")
    return decisions[decision_id]

# ============================================================================
# Memory Endpoints
# ============================================================================

@app.post("/memory/store")
async def store_memory(req: MemoryStoreRequest):
    """Store a memory entry."""
    memory_id = str(uuid.uuid4())
    memory[memory_id] = {
        "id": memory_id,
        "key": req.key,
        "value": req.value,
        "metadata": req.metadata,
        "created_at": datetime.utcnow().isoformat()
    }
    return {"id": memory_id}

@app.get("/memory/search")
async def search_memory(query: str):
    """Search memory entries."""
    results = [m for m in memory.values() if query.lower() in str(m["value"]).lower()]
    return {"query": query, "results": results}


# ============================================================================
# Session Logs
# ============================================================================

LOGS_DIR = Path(__file__).parent / "logs" / "sessions"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

class LogSaveRequest(BaseModel):
    session_id: str
    label: str
    messages: List[Dict[str, Any]]
    thinking: Optional[List[Dict[str, Any]]] = None

@app.post("/logs/save")
async def save_session_log(req: LogSaveRequest):
    """Persist a chat session log to disk."""
    file_path = LOGS_DIR / f"{req.session_id}.json"
    data = {
        "session_id": req.session_id,
        "label": req.label,
        "saved_at": datetime.utcnow().isoformat(),
        "messages": req.messages,
        "thinking": req.thinking or [],
    }
    file_path.write_text(json.dumps(data, indent=2))
    return {"status": "saved", "path": str(file_path)}

@app.get("/logs")
async def list_session_logs():
    """List all saved session log files."""
    logs = []
    for f in sorted(LOGS_DIR.glob("*.json"), reverse=True):
        try:
            meta = json.loads(f.read_text())
            logs.append({"session_id": meta.get("session_id"), "label": meta.get("label"), "saved_at": meta.get("saved_at"), "message_count": len(meta.get("messages", []))})
        except Exception:
            pass
    return {"logs": logs}

@app.get("/logs/{session_id}")
async def get_session_log(session_id: str):
    """Retrieve a specific session log."""
    file_path = LOGS_DIR / f"{session_id}.json"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Log not found")
    return json.loads(file_path.read_text())

# ============================================================================
# Health Check
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "timestamp": _utcnow(),
        "pipeline_available": _PIPELINE_AVAILABLE,
        "strategies_loaded": len(strategies),
        "backtests_total": len(backtests),
    }

# ============================================================================
# AI Chat Proxy (NVIDIA)
# ============================================================================

@app.post("/ai/chat")
async def proxy_chat(req: ChatRequest):
    """Proxy chat requests to NVIDIA API to avoid CORS."""
    key = get_nvidia_key()
    if not key:
        raise HTTPException(status_code=500, detail="NVIDIA_API_KEY not configured")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key}",
    }

    body = {
        "model": req.model,
        "messages": [m.model_dump(exclude_none=True) for m in req.messages],
        "stream": False,
    }
    if req.tools:
        body["tools"] = req.tools
        body["tool_choice"] = "auto"

    async with httpx.AsyncClient() as client:
        response = await client.post(NVIDIA_API_URL, headers=headers, json=body, timeout=120.0)
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        return response.json()

# ── P4: DQS Calibration ────────────────────────────────────────────────────
try:
    from dqs_calibration import calibrate_dqs, get_min_executable_dqs
    _HAS_DQS_CALIBRATION = True
except ImportError:
    _HAS_DQS_CALIBRATION = False

@app.get("/calibrate/dqs")
async def dqs_calibration():
    """Return DQS score → realized win-rate calibration from closed paper trades."""
    if not _HAS_DQS_CALIBRATION:
        raise HTTPException(status_code=503, detail="dqs_calibration module not available")
    try:
        result = calibrate_dqs()
        return _sanitize_nan(result)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Calibration error: {str(e)}")

@app.get("/calibrate/min-dqs")
async def min_executable_dqs(target_win_rate: float = 55.0):
    """Return the minimum DQS required to hit a target win-rate based on historical trades."""
    if not _HAS_DQS_CALIBRATION:
        raise HTTPException(status_code=503, detail="dqs_calibration module not available")
    try:
        cal = calibrate_dqs()
        min_dqs = get_min_executable_dqs(cal, target_win_rate)
        return _sanitize_nan({
            "target_win_rate": target_win_rate,
            "recommended_min_dqs": min_dqs,
            "calibration": cal,
        })
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Calibration error: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
