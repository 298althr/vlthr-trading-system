"""Bybit V5 Market Data Ingestion Core — funding rate, open interest, orderbook L2."""
from __future__ import annotations
import json, os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Tuple
import numpy as np, pandas as pd, requests

BYBIT_BASE = "https://api.bybit.com/v5/market"
CAT = "linear"
SYMBOLS = ["BTCUSDT","ETHUSDT","SOLUSDT","XRPUSDT","BNBUSDT","DOGEUSDT"]
DATA_ROOT = Path("data/bybit")
DEFAULT_START = datetime(2023,1,1,tzinfo=timezone.utc)
DELAY = 1.0
MAX_RETRY = 6
BACKOFF = 2.0
RL_SLEEP = 30.0
PAGE_LIMIT = 200

class C:
    OK,G,Y,R,E = "\033[92m","\033[94m","\033[93m","\033[91m","\033[0m"
def ok(m): print(f"{C.OK}[OK]{C.E} {m}")
def info(m): print(f"{C.G}[INFO]{C.E} {m}")
def warn(m): print(f"{C.Y}[WARN]{C.E} {m}", file=sys.stderr)
def err(m): print(f"{C.R}[ERR]{C.E} {m}", file=sys.stderr)

class Client:
    def __init__(self, delay=DELAY, testnet=False):
        self.base = "https://api-testnet.bybit.com/v5/market" if testnet else BYBIT_BASE
        self.delay = delay
        self._last = 0.0
        # Session with connection pooling disabled — fixes "Connection reset" in restricted networks
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "VLTHR-Engine/1.0",
            "Connection": "close",
            "Accept": "application/json",
        })
        # Proxy support — read from env (e.g. http://user:pass@host:port)
        proxy_url = os.environ.get("PROXY_URL", "")
        if proxy_url:
            self._session.proxies = {"http": proxy_url, "https": proxy_url}

    def _throttle(self):
        elapsed = time.monotonic() - self._last
        if elapsed < self.delay: time.sleep(self.delay - elapsed)
        self._last = time.monotonic()

    def _req(self, endpoint, params):
        url = f"{self.base}/{endpoint}"
        for a in range(1, MAX_RETRY + 1):
            self._throttle()
            try:
                r = self._session.get(url, params=params, timeout=60)
                r.raise_for_status()
                d = r.json()
                if d.get("retCode") == 10006:
                    ts = r.headers.get("X-Bapi-Limit-Reset-Timestamp")
                    wait = max(1.0, (int(ts)-time.time()*1000)/1000+1) if ts else RL_SLEEP * a
                    warn(f"Rate limit (attempt {a}), sleep {wait:.1f}s...")
                    time.sleep(wait); continue
                if d.get("retCode") != 0:
                    raise RuntimeError(f"Bybit error: {d.get('retMsg')}")
                return d
            except (requests.exceptions.ConnectionError, requests.exceptions.SSLError) as e:
                if a == MAX_RETRY: raise RuntimeError(f"Connection failed: {e}")
                wait = BACKOFF ** a; warn(f"Connection reset (attempt {a}), retry {wait:.1f}s...")
                time.sleep(wait)
            except requests.exceptions.RequestException as e:
                if a == MAX_RETRY: raise RuntimeError(f"Request failed: {e}")
                wait = BACKOFF ** a; warn(f"Request error (attempt {a}): {e}, retry {wait:.1f}s...")
                time.sleep(wait)
        raise RuntimeError("Exhausted retries")

    def fetch_funding(self, symbol, end_ms, limit=PAGE_LIMIT) -> List[dict]:
        d = self._req("funding/history", {"category": CAT, "symbol": symbol, "limit": limit, "endTime": end_ms})
        return d.get("result", {}).get("list", [])

    def fetch_oi(self, symbol, interval, end_ms, limit=PAGE_LIMIT) -> List[dict]:
        d = self._req("open-interest", {"category": CAT, "symbol": symbol, "intervalTime": interval, "limit": limit, "endTime": end_ms})
        return d.get("result", {}).get("list", [])

    def fetch_orderbook(self, symbol, depth) -> Tuple[dict, int]:
        d = self._req("orderbook", {"category": CAT, "symbol": symbol, "limit": depth})
        res = d.get("result", {})
        ts = int(res.get("ts", 0))
        bids = [[ts, symbol, "bid", i+1, float(b[0]), float(b[1])] for i, b in enumerate(res.get("b", []))]
        asks = [[ts, symbol, "ask", i+1, float(a[0]), float(a[1])] for i, a in enumerate(res.get("a", []))]
        return bids + asks, ts

