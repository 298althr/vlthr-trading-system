const { Pool } = require('pg');
require('dotenv').config({ path: require('path').join(__dirname, '..', '.env') });

const pool = new Pool({ connectionString: process.env.DB_URL });

async function inspect() {
  const res = await pool.query(`
    SELECT id, symbol, confidence, is_expired, expiry_reason, scan_time_utc,
           signal_bar_utc, price_at_signal, sl_price, tp_price,
           h4_direction, qty_contracts
    FROM high_confidence_signals
    WHERE confidence > 50
    ORDER BY scan_time_utc DESC
  `);
  console.log(`Found ${res.rows.length} high-confidence signals (conf > 50):\n`);
  for (const row of res.rows) {
    const expired = row.is_expired ? 'YES' : 'NO';
    const now = new Date();
    const scan = new Date(row.scan_time_utc);
    const diffHrs = ((now - scan) / 1000 / 60 / 60).toFixed(1);
    console.log(`ID ${row.id} | ${row.symbol} | conf=${row.confidence} | expired=${expired} | scan=${row.scan_time_utc} | real_age=${diffHrs}h`);
    console.log(`  price=${row.price_at_signal} | SL=${row.sl_price} | TP=${row.tp_price} | dir=${row.h4_direction} | qty=${row.qty_contracts}`);
    if (row.expiry_reason) console.log(`  expiry_reason: ${row.expiry_reason}`);
    console.log();
  }
  await pool.end();
}

inspect().catch(err => { console.error(err); process.exit(1); });
