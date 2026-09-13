const express = require('express');
const cors = require('cors');
const path = require('path');
const https = require('https');
const { spawn } = require('child_process');
const pq    = require('./parquet-reader.cjs');
const pool  = require('./db.cjs');
const vtc   = require('./vtc-engine.cjs');
const bybit = require('./bybit-client.cjs');

const app = express();
const PORT = process.env.PORT || 3001;

// ── Telegram Alert Helper ────────────────────────────────────────────────
const TELEGRAM_BOT_TOKEN = process.env.TELEGRAM_BOT_TOKEN;
const TELEGRAM_CHAT_ID_TRADING = process.env.TELEGRAM_CHAT_ID_TRADING;
const ENABLE_TELEGRAM_ALERTS = process.env.ENABLE_TELEGRAM_ALERTS === 'true';

function sendTelegramAlert(message, silent = false) {
  if (!ENABLE_TELEGRAM_ALERTS || !TELEGRAM_BOT_TOKEN || !TELEGRAM_CHAT_ID_TRADING) {
    return;
  }
  const payload = JSON.stringify({
    chat_id: TELEGRAM_CHAT_ID_TRADING,
    text: message,
    parse_mode: 'HTML',
    disable_notification: silent,
  });
  const options = {
    hostname: 'api.telegram.org',
    path: `/bot${TELEGRAM_BOT_TOKEN}/sendMessage`,
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(payload),
    },
  };
  const req = https.request(options, (res) => {
    let data = '';
    res.on('data', chunk => data += chunk);
    res.on('end', () => {
      try {
        const json = JSON.parse(data);
        if (!json.ok) console.error('[Telegram Alert] Failed:', json.description);
      } catch (e) { /* ignore */ }
    });
  });
  req.on('error', (err) => console.error('[Telegram Alert] Error:', err.message));
  req.write(payload);
  req.end();
}

// ── Structured Logging Helpers (v0.3.0 Phase 2) ──────────────────────────

async function logTradeEvent(tradeId, symbol, eventType, price, balanceBefore, balanceAfter, marginDelta, fee, pnlDelta, detail = null) {
  try {
    await pool.query(`
      INSERT INTO trade_event_log (
        trade_id, symbol, event_type, event_time, price_at_event,
        balance_before, balance_after, margin_delta, fee_applied, pnl_delta, detail
      ) VALUES ($1,$2,$3,NOW(),$4,$5,$6,$7,$8,$9,$10)
    `, [tradeId, symbol, eventType, price, balanceBefore, balanceAfter, marginDelta, fee, pnlDelta, detail ? JSON.stringify(detail) : null]);
  } catch (e) {
    console.error(`[logTradeEvent] Failed for trade ${tradeId}:`, e.message);
  }
}

async function logSignalAudit(signalId, symbol, dqs, gateReached, gatePassed, gateReason, finalAction) {
  try {
    await pool.query(`
      INSERT INTO signal_audit_log (
        run_time, signal_id, symbol, dqs, gate_reached, gate_passed, gate_reason, final_action
      ) VALUES (NOW(),$1,$2,$3,$4,$5,$6,$7)
    `, [signalId, symbol, dqs, gateReached, gatePassed, gateReason, finalAction]);
  } catch (e) {
    console.error(`[logSignalAudit] Failed for signal ${signalId}:`, e.message);
  }
}

// v0.3.3 — Trade debug context snapshot logger
async function logTradeDebug(tradeId, symbol, logType, ctx) {
  try {
    const btcIntel = database.marketIntel['BTCUSDT'] || {};
    const symIntel = database.marketIntel[symbol] || {};
    const live = database.livePrices[symbol] || {};
    const btcLive = database.livePrices['BTCUSDT'] || {};

    const payload = {
      trade_id: tradeId,
      symbol,
      log_type: logType,
      price: ctx.price || live.price || 0,
      btc_price: btcLive.price || 0,
      btc_24h_ret: (btcIntel.change24h || 0) / 100,
      symbol_24h_ret: (symIntel.change24h || 0) / 100,
      correlation_vs_btc: null, // TODO: compute from price history
      rsi_15m: ctx.rsi_15m || null,
      adx_4h: ctx.adx_4h || null,
      atr_15m: ctx.atr_15m || null,
      ema_20: null,
      ema_50: null,
      volume_ratio: null,
      funding_rate: ctx.funding_rate || (symIntel.funding ? symIntel.funding.rate : null),
      oi_delta_pct: ctx.oi_delta_pct || (symIntel.oi ? symIntel.oi.deltaPct : null),
      ob_spread_pct: ctx.ob_spread_pct || (symIntel.orderbook ? symIntel.orderbook.spreadPct : null),
      ob_imbalance: ctx.ob_imbalance || (symIntel.orderbook ? symIntel.orderbook.imbalance : null),
      liquidation_side: null,
      session: ctx.session || null,
      market_regime: ctx.market_regime || null,
      dqs_score: ctx.dqs_score || null,
      dqs_breakdown: ctx.dqs_breakdown ? JSON.stringify(ctx.dqs_breakdown) : null,
      calibrated_prob: ctx.calibrated_prob || null,
      brain_prob: ctx.brain_prob || null,
    };

    await pool.query(`
      INSERT INTO trade_debug_log (
        trade_id, symbol, log_type, price, btc_price, btc_24h_ret, symbol_24h_ret,
        correlation_vs_btc, rsi_15m, adx_4h, atr_15m, ema_20, ema_50, volume_ratio,
        funding_rate, oi_delta_pct, ob_spread_pct, ob_imbalance, liquidation_side,
        session, market_regime, dqs_score, dqs_breakdown, calibrated_prob, brain_prob
      ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25)
    `, [
      payload.trade_id, payload.symbol, payload.log_type,
      payload.price, payload.btc_price, payload.btc_24h_ret, payload.symbol_24h_ret,
      payload.correlation_vs_btc, payload.rsi_15m, payload.adx_4h, payload.atr_15m,
      payload.ema_20, payload.ema_50, payload.volume_ratio,
      payload.funding_rate, payload.oi_delta_pct, payload.ob_spread_pct, payload.ob_imbalance,
      payload.liquidation_side, payload.session, payload.market_regime,
      payload.dqs_score, payload.dqs_breakdown, payload.calibrated_prob, payload.brain_prob
    ]);
  } catch (e) {
    console.error(`[logTradeDebug] Failed for trade ${tradeId}:`, e.message);
  }
}

// v0.3.3 — Rule-based failure classifier
function classifyFailure(entryCtx, exitCtx, trade) {
  const side = (trade.side || trade.trade_decision || 'LONG').toUpperCase();
  const exitReason = (trade.exit_reason || '').toUpperCase();
  const hoursHeld = parseFloat(trade.hours_held || 0);
  const netPnl = parseFloat(trade.net_pnl_usd || trade.net_pnl_pct || 0);

  if (netPnl >= 0) {
    if (exitReason === 'TIME_EXIT') return 'CORRECT_SIGNAL';
    return 'UNKNOWN';
  }

  const btcRet = parseFloat((entryCtx && entryCtx.btc_24h_ret) || 0);
  if (side === 'LONG' && btcRet < -0.02) return 'MACRO_HEADWIND';
  if (side === 'SHORT' && btcRet > 0.02) return 'MACRO_HEADWIND';

  const entryAtr = Math.max(parseFloat((entryCtx && entryCtx.atr_15m) || 1), 0.0001);
  const exitAtr = parseFloat((exitCtx && exitCtx.atr_15m) || entryAtr);
  if (exitAtr / entryAtr > 1.5) return 'WHIPSAW';

  if (hoursHeld >= 5 && exitReason === 'TIME_EXIT') return 'TIMING';

  const obImb = parseFloat((entryCtx && entryCtx.ob_imbalance) || (entryCtx && entryCtx.ob_imbalance_pct) || 0);
  const funding = parseFloat((entryCtx && entryCtx.funding_rate) || 0);
  const oiDelta = parseFloat((entryCtx && entryCtx.oi_delta_pct) || 0);
  if (side === 'LONG' && funding > 0.0001 && oiDelta > 2 && obImb < -5) return 'ENTRY_QUALITY';
  if (side === 'SHORT' && funding < -0.0001 && oiDelta > 2 && obImb > 5) return 'ENTRY_QUALITY';

  const adx = parseFloat((entryCtx && entryCtx.adx_4h) || 0);
  const rsi = parseFloat((entryCtx && entryCtx.rsi_15m) || 50);
  if (adx < 25 && rsi < 35 && hoursHeld < 3 && ['SL_HIT', 'LIQUIDATED'].includes(exitReason)) return 'TIMING';

  return 'UNKNOWN';
}

function suggestedFix(category) {
  const fixes = {
    MACRO_HEADWIND: 'Add BTC trend gate: skip LONG if BTC 4h structure bearish',
    TIMING: 'Wait for ADX >= 25 and RSI >= 30 before entry; tighten entry timing',
    WHIPSAW: 'Widen SL by regime factor when ATR > 1.2x 20-bar average',
    CORRECT_SIGNAL: 'Extend hold time or widen TP for high-conviction setups',
    ENTRY_QUALITY: 'Raise liquidity threshold: require aligned funding + falling OI',
    UNKNOWN: 'Review raw DQS breakdown and macro context for edge cases',
  };
  return fixes[category] || 'Review trade context manually';
}

// v0.3.5 — Dynamic Stop-Loss engine
function computeDynamicSL(trade, livePrice, hoursOpen) {
  const side = (trade.side || 'LONG').toUpperCase();
  const entry = parseFloat(trade.entry_price_actual || trade.entry_price_planned || 0);
  const tp = parseFloat(trade.tp_price || 0);
  const slOriginal = parseFloat(trade.sl_original || trade.sl_price || 0);
  const atrEntry = parseFloat(trade.atr_at_entry || 0);
  if (entry <= 0 || slOriginal <= 0) return null;

  let newSl = slOriginal;
  let reason = [];

  // 1. Break-even stop: move SL to entry when floating P&L > 75% of TP distance
  // (raised from 50% — 50% was too aggressive, causing trades to be stopped out
  //  by normal noise before reaching TP)
  const tpDist = Math.abs(tp - entry);
  if (tpDist > 0) {
    const pnlFrac = side === 'LONG'
      ? (livePrice - entry) / tpDist
      : (entry - livePrice) / tpDist;
    if (pnlFrac > 0.75) {
      // Move SL to 0.3% below/above entry (was 0.05% — too tight, noise killed it)
      newSl = side === 'LONG' ? entry * 0.997 : entry * 1.003;
      reason.push('BREAK_EVEN');
    }
  }

  // 2. Time decay: tighten SL by 0.15 x ATR per hour after hour 3
  if (atrEntry > 0 && hoursOpen > 3) {
    const tightenAmt = 0.15 * atrEntry * (hoursOpen - 3);
    const decaySl = side === 'LONG'
      ? Math.max(entry, slOriginal + tightenAmt)
      : Math.min(entry, slOriginal - tightenAmt);
    // Only tighten (move SL toward entry), never loosen
    if (side === 'LONG' && decaySl > newSl) {
      newSl = decaySl;
      reason.push('TIME_DECAY');
    }
    if (side === 'SHORT' && decaySl < newSl) {
      newSl = decaySl;
      reason.push('TIME_DECAY');
    }
  }

  // 3. ATR expansion: widen SL when live volatility > 1.5x entry ATR
  // Approximate live ATR from recent price history if available
  const hist = database.marketIntel[trade.symbol]?.history?.prices || [];
  let liveAtr = atrEntry;
  if (hist.length >= 20) {
    // Simple proxy: average high-low range of last 14 points
    const recent = hist.slice(-14);
    let sumRange = 0;
    for (let i = 1; i < recent.length; i++) {
      const prev = typeof recent[i - 1] === 'number' ? recent[i - 1] : recent[i - 1]?.close || 0;
      const curr = typeof recent[i] === 'number' ? recent[i] : recent[i]?.close || 0;
      if (prev > 0 && curr > 0) {
        sumRange += Math.abs(curr - prev);
      }
    }
    liveAtr = (sumRange / (recent.length - 1)) || atrEntry;
  }
  if (atrEntry > 0 && liveAtr > 1.5 * atrEntry) {
    const expansionFactor = liveAtr / atrEntry;
    const expandedSl = side === 'LONG'
      ? entry - (entry - slOriginal) * expansionFactor
      : entry + (slOriginal - entry) * expansionFactor;
    // Only widen (move SL away from entry), never tighten via expansion
    if (side === 'LONG' && expandedSl < newSl) {
      newSl = expandedSl;
      reason.push('ATR_EXPANSION');
    }
    if (side === 'SHORT' && expandedSl > newSl) {
      newSl = expandedSl;
      reason.push('ATR_EXPANSION');
    }
  }

  return { newSl: parseFloat(newSl.toFixed(8)), reasons: reason };
}

const TRACKED_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT', 'DOGEUSDT'];
const MARKET_INTEL_SYMBOLS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT', 'BNBUSDT', 'DOGEUSDT', 'XAUUSD'];

// ── Bybit Mainnet Mechanics Constants (v0.3.0) ───────────────────────────
const QTY_STEP = {
  BTCUSDT: 0.001, ETHUSDT: 0.001, SOLUSDT: 0.1,
  XRPUSDT: 1, BNBUSDT: 0.01, DOGEUSDT: 1
};
const MMR = 0.005;           // Tier 1 Maintenance Margin Rate = 0.5%
const MIN_NOTIONAL = 5.0;    // Bybit min notional for USDT perps
const TAKER_FEE = 0.00055;   // 0.055% per side
const SLIPPAGE_BPS = {
  BTCUSDT: 2.0, ETHUSDT: 3.0, SOLUSDT: 4.0,
  XRPUSDT: 5.0, BNBUSDT: 5.0, DOGEUSDT: 8.0,
};

// ── Per-Symbol Config (mirrors portfolio_config.py) ──────────────────────
const PER_SYMBOL_MAX_HOURS = {
  BTCUSDT: 12, ETHUSDT: 24, SOLUSDT: 12, XRPUSDT: 12, BNBUSDT: 48, DOGEUSDT: 12
};
const PER_SYMBOL_CALIBRATION_GATE = {
  BTCUSDT: 0.20, ETHUSDT: 0.15, SOLUSDT: 0.20, XRPUSDT: 0.20, BNBUSDT: 0.15, DOGEUSDT: 0.15
};

const _allowedOrigins = [
  'http://localhost:5174',
  'http://localhost:3002',
  process.env.NGROK_DOMAIN ? `https://${process.env.NGROK_DOMAIN}` : null,
].filter(Boolean);

