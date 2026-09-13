#!/usr/bin/env python3
"""
Bybit V5 WebSocket Data Service
================================
Subscribes to Bybit public WebSocket streams for real-time market data.
Replaces REST polling for the pipeline's data ingestion layer.

Streams:
  - kline.15m: 15-minute candles (OHLCV) for all SYMBOLS
  - tickers: real-time ticker updates (price, funding rate, 24h volume)
  - orderbook.50: L2 orderbook snapshots (depth=50)

Data is written to parquet files compatible with the existing pipeline:
  /app/data/bybit/{SYMBOL}/{SYMBOL}_enriched.parquet (appended)
  /app/data/bybit/ws/{SYMBOL}_ticker.json (latest ticker)
  /app/data/bybit/ws/{SYMBOL}_orderbook.json (latest orderbook)

Usage:
  python3 bybit_ws.py --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,BNBUSDT

Or as a module:
  from bybit_ws import BybitWebSocketService
  ws = BybitWebSocketService(symbols=["BTCUSDT", "ETHUSDT"])
  ws.run()
"""
import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

import websockets

logger = logging.getLogger(__name__)

_WS_URL = "wss://stream.bybit.com/v5/public/linear"
_RECONNECT_DELAY = 3  # seconds between reconnect attempts
_HEARTBEAT_INTERVAL = 20  # seconds between ping messages
_MAX_RECONNECT_DELAY = 60  # max backoff

SYMBOLS_DEFAULT = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT"]