# ── PAGINATION ──────────────────────────────────────────────────────────────

def paginate_history(client: Client, symbol, fetch_fn, since_ms, until_ms, **kwargs) -> pd.DataFrame:
    """Generic backward pagination for time-series endpoints (funding, OI)."""
    all_rows: List[dict] = []
    current_end = until_ms
    while current_end > since_ms:
        rows = fetch_fn(symbol, end_ms=current_end, **kwargs)
        if not rows: break
        all_rows.extend(rows)
        oldest = int(rows[-1].get("fundingRateTimestamp", rows[-1].get("timestamp", 0)))
        if oldest <= since_ms or oldest >= current_end: break
        current_end = oldest - 1
    if not all_rows: return pd.DataFrame()
    return pd.DataFrame(all_rows)

# ── PARQUET I/O ─────────────────────────────────────────────────────────────

def _write_parquet(df: pd.DataFrame, out_path: Path):
    if df.empty: return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp")
    df.to_parquet(tmp, compression="zstd", index=False)
    tmp.replace(out_path)

def read_existing_funding(symbol: str) -> pd.DataFrame:
    p = DATA_ROOT / symbol / "funding_rate"
    if not p.exists(): return pd.DataFrame()
    dfs = []
    for yd in p.iterdir():
        if not yd.is_dir(): continue
        for f in yd.iterdir():
            if f.suffix != ".parquet": continue
            try:
                df = pd.read_parquet(f)
                if not df.empty and "fundingTime" in df.columns: dfs.append(df)
            except Exception as e: warn(f"Could not read {f}: {e}")
    if not dfs: return pd.DataFrame()
    c = pd.concat(dfs, ignore_index=True)
    c["fundingTime"] = pd.to_datetime(c["fundingTime"], utc=True)
    return c.sort_values("fundingTime").drop_duplicates("fundingTime").reset_index(drop=True)

def write_funding(symbol: str, df: pd.DataFrame):
    if df.empty: return
    df = df.copy()
    df["fundingTime"] = pd.to_datetime(df["fundingTime"], utc=True)
    df["year"] = df["fundingTime"].dt.year
    df["month"] = df["fundingTime"].dt.month
    for (y, m), g in df.groupby(["year", "month"]):
        out = g.drop(columns=["year", "month"]).copy()
        _write_parquet(out, DATA_ROOT / symbol / "funding_rate" / f"{y:04d}" / f"{m:02d}.parquet")

def read_existing_oi(symbol: str) -> pd.DataFrame:
    p = DATA_ROOT / symbol / "open_interest"
    if not p.exists(): return pd.DataFrame()
    dfs = []
    for yd in p.iterdir():
        if not yd.is_dir(): continue
        for f in yd.iterdir():
            if f.suffix != ".parquet": continue
            try:
                df = pd.read_parquet(f)
                if not df.empty and "timestamp" in df.columns: dfs.append(df)
            except Exception as e: warn(f"Could not read {f}: {e}")
    if not dfs: return pd.DataFrame()
    c = pd.concat(dfs, ignore_index=True)
    c["timestamp"] = pd.to_datetime(c["timestamp"], utc=True)
    return c.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

def write_oi(symbol: str, df: pd.DataFrame):
    if df.empty: return
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["year"] = df["timestamp"].dt.year
    df["month"] = df["timestamp"].dt.month
    for (y, m), g in df.groupby(["year", "month"]):
        out = g.drop(columns=["year", "month"]).copy()
        _write_parquet(out, DATA_ROOT / symbol / "open_interest" / f"{y:04d}" / f"{m:02d}.parquet")

def read_existing_orderbook(symbol: str) -> pd.DataFrame:
    p = DATA_ROOT / symbol / "orderbook"
    if not p.exists(): return pd.DataFrame()
    dfs = []
    for yd in p.iterdir():
        if not yd.is_dir(): continue
        for f in yd.iterdir():
            if f.suffix != ".parquet": continue
            try:
                df = pd.read_parquet(f)
                if not df.empty and "timestamp" in df.columns: dfs.append(df)
            except Exception as e: warn(f"Could not read {f}: {e}")
    if not dfs: return pd.DataFrame()
    c = pd.concat(dfs, ignore_index=True)
    c["timestamp"] = pd.to_datetime(c["timestamp"], utc=True)
    return c.sort_values("timestamp").reset_index(drop=True)

