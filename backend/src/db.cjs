const { Pool } = require('pg');
const path = require('path');
require('dotenv').config({ path: path.join(__dirname, '..', '.env') });

const _dbUrl = process.env.DB_URL || '';
const _needsSsl = _dbUrl.includes('supabase') || _dbUrl.includes('pooler');
const pool = new Pool({
  connectionString: _dbUrl,
  ssl: _needsSsl ? { rejectUnauthorized: false } : false
});

// Migrations
(async () => {
  const migrations = [
    [`ALTER TABLE paper_account ADD COLUMN IF NOT EXISTS totp_secret VARCHAR(255)`, 'totp_secret'],
    [`ALTER TABLE predictions ADD COLUMN IF NOT EXISTS close_price_at_prediction DOUBLE PRECISION`, 'predictions.close_price_at_prediction'],
    [`ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS brain_confidence_fused NUMERIC(6,4)`, 'hcs.brain_confidence_fused'],
    [`ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS brain_shadow_mode BOOLEAN DEFAULT TRUE`, 'hcs.brain_shadow_mode'],
    [`ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS liquidation_price NUMERIC(18,8)`, 'paper_trades.liquidation_price'],
    [`ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS maintenance_margin NUMERIC(12,4)`, 'paper_trades.maintenance_margin'],
    [`ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS last_funding_settlement_utc TIMESTAMPTZ`, 'paper_trades.last_funding_settlement_utc'],
    [`ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS mark_price_at_entry NUMERIC(18,8)`, 'paper_trades.mark_price_at_entry'],
    [`ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS calibrated_confidence NUMERIC(6,4)`, 'hcs.calibrated_confidence'],
    [`ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS information_ratio NUMERIC(8,4)`, 'hcs.information_ratio'],
    [`ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS margin_required NUMERIC(18,8)`, 'hcs.margin_required'],
    [`ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS liquidation_price NUMERIC(18,8)`, 'hcs.liquidation_price'],
    [`ALTER TABLE high_confidence_signals ADD COLUMN IF NOT EXISTS maintenance_margin NUMERIC(12,4)`, 'hcs.maintenance_margin'],
    [`ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS sl_original NUMERIC(18,8)`, 'paper_trades.sl_original'],
    [`ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS atr_at_entry NUMERIC(18,8)`, 'paper_trades.atr_at_entry'],
  ];
  for (const [sql, name] of migrations) {
    try {
      await pool.query(sql);
      console.log(`[Migration] ${name} verified`);
    } catch (e) {
      console.error(`[Migration] Failed: ${name}:`, e.message);
    }
  }

  // v0.3.0 — Structured logging tables
  const logTables = `
    CREATE TABLE IF NOT EXISTS signal_audit_log (
        id BIGSERIAL PRIMARY KEY,
        run_time TIMESTAMPTZ,
        signal_id INT,
        symbol VARCHAR(16),
        dqs INT,
        gate_reached VARCHAR(32),
        gate_passed BOOLEAN,
        gate_reason TEXT,
        final_action VARCHAR(16),
        created_at TIMESTAMPTZ DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_audit_signal ON signal_audit_log(signal_id);
    CREATE INDEX IF NOT EXISTS idx_audit_time ON signal_audit_log(run_time DESC);

    CREATE TABLE IF NOT EXISTS trade_event_log (
        id BIGSERIAL PRIMARY KEY,
        trade_id INT REFERENCES paper_trades(id),
        symbol VARCHAR(16),
        event_type VARCHAR(32),
        event_time TIMESTAMPTZ DEFAULT NOW(),
        price_at_event NUMERIC(18,8),
        balance_before NUMERIC(12,4),
        balance_after NUMERIC(12,4),
        margin_delta NUMERIC(12,4),
        fee_applied NUMERIC(12,6),
        pnl_delta NUMERIC(12,4),
        running_pnl NUMERIC(12,4),
        detail JSONB
    );
    CREATE INDEX IF NOT EXISTS idx_event_trade ON trade_event_log(trade_id);
    CREATE INDEX IF NOT EXISTS idx_event_time ON trade_event_log(event_time DESC);

    CREATE TABLE IF NOT EXISTS trade_debug_log (
        id              BIGSERIAL PRIMARY KEY,
        trade_id        INT REFERENCES paper_trades(id),
        symbol          VARCHAR(16),
        log_type        VARCHAR(16),
        logged_at       TIMESTAMPTZ DEFAULT NOW(),
        price           NUMERIC(18,8),
        btc_price       NUMERIC(18,8),
        btc_24h_ret     NUMERIC(8,4),
        symbol_24h_ret  NUMERIC(8,4),
        correlation_vs_btc NUMERIC(6,4),
        rsi_15m         NUMERIC(6,2),
        adx_4h          NUMERIC(6,2),
        atr_15m         NUMERIC(8,6),
        ema_20          NUMERIC(18,8),
        ema_50          NUMERIC(18,8),
        volume_ratio    NUMERIC(6,2),
        funding_rate    NUMERIC(10,6),
        oi_delta_pct    NUMERIC(8,4),
        ob_spread_pct   NUMERIC(8,4),
        ob_imbalance    NUMERIC(8,4),
        liquidation_side VARCHAR(8),
        session         VARCHAR(16),
        market_regime   VARCHAR(16),
        dqs_score       INT,
        dqs_breakdown   JSONB,
        calibrated_prob NUMERIC(6,4),
        brain_prob      NUMERIC(6,4),
        ai_interpretation TEXT,
        failure_category  VARCHAR(32),
        suggested_fix     TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_debug_trade ON trade_debug_log(trade_id);
    CREATE INDEX IF NOT EXISTS idx_debug_logged ON trade_debug_log(logged_at DESC);
    CREATE INDEX IF NOT EXISTS idx_debug_category ON trade_debug_log(failure_category);
  `;
  try {
    await pool.query(logTables);
    console.log('[Migration] signal_audit_log + trade_event_log + trade_debug_log tables verified');
  } catch (e) {
    console.error('[Migration] Logging tables failed:', e.message);
  }
})();

module.exports = pool;
