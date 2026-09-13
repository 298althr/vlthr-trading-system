const { Pool } = require('pg');
const pool = new Pool({ connectionString: process.env.DB_URL });
const sql = `
  ALTER TABLE watchlist_current
  ADD COLUMN IF NOT EXISTS gate_session_ok BOOLEAN,
  ADD COLUMN IF NOT EXISTS gate_adx_ok BOOLEAN,
  ADD COLUMN IF NOT EXISTS gate_direction_ok BOOLEAN,
  ADD COLUMN IF NOT EXISTS gate_rsi_ok BOOLEAN,
  ADD COLUMN IF NOT EXISTS gate_bull_4h_ok BOOLEAN;
`;
pool.query(sql)
  .then(() => { console.log('Gate columns added/verified'); pool.end(); })
  .catch(e => { console.error('Error:', e.message); pool.end(); process.exit(1); });
