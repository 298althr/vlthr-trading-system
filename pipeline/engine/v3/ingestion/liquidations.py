"""
V3 Liquidation History Fetcher
================================
Bybit V5 API provides liquidation data via WebSocket only (all-liquidation stream).
There is no REST endpoint for historical liquidation records in V5.

This module provides:
1. A placeholder REST fetcher that gracefully degrades (returns no_data)
2. A WebSocket-based collector for real-time liquidation capture

For historical liquidation data, use the Chainticks dataset already downloaded
in Phase 0 (data/ucb/liquidations/).

WebSocket: wss://stream.bybit.com/v5/public/linear
Topic: allLiquidation.{SYMBOL}
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any

import pandas as pd
import numpy as np

from .bybit_client import BybitClient
from .parquet_store import write_partitions, read_existing, validate_df


LIQ_LIMIT = 100  # API max for liquidation endpoint


def fetch_liquidations(client: BybitClient, symbol: str,
                       lookback_hours: int = 168) -> Dict[str, Any]:
    """
    Fetch liquidation records for a symbol.

    Bybit V5 has no REST endpoint for liquidations (WebSocket only).
    This function gracefully degrades — returns no_data status.
    Use the WebSocket collector (LiquidationCollector class below) for real-time capture.
    """
    result = {
        "symbol": symbol, "tf": "snapshot", "type": "liquidations",
        "existing": 0, "fetched": 0, "final": 0,
        "status": "no_data", "issues": ["Bybit V5 has no REST endpoint for liquidations — use WebSocket"],
    }
    print(f"[liquidations] {symbol}: REST endpoint not available (WebSocket only), skipping")
    return result


class LiquidationCollector:
    """
    WebSocket-based real-time liquidation collector.
    Subscribes to allLiquidation.{SYMBOL} topics and writes to parquet.

    Usage:
        collector = LiquidationCollector(symbols=["BTCUSDT", "ETHUSDT"])
        collector.start()  # runs in background thread
        # ... later ...
        collector.stop()
    """

    def __init__(self, symbols: list[str], data_root: str = "data/bybit"):
        self.symbols = symbols
        self.data_root = Path(data_root)
        self._ws = None
        self._thread = None
        self._running = False
        self._buffer: list[dict] = []
        self._buffer_lock = threading.Lock()
        self._flush_interval = 60  # flush to parquet every 60s

    def _on_message(self, message):
        """Handle incoming WebSocket message."""
        try:
            data = message.get("data", [])
            for liq in data:
                row = {
                    "symbol": liq.get("s", ""),
                    "side": liq.get("S", ""),
                    "size": float(liq.get("v", 0)),
                    "price": float(liq.get("p", 0)),
                    "liqTime": pd.to_datetime(int(liq.get("T", 0)), unit="ms", utc=True),
                }
                with self._buffer_lock:
                    self._buffer.append(row)
        except Exception as e:
            print(f"[liq-collector] Parse error: {e}")

    def _flush(self):
        """Flush buffered liquidations to parquet."""
        with self._buffer_lock:
            if not self._buffer:
                return
            df = pd.DataFrame(self._buffer)
            self._buffer.clear()

        for symbol, group in df.groupby("symbol"):
            write_partitions(group, "liquidations", symbol)
            print(f"[liq-collector] Flushed {len(group)} liquidations for {symbol}")

    def _run(self):
        """Main WebSocket loop."""
        try:
            import websocket
            import json
        except ImportError:
            print("[liq-collector] websocket-client not installed, cannot collect")
            return

        url = "wss://stream.bybit.com/v5/public/linear"
        self._ws = websocket.WebSocketApp(
            url,
            on_message=lambda ws, msg: self._on_message(json.loads(msg)),
            on_error=lambda ws, err: print(f"[liq-collector] WS error: {err}"),
            on_close=lambda ws, code, msg: print(f"[liq-collector] WS closed: {code}"),
        )

        # Subscribe after connect
        def on_open(ws):
            for sym in self.symbols:
                ws.send(json.dumps({"op": "subscribe", "args": [f"allLiquidation.{sym}"]}))
            print(f"[liq-collector] Subscribed to {len(self.symbols)} symbols")

        self._ws.on_open = on_open

        # Flush timer
        def flush_timer():
            while self._running:
                time.sleep(self._flush_interval)
                self._flush()

        t = threading.Thread(target=flush_timer, daemon=True)
        t.start()

        self._ws.run_forever()

    def start(self):
        """Start WebSocket collector in background thread."""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[liq-collector] Started for {self.symbols}")

    def stop(self):
        """Stop collector and flush remaining buffer."""
        self._running = False
        if self._ws:
            self._ws.close()
        self._flush()
        print("[liq-collector] Stopped")
