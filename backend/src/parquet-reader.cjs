/**
 * parquet-reader.cjs
 * Reads Bybit + Capital.com parquet files for market intel.
 * Data root is mounted at /data inside Docker (PARQUET_DATA_ROOT env var).
 * Columns read per type:
 *   5m       : timestamp, open, high, low, close, volume
 *   funding  : symbol, fundingRate, fundingTime
 *   OI       : symbol, openInterest, timestamp
 *   orderbook: timestamp, symbol, side, price, size  (skip 'level' — BigInt)
 */
'use strict';

const fs    = require('fs');
const path  = require('path');

const DATA_ROOT = process.env.PARQUET_DATA_ROOT || '/data';

// ── lazy-import ESM hyparquet ─────────────────────────────────────────────
let _hp, _hpc;
async function getHp() {
  if (!_hp) {
    _hp  = await import('hyparquet');
    _hpc = await import('hyparquet-compressors');
  }
  return { parquetRead: _hp.parquetRead, parquetMetadata: _hp.parquetMetadata, compressors: _hpc.compressors };
}

function toAB(nodeBuf) {
  return nodeBuf.buffer.slice(nodeBuf.byteOffset, nodeBuf.byteOffset + nodeBuf.byteLength);
}

/**
 * Read a single parquet file and return array of row-arrays.
 * @param {string} filePath
 * @param {string[]|null} columns  - optional column subset
 */
async function readParquet(filePath, columns) {
  if (!fs.existsSync(filePath)) return null;
  const { parquetRead, parquetMetadata, compressors } = await getHp();
  const raw = fs.readFileSync(filePath);
  const buf = toAB(raw);
  const meta = parquetMetadata(buf);
  const allCols = meta.schema.slice(1).map(s => s.name);
  return new Promise((resolve, reject) => {
    const opts = { file: buf, compressors, onComplete: rows => resolve({ cols: allCols, rows }) };
    if (columns) opts.columns = columns;
    parquetRead(opts).catch(reject);
  });
}

// ── Path helpers ─────────────────────────────────────────────────────────

function bybitPath(symbol, dataType, year, month) {
  const mm = String(month).padStart(2, '0');
  return path.join(DATA_ROOT, 'bybit', symbol, dataType, String(year), `${mm}.parquet`);
}

function orderBookPath(symbol, year, month, day) {
  const mm = String(month).padStart(2, '0');
  const dd = String(day).padStart(2, '0');
  return path.join(DATA_ROOT, 'bybit', symbol, 'orderbook', String(year), mm, `${dd}.parquet`);
}

function xauusdPath(year, month) {
  const mm = String(month).padStart(2, '0');
  return path.join(DATA_ROOT, 'capital.com', 'xauusd', '5M', String(year), `${mm}.parquet`);
}

// ── Helpers ───────────────────────────────────────────────────────────────

function colIdx(cols, name) { return cols.indexOf(name); }

function rowToObj(row, cols) {
  const o = {};
  cols.forEach((c, i) => { o[c] = row[i]; });
  return o;
}

// ── Public API ────────────────────────────────────────────────────────────

/**
 * Get latest close price and ~1h-ago close for a Bybit symbol from parquet.
 * Uses 1m data to compute hourly performance (change24h field holds the 1h change).
 * Returns { price, prevHourClose, change24h, priceHistory }
 * priceHistory: last N candles as { time, price }
 */
async function getPriceData(symbol, tf, historyCount = 80) {
  const now   = new Date();
  const year  = now.getUTCFullYear();
  const month = now.getUTCMonth() + 1;

  // Try current month, fallback to prev month
  let result = await readParquet(bybitPath(symbol, tf, year, month), ['timestamp', 'close']);
  if (!result || result.rows.length === 0) {
    const prevMonth = month === 1 ? 12 : month - 1;
    const prevYear  = month === 1 ? year - 1 : year;
    result = await readParquet(bybitPath(symbol, tf, prevYear, prevMonth), ['timestamp', 'close']);
  }
  if (!result) return null;

  const { rows } = result;
  const ciTs    = 0; // timestamp index
  const ciClose = 1; // close index

  // Latest close = last row
  const lastRow  = rows[rows.length - 1];
  const price    = lastRow ? Number(lastRow[ciClose]) : 0;
  const lastTs   = lastRow ? (lastRow[ciTs] instanceof Date ? lastRow[ciTs].getTime() : new Date(lastRow[ciTs]).getTime()) : 0;

  // Find close from ~1 hour ago (last row whose timestamp <= lastTs - 3600000 ms)
  const oneHourAgo = lastTs - 3600000;
  let prevHourClose = 0;
  for (let i = rows.length - 1; i >= 0; i--) {
    const ts = rows[i][ciTs];
    const rowTs = ts instanceof Date ? ts.getTime() : new Date(ts).getTime();
    if (rowTs <= oneHourAgo) {
      prevHourClose = Number(rows[i][ciClose]);
      break;
    }
  }

  const change24h = prevHourClose > 0 ? ((price - prevHourClose) / prevHourClose) * 100 : 0;

  // Build price history: last N candles (subsample if >N)
  const step = Math.max(1, Math.floor(rows.length / historyCount));
  const priceHistory = [];
  for (let i = 0; i < rows.length; i += step) {
    const r = rows[i];
    const ts = r[ciTs];
    const t = ts instanceof Date ? ts.getTime() : new Date(ts).getTime();
    const p = Number(r[ciClose]);
    if (p > 0) priceHistory.push({ time: t, price: p });
  }
  // Always include the last point
  if (rows.length > 0) {
    const r = rows[rows.length - 1];
    const ts = r[ciTs];
    const t = ts instanceof Date ? ts.getTime() : new Date(ts).getTime();
    const p = Number(r[ciClose]);
    if (priceHistory.length === 0 || priceHistory[priceHistory.length - 1].time !== t) {
      priceHistory.push({ time: t, price: p });
    }
  }

  return { price, prevHourClose, change24h, priceHistory };
}