def write_orderbook(symbol: str, df: pd.DataFrame):
    if df.empty: return
    df = df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["year"] = df["timestamp"].dt.year
    df["month"] = df["timestamp"].dt.month
    df["day"] = df["timestamp"].dt.day
    for (y, m, d), g in df.groupby(["year", "month", "day"]):
        out = g.drop(columns=["year", "month", "day"]).copy()
        _write_parquet(out, DATA_ROOT / symbol / "orderbook" / f"{y:04d}" / f"{m:02d}" / f"{d:02d}.parquet")

# ── INGESTION WORKERS ───────────────────────────────────────────────────────

def ingest_funding(client: Client, symbol: str, dry_run=False) -> Dict[str, Any]:
    res = {"symbol": symbol, "type": "funding_rate", "existing": 0, "fetched": 0, "final": 0, "status": "pending", "issues": []}
    info(f"--- {symbol} | funding_rate ---")
    existing = read_existing_funding(symbol)
    if not existing.empty:
        res["existing"] = len(existing)
        since = existing["fundingTime"].max() - timedelta(hours=1)
        info(f"Existing: {len(existing):,} rows, last {since.isoformat()}")
    else:
        info("No existing data")
        since = DEFAULT_START
    since_ms = int(since.timestamp() * 1000)
    until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    df = paginate_history(client, symbol, client.fetch_funding, since_ms, until_ms)
    if not df.empty:
        res["fetched"] = len(df)
        ok(f"Fetched {len(df):,} funding rate rows")
        df["fundingRate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
        df["fundingTime"] = pd.to_datetime(df["fundingRateTimestamp"].astype(np.int64), unit="ms", utc=True)
        df = df[["symbol", "fundingRate", "fundingTime"]].copy()
    else:
        info("No new funding rate data")
    if not existing.empty and not df.empty:
        combined = pd.concat([existing, df], ignore_index=True)
    elif not df.empty:
        combined = df.copy()
    else:
        combined = existing.copy()
    if combined.empty:
        res["status"] = "no_data"; warn("No funding rate data"); return res
    combined = combined.sort_values("fundingTime").drop_duplicates("fundingTime").reset_index(drop=True)
    # validate
    issues = []
    if combined["fundingRate"].isna().sum(): issues.append("null fundingRate")
    if ((combined["fundingRate"] < -0.1) | (combined["fundingRate"] > 0.1)).any(): issues.append("suspicious fundingRate outside [-0.1, 0.1]")
    res["issues"] = issues
    for i in issues: warn(f"Validation: {i}")
    if not issues: ok("Validation passed")
    res["final"] = len(combined)
    if not dry_run:
        write_funding(symbol, combined); ok(f"Written {len(combined):,} rows")
    else:
        info(f"Dry run: would write {len(combined):,} rows")
    res["status"] = "ok" if not issues else "issues"
    return res

def ingest_oi(client: Client, symbol: str, interval: str = "1h", dry_run=False) -> Dict[str, Any]:
    res = {"symbol": symbol, "type": f"open_interest_{interval}", "existing": 0, "fetched": 0, "final": 0, "status": "pending", "issues": []}
    info(f"--- {symbol} | open_interest ({interval}) ---")
    existing = read_existing_oi(symbol)
    if not existing.empty:
        res["existing"] = len(existing)
        since = existing["timestamp"].max() - timedelta(hours=1)
        info(f"Existing: {len(existing):,} rows, last {since.isoformat()}")
    else:
        info("No existing data")
        since = DEFAULT_START
    since_ms = int(since.timestamp() * 1000)
    until_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    df = paginate_history(client, symbol, lambda s, end_ms, limit=PAGE_LIMIT: client.fetch_oi(s, interval, end_ms, limit), since_ms, until_ms)
    if not df.empty:
        res["fetched"] = len(df)
        ok(f"Fetched {len(df):,} open interest rows")
        df["openInterest"] = pd.to_numeric(df["openInterest"], errors="coerce")
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True)
        df["symbol"] = symbol
        df = df[["symbol", "openInterest", "timestamp"]].copy()
    else:
        info("No new open interest data")
    if not existing.empty and not df.empty:
        combined = pd.concat([existing, df], ignore_index=True)
    elif not df.empty:
        combined = df.copy()
    else:
        combined = existing.copy()
    if combined.empty:
        res["status"] = "no_data"; warn("No open interest data"); return res
    combined = combined.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    issues = []
    if combined["openInterest"].isna().sum(): issues.append("null openInterest")
    if (combined["openInterest"] <= 0).any(): issues.append("non-positive openInterest")
    res["issues"] = issues
    for i in issues: warn(f"Validation: {i}")
    if not issues: ok("Validation passed")
    res["final"] = len(combined)
    if not dry_run:
        write_oi(symbol, combined); ok(f"Written {len(combined):,} rows")
    else:
        info(f"Dry run: would write {len(combined):,} rows")
    res["status"] = "ok" if not issues else "issues"
    return res

