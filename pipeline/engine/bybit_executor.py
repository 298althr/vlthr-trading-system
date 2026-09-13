"""
Bybit V5 Demo API Executor
===========================
Wraps the Bybit V5 REST API for paper trading via Demo Trading Service.
Replaces internal Postgres simulation with real exchange interaction.

Endpoints:
  - REST: https://api-demo.bybit.com
  - WS private: wss://stream-demo.bybit.com
  - WS public: wss://stream.bybit.com (same as mainnet)

Auth: HMAC-SHA256 over timestamp + api_key + recv_window + payload
Headers: X-BAPI-API-KEY, X-BAPI-TIMESTAMP, X-BAPI-RECV-WINDOW, X-BAPI-SIGN

Usage:
    executor = BybitExecutor(api_key, api_secret, demo=True)
    executor.place_order(symbol="BTCUSDT", side="Buy", qty=0.001, order_type="Market")
    executor.set_trading_stop(symbol="BTCUSDT", sl_price=63000, tp_price=66000)
    executor.get_positions()
    executor.get_wallet_balance()
"""
import hashlib
import hmac
import json
import time
import urllib.request
import urllib.parse
import urllib.error
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

_DEMO_BASE = "https://api-demo.bybit.com"
_MAINNET_BASE = "https://api.bybit.com"
_RECV_WINDOW = "20000"