/** Get latest close price from 1m parquet (high-frequency feed). */
async function get1mPriceData(symbol) {
  return getPriceData(symbol, '1m', 120); // 120 candles ≈ 2 hours of 1m data
}

/** Get latest close price from 5m parquet (legacy / fallback). */
async function get5mPriceData(symbol) {
  return getPriceData(symbol, '5m', 80); // 80 candles ≈ 6.7 hours of 5m data
}

/**
 * XAUUSD price from capital.com 5M parquet.
 * Uses 5m data to compute hourly performance (change24h field holds the 1h change).
 */
async function getXauusdPriceData() {
  const now   = new Date();
  const year  = now.getUTCFullYear();
  const month = now.getUTCMonth() + 1;

  let result = await readParquet(xauusdPath(year, month), ['timestamp', 'close']);
  if (!result || result.rows.length === 0) {
    const prevMonth = month === 1 ? 12 : month - 1;
    const prevYear  = month === 1 ? year - 1 : year;
    result = await readParquet(xauusdPath(prevYear, prevMonth), ['timestamp', 'close']);
  }
  if (!result) return null;

  const { rows } = result;
  const lastRow  = rows[rows.length - 1];
  const price    = lastRow ? Number(lastRow[1]) : 0;
  const lastTs   = lastRow ? (lastRow[0] instanceof Date ? lastRow[0].getTime() : new Date(lastRow[0]).getTime()) : 0;

  // Find close from ~1 hour ago (last row whose timestamp <= lastTs - 3600000 ms)
  const oneHourAgo = lastTs - 3600000;
  let prevHourClose = 0;
  for (let i = rows.length - 1; i >= 0; i--) {
    const ts = rows[i][0];
    const rowTs = ts instanceof Date ? ts.getTime() : new Date(ts).getTime();
    if (rowTs <= oneHourAgo) {
      prevHourClose = Number(rows[i][1]);
      break;
    }
  }
  const change24h = prevHourClose > 0 ? ((price - prevHourClose) / prevHourClose) * 100 : 0;

  const step = Math.max(1, Math.floor(rows.length / 80));
  const priceHistory = [];
  for (let i = 0; i < rows.length; i += step) {
    const r = rows[i]; const ts = r[0];
    const t = ts instanceof Date ? ts.getTime() : new Date(ts).getTime();
    const p = Number(r[1]);
    if (p > 0) priceHistory.push({ time: t, price: p });
  }

  return { price, prevHourClose, change24h, priceHistory };
}

/**
 * Latest funding rate for a symbol.
 * Returns { rate, fundingTime } or null.
 */
async function getLatestFunding(symbol) {
  const now   = new Date();
  const year  = now.getUTCFullYear();
  const month = now.getUTCMonth() + 1;

  let result = await readParquet(bybitPath(symbol, 'funding_rate', year, month));
  if (!result || result.rows.length === 0) {
    const pm = month === 1 ? 12 : month - 1;
    const py = month === 1 ? year - 1 : year;
    result = await readParquet(bybitPath(symbol, 'funding_rate', py, pm));
  }
  if (!result) return null;

  const { cols, rows } = result;
  const ciRate = colIdx(cols, 'fundingRate');
  const ciTime = colIdx(cols, 'fundingTime');
  const last   = rows[rows.length - 1];
  if (!last) return null;

  const fundingHistory = rows.slice(-40).map(r => ({
    time: r[ciTime] instanceof Date ? r[ciTime].getTime() : new Date(r[ciTime]).getTime(),
    rate: Number(r[ciRate])
  }));

  return {
    rate: Number(last[ciRate]),
    nextFundingTime: null,
    history: fundingHistory
  };
}