app.use(cors({
  origin: _allowedOrigins,
  credentials: true,
}));
app.use(express.json());

// ── In-memory state ──────────────────────────────────────────────────────
let lastVTCClosedCount = 0; // tracks closed trade count to avoid redundant VTC inserts
let database = {
  signals: [],       // from high_confidence_signals
  watchlist: [],     // from watchlist_current
  powerTrades: [],   // from power_trades
  winners: [],       // from winners
  history: [],       // from signals_historical (log view)
  paper: {
    account: { balance: 10000, equity: 10000, margin_used: 0, total_pnl: 0, total_trades: 0, win_rate: 0 },
    pendingTrades: [],
    activeTrades: [],
    closedTrades: []
  },
  livePrices: {},
  marketIntel: {},  // [symbol]: { orderbook, oi, funding, narrative, history }
  vtc: {
    price: 1.0,
    score: 50.0,
    confidence: 0.5,
    marketCap: 1000000,
    nav: 1000000,
    supply: 1000000,
    timestamp: new Date().toISOString(),
    history: [],       // price history points
    scores: [],        // score timeline
    treasury: { total_supply: 1000000, circulating_supply: 1000000, minted: 0, burned: 0 },
    audit: [],
    vector: null,
    layers: { performance: 0, risk: 0, growth: 0 },
  }
};

// ── Schema mapping helpers ───────────────────────────────────────────────

// Maps high_confidence_signals row → Signal object
function mapHCSSignal(row) {
  const dir = (row.h4_direction || '').toUpperCase();
  let type = 'LONG'; // HCS are always long-biased pullback setups
  if (dir === 'DOWN') type = 'SHORT';
  const trafficLight = type === 'LONG' ? 'GREEN' : 'RED';
  const conf = parseInt(row.confidence || 0);
  return {
    id: `HCS-${row.id}`,
    dbId: row.id,
    asset: row.symbol,
    type,
    confidence: conf,
    grade: row.grade || 'MEDIUM',
    signal_strength: row.signal_strength || '',
    price: parseFloat(row.price_at_signal || 0),
    tp: parseFloat(row.tp_price || 0),
    sl: parseFloat(row.sl_price || 0),
    rr: parseFloat(row.risk_reward || 0),
    dollar_risk: parseFloat(row.dollar_risk || 0),
    actual_dollar_risk: parseFloat(row.actual_dollar_risk || 0),
    leveraged_notional: parseFloat(row.leveraged_notional || 0),
    qty_contracts: parseFloat(row.qty_contracts || 0),
    rsi: parseFloat(row.rsi_15m || 0),
    adx: parseFloat(row.adx_4h || 0),
    funding_rate: parseFloat(row.funding_rate || 0),
    oi_delta_pct: parseFloat(row.oi_delta_pct || 0),
    ob_imbalance_pct: parseFloat(row.ob_imbalance_pct || 0),
    technical_score: parseInt(row.technical_score || 0),
    market_structure_score: parseInt(row.market_structure_score || 0),
    funding_oi_score: parseInt(row.funding_oi_score || 0),
    session_symbol_score: parseInt(row.session_symbol_score || 0),
    session: row.session || '',
    trade_decision: row.trade_decision || '',
    action: row.action || '',
    summary: row.summary || '',
    time: new Date(row.scan_time_utc).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    signal_bar_utc: row.signal_bar_utc,
    scan_time_utc: row.scan_time_utc,
    trafficLight,
    timestamp: new Date(row.scan_time_utc).getTime(),
    isPowerTrade: false,
    isExpired: !!row.is_expired,
    expiryReason: row.expiry_reason || '',
    ageMinutes: Math.round(((new Date() - new Date(row.scan_time_utc)) / 1000 / 60)),
    lifecycle_state: (() => {
      if (row.trade_status === 'OPEN') return 'IN_TRADE';
      if (row.trade_status === 'CLOSED') return row.trade_exit_reason || 'CLOSED';
      if (row.trade_status === 'CANCELLED') return 'CANCELED';
      if (row.is_expired) return 'ABANDONED';
      return 'PENDING';
    })()
  };
}

// Maps watchlist_current row → WatchlistItem object
function mapWatchlistItem(row) {
  const sigType = (row.signal_type || '').toUpperCase();
  const gates = row.gates_passed || '0/0';
  const [passed, total] = gates.split('/').map(Number);
  let status = 'WATCHING';
  if (sigType === 'CONFIRMED') status = 'CONFIRMED';
  else if (sigType === 'APPROACHING') status = 'APPROACHING';
  else if (sigType === 'OFF_SESSION') status = 'OFF_SESSION';
  else if (!isNaN(passed) && !isNaN(total) && total > 0) {
    // Derive status from gate pass ratio when signal_type is missing
    const ratio = passed / total;
    if (ratio >= 0.9) status = 'CONFIRMED';
    else if (ratio >= 0.6) status = 'APPROACHING';
    else if (ratio >= 0.3) status = 'WATCHING';
    else status = 'OFF_SESSION';
  }

  return {
    id: `WL-${row.id}`,
    symbol: row.symbol,
    status,
    quality: row.quality || '',
    gates_passed: row.gates_passed || '0/4',
    gates: {
      session:   { pass: row.gate_session_ok,    label: 'Session' },
      adx:       { pass: row.gate_adx_ok,        label: 'ADX ≥25' },
      direction: { pass: row.gate_direction_ok,  label: '4h Direction' },
      rsi:       { pass: row.gate_rsi_ok,        label: 'RSI <40' },
      bull:      { pass: row.gate_bull_4h_ok,   label: 'EMA Trend' }
    },
    current_price: parseFloat(row.current_price || 0),
    rsi: parseFloat(row.rsi_15m_now || 0),
    adx: parseFloat(row.adx_4h_now || 0),
    atr: parseFloat(row.atr_15m_now || 0),
    session: row.session_now || '',
    regime: row.regime || '',
    h4_direction: row.h4_direction || '',
    h4_ema_trend: row.h4_ema_trend || '',
    sl_level: parseFloat(row.sl_level || 0),
    tp_level: parseFloat(row.tp_level || 0),
    notes: row.notes || '',
    scan_time_utc: row.scan_time_utc,
    timestamp: new Date(row.scan_time_utc).getTime()
  };
}

// Maps power_trades row → PowerTrade object
function mapPowerTrade(row) {
  return {
    id: `PT-${row.id}`,
    dbId: row.id,
    signalId: row.signal_id,
    symbol: row.symbol,
    confidence: parseInt(row.confidence || 0),
    signal_strength: row.signal_strength || '',
    price: parseFloat(row.price_at_signal || 0),
    tp: parseFloat(row.tp_price || 0),
    sl: parseFloat(row.sl_price || 0),
    rr: parseFloat(row.risk_reward || 0),
    dollar_risk: parseFloat(row.dollar_risk || 0),
    leveraged_notional: parseFloat(row.leveraged_notional || 0),
    session: row.session || '',
    h4_direction: row.h4_direction || '',
    action: row.action || '',
    summary: row.summary || '',
    urgency: row.urgency || 'NORMAL',
    analyst_note: row.analyst_note || '',
    status: row.status || 'ACTIVE',
    promoted_at: row.promoted_at,
    timestamp: new Date(row.promoted_at || row.created_at).getTime()
  };
}

// Maps winners row → Winner object
function mapWinner(row) {
  return {
    id: `WIN-${row.id}`,
    symbol: row.symbol,
    confidence: parseInt(row.confidence || 0),
    signal_strength: row.signal_strength || '',
    entry_price: parseFloat(row.entry_price || 0),
    exit_price: parseFloat(row.exit_price || 0),
    net_pnl_pct: parseFloat(row.net_pnl_pct || 0),
    net_pnl_usd: parseFloat(row.net_pnl_usd || 0),
    hours_held: parseFloat(row.hours_held || 0),
    exit_reason: row.exit_reason || '',
    session: row.session || '',
    recorded_at: row.recorded_at,
    timestamp: new Date(row.recorded_at || row.created_at).getTime()
  };
}

// Maps paper_trades row → PaperTrade object (fixes hardcoded side bug)
function mapPaperTrade(row) {
  const entryPrice = parseFloat(row.entry_price_actual || row.entry_price_planned || 0);
  const pnlPct = parseFloat(row.net_pnl_pct || 0);
  const pnlUsd = parseFloat(row.net_pnl_usd || 0);
  const entryTime = row.created_at ? new Date(row.created_at).toISOString() : null;
  const exitTime = row.exit_time_utc ? new Date(row.exit_time_utc).toISOString() : null;
  const leverage = parseFloat(row.leverage || 4);  // Use actual leverage from trade, not hardcoded
  const leveragedNotional = parseFloat(row.leveraged_notional || 0);
  // margin_required is stored directly in DB; fallback to notional_1x = leveraged_notional / leverage
  const margin = parseFloat(row.margin_required || 0) > 0 
    ? parseFloat(row.margin_required) 
    : (leveragedNotional > 0 ? leveragedNotional / leverage : 0);
  // Determine side: prefer DB column, fallback to notes text
  let side = row.side || '';
  if (!side && row.notes && row.notes.toUpperCase().includes('SHORT')) side = 'SHORT';
  if (!side) side = 'LONG';
  return {
    id: row.id,
    signalId: row.signal_id,
    symbol: row.symbol,
    side,
    entry_price: entryPrice,
    entry_price_planned: parseFloat(row.entry_price_planned || 0),
    exit_price: row.exit_price ? parseFloat(row.exit_price) : null,
    sl_price: parseFloat(row.sl_price || 0),
    tp_price: parseFloat(row.tp_price || 0),
    qty: parseFloat(row.qty_contracts || 0),
    leverage,
    leveraged_notional: leveragedNotional,
    margin,                                  // cash at risk = notional / leverage
    confidence: parseInt(row.confidence || 0),
    funding_rate: parseFloat(row.funding_rate_at_entry || 0),
    liquidation_price: parseFloat(row.liquidation_price || 0),
    maintenance_margin: parseFloat(row.maintenance_margin || 0),
    fees_total: parseFloat(row.fees_total || 0),
    pnl: pnlUsd,
    pnl_pct: pnlPct,
    live_pnl: null,     // populated for OPEN trades after mapping
    live_pnl_pct: null,
    status: row.status || 'PENDING',
    notes: row.notes || '',
    entry_time: entryTime,
    exit_time: exitTime,
    exit_reason: row.exit_reason || null,
    hours_held: row.hours_held ? parseFloat(row.hours_held) : null
  };
}