def ingest_orderbook(client: Client, symbol: str, depth: int = 50, dry_run=False) -> Dict[str, Any]:
    res = {"symbol": symbol, "type": "orderbook", "fetched": 0, "status": "pending", "issues": []}
    info(f"--- {symbol} | orderbook (depth={depth}) ---")
    rows, ts = client.fetch_orderbook(symbol, depth)
    if not rows:
        warn("No orderbook data returned"); res["status"] = "no_data"; return res
    df = pd.DataFrame(rows, columns=["timestamp", "symbol", "side", "level", "price", "size"])
    df["timestamp"] = pd.to_datetime(df["timestamp"].astype(np.int64), unit="ms", utc=True)
    res["fetched"] = len(df)
    ok(f"Fetched orderbook snapshot: {len(df)} levels ({len(df[df.side=='bid'])} bids, {len(df[df.side=='ask'])} asks)")
    # validate
    issues = []
    if df["price"].isna().sum(): issues.append("null prices")
    if df["size"].isna().sum(): issues.append("null sizes")
    best_bid = df[df["side"] == "bid"]["price"].max() if len(df[df["side"] == "bid"]) else 0
    best_ask = df[df["side"] == "ask"]["price"].min() if len(df[df["side"] == "ask"]) else float("inf")
    if best_bid >= best_ask: issues.append(f"spread inverted: bid {best_bid} >= ask {best_ask}")
    res["issues"] = issues
    for i in issues: warn(f"Validation: {i}")
    if not issues: ok("Validation passed")
    if not dry_run:
        write_orderbook(symbol, df); ok(f"Written {len(df):,} rows")
    else:
        info(f"Dry run: would write {len(df):,} rows")
    res["status"] = "ok" if not issues else "issues"
    return res

# ── MAIN RUNNER ─────────────────────────────────────────────────────────────

def run_symbol(symbol: str, data_types: List[str], oi_interval: str = "1h", depth: int = 50, dry_run=False, delay=DELAY, testnet=False):
    client = Client(delay=delay, testnet=testnet)
    print("=" * 70)
    print(f"  BYBIT V5 MARKET DATA — {symbol}")
    print(f"  Data types: {', '.join(data_types)}")
    print(f"  API:        {client.base}")
    print("=" * 70)
    results = []
    if "funding" in data_types:
        try: results.append(ingest_funding(client, symbol, dry_run))
        except Exception as e: err(f"{symbol}/funding failed: {e}"); results.append({"symbol": symbol, "type": "funding_rate", "status": "error", "error": str(e)})
    if "oi" in data_types:
        try: results.append(ingest_oi(client, symbol, oi_interval, dry_run))
        except Exception as e: err(f"{symbol}/oi failed: {e}"); results.append({"symbol": symbol, "type": f"open_interest_{oi_interval}", "status": "error", "error": str(e)})
    if "orderbook" in data_types:
        try: results.append(ingest_orderbook(client, symbol, depth, dry_run))
        except Exception as e: err(f"{symbol}/orderbook failed: {e}"); results.append({"symbol": symbol, "type": "orderbook", "status": "error", "error": str(e)})
    print("=" * 70)
    print(f"  SUMMARY — {symbol}")
    print("=" * 70)
    for r in results:
        print(f"  {r.get('type','?'):20s} -> {r.get('status','?'):10s}  existing={r.get('existing',0):>7,}  fetched={r.get('fetched',0):>7,}  final={r.get('final',0):>7,}")
        if r.get("issues"): print(f"    issues: {r['issues']}")
    print("=" * 70)
    summary_path = DATA_ROOT / f"market_data_summary_{symbol}.json"
    summary = {"run_at": datetime.now(timezone.utc).isoformat(), "results": results}
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    info(f"Summary written to {summary_path}")