/**
 * Latest OI data for a symbol.
 * Returns { openInterest, oiDeltaPct, history } or null.
 */
async function getLatestOI(symbol) {
  const now   = new Date();
  const year  = now.getUTCFullYear();
  const month = now.getUTCMonth() + 1;

  let result = await readParquet(bybitPath(symbol, 'open_interest', year, month));
  if (!result || result.rows.length === 0) {
    const pm = month === 1 ? 12 : month - 1;
    const py = month === 1 ? year - 1 : year;
    result = await readParquet(bybitPath(symbol, 'open_interest', py, pm));
  }
  if (!result) return null;

  const { cols, rows } = result;
  const ciOI   = colIdx(cols, 'openInterest');
  const ciTime = colIdx(cols, 'timestamp');

  if (rows.length < 2) return null;

  const last     = rows[rows.length - 1];
  const prev     = rows[rows.length - 2];
  const oiNow    = Number(last[ciOI]);
  const oiPrev   = Number(prev[ciOI]);
  const oiDelta  = oiPrev > 0 ? ((oiNow - oiPrev) / oiPrev) * 100 : 0;

  const oiHistory = rows.slice(-40).map(r => ({
    time: r[ciTime] instanceof Date ? r[ciTime].getTime() : new Date(r[ciTime]).getTime(),
    oi  : Number(r[ciOI])
  }));

  return { openInterest: oiNow, oiDeltaPct: oiDelta, history: oiHistory };
}

/**
 * Latest orderbook snapshot for a symbol.
 * Returns { bids, asks, bestBid, bestAsk, depthBid, depthAsk, imbalance, spread } or null.
 */
async function getLatestOrderbook(symbol) {
  const now  = new Date();
  const year = now.getUTCFullYear();
  const mon  = now.getUTCMonth() + 1;
  const day  = now.getUTCDate();

  // Try today then yesterday
  let result = await readParquet(orderBookPath(symbol, year, mon, day), ['timestamp', 'symbol', 'side', 'price', 'size']);
  if (!result || result.rows.length === 0) {
    const yd = new Date(now); yd.setUTCDate(yd.getUTCDate() - 1);
    result = await readParquet(orderBookPath(symbol, yd.getUTCFullYear(), yd.getUTCMonth() + 1, yd.getUTCDate()), ['timestamp', 'symbol', 'side', 'price', 'size']);
  }
  if (!result) return null;

  const { rows } = result;
  // cols when filtered: timestamp, symbol, side, price, size
  // Note: 'level' is filtered out but hyparquet still returns it when specified explicitly
  // We read with columns filter: ['timestamp','symbol','side','price','size']
  // Actual cols returned depend on file
  const ciTs   = 0;
  const ciSide = 2;
  const ciPr   = 3;
  const ciSz   = 4;

  // Latest snapshot = rows with the most recent timestamp
  const latestTs = rows[rows.length - 1]?.[ciTs];
  const snap = rows.filter(r => {
    const ts = r[ciTs];
    const a = ts instanceof Date ? ts.getTime() : new Date(ts).getTime();
    const b = latestTs instanceof Date ? latestTs.getTime() : new Date(latestTs).getTime();
    return a === b;
  });

  if (snap.length === 0) return null;

  const bids = snap.filter(r => String(r[ciSide]) === 'bid')
    .map(r => [Number(r[ciPr]), Number(r[ciSz])]);
  const asks = snap.filter(r => String(r[ciSide]) === 'ask')
    .map(r => [Number(r[ciPr]), Number(r[ciSz])]);

  bids.sort((a, b) => b[0] - a[0]); // desc by price
  asks.sort((a, b) => a[0] - b[0]); // asc by price

  const top = Math.min(10, bids.length, asks.length);
  const bidSlice = bids.slice(0, top);
  const askSlice = asks.slice(0, top);

  const totalBid = bidSlice.reduce((s, r) => s + r[1], 0);
  const totalAsk = askSlice.reduce((s, r) => s + r[1], 0);
  const total    = totalBid + totalAsk || 1;
  const imbalance = (totalBid / total) * 100;

  const bestBid = bidSlice[0]?.[0] || 0;
  const bestAsk = askSlice[0]?.[0] || 0;
  const spread  = bestAsk > 0 && bestBid > 0 ? bestAsk - bestBid : 0;

  return {
    bids: bidSlice,
    asks: askSlice,
    bestBid,
    bestAsk,
    depthBid: totalBid,
    depthAsk: totalAsk,
    imbalance,
    spread
  };
}

module.exports = {
  get1mPriceData,
  get5mPriceData,
  getXauusdPriceData,
  getLatestFunding,
  getLatestOI,
  getLatestOrderbook,
};