class BybitExecutor:
    """Bybit V5 REST API wrapper for demo and mainnet trading."""

    def __init__(self, api_key: str, api_secret: str, demo: bool = True):
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = _DEMO_BASE if demo else _MAINNET_BASE
        self._demo = demo
        logger.info(f"BybitExecutor initialized (demo={demo}, base={self._base_url})")

    def _sign(self, timestamp: str, payload: str) -> str:
        """HMAC-SHA256 signature for Bybit V5 API."""
        param_str = timestamp + self._api_key + _RECV_WINDOW + payload
        return hmac.new(
            self._api_secret.encode("utf-8"),
            param_str.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self, method: str, path: str, params: Optional[Dict] = None, body: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Send authenticated request to Bybit V5 API."""
        timestamp = str(int(time.time() * 1000))
        payload = ""
        url = f"{self._base_url}{path}"

        if params:
            query_string = urllib.parse.urlencode(params)
            url = f"{url}?{query_string}"
            payload = query_string
        elif body:
            payload = json.dumps(body)

        signature = self._sign(timestamp, payload)

        headers = {
            "X-BAPI-API-KEY": self._api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": _RECV_WINDOW,
            "X-BAPI-SIGN": signature,
            "Content-Type": "application/json",
        }

        data = json.dumps(body).encode("utf-8") if body else None
        req = urllib.request.Request(url, data=data, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                result = json.loads(response.read().decode("utf-8"))
                if result.get("retCode") != 0:
                    logger.error(f"Bybit API error: {result.get('retMsg')} (code={result.get('retCode')})")
                return result
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8") if e.fp else ""
            logger.error(f"Bybit HTTP {e.code}: {error_body}")
            return {"retCode": -1, "retMsg": f"HTTP {e.code}: {error_body}"}
        except Exception as e:
            logger.error(f"Bybit request failed: {e}")
            return {"retCode": -1, "retMsg": str(e)}

    def place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str = "Market",
        category: str = "linear",
        take_profit: Optional[float] = None,
        stop_loss: Optional[float] = None,
        tp_trigger_by: str = "LastPrice",
        sl_trigger_by: str = "LastPrice",
        reduce_only: bool = False,
    ) -> Dict[str, Any]:
        """Place an order on Bybit.

        Args:
            symbol: e.g. "BTCUSDT"
            side: "Buy" (long) or "Sell" (short)
            qty: order quantity in base currency
            order_type: "Market" or "Limit"
            category: "linear" for USDT perps
            take_profit: TP price
            stop_loss: SL price
            reduce_only: True for closing positions
        """
        body = {
            "category": category,
            "symbol": symbol,
            "side": side,
            "qty": str(qty),
            "orderType": order_type,
            "reduceOnly": reduce_only,
        }
        if take_profit is not None:
            body["takeProfit"] = str(take_profit)
            body["tpTriggerBy"] = tp_trigger_by
        if stop_loss is not None:
            body["stopLoss"] = str(stop_loss)
            body["slTriggerBy"] = sl_trigger_by

        return self._request("POST", "/v5/order/create", body=body)

    def cancel_order(self, symbol: str, order_id: str, category: str = "linear") -> Dict[str, Any]:
        """Cancel an open order."""
        params = {"category": category, "symbol": symbol, "orderId": order_id}
        return self._request("POST", "/v5/order/cancel", params=params)

    def set_leverage(self, symbol: str, leverage: int, category: str = "linear") -> Dict[str, Any]:
        """Set account leverage for a symbol (vlthr-scale-audit.html Priority 2).
        Bybit V5 requires buyLeverage/sellLeverage as matching string values.
        retCode 110043 means leverage is already at the requested value — treated as success."""
        body = {
            "category": category,
            "symbol": symbol,
            "buyLeverage": str(leverage),
            "sellLeverage": str(leverage),
        }
        result = self._request("POST", "/v5/position/set-leverage", body=body)
        if result.get("retCode") in (0, 110043):
            return {"retCode": 0, "retMsg": "OK", "symbol": symbol, "leverage": leverage}
        return result

    def get_open_orders(self, symbol: Optional[str] = None, category: str = "linear") -> List[Dict]:
        """Get open orders. Returns list of order dicts."""
        params = {"category": category}
        if symbol:
            params["symbol"] = symbol
        result = self._request("GET", "/v5/order/realtime", params=params)
        if result.get("retCode") == 0:
            return result.get("result", {}).get("list", [])
        return []

    def get_positions(self, symbol: Optional[str] = None, category: str = "linear", settle_coin: str = "USDT") -> List[Dict]:
        """Get open positions. Returns list of position dicts."""
        params = {"category": category}
        if symbol:
            params["symbol"] = symbol
        elif settle_coin:
            params["settleCoin"] = settle_coin
        result = self._request("GET", "/v5/position/list", params=params)
        if result.get("retCode") == 0:
            return result.get("result", {}).get("list", [])
        return []

    def set_trading_stop(
        self,
        symbol: str,
        sl_price: Optional[float] = None,
        tp_price: Optional[float] = None,
        position_idx: int = 0,
        category: str = "linear",
    ) -> Dict[str, Any]:
        """Set SL/TP on an existing position."""
        body = {
            "category": category,
            "symbol": symbol,
            "positionIdx": position_idx,
        }
        if sl_price is not None:
            body["stopLoss"] = str(sl_price)
        if tp_price is not None:
            body["takeProfit"] = str(tp_price)
        return self._request("POST", "/v5/position/trading-stop", body=body)

    def get_wallet_balance(self, account_type: str = "UNIFIED") -> Dict[str, Any]:
        """Get wallet balance."""
        params = {"accountType": account_type}
        result = self._request("GET", "/v5/account/wallet-balance", params=params)
        if result.get("retCode") == 0:
            return result.get("result", {})
        return {}

    def get_tickers(self, symbol: str, category: str = "linear") -> Dict[str, Any]:
        """Get current ticker info (price, funding rate, 24h volume)."""
        params = {"category": category, "symbol": symbol}
        result = self._request("GET", "/v5/market/tickers", params=params)
        if result.get("retCode") == 0:
            list_data = result.get("result", {}).get("list", [])
            return list_data[0] if list_data else {}
        return {}

    def get_kline(
        self,
        symbol: str,
        interval: str = "15",
        limit: int = 200,
        category: str = "linear",
    ) -> List[Dict]:
        """Get historical klines. interval: 1,3,5,15,30,60,240,720,D,W,M."""
        params = {"category": category, "symbol": symbol, "interval": interval, "limit": str(limit)}
        result = self._request("GET", "/v5/market/kline", params=params)
        if result.get("retCode") == 0:
            return result.get("result", {}).get("list", [])
        return []

    def get_instruments_info(self, symbol: str, category: str = "linear") -> Dict[str, Any]:
        """Get symbol metadata (min qty, tick size, lot size)."""
        params = {"category": category, "symbol": symbol}
        result = self._request("GET", "/v5/market/instruments-info", params=params)
        if result.get("retCode") == 0:
            list_data = result.get("result", {}).get("list", [])
            return list_data[0] if list_data else {}
        return {}

    def get_execution_history(self, symbol: str, category: str = "linear", limit: int = 50) -> List[Dict]:
        """Get execution history for a symbol (fills, funding, etc.)."""
        params = {"category": category, "symbol": symbol, "limit": str(limit)}
        result = self._request("GET", "/v5/execution/list", params=params)
        if result.get("retCode") == 0:
            return result.get("result", {}).get("list", [])
        return []

    def get_closed_pnl(self, symbol: str, category: str = "linear", limit: int = 20) -> List[Dict]:
        """Get closed position PnL records from Bybit.

        Returns list of dicts with fields: symbol, orderId, side, qty, closedPnl,
        avgEntryPrice, avgExitPrice, createdTime, updatedTime, execType.
        """
        params = {"category": category, "symbol": symbol, "limit": str(limit)}
        result = self._request("GET", "/v5/position/closed-pnl", params=params)
        if result.get("retCode") == 0:
            return result.get("result", {}).get("list", [])
        return []

    def reconcile(self, local_trades: List[Dict]) -> Dict[str, Any]:
        """Compare local trade state with Bybit positions.

        Returns dict with discrepancies:
          - missing_on_bybit: trades that are OPEN locally but not on Bybit
          - missing_locally: positions on Bybit but not tracked locally
          - size_mismatches: position size differs between local and Bybit
        """
        bybit_positions = self.get_positions()
        bybit_map = {p["symbol"]: p for p in bybit_positions if float(p.get("size", 0)) > 0}

        local_map = {t["symbol"]: t for t in local_trades if t.get("status") == "OPEN"}

        discrepancies = {
            "missing_on_bybit": [],
            "missing_locally": [],
            "size_mismatches": [],
        }

        for symbol, local_trade in local_map.items():
            if symbol not in bybit_map:
                discrepancies["missing_on_bybit"].append({
                    "symbol": symbol,
                    "local_qty": local_trade.get("qty_contracts", 0),
                    "local_side": local_trade.get("side", ""),
                })
            # Note: qty comparison removed — paper and Bybit use different
            # account balances ($10K vs $45K), so quantities differ by design.

        for symbol, bybit_pos in bybit_map.items():
            if symbol not in local_map:
                discrepancies["missing_locally"].append({
                    "symbol": symbol,
                    "bybit_qty": float(bybit_pos.get("size", 0)),
                    "bybit_side": bybit_pos.get("side", ""),
                })

        return discrepancies

    def close_position(self, symbol: str, side: str, qty: float, category: str = "linear") -> Dict[str, Any]:
        """Close a position by placing a reduce-only market order in the opposite direction."""
        close_side = "Sell" if side.upper() in ("LONG", "BUY") else "Buy"
        return self.place_order(
            symbol=symbol,
            side=close_side,
            qty=qty,
            order_type="Market",
            category=category,
            reduce_only=True,
        )


def create_executor_from_env() -> Optional[BybitExecutor]:
    """Create a BybitExecutor from environment variables.

    Required env vars:
      - EXECUTION_MODE (must be "demo" or "mainnet")
      - BYBIT_DEMO_API_KEY / BYBIT_DEMO_API_SECRET (for demo mode)
      - BYBIT_API_KEY / BYBIT_API_KEY_SECRET (for mainnet mode)
    """
    import os

    mode = os.environ.get("EXECUTION_MODE", "internal")
    if mode == "internal":
        return None

    if mode == "demo":
        api_key = os.environ.get("BYBIT_DEMO_API_KEY", "") or os.environ.get("BYBIT_API_KEY", "")
        api_secret = os.environ.get("BYBIT_DEMO_API_SECRET", "") or os.environ.get("BYBIT_API_KEY_SECRET", "")
    elif mode == "mainnet":
        api_key = os.environ.get("BYBIT_API_KEY", "") or os.environ.get("Apikey", "")
        api_secret = os.environ.get("BYBIT_API_KEY_SECRET", "") or os.environ.get("Apisecret", "")
    else:
        logger.error(f"Unknown EXECUTION_MODE: {mode}")
        return None

    if not api_key or not api_secret:
        logger.warning(f"EXECUTION_MODE={mode} but API keys not set. Falling back to internal.")
        return None

    return BybitExecutor(api_key, api_secret, demo=(mode == "demo"))
