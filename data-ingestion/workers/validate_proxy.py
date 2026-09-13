"""Validate PROXY_URL is working for Bybit data fetchers."""
import os, sys

PROXY_URL = os.environ.get("PROXY_URL", "")
if not PROXY_URL:
    print("[FAIL] PROXY_URL is not set in environment.")
    print("       Set it before running: $env:PROXY_URL='http://user:pass@host:port'")
    sys.exit(1)

print(f"[INFO] PROXY_URL = {PROXY_URL}")

# Test 1: direct requests via proxy
import requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

session = requests.Session()
session.proxies = {"http": PROXY_URL, "https": PROXY_URL}
session.verify = False

print("[TEST] Hitting Bybit /v5/market/time ...")
resp = session.get("https://api.bybit.com/v5/market/time", timeout=30)
print(f"[OK]   Status {resp.status_code} — {resp.json()}")

# Test 2: bybit_ingest_core client
print("[TEST] Using BybitClient (bybit_ingest_core.py) ...")
from bybit_ingest_core import BybitClient, BYBIT_BASE_URL

client = BybitClient(base_url=BYBIT_BASE_URL)
params = {
    "category": "linear",
    "symbol": "BTCUSDT",
    "interval": "15",
    "limit": 1,
}
data = client._request(params)
print(f"[OK]   BybitClient retCode={data.get('retCode')} — fetched {len(data.get('result',{}).get('list',[]))} candles")

# Test 3: bybit_market_data_core client
print("[TEST] Using Client (bybit_market_data_core.py) ...")
from bybit_market_data_core import Client

md_client = Client()
funding = md_client.fetch_funding("BTCUSDT", end_ms=None, limit=1)
print(f"[OK]   MarketDataClient fetched {len(funding)} funding records")

print("\n[ALL PASSED] Proxy is working. You can now run the full pipeline.")