// Maps signals_historical row → HistoryItem
function mapHistory(row) {
  const sigType = (row.signal_type || '').toUpperCase();
  let type = 'WAIT';
  if (sigType === 'CONFIRMED') type = 'LONG';
  else if (sigType === 'OFF_SESSION') type = 'WAIT';
  return {
    id: `HIST-${row.id}`,
    asset: row.symbol,
    type,
    grade: row.quality || 'MONITOR',
    price: parseFloat(row.price_at_signal || 0),
    status: 'Skipped',
    pnl: '--',
    time: new Date(row.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    timestamp: new Date(row.created_at).getTime()
  };
}

// ── Market Intel Engine ──────────────────────────────────────────────────

function httpsGet(url) {
  return new Promise((resolve, reject) => {
    let done = false;
    const finish = (err, val) => { if (done) return; done = true; err ? reject(err) : resolve(val); };
    const timer = setTimeout(() => finish(new Error('deadline'), null), 4000);
    try {
      const req = https.get(url, { timeout: 3000 }, (res) => {
        let data = '';
        res.on('data', chunk => data += chunk);
        res.on('end', () => {
          clearTimeout(timer);
          try { finish(null, JSON.parse(data)); } catch { finish(null, data); }
        });
        res.on('error', e => { clearTimeout(timer); finish(e, null); });
      });
      req.on('error', e => { clearTimeout(timer); finish(e, null); });
      req.on('timeout', () => { clearTimeout(timer); req.destroy(); finish(new Error('timeout'), null); });
    } catch (e) { clearTimeout(timer); finish(e, null); }
  });
}

async function fetchBybitOrderbook(symbol) {
  try {
    const json = await httpsGet(`https://api.bybit.com/v5/market/orderbook?category=linear&symbol=${symbol}&limit=20`);
    if (json.retCode !== 0) return null;
    const r = json.result;
    const bids = (r.b || []).slice(0, 10).map(([p, q]) => [parseFloat(p), parseFloat(q)]);
    const asks = (r.a || []).slice(0, 10).map(([p, q]) => [parseFloat(p), parseFloat(q)]);
    const bestBid = bids[0]?.[0] || 0;
    const bestAsk = asks[0]?.[0] || 0;
    const depthBid = bids.reduce((s, [, q]) => s + q, 0);
    const depthAsk = asks.reduce((s, [, q]) => s + q, 0);
    const totalDepth = depthBid + depthAsk || 1;
    const imbalance = (depthBid / totalDepth) * 100;
    return {
      bids, asks, bestBid, bestAsk, depthBid, depthAsk, imbalance,
      spread: bestAsk - bestBid
    };
  } catch (e) { return null; }
}

async function fetchBybitFunding(symbol) {
  try {
    const json = await httpsGet(`https://api.bybit.com/v5/market/funding/history?category=linear&symbol=${symbol}&limit=1`);
    if (json.retCode !== 0) return null;
    const item = json.result?.list?.[0];
    if (!item) return null;
    return {
      rate: parseFloat(item.fundingRate || 0),
      nextFundingTime: item.fundingRateTimestamp || null
    };
  } catch (e) { return null; }
}

async function fetchBybitOI(symbol) {
  try {
    const json = await httpsGet(`https://api.bybit.com/v5/market/open-interest?category=linear&symbol=${symbol}&interval=15min&limit=2`);
    if (json.retCode !== 0) return null;
    const list = json.result?.list || [];
    if (list.length === 0) return null;
    const latest = list[0];
    const prev = list[1] || latest;
    const oiNow = parseFloat(latest.openInterest || 0);
    const oiPrev = parseFloat(prev.openInterest || oiNow);
    const oiDelta = oiPrev > 0 ? ((oiNow - oiPrev) / oiPrev) * 100 : 0;
    return {
      openInterest: oiNow,
      oiDeltaPct: oiDelta,
      timestamp: parseInt(latest.openInterest || Date.now())
    };
  } catch (e) { return null; }
}

async function fetchGoldPrice() {
  try {
    // Try Yahoo Finance for XAU/USD
    const json = await httpsGet('https://query1.finance.yahoo.com/v8/finance/chart/GC=F?interval=15m&range=1d');
    const result = json?.chart?.result?.[0];
    if (!result) return null;
    const meta = result.meta;
    const prices = result.indicators?.quote?.[0]?.close || [];
    const lastPrice = prices[prices.length - 1] || meta?.regularMarketPrice || 0;
    const prevClose = meta?.previousClose || meta?.chartPreviousClose || lastPrice;
    const change24h = prevClose > 0 ? ((lastPrice - prevClose) / prevClose) * 100 : 0;
    return { price: lastPrice, change24h };
  } catch (e) { return null; }
}

function generateNarrative(symbol, intel) {
  const { price, change24h, orderbook, oi, funding } = intel;
  const sentences = [];

  // Price trend (change24h now holds 1h change from 1m data)
  const chg = change24h || 0;
  if (chg > 5) sentences.push(`${symbol.replace('USDT', '')} is surging +${chg.toFixed(1)}% in 1h with strong directional momentum.`);
  else if (chg > 2) sentences.push(`${symbol.replace('USDT', '')} is up +${chg.toFixed(1)}% in 1h, showing moderate bullish momentum.`);
  else if (chg > 0) sentences.push(`${symbol.replace('USDT', '')} is up +${chg.toFixed(1)}% in 1h, consolidating recent gains.`);
  else if (chg > -2) sentences.push(`${symbol.replace('USDT', '')} is down ${Math.abs(chg).toFixed(1)}% in 1h, showing minor weakness.`);
  else if (chg > -5) sentences.push(`${symbol.replace('USDT', '')} is down ${Math.abs(chg).toFixed(1)}% in 1h, under moderate selling pressure.`);
  else sentences.push(`${symbol.replace('USDT', '')} is sharply down ${Math.abs(chg).toFixed(1)}% in 1h, facing a significant sell-off.`);

  // Orderbook
  if (orderbook) {
    const imb = orderbook.imbalance;
    if (imb > 58) sentences.push(`Orderbook is heavily bid-side dominant (${imb.toFixed(0)}%), indicating aggressive buying interest.`);
    else if (imb > 52) sentences.push(`Orderbook leans bid-side (${imb.toFixed(0)}%), supporting upward pressure.`);
    else if (imb < 42) sentences.push(`Orderbook is ask-heavy (${imb.toFixed(0)}%), suggesting distribution and potential further downside.`);
    else sentences.push(`Orderbook is relatively balanced (${imb.toFixed(0)}%), indicating a consolidation phase with no clear directional edge.`);
  }

  // OI + price confluence
  if (oi) {
    const oiRising = oi.oiDeltaPct > 0;
    if (chg > 0 && oiRising) sentences.push(`Open Interest is rising (+${oi.oiDeltaPct.toFixed(2)}%) alongside price — new long positions are opening, suggesting genuine bullish conviction.`);
    else if (chg <= 0 && oiRising) sentences.push(`Open Interest is rising (+${oi.oiDeltaPct.toFixed(2)}%) while price declines — shorts are building, indicating bearish conviction.`);
    else if (chg > 0 && !oiRising) sentences.push(`Price is rising but Open Interest is falling — this move may be driven by short covering rather than new buying.`);
    else sentences.push(`Both price and Open Interest are declining — leveraged positions are being unwound across the board.`);
  }

  // Funding
  if (funding) {
    const fr = funding.rate;
    if (fr > 0.0008) sentences.push(`Funding is elevated at ${(fr * 100).toFixed(4)}% — longs are paying shorts, a potential contrarian signal if momentum stalls.`);
    else if (fr > 0.0001) sentences.push(`Funding is mildly positive at ${(fr * 100).toFixed(4)}%, indicating a slight long bias in the perp market.`);
    else if (fr < -0.0008) sentences.push(`Funding is deeply negative at ${(fr * 100).toFixed(4)}% — shorts pay longs, a structurally bullish condition for potential short squeeze.`);
    else if (fr < -0.0001) sentences.push(`Funding is mildly negative at ${(fr * 100).toFixed(4)}%, suggesting a slight short bias.`);
    else sentences.push(`Funding is near neutral at ${(fr * 100).toFixed(4)}%, indicating balanced long/short positioning.`);
  }

  // Conclusion
  const bullishConfluence = (chg > 1) && (orderbook?.imbalance > 52) && (oi?.oiDeltaPct > 0) && (funding?.rate < 0.0005);
  const bearishConfluence = (chg < -1) && (orderbook?.imbalance < 48) && (oi?.oiDeltaPct > 0) && (funding?.rate > 0.0005);
  if (bullishConfluence) sentences.push(`Overall: Strong bullish confluence — rising price, bid-heavy orderbook, rising OI, and manageable funding. This is a high-conviction long environment.`);
  else if (bearishConfluence) sentences.push(`Overall: Bearish confluence — falling price, ask-heavy book, rising OI, and elevated funding. Consider defensive positioning or wait for support.`);
  else sentences.push(`Overall: Mixed signals. No strong directional edge yet. Monitor for a clearer setup before committing capital.`);

  return sentences.join(' ');
}

async function updateMarketIntel() {
  for (const sym of MARKET_INTEL_SYMBOLS) {
    try {
      const existing = database.marketIntel[sym] || {
        symbol: sym, price: 0, change24h: 0,
        orderbook: null, oi: null, funding: null, narrative: '',
        history: { prices: [], funding: [], oi: [] }
      };

      const price     = database.livePrices[sym]?.price     || existing.price     || 0;
      const change24h = database.livePrices[sym]?.change24h || existing.change24h || 0;

      // ── Fetch OB / Funding / OI from parquet (skip for XAUUSD — no futures data) ──
      let orderbook = existing.orderbook;
      let funding   = existing.funding;
      let oi        = existing.oi;

      if (sym !== 'XAUUSD') {
        const [obData, frData, oiData] = await Promise.all([
          pq.getLatestOrderbook(sym).catch(() => null),
          pq.getLatestFunding(sym).catch(() => null),
          pq.getLatestOI(sym).catch(() => null)
        ]);
        if (obData) orderbook = obData;
        if (frData) {
          funding = { rate: frData.rate, nextFundingTime: frData.nextFundingTime };
          // Use parquet funding history (replaces rolling append)
          if (frData.history && frData.history.length > 1) {
            existing.history.funding = frData.history;
          }
        }
        if (oiData) {
          oi = { openInterest: oiData.openInterest, oiDeltaPct: oiData.oiDeltaPct };
          // Use parquet OI history
          if (oiData.history && oiData.history.length > 1) {
            existing.history.oi = oiData.history;
          }
        }
      }

      // Price history comes from refreshLivePricesFromParquet (already set)
      const hist = existing.history;

      const intel = {
        symbol: sym,
        price,
        change24h,
        orderbook: orderbook || null,
        oi:        oi        || null,
        funding:   funding   || null,
        history:   hist
      };
      intel.narrative = generateNarrative(sym, intel);
      database.marketIntel[sym] = intel;
    } catch (e) {
      console.error(`[Intel ${sym}]`, e.message);
    }
  }

  sendSSEToAll('intel', { marketIntel: database.marketIntel });
}

async function computePaperAccount() {
  const accRes = await pool.query('SELECT * FROM paper_account LIMIT 1');
  let account = accRes.rows[0];
  if (!account) {
    await pool.query('INSERT INTO paper_account (balance, equity, realized_pnl) VALUES (10000.00, 10000.00, 0)');
    account = { balance: 10000, equity: 10000, margin_used: 0, realized_pnl: 0, cumulative_funding_paid: 0, total_pnl: 0, total_trades: 0, win_rate: 0 };
  }

  // ── Closed trade stats ──
  const closedRes = await pool.query(`
    SELECT COUNT(*) as total,
           COUNT(*) FILTER (WHERE net_pnl_pct > 0) as wins,
           COALESCE(SUM(net_pnl_pct), 0) as total_pnl,
           COALESCE(SUM(net_pnl_usd), 0) as total_pnl_usd
    FROM paper_trades WHERE status = 'CLOSED'
  `);
  const stats     = closedRes.rows[0];
  const total     = parseInt(stats.total || 0);
  const wins      = parseInt(stats.wins || 0);
  const totalPnl  = parseFloat(stats.total_pnl || 0);
  const realisedUsd = parseFloat(stats.total_pnl_usd || 0);
  const winRate   = total > 0 ? (wins / total * 100) : 0;

  // ── Open trade margin ──
  // Use stored margin_required if available, else fall back to old calc
  const openRes = await pool.query(`
    SELECT COALESCE(SUM(
      COALESCE(margin_required, (COALESCE(entry_price_actual, entry_price_planned) * COALESCE(qty_contracts, 0)) / 4.0)
    ), 0) as total_margin
    FROM paper_trades WHERE status = 'OPEN'
  `);
  const marginHeld = parseFloat(openRes.rows[0]?.total_margin || 0);

  // ── Corrected Balance Model (per paper.md) ──
  // wallet_balance = seed + realized_pnl - open_entry_fees - cumulative_funding
  // available      = wallet_balance - margin_used
  // equity         = wallet_balance + floating_pnl  (computed in syncFromPostgres)
  const seed = 10000;
  const realizedPnl = realisedUsd; // always compute from closed trades, don't trust stale DB
  const cumulativeFunding = parseFloat(account.cumulative_funding_paid || 0);

  // Sum entry fees already paid for OPEN trades (realized costs not yet reflected in net_pnl)
  const openFeesRes = await pool.query(`
    SELECT COALESCE(SUM(entry_fee), 0)::float as total FROM paper_trades WHERE status = 'OPEN'
  `);
  const openEntryFees = parseFloat(openFeesRes.rows[0]?.total || 0);

  const walletBalance = seed + realizedPnl - openEntryFees - cumulativeFunding;
  const available = Math.max(0, walletBalance - marginHeld);

  await pool.query(`
    UPDATE paper_account
    SET balance = $1, equity = $2, margin_used = $3,
        total_pnl = $4, total_trades = $5, win_rate = $6,
        realized_pnl = $7, updated_at = NOW()
    WHERE id = $8
  `, [walletBalance, walletBalance, marginHeld, totalPnl, total, winRate, realizedPnl, account.id]);

  return {
    balance:        walletBalance,    // total cash (seed + realized - open_fees - funding)
    wallet_balance: walletBalance,
    available:      available,         // free cash = total cash - margin_used
    equity:         walletBalance,     // total cash; syncFromPostgres adds floating PnL for live equity
    margin_used:    marginHeld,
    total_pnl:      totalPnl,
    total_pnl_usd:  realisedUsd,
    total_trades:   total,
    win_rate:       winRate,
    realized_pnl:   realizedPnl,
    cumulative_funding_paid: cumulativeFunding
  };
}

// ── Main sync ────────────────────────────────────────────────────────────

async function syncFromPostgres() {
  try {
    // 0. AUTO-EXPIRE stale PENDING trades (>90 min old) — prevents gate deadlock
    const expireRes = await pool.query(`
      UPDATE paper_trades
      SET status = 'EXPIRED',
          exit_reason = 'PENDING timeout',
          exit_time_utc = NOW(),
          updated_at = NOW()
      WHERE status = 'PENDING'
        AND created_at < NOW() - INTERVAL '90 minutes'
      RETURNING id, symbol
    `);
    if (expireRes.rowCount > 0) {
      console.log(`[Sync] Auto-expired ${expireRes.rowCount} stale PENDING trade(s): ${expireRes.rows.map(r => r.symbol).join(', ')}`);
    }

    // 1. HIGH CONFIDENCE SIGNALS — primary tradeable feed
    // Include all recent signals and join with paper_trades to compute lifecycle state
    const hcsRes = await pool.query(`
      WITH best_trade AS (
        SELECT DISTINCT ON (signal_id)
          signal_id,
          status AS trade_status,
          exit_reason AS trade_exit_reason
        FROM paper_trades
        ORDER BY signal_id,
          CASE status
            WHEN 'CLOSED' THEN 1
            WHEN 'OPEN' THEN 2
            WHEN 'PENDING' THEN 3
            WHEN 'CANCELLED' THEN 4
            ELSE 5
          END
      )
      SELECT hcs.*,
        bt.trade_status,
        bt.trade_exit_reason
      FROM high_confidence_signals hcs
      LEFT JOIN best_trade bt ON bt.signal_id = hcs.id
      WHERE hcs.scan_time_utc > NOW() - INTERVAL '48 hours'
      ORDER BY hcs.scan_time_utc DESC
      LIMIT 50
    `);
    const mappedSignals = hcsRes.rows.map(mapHCSSignal);

    // 1b. AUTO-CREATE PENDING paper trades for high-confidence signals (confidence >= 55)
    // Quality gates: skip expired, skip stale (>4h), include full pre-trade estimates.
    const MIN_PENDING_CONFIDENCE = 55; // Only signals >= 55 create pending trades
    const nowTs = Date.now();
    for (const sig of hcsRes.rows) {
      const conf = parseInt(sig.confidence || 0);
      if (conf < MIN_PENDING_CONFIDENCE) continue;  // 50-54: visible but no pending

      // Gate 1: skip expired signals
      if (sig.is_expired) {
        await logSignalAudit(sig.id, sig.symbol, conf, 'GATE_1_EXPIRED', false, 'Signal already expired', 'SKIPPED');
        continue;
      }

      // Gate 2: skip stale signals (>45 min old) and mark them expired
      const scanTs = new Date(sig.scan_time_utc).getTime();
      if (nowTs - scanTs > 45 * 60 * 1000) {
        await pool.query(
          `UPDATE high_confidence_signals SET is_expired = TRUE, expiry_reason = 'Auto-expired: stale (>45min)' WHERE id = $1`,
          [sig.id]
        );
        await logSignalAudit(sig.id, sig.symbol, conf, 'GATE_2_STALE', false, 'Signal stale (>45min)', 'EXPIRED');
        continue;
      }

      // Gate 3: Only CONFIRMED signals get auto-pending (not OFF_SESSION / APPROACHING)
      // CONFIRMED means session + majority gates passed; these are the tradeable setups
      const wlRes = await pool.query(
        `SELECT signal_type FROM watchlist_current WHERE symbol = $1 ORDER BY scan_time_utc DESC LIMIT 1`,
        [sig.symbol]
      );
      const wlType = wlRes.rows[0]?.signal_type;
      if (wlType !== 'CONFIRMED') {
        await logSignalAudit(sig.id, sig.symbol, conf, 'GATE_3_NOT_CONFIRMED', false, `Watchlist type=${wlType}`, 'SKIPPED');
        continue;
      }

      // Gate 4: Max 5 fresh PENDING trades (only count created in last 30 min — stale PENDING doesn't block)
      const pendingCountRes = await pool.query(
        `SELECT COUNT(*)::int as n FROM paper_trades WHERE status = 'PENDING' AND created_at >= NOW() - INTERVAL '30 minutes'`
      );
      if (parseInt(pendingCountRes.rows[0]?.n || 0) >= 5) {
        await logSignalAudit(sig.id, sig.symbol, conf, 'GATE_4_MAX_PENDING', false, 'Max 5 fresh PENDING reached', 'SKIPPED');
        continue;
      }

      // Gate 5: Max 5 total OPEN trades (correlation guard)
      const openCountRes = await pool.query(`SELECT COUNT(*)::int as n FROM paper_trades WHERE status = 'OPEN'`);
      if (parseInt(openCountRes.rows[0]?.n || 0) >= 5) {
        await logSignalAudit(sig.id, sig.symbol, conf, 'GATE_5_MAX_OPEN', false, 'Max 5 OPEN reached', 'SKIPPED');
        continue;
      }

      let side = (sig.trade_decision || '').toUpperCase();
      if (side !== 'LONG' && side !== 'SHORT') {
        const dir = (sig.h4_direction || '').toUpperCase();
        side = dir === 'DOWN' ? 'SHORT' : 'LONG';
      }
      const price = parseFloat(sig.price_at_signal || 0);
      const sl = parseFloat(sig.sl_price || 0);
      const tp = parseFloat(sig.tp_price || 0);
      let qty = parseFloat(sig.qty_contracts || 0);
      const lev = parseFloat(sig.leverage || 4);
      let notional = price * qty;

      // v0.3.0 — Enforce min notional ($5) and qtyStep rounding
      if (notional < MIN_NOTIONAL) {
        console.log(`[Auto-Pending] Skipped ${sig.symbol}: notional $${notional.toFixed(2)} below min $${MIN_NOTIONAL}`);
        await logSignalAudit(sig.id, sig.symbol, conf, 'GATE_MIN_NOTIONAL', false, `Notional $${notional.toFixed(2)} < $${MIN_NOTIONAL}`, 'SKIPPED');
        continue;
      }
      const step = QTY_STEP[sig.symbol] || 0.001;
      qty = Math.floor(qty / step) * step;
      notional = price * qty;

      // v0.3.0 — Correct Bybit initial margin: (notional/lev) + fee_to_close
      const feeToClose = notional * TAKER_FEE;
      let marginReq = notional > 0 ? (notional / lev) + feeToClose : 0;

      // v0.3.0 — Calculate liquidation price and maintenance margin
      const mm = notional > 0 ? notional * MMR : 0;
      let liquidationPrice = 0;
      if (qty > 0 && marginReq > mm) {
        liquidationPrice = side === 'LONG'
          ? price - ((marginReq - mm) / qty)
          : price + ((marginReq - mm) / qty);
      }

      const funding = parseFloat(sig.funding_rate || 0);
      const oi = parseFloat(sig.oi_delta_pct || 0);

      // Gate 6: No duplicate for this signal
      // Skip if any non-CANCELLED trade exists, or if the signal was manually dismissed
      const existingTrades = await pool.query(
        "SELECT status FROM paper_trades WHERE signal_id = $1",
        [sig.id]
      );
      const hasNonCancelled = existingTrades.rows.some(r => r.status !== 'CANCELLED');
      if (hasNonCancelled) {
        await logSignalAudit(sig.id, sig.symbol, conf, 'GATE_6_DUPLICATE', false, 'Non-cancelled trade exists for signal', 'SKIPPED');
        continue;
      }
      const wasManuallyDismissed = sig.is_expired && sig.expiry_reason && sig.expiry_reason.includes('cancelled');
      if (wasManuallyDismissed) {
        await logSignalAudit(sig.id, sig.symbol, conf, 'GATE_6_DISMISSED', false, 'Signal manually dismissed', 'SKIPPED');
        continue;
      }

      // Gate 5b: Per-symbol margin cap (no symbol can exceed $1,667 allocated share)
      const PER_SYMBOL_ALLOCATION = 1667;
      const symMarginRes = await pool.query(
        `SELECT COALESCE(SUM(margin_required), 0)::float as total FROM paper_trades WHERE symbol = $1 AND status = 'OPEN'`,
        [sig.symbol]
      );
      const symMargin = parseFloat(symMarginRes.rows[0]?.total || 0);
      if (symMargin + marginReq > PER_SYMBOL_ALLOCATION) {
        console.log(`[Auto-Pending] Skipped ${sig.symbol}: per-symbol margin cap $${PER_SYMBOL_ALLOCATION} exceeded (current $${symMargin.toFixed(2)} + new $${marginReq.toFixed(2)})`);
        await logSignalAudit(sig.id, sig.symbol, conf, 'GATE_MARGIN_CAP', false, `Per-symbol margin cap $${PER_SYMBOL_ALLOCATION} exceeded`, 'SKIPPED');
        continue;
      }

      // ── Resize qty to fit per-symbol allocation cap ($1,667) ──
      const remainingCap = PER_SYMBOL_ALLOCATION - symMargin;
      if (marginReq > remainingCap && price > 0 && lev > 0) {
        qty = (remainingCap * lev) / price;
        notional = price * qty;
        marginReq = notional / lev;
        console.log(`[Resize] ${sig.symbol} resized qty=${qty.toFixed(4)} to fit $${PER_SYMBOL_ALLOCATION} cap (remaining $${remainingCap.toFixed(2)})`);
      }

      // Pre-trade cost estimates (mirror Python pipeline)
      let entryFee = notional * TAKER_FEE;
      let exitFee  = notional * TAKER_FEE;
      let feesTotal = entryFee + exitFee;
      let fundingEst = notional * Math.abs(funding);
      let slippageUsd = notional * 0.001; // 0.10% conservative
      let totalCostPct = notional > 0 ? ((feesTotal + fundingEst + slippageUsd) / notional) * 100 : 0;

      // V8: Always create PENDING trades. Pipeline orchestrator _reprice_pending_signals
      // handles promotion to OPEN through the full gate chain (CorrelationGuard,
      // TradeCountGuard, DirectionalGuard, RiskBudgetLedger). Backend no longer auto-opens.
      const tradeStatus = 'PENDING';
      await logSignalAudit(sig.id, sig.symbol, conf, 'GATE_AUTO_OPEN', false, `conf=${conf} — PENDING for pipeline reprice`, 'PENDING');

      const atrAtEntry = parseFloat(sig.atr_15m || sig.atr || 0);
      const slOriginal = sl;

      // Apply per-symbol slippage to entry_price_actual (market order simulation)
      const slipPct = (SLIPPAGE_BPS[sig.symbol] || 5.0) / 10000.0;
      const entryActual = side === 'LONG' ? price * (1 + slipPct) : price * (1 - slipPct);
      const slippageUsdActual = Math.abs(entryActual - price) * qty;

      const tradeRes = await pool.query(`
        INSERT INTO paper_trades (
          signal_id, symbol, signal_bar_utc, scan_time_utc, side,
          entry_price_planned, entry_price_actual, sl_price, tp_price,
          qty_contracts, leveraged_notional, margin_required, leverage, confidence,
          funding_rate_at_entry, oi_delta_at_entry,
          entry_fee, exit_fee, fees_total, funding_events, funding_total,
          slippage_pct, slippage_usd, status,
          liquidation_price, maintenance_margin, last_funding_settlement_utc,
          notes,
          sl_original, atr_at_entry,
          entry_session, entry_strategy, entry_rsi, entry_adx, calibrated_prob
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,$27,$28,$29,$30,$31,$32,$33,$34,$35)
        RETURNING id
      `, [sig.id, sig.symbol, sig.signal_bar_utc, sig.scan_time_utc, side,
          price, entryActual, sl, tp,
          qty, notional * lev, marginReq, lev, conf,
          funding, oi,
          entryFee, exitFee, feesTotal, 1, fundingEst,
          (SLIPPAGE_BPS[sig.symbol] || 5.0) / 100.0, slippageUsdActual, tradeStatus,
          liquidationPrice > 0 ? liquidationPrice : null,
          mm > 0 ? mm : null,
          null,
          `Pre-trade cost estimate: ${totalCostPct.toFixed(3)}% of notional. Fees=${feesTotal.toFixed(2)} | Funding(est)=${fundingEst.toFixed(2)} | Slippage(est)=${slippageUsd.toFixed(2)}`,
          slOriginal, atrAtEntry > 0 ? atrAtEntry : null,
          sig.session || null, sig.strategy || null,
          sig.rsi_15m ? parseFloat(sig.rsi_15m) : null,
          sig.adx_4h ? parseFloat(sig.adx_4h) : null,
          sig.calibrated_confidence ? parseFloat(sig.calibrated_confidence) : null]);

      {
        const pendingTradeId = tradeRes.rows[0].id;
        await logTradeEvent(pendingTradeId, sig.symbol, 'CREATED', price, null, null, 0, 0, 0, { side, leverage: lev, qty, status: 'PENDING', reason: 'Pipeline reprice will promote' });
        console.log(`[Auto-Pending] Created pending trade for ${sig.symbol} (confidence=${conf}, side=${side})`);
      }
    }

    // 2. WATCHLIST — market monitoring (APPROACHING / OFF_SESSION)
    // Deduplicate by symbol, keeping only the most recent scan per symbol
    const wlRes = await pool.query(`
      SELECT DISTINCT ON (symbol) *
      FROM watchlist_current
      ORDER BY symbol, scan_time_utc DESC
      LIMIT 20
    `);
    const mappedWatchlist = wlRes.rows.map(mapWatchlistItem);

    // 3. POWER TRADES — manually promoted elite setups
    const ptRes = await pool.query(`
      SELECT * FROM power_trades
      ORDER BY promoted_at DESC LIMIT 20
    `).catch(() => ({ rows: [] }));
    const mappedPowerTrades = ptRes.rows.map(mapPowerTrade);

    // Mark HCS signals that are also power trades
    const powerSignalIds = new Set(ptRes.rows.map(r => r.signal_id));
    mappedSignals.forEach(s => { s.isPowerTrade = powerSignalIds.has(s.dbId); });

    // 4. WINNERS
    const winRes = await pool.query(`
      SELECT * FROM winners ORDER BY recorded_at DESC LIMIT 20
    `).catch(() => ({ rows: [] }));
    const mappedWinners = winRes.rows.map(mapWinner);

    // 5. PAPER ACCOUNT
    const paperAccount = await computePaperAccount();

    // 6. PENDING paper trades (awaiting activation via PAPER button click)
    const pendingRes = await pool.query(`
      SELECT pt.*, hcs.signal_strength FROM paper_trades pt
      LEFT JOIN high_confidence_signals hcs ON pt.signal_id = hcs.id
      WHERE pt.status = 'PENDING' ORDER BY pt.created_at DESC
    `);
    const pendingTrades = pendingRes.rows.map(mapPaperTrade);

    // 7. OPEN paper trades — inject live floating PnL from parquet prices
    const activeRes = await pool.query(`
      SELECT * FROM paper_trades WHERE status = 'OPEN' ORDER BY created_at DESC
    `);
    const activeTrades = activeRes.rows.map(mapPaperTrade);

    // ── 8-Hour Funding Simulation (v0.3.0) ──
    // Bybit funding intervals: 00:00, 08:00, 16:00 UTC
    // Per-trade settlement: only charge if trade was open at window start
    // and hasn't been settled for this window yet.
    function getFundingWindowStart(date) {
      const d = new Date(date);
      const h = d.getUTCHours();
      const windowHour = h < 8 ? 0 : h < 16 ? 8 : 16;
      return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), windowHour, 0, 0));
    }
    const nowUtc = new Date();
    const utcHour = nowUtc.getUTCHours();
    const currentInterval = [0, 8, 16].find(h => utcHour >= h && utcHour < h + 8);
    const windowStart = getFundingWindowStart(nowUtc);

    if (currentInterval !== undefined && currentInterval !== lastFundingInterval) {
      let totalFundingDelta = 0;
      for (const t of activeTrades) {
        const livePrice = database.livePrices[t.symbol]?.price || t.entry_price || 0;
        if (livePrice <= 0 || t.qty <= 0) continue;

        // Per-trade safeguard: skip if already settled for this window
        const lastSettled = t.last_funding_settlement_utc ? new Date(t.last_funding_settlement_utc) : null;
        if (lastSettled && lastSettled.getTime() >= windowStart.getTime()) continue;

        // Skip if trade was opened after this window started
        const entryTime = new Date(t.entry_time || t.created_at);
        if (entryTime > windowStart) continue;

        const positionValue = livePrice * t.qty;
        const rate = parseFloat(t.funding_rate || 0.0001);
        const fee = positionValue * Math.abs(rate);
        const isLong = t.side === 'LONG';
        const sign = rate > 0 ? (isLong ? -1 : 1) : (isLong ? 1 : -1);
        const signedFee = fee * sign;
        totalFundingDelta += signedFee;

        // Update trade cumulative funding + settlement timestamp
        await pool.query(`
          UPDATE paper_trades
          SET funding_total = COALESCE(funding_total, 0) + $1,
              funding_events = COALESCE(funding_events, 0) + 1,
              last_funding_settlement_utc = $2,
              updated_at = NOW()
          WHERE id = $3
        `, [signedFee, windowStart, t.id]);

        // Log per-trade funding settlement
        await logTradeEvent(t.id, t.symbol, 'FUNDING_SETTLEMENT', livePrice, null, null, 0, Math.abs(signedFee), signedFee, { rate, sign, windowStart });
      }
      // Update account cumulative funding paid (deduct from available)
      if (totalFundingDelta !== 0) {
        await pool.query(`
          UPDATE paper_account
          SET cumulative_funding_paid = COALESCE(cumulative_funding_paid, 0) + $1,
              balance = balance + $1,
              updated_at = NOW()
        `, [totalFundingDelta]);
        console.log(`[Funding] Window ${windowStart.toISOString()} — net delta=${totalFundingDelta.toFixed(4)} USD`);
      }
      lastFundingInterval = currentInterval;
    }

    const tradesToClose = [];

    // Immutable enrichment: create new objects instead of mutating in-place
    const enrichedActiveTrades = activeTrades.map(t => {
      const livePrice = database.livePrices[t.symbol]?.price || 0;
      if (livePrice <= 0 || t.entry_price <= 0 || t.qty <= 0) {
        return { ...t, live_pnl: 0, live_pnl_pct: 0, live_price: 0, pct_to_tp: null, pct_to_sl: null };
      }
      // Flash-wick guard: if price moved >10% in one 1m bar (vs previous price), skip close this cycle
      // (likely anomalous parquet bar or data glitch, not a real market move)
      const prevPrice = database.livePrices[t.symbol]?.prevPrice || 0;
      if (prevPrice > 0 && Math.abs(livePrice - prevPrice) / prevPrice > 0.10) {
        console.warn(`[FlashWick] ${t.symbol} price ${livePrice} vs prev ${prevPrice} (${((Math.abs(livePrice - prevPrice) / prevPrice) * 100).toFixed(1)}%) — skipping close check`);
        return { ...t, live_pnl: 0, live_pnl_pct: 0, live_price: livePrice, pct_to_tp: null, pct_to_sl: null };
      }

      // PnL: LONG = (current - entry), SHORT = (entry - current)
      const rawPnl = t.side === 'SHORT'
        ? (t.entry_price - livePrice) * t.qty
        : (livePrice - t.entry_price) * t.qty;
      const pnlAfterFees = rawPnl - (t.fees_total || 0);
      const notional = t.entry_price * t.qty;
      const pnlPct = notional > 0 ? (rawPnl / notional) * 100 * t.leverage : 0;

      const displayTp = t.tp_price;
      const displaySl = t.sl_price;
      const pctToTp = displayTp > 0 ? parseFloat(((displayTp - livePrice) / livePrice * 100).toFixed(4)) : null;
      const pctToSl = displaySl > 0 ? parseFloat(((displaySl - livePrice) / livePrice * 100).toFixed(4)) : null;

      // v0.3.5 — Dynamic SL adjustment before auto-close checks
      const entryTs = t.entry_time ? new Date(t.entry_time) : new Date(t.created_at || Date.now());
      const hoursOpen = (Date.now() - entryTs) / 3600000;
      const dyn = computeDynamicSL(t, livePrice, hoursOpen);
      if (dyn && Math.abs(dyn.newSl - parseFloat(t.sl_price || 0)) > 0.0001) {
        const oldSl = parseFloat(t.sl_price || 0);
        const newDollarRisk = Math.abs(t.entry_price - dyn.newSl) * t.qty;
        pool.query(
          'UPDATE paper_trades SET sl_price = $1, updated_at = NOW() WHERE id = $2',
          [dyn.newSl, t.id]
        ).then(() =>
          pool.query(
            'UPDATE risk_ledger SET sl_price = $1, dollar_risk = $2 WHERE trade_id = $3 AND is_open = TRUE',
            [dyn.newSl, newDollarRisk, t.id]
          )
        ).then(() => console.log(`[DynamicSL] Trade ${t.id} ${t.symbol}: SL ${oldSl.toFixed(4)} -> ${dyn.newSl.toFixed(4)} (${dyn.reasons.join(',')}) | risk_ledger synced, dollar_risk=$${newDollarRisk.toFixed(2)}`))
          .catch(e => console.error(`[DynamicSL] DB update failed for trade ${t.id}:`, e.message));
        t.sl_price = dyn.newSl; // update in-memory for this sync's close checks
      }

      // Auto-close logic
      // V10: TIME_EXIT — close if open trade age exceeds max hold limit.
      // Exception: if the trade is currently profitable, allow up to 2 extra hours
      // for the trade to reach TP rather than cutting winners short.
      const symbolMaxHours = PER_SYMBOL_MAX_HOURS[t.symbol] || 12;
      const isWinning = rawPnl > 0;
      const effectiveMaxHours = isWinning ? symbolMaxHours + 2 : symbolMaxHours;
      if (hoursOpen >= effectiveMaxHours) {
        tradesToClose.push({ id: t.id, exitPrice: livePrice, reason: 'TIME_EXIT', side: t.side, tp_price: t.tp_price, sl_price: t.sl_price, entry_price: t.entry_price, qty: t.qty, signal_id: t.signalId });
      } else if (t.side === 'LONG') {
        if (t.tp_price > 0 && livePrice >= t.tp_price) {
          tradesToClose.push({ id: t.id, exitPrice: livePrice, reason: 'TP_HIT', side: t.side, tp_price: t.tp_price, sl_price: t.sl_price, entry_price: t.entry_price, qty: t.qty, signal_id: t.signalId });
        } else if (t.sl_price > 0 && livePrice <= t.sl_price) {
          const slExit = parseFloat(t.sl_price);
          const slippage = slExit * 0.001; // 0.1% slippage on SL fill
          tradesToClose.push({ id: t.id, exitPrice: slExit - slippage, reason: 'SL_HIT', side: t.side, tp_price: t.tp_price, sl_price: t.sl_price, entry_price: t.entry_price, qty: t.qty, signal_id: t.signalId });
        } else if (t.liquidation_price > 0 && livePrice <= t.liquidation_price) {
          tradesToClose.push({ id: t.id, exitPrice: t.liquidation_price, reason: 'LIQUIDATED', side: t.side, tp_price: t.tp_price, sl_price: t.sl_price, entry_price: t.entry_price, qty: t.qty, signal_id: t.signalId });
        }
      } else if (t.side === 'SHORT') {
        // SHORT: TP is below entry, SL is above entry (correctly stored by get_sl_tp/get_sl_tp_pct)
        if (t.tp_price > 0 && livePrice <= t.tp_price) {
          tradesToClose.push({ id: t.id, exitPrice: livePrice, reason: 'TP_HIT', side: t.side, tp_price: t.tp_price, sl_price: t.sl_price, entry_price: t.entry_price, qty: t.qty, signal_id: t.signalId });
        } else if (t.sl_price > 0 && livePrice >= t.sl_price) {
          const slExit = parseFloat(t.sl_price);
          const slippage = slExit * 0.001; // 0.1% slippage on SL fill
          tradesToClose.push({ id: t.id, exitPrice: slExit + slippage, reason: 'SL_HIT', side: t.side, tp_price: t.tp_price, sl_price: t.sl_price, entry_price: t.entry_price, qty: t.qty, signal_id: t.signalId });
        } else if (t.liquidation_price > 0 && livePrice >= t.liquidation_price) {
          tradesToClose.push({ id: t.id, exitPrice: t.liquidation_price, reason: 'LIQUIDATED', side: t.side, tp_price: t.tp_price, sl_price: t.sl_price, entry_price: t.entry_price, qty: t.qty, signal_id: t.signalId });
        }
      }

      return {
        ...t,
        live_pnl: parseFloat(pnlAfterFees.toFixed(2)),
        live_pnl_pct: parseFloat(pnlPct.toFixed(4)),
        live_price: livePrice,
        pct_to_tp: pctToTp,
        pct_to_sl: pctToSl,
      };
    });

    // Process auto-closes
    for (const tc of tradesToClose) {
      try {
        const tradeRes = await pool.query('SELECT * FROM paper_trades WHERE id = $1', [tc.id]);
        const trade = tradeRes.rows[0];
        if (!trade || trade.status !== 'OPEN') continue;

        const entry = parseFloat(trade.entry_price_actual || trade.entry_price_planned);
        const qty = parseFloat(trade.qty_contracts || 0);
        const notional = entry * qty;
        const exitNotional = tc.exitPrice * qty;
        const lev = parseInt(trade.leverage || 4);
        const isLong = trade.side === 'LONG';
        const grossPct = isLong
          ? ((tc.exitPrice - entry) / entry) * 100
          : ((entry - tc.exitPrice) / entry) * 100;
        const grossUsd = isLong
          ? (tc.exitPrice - entry) * qty
          : (entry - tc.exitPrice) * qty;
        const openFee = parseFloat(trade.entry_fee || notional * 0.00055);
        const closeFee = exitNotional * 0.00055;
        const cumulativeFunding = parseFloat(trade.funding_total || trade.cumulative_funding || trade.funding_cost || 0);
        const totalFees = openFee + closeFee + cumulativeFunding;
        const netUsd = grossUsd - totalFees;
        const marginReleased = parseFloat(trade.margin_required) || (notional / lev);
        const netPct = (netUsd / marginReleased) * 100;

        const entryTs = new Date(trade.created_at);
        const now = new Date();
        const hoursHeld = (now - entryTs) / 3600000;

        await pool.query(`
          UPDATE paper_trades
          SET exit_price=$1, exit_reason=$2, exit_time_utc=NOW(),
              gross_pnl_pct=$3, gross_pnl_usd=$4, exit_fee=$5, fees_total=$6,
              net_pnl_pct=$7, net_pnl_usd=$8, hours_held=$9,
              status='CLOSED', updated_at=NOW()
          WHERE id=$10
        `, [tc.exitPrice, tc.reason, grossPct, grossUsd, closeFee, totalFees, netPct, netUsd, hoursHeld.toFixed(2), tc.id]);

        const accRes = await pool.query('SELECT * FROM paper_account LIMIT 1');
        const account = accRes.rows[0];
        // Return margin + net PnL to available; margin is unlocked
        await pool.query(`
          UPDATE paper_account SET balance = balance + $1, margin_used = GREATEST(0, margin_used - $2), realized_pnl = realized_pnl + $3 WHERE id = $4
        `, [marginReleased + netUsd, marginReleased, netUsd, account.id]);

        // Log trade CLOSE event
        const balBeforeClose = parseFloat(account.balance);
        const balAfterClose = balBeforeClose + marginReleased + netUsd;
        await logTradeEvent(tc.id, trade.symbol, 'CLOSE', tc.exitPrice, balBeforeClose, balAfterClose, -marginReleased, closeFee, netUsd, { reason: tc.reason, grossUsd, netPct, hoursHeld: hoursHeld.toFixed(2) });

        // v0.3.3 — EXIT debug snapshot + failure classification
        const sigRes = await pool.query(`SELECT * FROM high_confidence_signals WHERE id = $1`, [trade.signal_id]);
        const sig = sigRes.rows[0];
        const entryCtx = sig ? {
          btc_24h_ret: (database.marketIntel['BTCUSDT']?.change24h || 0) / 100,
          atr_15m: sig.atr_15m,
          ob_imbalance: sig.ob_imbalance_pct,
          funding_rate: sig.funding_rate,
          oi_delta_pct: sig.oi_delta_pct,
          rsi_15m: sig.rsi_15m,
          adx_4h: sig.adx_4h,
        } : {};
        const exitCtx = {
          price: tc.exitPrice,
          atr_15m: null, // not available at close without enriched data
        };
        const tradeForClassify = {
          side: trade.side,
          exit_reason: tc.reason,
          hours_held: hoursHeld.toFixed(2),
          net_pnl_usd: netUsd,
        };
        const category = classifyFailure(entryCtx, exitCtx, tradeForClassify);
        const fix = suggestedFix(category);

        await logTradeDebug(tc.id, trade.symbol, 'EXIT', {
          price: tc.exitPrice,
          rsi_15m: sig ? sig.rsi_15m : null,
          adx_4h: sig ? sig.adx_4h : null,
          atr_15m: sig ? sig.atr_15m : null,
          funding_rate: sig ? sig.funding_rate : null,
          oi_delta_pct: sig ? sig.oi_delta_pct : null,
          ob_spread_pct: sig ? sig.ob_spread_pct : null,
          ob_imbalance: sig ? sig.ob_imbalance_pct : null,
          session: sig ? sig.session : null,
          dqs_score: sig ? sig.confidence : null,
          calibrated_prob: sig ? sig.calibrated_confidence : null,
          brain_prob: sig ? sig.brain_confidence_fused : null,
        });
        // Backfill failure_category + suggested_fix into the EXIT debug row
        try {
          await pool.query(`
            UPDATE trade_debug_log
            SET failure_category=$1, suggested_fix=$2
            WHERE trade_id=$3 AND log_type='EXIT'
          `, [category, fix, tc.id]);
        } catch (e) {
          console.error(`[Auto-Close] Failed to backfill debug classification for trade ${tc.id}:`, e.message);
        }
        console.log(`[Auto-Close] Trade ${tc.id} (${trade.symbol}) classified: ${category} | ${fix}`);

        // Auto-record to winners table if TP hit (skip LIQUIDATED)
        if (tc.reason === 'TP_HIT') {
          if (sig) {
            await pool.query(`
              INSERT INTO winners (trade_id, signal_id, symbol, confidence, signal_strength,
                entry_price, exit_price, tp_price, sl_price,
                gross_pnl_pct, net_pnl_pct, net_pnl_usd, hours_held, exit_reason, session, h4_direction)
              VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
              ON CONFLICT (trade_id) DO NOTHING
            `, [
              tc.id, trade.signal_id, trade.symbol, sig.confidence, sig.signal_strength,
              entry, tc.exitPrice, tc.tp_price, tc.sl_price,
              grossPct, netPct, netUsd, hoursHeld.toFixed(2), tc.reason, sig.session, sig.h4_direction
            ]);
          }
        }

        console.log(`[Auto-Close] Trade ${tc.id} (${trade.symbol} ${tc.side}) closed at ${tc.exitPrice} - ${tc.reason}`);

        // Telegram alert for trade close
        const leverage = parseInt(trade.leverage || 4);
        const emoji = tc.reason === 'TP_HIT' ? '🎯' : tc.reason === 'LIQUIDATED' ? '💥' : '🛑';
        const pnlEmoji = netUsd >= 0 ? '🟢' : '🔴';
        sendTelegramAlert(
          `${emoji} <b>VLTHR Trade Closed — ${tc.reason}</b><br>` +
          `<pre>Symbol:  ${trade.symbol}\n` +
          `Side:    ${tc.side}\n` +
          `Entry:   ${entry.toFixed(2)}\n` +
          `Exit:    ${tc.exitPrice.toFixed(2)}\n` +
          `Qty:     ${tc.qty.toFixed(4)}\n` +
          `Leverage: ${leverage}x\n` +
          `Hours:   ${hoursHeld.toFixed(1)}\n\n` +
          `P&L %:   ${netPct.toFixed(2)}%\n` +
          `P&L $:   ${pnlEmoji} ${netUsd.toFixed(2)} USD</pre>`
        );
      } catch (err) {
        console.error(`[Auto-Close Error] Trade ${tc.id}:`, err);
      }
    }
    // Aggregate live floating PnL across all open trades
    const liveTotalFloating = enrichedActiveTrades.reduce((sum, t) => sum + (t.live_pnl || 0), 0);

    // 8. CLOSED paper trades
    const closedRes = await pool.query(`
      SELECT * FROM paper_trades WHERE status = 'CLOSED' ORDER BY exit_time_utc DESC LIMIT 20
    `);
    const closedTrades = closedRes.rows.map(mapPaperTrade);

    // 9. Historical signal log
    const histRes = await pool.query(`
      SELECT * FROM signals_historical ORDER BY created_at DESC LIMIT 30
    `);
    const mappedHistory = histRes.rows.map(mapHistory);

    // Enrich paper account with live floating data from open positions
    paperAccount.live_floating_pnl   = parseFloat(liveTotalFloating.toFixed(2));
    // True equity = wallet_balance + floating_pnl (per paper.md)
    paperAccount.equity = parseFloat((paperAccount.wallet_balance + liveTotalFloating).toFixed(2));
    paperAccount.live_total_pnl_usd  = parseFloat((paperAccount.total_pnl_usd + liveTotalFloating).toFixed(2));
    // Live win rate: count open trades in profit as if they closed now
    const openWins   = enrichedActiveTrades.filter(t => (t.live_pnl || 0) > 0).length;
    const totalWithOpen = paperAccount.total_trades + enrichedActiveTrades.length;
    paperAccount.live_win_rate = totalWithOpen > 0
      ? parseFloat(((( paperAccount.total_trades * paperAccount.win_rate / 100) + openWins) / totalWithOpen * 100).toFixed(1))
      : paperAccount.win_rate;

    database.signals = mappedSignals;
    database.watchlist = mappedWatchlist;
    database.powerTrades = mappedPowerTrades;
    database.winners = mappedWinners;
    database.history = mappedHistory;
    database.paper = { account: paperAccount, pendingTrades, activeTrades: enrichedActiveTrades, closedTrades };

    // 10. VTC — compute performance vector, score, price from paper trading data
    // Only insert a new vector when a new trade has closed since last sync.
    // When no new trades, load existing data (score/price stay flat — correct
    // behavior for a performance-based token with no new performance).
    try {
      const closedCountRes = await pool.query(`SELECT COUNT(*)::int as n FROM paper_trades WHERE status = 'CLOSED'`);
      const currentClosedCount = closedCountRes.rows[0]?.n || 0;

      const treasuryRes = await pool.query(`SELECT * FROM vtc_treasury ORDER BY timestamp DESC LIMIT 1`).catch(() => ({ rows: [{ total_supply: 1000000, circulating_supply: 1000000, minted: 0, burned: 0 }] }));
      const treasury = treasuryRes.rows[0] || { total_supply: 1000000, circulating_supply: 1000000, minted: 0, burned: 0 };

      let latestPrice = 1.0;
      let latestScore = 50.0;
      let latestConfidence = 0.5;
      let latestVector = null;
      let latestLayers = { performance: 0, risk: 0, growth: 0 };

      if (currentClosedCount > lastVTCClosedCount) {
        // New trade(s) closed — compute fresh vector, score, price
        lastVTCClosedCount = currentClosedCount;
        const vector = await vtc.derivePerformanceVector(pool);
        const scoreResult = vtc.calculateScore(vector);
        const prevPriceRow = await pool.query(`SELECT price FROM vtc_prices ORDER BY timestamp DESC LIMIT 1`).catch(() => ({ rows: [{ price: 1.0 }] }));
        const prevPrice = parseFloat(prevPriceRow.rows[0]?.price || 1.0);
        const newPrice = vtc.calculatePrice(prevPrice, scoreResult.score);

        // Store vector
        const vecRes = await pool.query(`
          INSERT INTO vtc_vectors (timestamp, sharpe, sortino, drawdown, expectancy, win_rate, capital_growth, stability, risk_score)
          VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id
        `, [vector.timestamp, vector.sharpe, vector.sortino, vector.drawdown, vector.expectancy, vector.win_rate, vector.capital_growth, vector.stability, vector.risk_score]);
        const vectorId = vecRes.rows[0].id;

        // Store score
        await pool.query(`
          INSERT INTO vtc_scores (score, confidence, performance_layer, risk_layer, growth_layer)
          VALUES ($1,$2,$3,$4,$5)
        `, [scoreResult.score, scoreResult.confidence, scoreResult.layers.performance, scoreResult.layers.risk, scoreResult.layers.growth]);

        // Store price
        await pool.query(`INSERT INTO vtc_prices (price, score) VALUES ($1,$2)`, [newPrice, scoreResult.score]);

        // Audit log
        await pool.query(`
          INSERT INTO vtc_audit_log (vector_id, score, old_price, new_price, reason, vector_snapshot)
          VALUES ($1,$2,$3,$4,$5,$6)
        `, [vectorId, scoreResult.score, prevPrice, newPrice,
            `Trade closed. New score: ${scoreResult.score.toFixed(2)}, Confidence: ${scoreResult.confidence.toFixed(2)}`,
            JSON.stringify(vector)]);

        latestPrice = newPrice;
        latestScore = scoreResult.score;
        latestConfidence = scoreResult.confidence;
        latestVector = vector;
        latestLayers = scoreResult.layers;
      } else {
        // No new trades — load the latest existing data
        const latestPriceRow = await pool.query(`SELECT price, score FROM vtc_prices ORDER BY timestamp DESC LIMIT 1`).catch(() => ({ rows: [{ price: 1.0, score: 50 }] }));
        const latestScoreRow = await pool.query(`SELECT score, confidence, performance_layer, risk_layer, growth_layer FROM vtc_scores ORDER BY timestamp DESC LIMIT 1`).catch(() => ({ rows: [{ score: 50, confidence: 0.5, performance_layer: 0, risk_layer: 0, growth_layer: 0 }] }));
        const latestVectorRow = await pool.query(`SELECT * FROM vtc_vectors ORDER BY timestamp DESC LIMIT 1`).catch(() => ({ rows: [null] }));
        latestPrice = parseFloat(latestPriceRow.rows[0]?.price || 1.0);
        latestScore = parseFloat(latestPriceRow.rows[0]?.score || 50);
        latestConfidence = parseFloat(latestScoreRow.rows[0]?.confidence || 0.5);
        latestLayers = {
          performance: parseFloat(latestScoreRow.rows[0]?.performance_layer || 0),
          risk: parseFloat(latestScoreRow.rows[0]?.risk_layer || 0),
          growth: parseFloat(latestScoreRow.rows[0]?.growth_layer || 0),
        };
        if (latestVectorRow.rows[0]) {
          latestVector = {
            timestamp: latestVectorRow.rows[0].timestamp?.toISOString?.() || latestVectorRow.rows[0].timestamp,
            sharpe: parseFloat(latestVectorRow.rows[0].sharpe),
            sortino: parseFloat(latestVectorRow.rows[0].sortino),
            drawdown: parseFloat(latestVectorRow.rows[0].drawdown),
            expectancy: parseFloat(latestVectorRow.rows[0].expectancy),
            win_rate: parseFloat(latestVectorRow.rows[0].win_rate),
            capital_growth: parseFloat(latestVectorRow.rows[0].capital_growth),
            stability: parseFloat(latestVectorRow.rows[0].stability),
            risk_score: parseFloat(latestVectorRow.rows[0].risk_score),
          };
        }
      }

      // Load recent history for SSE (always refresh from DB)
      const priceHist = await pool.query(`
        SELECT timestamp, price FROM vtc_prices ORDER BY timestamp DESC LIMIT 100
      `).catch(() => ({ rows: [] }));
      const scoreHist = await pool.query(`
        SELECT timestamp, score FROM vtc_scores ORDER BY timestamp DESC LIMIT 100
      `).catch(() => ({ rows: [] }));
      const auditHist = await pool.query(`
        SELECT * FROM vtc_audit_log ORDER BY timestamp DESC LIMIT 20
      `).catch(() => ({ rows: [] }));

      database.vtc = {
        price: latestPrice,
        score: latestScore,
        confidence: latestConfidence,
        marketCap: latestPrice * parseFloat(treasury.total_supply),
        nav: latestPrice * parseFloat(treasury.circulating_supply),
        supply: parseFloat(treasury.total_supply),
        timestamp: latestVector?.timestamp || new Date().toISOString(),
        history: priceHist.rows.map(r => ({ timestamp: r.timestamp, price: parseFloat(r.price) })).reverse(),
        scores: scoreHist.rows.map(r => ({ timestamp: r.timestamp, score: parseFloat(r.score) })).reverse(),
        treasury: {
          total_supply: parseFloat(treasury.total_supply),
          circulating_supply: parseFloat(treasury.circulating_supply),
          minted: parseFloat(treasury.minted),
          burned: parseFloat(treasury.burned),
        },
        audit: auditHist.rows.map(r => ({
          id: r.id,
          timestamp: r.timestamp,
          score: parseFloat(r.score),
          old_price: parseFloat(r.old_price),
          new_price: parseFloat(r.new_price),
          reason: r.reason,
        })),
        vector: latestVector,
        layers: latestLayers,
      };
    } catch (vtcErr) {
      console.error('[VTC Sync Error]', vtcErr.message);
    }

    sendSSEToAll('update', {
      signals: database.signals,
      watchlist: database.watchlist,
      powerTrades: database.powerTrades,
      winners: database.winners,
      history: database.history,
      paper: database.paper,
      vtc: database.vtc
    });
    console.log(`[Sync] hcs:${mappedSignals.length} wl:${mappedWatchlist.length} pt:${mappedPowerTrades.length} paper_pending:${pendingTrades.length} paper_open:${enrichedActiveTrades.length}`);
  } catch (err) {
    console.error('[Sync Error]', err.message);
  }
}

