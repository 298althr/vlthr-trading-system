"""
Validate PROXY_URL_2 is working for Bybit data fetchers.
Usage:
    $env:PROXY_URL_2='http://user:pass@host:port'
    python data/bybit/workers/test_proxy2.py
"""
import os
import sys


def normalize_proxy_url(raw: str) -> str:
    """
    Convert shorthand proxy format (user:pass:host:port)
    to full URL (http://user:pass@host:port).
    """
    raw = raw.strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    parts = raw.split(":")
    if len(parts) < 4:
        # Maybe already user:pass@host:port without scheme
        if "@" in raw:
            return f"http://{raw}"
        raise ValueError(f"Cannot parse proxy URL: {raw}")
    port = parts[-1]
    host = parts[-2]
    user_pass = ":".join(parts[:-2])
    return f"http://{user_pass}@{host}:{port}"


PROXY_URL_2_RAW = os.environ.get("PROXY_URL_2", "")
if not PROXY_URL_2_RAW:
    print("[FAIL] PROXY_URL_2 is not set in environment.")
    print("       Set it before running: $env:PROXY_URL_2='user:pass:host:port'")
    sys.exit(1)

try:
    PROXY_URL_2 = normalize_proxy_url(PROXY_URL_2_RAW)
except ValueError as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

print(f"[INFO] PROXY_URL_2 (raw)    = {PROXY_URL_2_RAW}")
print(f"[INFO] PROXY_URL_2 (resolved) = {PROXY_URL_2.split('@')[-1]}")

# Temporarily set PROXY_URL so bybit_ingest_core picks it up
os.environ["PROXY_URL"] = PROXY_URL_2

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

session = requests.Session()
session.proxies = {"http": PROXY_URL_2, "https": PROXY_URL_2}
session.verify = False

print("[TEST 1] Direct request via proxy → Bybit /v5/market/time ...")
try:
    resp = session.get("https://api.bybit.com/v5/market/time", timeout=30)
    data = resp.json()
    print(f"[OK]   Status {resp.status_code} — retCode={data.get('retCode')} time={data.get('result',{}).get('timeNano')}")
except Exception as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

print("[TEST 2] BybitClient (bybit_ingest_core.py) via PROXY_URL_2 ...")
sys.path.insert(0, os.path.dirname(__file__))
try:
    from bybit_ingest_core import BybitClient, BYBIT_BASE_URL
    client = BybitClient(base_url=BYBIT_BASE_URL)
    params = {
        "category": "linear",
        "symbol": "BTCUSDT",
        "interval": "1",
        "limit": 5,
    }
    data = client._request(params)
    candles = data.get("result", {}).get("list", [])
    print(f"[OK]   BybitClient retCode={data.get('retCode')} — fetched {len(candles)} 1m candles")
    if candles:
        print(f"       Latest candle: open={candles[0][1]} close={candles[0][4]} time={candles[0][0]}")
except Exception as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

print("[TEST 3] MarketDataClient (bybit_market_data_core.py) via PROXY_URL_2 ...")
try:
    from bybit_market_data_core import Client
    md_client = Client()
    funding = md_client.fetch_funding("BTCUSDT", end_ms=None, limit=1)
    print(f"[OK]   MarketDataClient fetched {len(funding)} funding records")
except Exception as e:
    print(f"[FAIL] {e}")
    sys.exit(1)

print("\n[ALL PASSED] PROXY_URL_2 is working. Ready to run minute_scheduler.py.")
