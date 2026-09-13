/**
 * cleanup_bad_pending.cjs
 * Deletes low-quality PENDING trades created by the old buggy auto-pending logic.
 * These trades have TP=0, SL=0, fees=0 and lack pre-trade estimates.
 *
 * Run: node backend/cleanup_bad_pending.cjs
 */

const { Pool } = require('pg');
require('dotenv').config({ path: require('path').join(__dirname, '..', '.env') });

const pool = new Pool({ connectionString: process.env.DB_URL });

async function cleanup() {
  // Find low-quality pending trades: TP=0 AND SL=0 AND fees_total=0
  // These were created by the old Node.js auto-pending logic (before the fix).
  const findRes = await pool.query(`
    SELECT id, symbol, entry_price_planned, notes
    FROM paper_trades
    WHERE status = 'PENDING'
      AND COALESCE(tp_price, 0) = 0
      AND COALESCE(sl_price, 0) = 0
      AND COALESCE(fees_total, 0) = 0
    ORDER BY id DESC
  `);

  if (findRes.rows.length === 0) {
    console.log('[Cleanup] No bad pending trades found. All clear.');
    await pool.end();
    return;
  }

  console.log(`[Cleanup] Found ${findRes.rows.length} bad pending trade(s):`);
  for (const row of findRes.rows) {
    console.log(`  - ID ${row.id}: ${row.symbol} @ $${row.entry_price_planned} — ${row.notes}`);
  }

  const ids = findRes.rows.map(r => r.id);
  // Delete associated statements first (FK safety)
  await pool.query(`
    DELETE FROM paper_trade_statements WHERE trade_id = ANY($1)
  `, [ids]);

  const delRes = await pool.query(`
    DELETE FROM paper_trades WHERE id = ANY($1)
  `, [ids]);

  console.log(`[Cleanup] Deleted ${delRes.rowCount} bad pending trade(s).`);
  await pool.end();
}

cleanup().catch(err => {
  console.error('[Cleanup Error]', err);
  process.exit(1);
});