// ── SSE ──────────────────────────────────────────────────────────────────

let clients = [];

function sendSSEToAll(type, data) {
  const msg = `event: ${type}\ndata: ${JSON.stringify(data)}\n\n`;
  for (let i = clients.length - 1; i >= 0; i--) {
    try { clients[i].res.write(msg); }
    catch { clients.splice(i, 1); }
  }
}

app.use(require('./routes/signals.cjs')(database, clients, sendSSEToAll));

// ── Seed history from DB on startup ──────────────────────────────────────
async function seedMarketIntelHistory() {
  try {
    for (const sym of MARKET_INTEL_SYMBOLS) {
      if (sym === 'XAUUSD') continue;
      const res = await pool.query(`
        SELECT price_at_signal, funding_rate, oi_delta_pct, ob_imbalance_pct, scan_time_utc
        FROM high_confidence_signals
        WHERE symbol = $1
        ORDER BY scan_time_utc ASC
        LIMIT 80
      `, [sym]).catch(() => ({ rows: [] }));
      if (res.rows.length === 0) continue;
      const hist = { prices: [], funding: [], oi: [] };
      res.rows.forEach(row => {
        const t = new Date(row.scan_time_utc).getTime();
        const price = parseFloat(row.price_at_signal || 0);
        const fr = parseFloat(row.funding_rate || 0);
        const oiDelta = parseFloat(row.oi_delta_pct || 0);
        if (price > 0) hist.prices.push({ time: t, price });
        if (fr !== 0)  hist.funding.push({ time: t, rate: fr });
        hist.oi.push({ time: t, oi: oiDelta });
      });
      const existing = database.marketIntel[sym];
      if (existing) { existing.history = hist; }
      else {
        database.marketIntel[sym] = { symbol: sym, price: 0, change24h: 0, orderbook: null, oi: null, funding: null, narrative: '', history: hist };
      }
    }
    console.log('[Intel] History seeded from DB for', MARKET_INTEL_SYMBOLS.filter(s => s !== 'XAUUSD').length, 'symbols');
  } catch (e) {
    console.error('[Intel Seed Error]', e.message);
  }
}

