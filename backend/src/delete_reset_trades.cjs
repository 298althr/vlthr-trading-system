require('dotenv').config();
const { Pool } = require('pg');
const pool = new Pool({ connectionString: process.env.DB_URL });

async function main() {
  // Delete associated statements first to avoid FK violation
  const stmtRes = await pool.query(
    "DELETE FROM paper_trade_statements WHERE trade_id IN (SELECT id FROM paper_trades WHERE status='CLOSED' AND exit_reason IN ('RESET','MANUAL'))"
  );
  console.log(`Deleted ${stmtRes.rowCount} paper_trade_statement rows`);

  const res = await pool.query(
    "DELETE FROM paper_trades WHERE status='CLOSED' AND exit_reason IN ('RESET','MANUAL') RETURNING id, symbol, exit_reason"
  );
  console.log(`Deleted ${res.rows.length} RESET/MANUAL trades:`);
  res.rows.forEach(r => console.log(`  id=${r.id} ${r.symbol} reason=${r.exit_reason}`));

  // Recalculate account stats from remaining closed trades
  const stats = await pool.query(`
    SELECT COUNT(*) as total,
           COUNT(*) FILTER (WHERE net_pnl_pct > 0) as wins,
           COALESCE(SUM(net_pnl_pct), 0) as total_pnl,
           COALESCE(SUM(net_pnl_usd), 0) as total_pnl_usd
    FROM paper_trades WHERE status = 'CLOSED'
  `);
  const s = stats.rows[0];
  const total = parseInt(s.total || 0);
  const wins = parseInt(s.wins || 0);
  const totalPnl = parseFloat(s.total_pnl || 0);
  const totalPnlUsd = parseFloat(s.total_pnl_usd || 0);
  const winRate = total > 0 ? (wins / total * 100) : 0;
  const realizedPnl = totalPnlUsd;
  const seed = 10000;
  const walletBalance = seed + realizedPnl;

  await pool.query(`
    UPDATE paper_account
    SET total_trades = $1,
        win_rate = $2,
        total_pnl = $3,
        realized_pnl = $4,
        balance = $5,
        equity = $5,
        updated_at = NOW()
  `, [total, winRate, totalPnl, realizedPnl, walletBalance]);

  console.log(`Account updated: total_trades=${total}, win_rate=${winRate.toFixed(1)}%, realized_pnl=$${realizedPnl.toFixed(2)}, balance=$${walletBalance.toFixed(2)}`);
  await pool.end();
}

main().catch(e => { console.error(e); process.exit(1); });
