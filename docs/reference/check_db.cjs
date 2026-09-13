const { Pool } = require('pg');
const pool = new Pool({ 
  connectionString: process.env.DATABASE_URL || 'postgresql://postgres:your_postgres_password@localhost:5432/postgres',
  ssl: { rejectUnauthorized: false }
});
async function main() {
  const hcsCols = await pool.query("SELECT column_name FROM information_schema.columns WHERE table_name = 'high_confidence_signals'");
  console.log('=== high_confidence_signals columns ===');
  console.log(hcsCols.rows.map(r => r.column_name).join(', '));
  
  const watchCols = await pool.query("SELECT column_name FROM information_schema.columns WHERE table_name = 'watchlist_current'");
  console.log('=== watchlist_current columns ===');
  console.log(watchCols.rows.map(r => r.column_name).join(', '));
  
  const hcs = await pool.query('SELECT * FROM high_confidence_signals ORDER BY created_at DESC LIMIT 10');
  console.log('\n=== high_confidence_signals (last 10) ===');
  console.table(hcs.rows);
  
  const watch = await pool.query('SELECT * FROM watchlist_current ORDER BY created_at DESC LIMIT 10');
  console.log('\n=== watchlist_current (last 10) ===');
  console.table(watch.rows);
  
  await pool.end();
}
main().catch(e => { console.error(e.message); process.exit(1); });