class BybitWebSocketService:
    """WebSocket client for Bybit V5 public market data."""

    def __init__(
        self,
        symbols: List[str],
        data_root: str = "/app/data",
        subscribe_kline: bool = True,
        subscribe_ticker: bool = True,
        subscribe_orderbook: bool = True,
        kline_interval: str = "15",
    ):
        self.symbols = symbols
        self.data_root = Path(data_root)
        self.ws_dir = self.data_root / "bybit" / "ws"
        self.ws_dir.mkdir(parents=True, exist_ok=True)
        self.subscribe_kline = subscribe_kline
        self.subscribe_ticker = subscribe_ticker
        self.subscribe_orderbook = subscribe_orderbook
        self.kline_interval = kline_interval

        self._running = False
        self._ws = None
        self._last_ping = 0
        self._reconnect_count = 0
        self._msg_count = 0
        self._kline_count = 0
        self._ticker_count = 0
        self._ob_count = 0
        self._start_time = 0

        # Latest data cache
        self._latest_tickers: Dict[str, dict] = {}
        self._latest_orderbooks: Dict[str, dict] = {}
        self._kline_buffer: Dict[str, list] = {s: [] for s in symbols}

    def _build_subscribe_args(self) -> List[str]:
        """Build list of topics to subscribe to."""
        args = []
        for symbol in self.symbols:
            if self.subscribe_kline:
                args.append(f"kline.{self.kline_interval}.{symbol}")
            if self.subscribe_ticker:
                args.append(f"tickers.{symbol}")
            if self.subscribe_orderbook:
                args.append(f"orderbook.50.{symbol}")
        return args

    async def _connect(self):
        """Connect to Bybit WebSocket."""
        logger.info(f"Connecting to {_WS_URL}...")
        self._ws = await websockets.connect(_WS_URL, ping_interval=None)
        logger.info("WebSocket connected")

        # Send subscribe request
        sub_args = self._build_subscribe_args()
        sub_msg = {"op": "subscribe", "args": sub_args}
        await self._ws.send(json.dumps(sub_msg))
        logger.info(f"Subscribed to {len(sub_args)} topics: {sub_args[:3]}...")

    async def _send_ping(self):
        """Send ping to keep connection alive."""
        if self._ws and time.time() - self._last_ping > _HEARTBEAT_INTERVAL:
            try:
                await self._ws.send(json.dumps({"op": "ping"}))
                self._last_ping = time.time()
            except Exception as e:
                logger.warning(f"Ping failed: {e}")

    def _handle_message(self, msg: dict):
        """Process a single WebSocket message."""
        topic = msg.get("topic", "")
        data = msg.get("data", {})
        msg_type = msg.get("type", "")

        if topic.startswith("kline."):
            self._handle_kline(topic, data, msg_type)
        elif topic.startswith("tickers."):
            self._handle_ticker(topic, data)
        elif topic.startswith("orderbook."):
            self._handle_orderbook(topic, data, msg_type)

    def _handle_kline(self, topic: str, data: dict, msg_type: str):
        """Handle kline update."""
        symbol = topic.split(".")[-1]
        kline_data = data if isinstance(data, list) else [data]

        for candle in kline_data:
            # Bybit kline format: [start, open, high, low, close, volume, turnover]
            if isinstance(candle, dict):
                # Delta format
                formatted = {
                    "timestamp": datetime.fromtimestamp(int(candle.get("start", 0)) / 1000, tz=timezone.utc),
                    "open": float(candle.get("open", 0)),
                    "high": float(candle.get("high", 0)),
                    "low": float(candle.get("low", 0)),
                    "close": float(candle.get("close", 0)),
                    "volume": float(candle.get("volume", 0)),
                    "turnover": float(candle.get("turnover", 0)),
                    "confirmed": candle.get("confirm", False),
                }
            else:
                # Snapshot format: [start, open, high, low, close, volume, turnover]
                formatted = {
                    "timestamp": datetime.fromtimestamp(int(candle[0]) / 1000, tz=timezone.utc),
                    "open": float(candle[1]),
                    "high": float(candle[2]),
                    "low": float(candle[3]),
                    "close": float(candle[4]),
                    "volume": float(candle[5]),
                    "turnover": float(candle[6]),
                    "confirmed": True,
                }

            self._kline_buffer[symbol].append(formatted)
            self._kline_count += 1

            if msg_type == "snapshot":
                logger.info(f"[Kline] {symbol} snapshot: {len(kline_data)} candles")
            elif formatted["confirmed"]:
                logger.debug(f"[Kline] {symbol} confirmed candle: close={formatted['close']}")

    def _handle_ticker(self, topic: str, data: dict):
        """Handle ticker update. Merges delta updates with cached state."""
        symbol = topic.split(".")[-1]

        # Get existing cached ticker or start fresh
        existing = self._latest_tickers.get(symbol, {})

        # Merge new fields into existing
        merged = dict(existing)
        merged.update({
            "symbol": symbol,
            "last_price": float(data.get("lastPrice", existing.get("last_price", 0))),
            "bid1_price": float(data.get("bid1Price", existing.get("bid1_price", 0))),
            "ask1_price": float(data.get("ask1Price", existing.get("ask1_price", 0))),
            "bid1_size": float(data.get("bid1Size", existing.get("bid1_size", 0))),
            "ask1_size": float(data.get("ask1Size", existing.get("ask1_size", 0))),
            "funding_rate": float(data.get("fundingRate", existing.get("funding_rate", 0))),
            "volume_24h": float(data.get("volume24h", existing.get("volume_24h", 0))),
            "turnover_24h": float(data.get("turnover24h", existing.get("turnover_24h", 0))),
            "high_price_24h": float(data.get("highPrice24h", existing.get("high_price_24h", 0))),
            "low_price_24h": float(data.get("lowPrice24h", existing.get("low_price_24h", 0))),
            "open_interest": float(data.get("openInterest", existing.get("open_interest", 0))),
            "mark_price": float(data.get("markPrice", existing.get("mark_price", 0))),
            "index_price": float(data.get("indexPrice", existing.get("index_price", 0))),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        self._latest_tickers[symbol] = merged
        self._ticker_count += 1

        # Write to file
        ticker_path = self.ws_dir / f"{symbol}_ticker.json"
        try:
            ticker_path.write_text(json.dumps(merged))
        except Exception as e:
            logger.warning(f"Failed to write ticker for {symbol}: {e}")

    def _handle_orderbook(self, topic: str, data: dict, msg_type: str):
        """Handle orderbook update."""
        symbol = topic.split(".")[-1]

        if msg_type == "snapshot":
            ob = {
                "symbol": symbol,
                "bids": [[float(p), float(s)] for p, s in data.get("b", [])],
                "asks": [[float(p), float(s)] for p, s in data.get("a", [])],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            # Compute spread and imbalance
            if ob["bids"] and ob["asks"]:
                best_bid = ob["bids"][0][0]
                best_ask = ob["asks"][0][0]
                ob["spread_pct"] = ((best_ask - best_bid) / best_ask * 100) if best_ask > 0 else 0
                bid_vol = sum(s for _, s in ob["bids"][:10])
                ask_vol = sum(s for _, s in ob["asks"][:10])
                total = bid_vol + ask_vol
                ob["imbalance_pct"] = ((bid_vol - ask_vol) / total * 100) if total > 0 else 0
            else:
                ob["spread_pct"] = 0
                ob["imbalance_pct"] = 0

            self._latest_orderbooks[symbol] = ob
            self._ob_count += 1

            # Write to file
            ob_path = self.ws_dir / f"{symbol}_orderbook.json"
            try:
                ob_path.write_text(json.dumps(ob))
            except Exception as e:
                logger.warning(f"Failed to write orderbook for {symbol}: {e}")

    def _flush_kline_buffer(self):
        """Flush accumulated kline data to parquet files."""
        for symbol, candles in self._kline_buffer.items():
            if not candles:
                continue
            # Only keep confirmed candles for parquet
            confirmed = [c for c in candles if c["confirmed"]]
            if not confirmed:
                continue

            try:
                import pandas as pd
                df = pd.DataFrame(confirmed)
                symbol_dir = self.data_root / "bybit" / symbol
                symbol_dir.mkdir(parents=True, exist_ok=True)
                parquet_path = symbol_dir / f"{symbol}_ws_klines.parquet"

                if parquet_path.exists():
                    existing = pd.read_parquet(parquet_path)
                    df = pd.concat([existing, df]).drop_duplicates(subset=["timestamp"]).sort_values("timestamp")

                df.to_parquet(parquet_path, index=False)
                self._kline_buffer[symbol] = [c for c in candles if not c["confirmed"]]
                logger.info(f"[Kline] Flushed {len(confirmed)} candles for {symbol} to {parquet_path}")
            except Exception as e:
                logger.warning(f"Failed to flush klines for {symbol}: {e}")

    def get_stats(self) -> dict:
        """Return service statistics."""
        uptime = time.time() - self._start_time if self._start_time else 0
        return {
            "running": self._running,
            "uptime_sec": round(uptime, 1),
            "reconnects": self._reconnect_count,
            "total_messages": self._msg_count,
            "kline_messages": self._kline_count,
            "ticker_messages": self._ticker_count,
            "orderbook_messages": self._ob_count,
            "symbols": self.symbols,
            "latest_tickers": list(self._latest_tickers.keys()),
            "latest_orderbooks": list(self._latest_orderbooks.keys()),
        }

    async def run(self):
        """Main event loop."""
        self._running = True
        self._start_time = time.time()
        logger.info(f"Starting Bybit WS service for {len(self.symbols)} symbols: {self.symbols}")

        while self._running:
            try:
                await self._connect()

                while self._running:
                    try:
                        raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
                        self._msg_count += 1

                        msg = json.loads(raw)
                        if "topic" in msg:
                            self._handle_message(msg)
                        elif msg.get("op") == "pong":
                            pass
                        elif msg.get("op") == "subscribe":
                            if msg.get("success"):
                                logger.info(f"Subscribe confirmed: {len(msg.get('args', []))} topics")
                            else:
                                logger.error(f"Subscribe failed: {msg.get('retMsg')}")

                        await self._send_ping()

                        # Flush klines every 100 messages
                        if self._msg_count % 100 == 0:
                            self._flush_kline_buffer()

                    except asyncio.TimeoutError:
                        await self._send_ping()
                    except websockets.ConnectionClosed as e:
                        logger.warning(f"WebSocket closed: {e.code} {e.reason}")
                        break

            except Exception as e:
                logger.error(f"Connection error: {e}")

            if not self._running:
                break

            # Reconnect with backoff
            self._reconnect_count += 1
            delay = min(_RECONNECT_DELAY * self._reconnect_count, _MAX_RECONNECT_DELAY)
            logger.info(f"Reconnecting in {delay}s (attempt {self._reconnect_count})...")
            await asyncio.sleep(delay)

        # Cleanup
        self._flush_kline_buffer()
        if self._ws:
            await self._ws.close()
        logger.info("WebSocket service stopped")

    def stop(self):
        """Signal the service to stop."""
        self._running = False
        logger.info("Stop signal received")


def main():
    parser = argparse.ArgumentParser(description="Bybit V5 WebSocket Data Service")
    parser.add_argument("--symbols", default=",".join(SYMBOLS_DEFAULT), help="Comma-separated symbols")
    parser.add_argument("--data-root", default=os.environ.get("DATA_ROOT", "/app/data"), help="Data root directory")
    parser.add_argument("--no-kline", action="store_true", help="Disable kline subscription")
    parser.add_argument("--no-ticker", action="store_true", help="Disable ticker subscription")
    parser.add_argument("--no-orderbook", action="store_true", help="Disable orderbook subscription")
    parser.add_argument("--interval", default="15", help="Kline interval (1,3,5,15,30,60,240)")
    parser.add_argument("--log-level", default="INFO", help="Log level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    service = BybitWebSocketService(
        symbols=symbols,
        data_root=args.data_root,
        subscribe_kline=not args.no_kline,
        subscribe_ticker=not args.no_ticker,
        subscribe_orderbook=not args.no_orderbook,
        kline_interval=args.interval,
    )

    # Graceful shutdown
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def shutdown_handler(sig, frame):
        logger.info(f"Signal {sig} received, shutting down...")
        service.stop()

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    try:
        loop.run_until_complete(service.run())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
