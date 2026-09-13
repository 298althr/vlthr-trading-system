const pool = require('./db.cjs');
(async () => {
  try {
    const r = await pool.query("SELECT column_name FROM information_schema.columns WHERE table_name='paper_trades' ORDER BY ordinal_position");
    console.log(r.rows.map(x => x.column_name).join(', '));
  } catch (e) {
    console.error(e.message);
  } finally {
    await pool.end();
  }
})();
