// Reset paper account + apply migration — preserves closed trade history
require('dotenv').config();
const { Pool } = require('pg');

const pool = new Pool({ connectionString: process.env.DB_URL || process.env.DATABASE_URL });

async function main() {
  // 1. Apply migration: add columns if missing
  await pool.query(`
    ALTER TABLE paper_account
      ADD COLUMN IF NOT EXISTS realized_pnl numeric(15,2) DEFAULT 0.00,
      ADD COLUMN IF NOT EXISTS cumulative_funding_paid numeric(15,2) DEFAULT 0.00;

    ALTER TABLE paper_trades
      ADD COLUMN IF NOT EXISTS margin_required numeric(15,2) DEFAULT 0.00,
      ADD COLUMN IF NOT EXISTS open_fee numeric(15,2) DEFAULT 0.00,
      ADD COLUMN IF NOT EXISTS close_fee numeric(15,2) DEFAULT 0.00,
      ADD COLUMN IF NOT EXISTS cumulative_funding numeric(15,2) DEFAULT 0.00;
  `);
  console.log('[Reset] Migration columns ensured');

  // 2. Close all OPEN trades as RESET (preserve in history)
  const openRes = await pool.query(`
    UPDATE paper_trades
    SET status='CLOSED',
        exit_price = entry_price_actual,
        exit_reason = 'RESET',
        exit_time_utc = NOW(),
        gross_pnl_pct = 0,
        gross_pnl_usd = 0,
        net_pnl_pct = 0,
        net_pnl_usd = 0,
        fees_total = COALESCE(open_fee, 0) + COALESCE(close_fee, 0) + COALESCE(cumulative_funding, 0),
        updated_at = NOW()
    WHERE status = 'OPEN'
    RETURNING id, symbol
  `);
  console.log(`[Reset] Closed ${openRes.rows.length} open trades as RESET`);

  // 3. Cancel all PENDING trades (mark as CLOSED with RESET reason, zero PnL)
  const pendingRes = await pool.query(`
    UPDATE paper_trades
    SET status='CLOSED',
        exit_price = entry_price_planned,
        exit_reason = 'RESET',
        exit_time_utc = NOW(),
        gross_pnl_pct = 0,
        gross_pnl_usd = 0,
        net_pnl_pct = 0,
        net_pnl_usd = 0,
        updated_at = NOW()
    WHERE status = 'PENDING'
    RETURNING id, symbol
  `);
  console.log(`[Reset] Cancelled ${pendingRes.rows.length} pending trades as RESET`);

  // 4. Reset paper_account to initial state, keeping historical realized_pnl
  // Calculate realized_pnl from all CLOSED trades (excluding RESET ones if desired, but include them)
  const pnlRes = await pool.query(`
    SELECT COALESCE(SUM(net_pnl_usd), 0) as realized_pnl
    FROM paper_trades
    WHERE status = 'CLOSED' AND exit_reason != 'RESET'
  `);
  const realizedPnl = parseFloat(pnlRes.rows[0].realized_pnl || 0);
  const seed = 10000.00;
  const available = seed + realizedPnl;

  await pool.query(`
    UPDATE paper_account
    SET balance = $1,
        equity = $1,
        margin_used = 0,
        realized_pnl = $2,
        cumulative_funding_paid = 0,
        total_pnl = $2,
        total_trades = (SELECT COUNT(*) FROM paper_trades WHERE status='CLOSED' AND exit_reason != 'RESET'),
        win_rate = (SELECT COALESCE(AVG(CASE WHEN net_pnl_pct > 0 THEN 1.0 ELSE 0.0 END) * 100, 0) FROM paper_trades WHERE status='CLOSED' AND exit_reason != 'RESET'),
        updated_at = NOW()
  `, [available, realizedPnl]);
  console.log(`[Reset] Account reset: balance=$${available.toFixed(2)}, realized_pnl=$${realizedPnl.toFixed(2)}`);

  await pool.end();
}

main().catch(e => { console.error(e); process.exit(1); });