function isInTradingSession() {
  const utcHour = new Date().getUTCHours();
  // London: 07:00-12:00 UTC | NY Late: 20:00-00:00 UTC
  return (utcHour >= 7 && utcHour < 12) || (utcHour >= 20);
}

// ── Start ─────────────────────────────────────────────────────────────────

(async () => {
  // ── Ensure all auxiliary tables exist ──────────────────────────────────
  try {
    await pool.query(`
      CREATE TABLE IF NOT EXISTS risk_ledger (
        id SERIAL PRIMARY KEY,
        symbol VARCHAR(16) NOT NULL,
        trade_id INT NOT NULL,
        entry_price NUMERIC(18,8),
        sl_price NUMERIC(18,8),
        qty_contracts NUMERIC(18,8),
        dollar_risk NUMERIC(12,2),
        risk_pct_of_account NUMERIC(8,4),
        is_open BOOLEAN DEFAULT TRUE,
        created_at TIMESTAMPTZ DEFAULT NOW(),
        closed_at TIMESTAMPTZ
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS signal_state (
        symbol VARCHAR(16) PRIMARY KEY,
        status VARCHAR(32) DEFAULT 'ACTIVE',
        updated_at TIMESTAMPTZ DEFAULT NOW()
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS vtc_vectors (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMPTZ DEFAULT NOW(),
        sharpe FLOAT NOT NULL,
        sortino FLOAT NOT NULL,
        drawdown FLOAT NOT NULL,
        expectancy FLOAT NOT NULL,
        win_rate FLOAT NOT NULL,
        capital_growth FLOAT NOT NULL,
        stability FLOAT NOT NULL,
        risk_score FLOAT NOT NULL
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS vtc_scores (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMPTZ DEFAULT NOW(),
        score FLOAT NOT NULL,
        confidence FLOAT NOT NULL,
        performance_layer FLOAT,
        risk_layer FLOAT,
        growth_layer FLOAT
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS vtc_prices (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMPTZ DEFAULT NOW(),
        price FLOAT NOT NULL,
        score FLOAT NOT NULL
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS vtc_treasury (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMPTZ DEFAULT NOW(),
        total_supply FLOAT NOT NULL DEFAULT 1000000,
        circulating_supply FLOAT NOT NULL DEFAULT 1000000,
        minted FLOAT NOT NULL DEFAULT 0,
        burned FLOAT NOT NULL DEFAULT 0
      )
    `);
    await pool.query(`
      CREATE TABLE IF NOT EXISTS vtc_audit_log (
        id SERIAL PRIMARY KEY,
        timestamp TIMESTAMPTZ DEFAULT NOW(),
        vector_id INT,
        score FLOAT NOT NULL,
        old_price FLOAT NOT NULL,
        new_price FLOAT NOT NULL,
        reason TEXT NOT NULL,
        vector_snapshot JSONB
      )
    `);
    // Seed initial VTC data if empty
    await pool.query(`
      INSERT INTO vtc_treasury (total_supply, circulating_supply, minted, burned)
      SELECT 1000000, 1000000, 0, 0 WHERE NOT EXISTS (SELECT 1 FROM vtc_treasury)
    `);
    await pool.query(`
      INSERT INTO vtc_prices (price, score)
      SELECT 1.0, 50.0 WHERE NOT EXISTS (SELECT 1 FROM vtc_prices)
    `);

    // v0.3.3 — Add strategy column to high_confidence_signals if missing
    try {
      await pool.query(`ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS strategy VARCHAR(32) DEFAULT 'trend_following'`);
    } catch (e) {
      console.error('[Startup] strategy column migration:', e.message);
    }

    console.log('[Startup] Auxiliary tables ensured');
  } catch (e) {
    console.error('[Startup] Table creation error:', e.message);
  }

  await syncFromPostgres();
  setInterval(syncFromPostgres, 15000);

  // Fix: Backfill missing sl_price/tp_price for open trades from their original signals
  try {
    const fixRes = await pool.query(`
      UPDATE paper_trades pt
      SET sl_price = s.sl_price,
          tp_price = s.tp_price
      FROM high_confidence_signals s
      WHERE pt.signal_id = s.id
        AND pt.status = 'OPEN'
        AND (pt.sl_price IS NULL OR pt.sl_price = 0 OR pt.tp_price IS NULL OR pt.tp_price = 0)
        AND s.sl_price > 0
        AND s.tp_price > 0
    `);
    if (fixRes.rowCount > 0) {
      console.log(`[Fix] Backfilled sl_price/tp_price for ${fixRes.rowCount} open trade(s)`);
    }
  } catch (e) {
    console.error('[Fix Error]', e.message);
  }

  // Fix: Backfill risk_ledger entries for open trades not yet tracked
  try {
    const ledgerFix = await pool.query(`
      INSERT INTO risk_ledger (symbol, trade_id, entry_price, sl_price, qty_contracts, dollar_risk, risk_pct_of_account, is_open)
      SELECT pt.symbol, pt.id, pt.entry_price_actual, pt.sl_price, pt.qty_contracts,
             ABS(pt.entry_price_actual - pt.sl_price) * pt.qty_contracts,
             (ABS(pt.entry_price_actual - pt.sl_price) * pt.qty_contracts / pa.balance) * 100,
             TRUE
      FROM paper_trades pt
      CROSS JOIN paper_account pa
      WHERE pt.status = 'OPEN'
        AND pt.sl_price > 0
        AND pt.qty_contracts > 0
        AND pa.balance > 0
        AND NOT EXISTS (SELECT 1 FROM risk_ledger rl WHERE rl.trade_id = pt.id)
    `);
    if (ledgerFix.rowCount > 0) {
      console.log(`[Fix] Backfilled risk_ledger for ${ledgerFix.rowCount} open trade(s)`);
    }
  } catch (e) {
    console.error('[RiskLedger Fix Error]', e.message);
  }

  // 1. Load prices + price history from parquet (fast, local disk)
  await refreshLivePricesFromParquet();
  // 2. Load OB/Funding/OI from parquet and build full intel objects
  await updateMarketIntel();
  setInterval(updateMarketIntel, 30000);
  
  // Start server AFTER all routes are defined
  app.listen(PORT, '0.0.0.0', () => {
    console.log(`[Server] Backend listening on http://0.0.0.0:${PORT}`);
    console.log(`[Server] DB_URL: ${process.env.DB_URL ? 'configured' : 'MISSING'}`);
    console.log(`[Server] PARQUET_DATA_ROOT: ${process.env.PARQUET_DATA_ROOT || '/data (default)'}`);
    console.log(`[Server] Market intel engine started for ${MARKET_INTEL_SYMBOLS.length} symbols`);
  });
})();

app.use(require('./routes/paper.cjs')(pool, syncFromPostgres, sendTelegramAlert, database));

app.use(require('./routes/power.cjs')(pool, syncFromPostgres));

const refreshState = { running: false };
let lastFundingInterval = null; // tracks last 8h funding interval processed (0, 8, 16 UTC)

app.use(require('./routes/refresh.cjs')(syncFromPostgres, sendSSEToAll, refreshState));

// ── VTC API (Performance-Weighted Token) ───────────────────────────────
app.get('/api/vtc/current', async (req, res) => {
  res.json({
    price: database.vtc.price,
    score: database.vtc.score,
    confidence: database.vtc.confidence,
    marketCap: database.vtc.marketCap,
    nav: database.vtc.nav,
    supply: database.vtc.supply,
    timestamp: database.vtc.timestamp,
    layers: database.vtc.layers,
    vector: database.vtc.vector,
  });
});

app.get('/api/vtc/vector', async (req, res) => {
  res.json(database.vtc.vector || {});
});

app.get('/api/vtc/history', async (req, res) => {
  const timeframe = req.query.timeframe || '24H';
  const hours = { '1H': 1, '24H': 24, '7D': 24 * 7, '30D': 24 * 30 };
  const h = hours[timeframe] || 24;
  const cutoff = new Date(Date.now() - h * 60 * 60 * 1000);
  const rows = await pool.query(
    `SELECT timestamp, price FROM vtc_prices WHERE timestamp >= $1 ORDER BY timestamp ASC`,
    [cutoff]
  ).catch(() => ({ rows: [] }));
  res.json({ points: rows.rows.map(r => ({ timestamp: r.timestamp, price: parseFloat(r.price) })) });
});

app.get('/api/vtc/scores', async (req, res) => {
  res.json({ points: database.vtc.scores || [] });
});

app.get('/api/vtc/treasury', async (req, res) => {
  res.json(database.vtc.treasury || { total_supply: 1000000, circulating_supply: 1000000, minted: 0, burned: 0 });
});

app.get('/api/vtc/audit', async (req, res) => {
  res.json(database.vtc.audit || []);
});

app.post('/api/vtc/mint', async (req, res) => {
  const { amount, reason } = req.body;
  if (!amount || amount <= 0) return res.status(400).json({ error: 'Invalid amount' });
  try {
    const latest = await pool.query(`SELECT * FROM vtc_treasury ORDER BY timestamp DESC LIMIT 1`);
    const t = latest.rows[0] || { total_supply: 1000000, circulating_supply: 1000000, minted: 0, burned: 0 };
    const newSupply = parseFloat(t.total_supply) + amount;
    await pool.query(
      `INSERT INTO vtc_treasury (total_supply, circulating_supply, minted, burned) VALUES ($1,$2,$3,$4)`,
      [newSupply, newSupply, parseFloat(t.minted) + amount, parseFloat(t.burned)]
    );
    database.vtc.supply = newSupply;
    database.vtc.treasury = { total_supply: newSupply, circulating_supply: newSupply, minted: parseFloat(t.minted) + amount, burned: parseFloat(t.burned) };
    sendSSEToAll('update', { vtc: database.vtc });
    res.json({ supply: newSupply });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/vtc/burn', async (req, res) => {
  const { amount, reason } = req.body;
  if (!amount || amount <= 0) return res.status(400).json({ error: 'Invalid amount' });
  try {
    const latest = await pool.query(`SELECT * FROM vtc_treasury ORDER BY timestamp DESC LIMIT 1`);
    const t = latest.rows[0] || { total_supply: 1000000, circulating_supply: 1000000, minted: 0, burned: 0 };
    if (amount > parseFloat(t.circulating_supply)) return res.status(400).json({ error: 'Burn amount exceeds supply' });
    const newSupply = parseFloat(t.total_supply) - amount;
    await pool.query(
      `INSERT INTO vtc_treasury (total_supply, circulating_supply, minted, burned) VALUES ($1,$2,$3,$4)`,
      [newSupply, newSupply, parseFloat(t.minted), parseFloat(t.burned) + amount]
    );
    database.vtc.supply = newSupply;
    database.vtc.treasury = { total_supply: newSupply, circulating_supply: newSupply, minted: parseFloat(t.minted), burned: parseFloat(t.burned) + amount };
    sendSSEToAll('update', { vtc: database.vtc });
    res.json({ supply: newSupply });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

// ── AI-Trader Engine Proxy (port 8001) ─────────────────────────────────
const ENGINE_URL = process.env.ENGINE_URL || 'http://host.docker.internal:8001';

async function proxyToEngine(reqPath, method = 'GET', body = null) {
  const url = `${ENGINE_URL}${reqPath}`;
  const opts = { method, headers: {} };
  if (body) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  return { status: r.status, data };
}

app.get('/api/engine/health', async (req, res) => {
  const { status, data } = await proxyToEngine('/health');
  res.status(status).json(data);
});

app.get('/api/engine/data/symbols', async (req, res) => {
  const { status, data } = await proxyToEngine('/data/symbols');
  res.status(status).json(data);
});

app.get('/api/engine/data/intervals', async (req, res) => {
  const { status, data } = await proxyToEngine(`/data/intervals?${new URLSearchParams(req.query)}`);
  res.status(status).json(data);
});

app.post('/api/engine/probe/:action', async (req, res) => {
  const { status, data } = await proxyToEngine(`/probe/${req.params.action}`, 'POST', req.body);
  res.status(status).json(data);
});

app.post('/api/engine/pipeline/:stage', async (req, res) => {
  const { status, data } = await proxyToEngine(`/pipeline/${req.params.stage}`, 'POST', req.body);
  res.status(status).json(data);
});

app.post('/api/engine/backtest/start', async (req, res) => {
  const { status, data } = await proxyToEngine('/backtest/start', 'POST', req.body);
  res.status(status).json(data);
});

app.get('/api/engine/backtest/status', async (req, res) => {
  const { status, data } = await proxyToEngine(`/backtest/status?${new URLSearchParams(req.query)}`);
  res.status(status).json(data);
});

app.get('/api/engine/backtest/result', async (req, res) => {
  const { status, data } = await proxyToEngine(`/backtest/result?${new URLSearchParams(req.query)}`);
  res.status(status).json(data);
});

app.post('/api/engine/strategy/create', async (req, res) => {
  const { status, data } = await proxyToEngine('/strategy/create', 'POST', req.body);
  res.status(status).json(data);
});

app.get('/api/engine/strategy', async (req, res) => {
  const { status, data } = await proxyToEngine('/strategy');
  res.status(status).json(data);
});

app.get('/api/engine/strategy/:id', async (req, res) => {
  const { status, data } = await proxyToEngine(`/strategy/${req.params.id}`);
  res.status(status).json(data);
});

app.post('/api/engine/ai/chat', async (req, res) => {
  const { status, data } = await proxyToEngine('/ai/chat', 'POST', req.body);
  res.status(status).json(data);
});

// Market Data
app.get('/api/engine/candles', async (req, res) => {
  const { status, data } = await proxyToEngine(`/candles?${new URLSearchParams(req.query)}`);
  res.status(status).json(data);
});

app.get('/api/engine/funding', async (req, res) => {
  const { status, data } = await proxyToEngine(`/funding?${new URLSearchParams(req.query)}`);
  res.status(status).json(data);
});

app.get('/api/engine/open-interest', async (req, res) => {
  const { status, data } = await proxyToEngine(`/open-interest?${new URLSearchParams(req.query)}`);
  res.status(status).json(data);
});

app.get('/api/engine/features', async (req, res) => {
  const { status, data } = await proxyToEngine(`/features?${new URLSearchParams(req.query)}`);
  res.status(status).json(data);
});

// Validation
app.post('/api/engine/validate/walkforward', async (req, res) => {
  const { status, data } = await proxyToEngine('/validate/walkforward', 'POST', req.body);
  res.status(status).json(data);
});

app.post('/api/engine/validate/montecarlo', async (req, res) => {
  const { status, data } = await proxyToEngine('/validate/montecarlo', 'POST', req.body);
  res.status(status).json(data);
});

app.post('/api/engine/validate/oos', async (req, res) => {
  const { status, data } = await proxyToEngine('/validate/oos', 'POST', req.body);
  res.status(status).json(data);
});

// Decision
app.post('/api/engine/decision/create', async (req, res) => {
  const { status, data } = await proxyToEngine('/decision/create', 'POST', req.body);
  res.status(status).json(data);
});

app.get('/api/engine/decision/:id', async (req, res) => {
  const { status, data } = await proxyToEngine(`/decision/${req.params.id}`);
  res.status(status).json(data);
});

// Memory
app.post('/api/engine/memory/store', async (req, res) => {
  const { status, data } = await proxyToEngine('/memory/store', 'POST', req.body);
  res.status(status).json(data);
});

app.post('/api/engine/memory/search', async (req, res) => {
  const { status, data } = await proxyToEngine('/memory/search', 'POST', req.body);
  res.status(status).json(data);
});

// Backtest cancel
app.post('/api/engine/backtest/cancel', async (req, res) => {
  const { status, data } = await proxyToEngine('/backtest/cancel', 'POST', req.body);
  res.status(status).json(data);
});

// ── Bybit Live API + Dual-Execution Endpoints ─────────────────────────

// Bybit health check — tests DNS resolution, API connectivity, and wallet balance
app.get('/api/bybit/health', async (req, res) => {
  const health = {
    timestamp: new Date().toISOString(),
    configured: bybit.isConfigured,
    mode: bybit.isDemo ? 'demo' : 'mainnet',
    dns_resolved: false,
    api_reachable: false,
    wallet_ok: false,
    balance: 0,
    latency_ms: 0,
    error: null,
  };
  const t0 = Date.now();
  try {
    const wallet = await bybit.getWalletBalance();
    health.latency_ms = Date.now() - t0;
    if (wallet) {
      health.dns_resolved = true;
      health.api_reachable = true;
      health.wallet_ok = true;
      for (const coin of (wallet.coin || [])) {
        if (coin.coin === 'USDT') {
          health.balance = parseFloat(coin.walletBalance || 0);
          break;
        }
      }
    } else {
      health.error = 'Bybit API call failed — check DNS, network, and API keys';
    }
  } catch (e) {
    health.latency_ms = Date.now() - t0;
    health.error = e.message;
    if (e.code === 'EAI_AGAIN' || e.code === 'ENOTFOUND') {
      health.error = `DNS resolution failed: ${e.message}`;
    } else if (e.code === 'ECONNREFUSED' || e.code === 'ECONNRESET') {
      health.dns_resolved = true;
      health.error = `Connection failed: ${e.message}`;
    }
  }
  const ok = health.configured && health.dns_resolved && health.api_reachable && health.wallet_ok;
  res.status(ok ? 200 : 503).json(health);
});

// Get all Bybit trades from DB (pipeline-tracked dual-execution data)
app.get('/api/bybit/trades', async (req, res) => {
  try {
    const statusFilter = req.query.status || 'ALL';
    let statusClause = '';
    const params = [];
    if (statusFilter !== 'ALL') {
      statusClause = 'AND bybit_status = $1';
      params.push(statusFilter);
    }
    const result = await pool.query(`
      SELECT id, symbol, side, status, entry_price_actual, sl_price, tp_price,
             qty_contracts, net_pnl_usd, net_pnl_pct, exit_reason, exit_time_utc,
             bybit_order_id, bybit_qty, bybit_entry_price, bybit_sl_price, bybit_tp_price,
             bybit_status, bybit_close_price, bybit_pnl_usd, bybit_pnl_pct,
             bybit_close_time, bybit_close_reason,
             bybit_account_balance, bybit_risk_pct, bybit_dollar_risk,
             confidence, entry_strategy, entry_regime, created_at
      FROM paper_trades
      WHERE bybit_order_id IS NOT NULL
      ${statusClause}
      ORDER BY created_at DESC
      LIMIT 100
    `, params);
    res.json({ trades: result.rows });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Get trades with Bybit execution gap (no Bybit order was ever placed, or order failed/skipped)
app.get('/api/bybit/trades/gap', async (req, res) => {
  try {
    const result = await pool.query(`
      SELECT id, symbol, side, status, entry_price_actual, sl_price, tp_price,
             qty_contracts, net_pnl_usd, net_pnl_pct, exit_reason, exit_time_utc,
             bybit_order_id, bybit_status, bybit_qty, bybit_entry_price,
             bybit_account_balance, bybit_risk_pct, bybit_dollar_risk,
             confidence, entry_strategy, entry_regime, created_at
      FROM paper_trades
      WHERE bybit_order_id IS NULL
      ORDER BY created_at DESC
      LIMIT 100
    `);
    res.json({ trades: result.rows, count: result.rows.length });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Get Bybit LIVE account summary (queries Bybit API directly)
app.get('/api/bybit/account', async (req, res) => {
  try {
    const [wallet, positions, openOrders, closedPnl, paperRes] = await Promise.all([
      bybit.getWalletBalance(),
      bybit.getPositions(),
      bybit.getOpenOrders(),
      bybit.getClosedPnl('linear', 50),
      pool.query('SELECT balance, equity, margin_used, realized_pnl FROM paper_account LIMIT 1'),
    ]);

    let bybitBalance = 0;
    let bybitEquity = 0;
    let bybitAvailable = 0;
    let bybitMarginUsed = 0;
    let bybitUnrealizedPnl = 0;
    if (wallet) {
      for (const coin of (wallet.coin || [])) {
        if (coin.coin === 'USDT') {
          bybitBalance = parseFloat(coin.walletBalance || 0);
          bybitEquity = parseFloat(coin.equity || bybitBalance);
          bybitAvailable = parseFloat(coin.availableToWithdraw || 0);
          bybitMarginUsed = parseFloat(coin.totalPositionIM || 0);
          bybitUnrealizedPnl = parseFloat(coin.unrealisedPnl || 0);
          break;
        }
      }
    }

    let closedCount = closedPnl.length;
    let wins = 0;
    let totalPnlUsd = 0;
    let totalPnlPct = 0;
    let bestTrade = 0;
    let worstTrade = 0;
    for (const t of closedPnl) {
      const pnl = parseFloat(t.closedPnl || 0);
      const pnlPct = parseFloat(t.closedPnl / (t.qty * t.avgEntryPrice) * 100 || 0);
      if (pnl > 0) wins++;
      totalPnlUsd += pnl;
      totalPnlPct += pnlPct;
      if (pnl > bestTrade) bestTrade = pnl;
      if (pnl < worstTrade) worstTrade = pnl;
    }

    const paper = paperRes.rows[0];

    res.json({
      bybit: {
        connected: bybit.isConfigured,
        mode: bybit.isDemo ? 'demo' : 'mainnet',
        balance: bybitBalance.toFixed(2),
        equity: bybitEquity.toFixed(2),
        available: bybitAvailable.toFixed(2),
        margin_used: bybitMarginUsed.toFixed(2),
        unrealized_pnl: bybitUnrealizedPnl.toFixed(2),
        open_positions: positions,
        open_orders: openOrders,
        closed_count: closedCount,
        wins,
        win_rate: closedCount > 0 ? ((wins / closedCount) * 100).toFixed(2) : '0.00',
        total_pnl_usd: totalPnlUsd.toFixed(2),
        avg_pnl_pct: closedCount > 0 ? (totalPnlPct / closedCount).toFixed(4) : '0.0000',
        best_trade: bestTrade.toFixed(2),
        worst_trade: worstTrade.toFixed(2),
        closed_pnl_list: closedPnl.slice(0, 20),
      },
      paper: {
        balance: parseFloat(paper?.balance || 10000).toFixed(2),
        equity: parseFloat(paper?.equity || 10000).toFixed(2),
        margin_used: parseFloat(paper?.margin_used || 0).toFixed(2),
        realized_pnl: parseFloat(paper?.realized_pnl || 0).toFixed(2),
      },
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Get live Bybit positions with unrealized PnL
app.get('/api/bybit/positions', async (req, res) => {
  try {
    const positions = await bybit.getPositions();
    const tickers = {};
    for (const p of positions) {
      const tlist = await bybit.getTickers(p.symbol);
      if (tlist.length > 0) tickers[p.symbol] = tlist[0];
    }
    res.json({ positions, tickers });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Get live Bybit open orders
app.get('/api/bybit/orders', async (req, res) => {
  try {
    const orders = await bybit.getOpenOrders();
    res.json({ orders });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Get Bybit closed PnL history from exchange
app.get('/api/bybit/closed-pnl', async (req, res) => {
  try {
    const limit = parseInt(req.query.limit) || 50;
    const list = await bybit.getClosedPnl('linear', limit);
    res.json({ trades: list });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Get side-by-side comparison of paper vs Bybit performance (from DB)
app.get('/api/bybit/compare', async (req, res) => {
  try {
    const result = await pool.query(`
      SELECT
        symbol, side, status, exit_reason,
        entry_price_actual, exit_price, net_pnl_usd, net_pnl_pct,
        qty_contracts, bybit_qty,
        bybit_entry_price, bybit_close_price, bybit_pnl_usd, bybit_pnl_pct,
        bybit_status, bybit_close_reason,
        bybit_account_balance, bybit_risk_pct,
        confidence, entry_strategy, created_at, exit_time_utc, bybit_close_time
      FROM paper_trades
      WHERE bybit_order_id IS NOT NULL
      ORDER BY created_at DESC
      LIMIT 50
    `);
    res.json({ comparisons: result.rows });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── P5: Twice-Daily AI Market Overview ───────────────────────────────

const OVERVIEW_SYMBOLS = ['BTCUSDT','ETHUSDT','SOLUSDT','XRPUSDT','BNBUSDT','DOGEUSDT'];

async function getLatestDateRange(symbol, interval = '15m') {
  const { status, data } = await proxyToEngine(`/data/range?symbol=${symbol}&interval=${interval}`);
  if (status !== 200 || !data || !data.latest) return null;
  const end = data.latest.split('T')[0];
  const d = new Date(data.latest);
  d.setDate(d.getDate() - 7);
  const start = d.toISOString().split('T')[0];
  return { start, end };
}

app.post('/api/overview/generate', async (req, res) => {
  const startedAt = Date.now();
  try {
    // 1. Determine date range from latest data
    const range = await getLatestDateRange('BTCUSDT', '15m');
    if (!range) {
      return res.status(503).json({ error: 'Engine data/range unavailable. Is the engine running?' });
    }
    const { start, end } = range;

    // 2. Probe compare across all 6 symbols
    const { status: cmpStatus, data: cmpData } = await proxyToEngine('/probe/compare', 'POST', {
      symbols: OVERVIEW_SYMBOLS.join(','),
      interval: '15m',
      start,
      end
    });
    if (cmpStatus !== 200) {
      return res.status(502).json({ error: 'Probe compare failed', engineStatus: cmpStatus, engineData: cmpData });
    }

    // 3. Per-symbol probe for richer context
    const perSymbol = [];
    for (const sym of OVERVIEW_SYMBOLS) {
      try {
        const { status: ps, data: pd } = await proxyToEngine('/probe/symbol', 'POST', {
          symbol: sym, interval: '15m', start, end
        });
        if (ps === 200) perSymbol.push({ symbol: sym, ...pd });
      } catch (e) {
        console.warn(`[Overview] probe/symbol failed for ${sym}:`, e.message);
      }
    }

    // 4. Get current portfolio config for context
    const riskTiers = {
      fair: { min_dqs: 50, risk_pct: 2.0, rr: 1.33 },
      good: { min_dqs: 65, risk_pct: 3.0, rr: 1.67 },
      excellent: { min_dqs: 75, risk_pct: 5.0, rr: 2.33 }
    };
    const perSymbolParams = {
      BTCUSDT: { sl_mult: 1.60, tp_mult: 1.90 },
      ETHUSDT: { sl_mult: 1.89, tp_mult: 2.14 },
      SOLUSDT: { sl_mult: 1.50, tp_mult: 3.00 },
      XRPUSDT: { sl_mult: 1.44, tp_mult: 1.73 },
      BNBUSDT: { sl_mult: 1.50, tp_mult: 3.00 },
      DOGEUSDT: { sl_mult: 1.50, tp_mult: 3.00 }
    };

    // 5. Build AI prompt
    const prompt = `You are VLTHR's senior market analyst. Review the current market regime for all 6 crypto symbols based on the probe data below.

DATA WINDOW: ${start} to ${end} (15m candles)

PROBE COMPARE (multi-symbol ranking):
${JSON.stringify(cmpData, null, 2)}

PER-SYMBOL DEEP PROBE:
${JSON.stringify(perSymbol.map(p => ({ symbol: p.symbol, regime: p.regime, key_features: p.top_features?.slice(0, 3) || [] })), null, 2)}

CURRENT CONFIG:
- Risk tiers: ${JSON.stringify(riskTiers)}
- Per-symbol SL/TP multipliers: ${JSON.stringify(perSymbolParams)}
- Max hold time: 5 hours
- Min R:R floor: 1.5

TASK:
1. Regime per symbol: trend, mean-reversion, or chop? State confidence.
2. Alignment: Is the current strategy (RSI pullback + ADX trend filter + session gating) aligned with each regime?
3. Flag any params that look stale or mismatched (e.g., SL too tight for current volatility, TP unreachable in 5h).
4. Recommendation: Should we raise/lower min DQS, adjust session windows, or pause any symbol?
5. One-line risk summary.

Respond in structured markdown with sections: ## Regimes, ## Alignment, ## Stale Params, ## Recommendations, ## Risk Summary.`;

    // 6. Call AI
    const { status: aiStatus, data: aiData } = await proxyToEngine('/ai/chat', 'POST', {
      model: 'nvidia/llama-3.1-nemotron-70b-instruct',
      messages: [
        { role: 'system', content: 'You are a concise quantitative strategist. Use bullet points. No fluff.' },
        { role: 'user', content: prompt }
      ],
      max_tokens: 2048,
      temperature: 0.3
    });

    const aiContent = aiData?.choices?.[0]?.message?.content || 'AI response unavailable';
    const report = {
      generated_at: new Date().toISOString(),
      data_window: { start, end },
      probe_compare: cmpData,
      per_symbol: perSymbol.map(p => ({ symbol: p.symbol, regime: p.regime, top_features: p.top_features?.slice(0, 3) || [] })),
      ai_analysis: aiContent,
      duration_ms: Date.now() - startedAt
    };

    // 7. Persist to DB (audit trail)
    await pool.query(`
      CREATE TABLE IF NOT EXISTS ai_overviews (
        id SERIAL PRIMARY KEY,
        generated_at TIMESTAMPTZ DEFAULT NOW(),
        data_window JSONB,
        probe_compare JSONB,
        per_symbol JSONB,
        ai_analysis TEXT,
        duration_ms INT
      )
    `);
    await pool.query(`
      INSERT INTO ai_overviews (data_window, probe_compare, per_symbol, ai_analysis, duration_ms)
      VALUES ($1, $2, $3, $4, $5)
    `, [
      JSON.stringify(report.data_window),
      JSON.stringify(report.probe_compare),
      JSON.stringify(report.per_symbol),
      report.ai_analysis,
      report.duration_ms
    ]);

    res.json({ success: true, report });
  } catch (err) {
    console.error('[Overview Generate Error]', err);
    res.status(500).json({ error: err.message });
  }
});

app.get('/api/overview/latest', async (req, res) => {
  try {
    // Ensure table exists before querying
    await pool.query(`
      CREATE TABLE IF NOT EXISTS ai_overviews (
        id SERIAL PRIMARY KEY,
        generated_at TIMESTAMPTZ DEFAULT NOW(),
        data_window JSONB,
        probe_compare JSONB,
        per_symbol JSONB,
        ai_analysis TEXT,
        duration_ms INT
      )
    `);
    const result = await pool.query(`
      SELECT * FROM ai_overviews ORDER BY generated_at DESC LIMIT 1
    `);
    if (result.rows.length === 0) {
      return res.status(404).json({ error: 'No overview generated yet. Run POST /api/overview/generate first.' });
    }
    res.json({ success: true, overview: result.rows[0] });
  } catch (err) {
    console.error('[Overview Latest Error]', err);
    res.status(500).json({ error: err.message });
  }
});

// ── Parquet-Based Live Price Feed ──────────────────────────────────────
// Reads 1m parquet for crypto symbols (high-freq) and 5m for XAUUSD.

async function refreshLivePricesFromParquet() {
  for (const sym of MARKET_INTEL_SYMBOLS) {
    try {
      let data;
      if (sym === 'XAUUSD') {
        data = await pq.getXauusdPriceData();
      } else {
        data = await pq.get1mPriceData(sym);
      }
      if (data && data.price > 0) {
        const oldPrice = database.livePrices[sym]?.price || 0;
        database.livePrices[sym] = {
          price:     data.price,
          prevPrice: oldPrice > 0 ? oldPrice : data.price,
          change24h: data.change24h,
          updated:   Date.now()
        };
        // Cache price history for intel charts
        if (!database.marketIntel[sym]) {
          database.marketIntel[sym] = {
            symbol: sym, price: data.price, change24h: data.change24h,
            orderbook: null, oi: null, funding: null, narrative: '',
            history: { prices: data.priceHistory || [], funding: [], oi: [] }
          };
        } else {
          database.marketIntel[sym].price     = data.price;
          database.marketIntel[sym].change24h = data.change24h;
          // Update price history with new data
          if (data.priceHistory && data.priceHistory.length > 0) {
            database.marketIntel[sym].history.prices = data.priceHistory;
          }
        }
      }
    } catch (e) {
      console.error(`[PriceParquet ${sym}]`, e.message);
    }
  }
  if (Object.keys(database.livePrices).length > 0) {
    sendSSEToAll('prices', { livePrices: database.livePrices });
  }
}

refreshLivePricesFromParquet();
setInterval(refreshLivePricesFromParquet, 15000);   // 15s — reads 1m parquet for near real-time prices
