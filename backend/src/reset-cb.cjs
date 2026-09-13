const pool = require('./db.cjs');

(async () => {
  try {
    const r = await pool.query("DELETE FROM error_log WHERE is_critical = TRUE AND run_time >= NOW() - INTERVAL '2 hours'");
    console.log('[CB Reset] Deleted', r.rowCount, 'critical error(s) from last 2h');
    await pool.query("INSERT INTO error_log (source, error_type, message, run_time, is_critical) VALUES ($1,$2,$3,NOW(),$4)", ['safety', 'INFO', 'Circuit breaker manually reset by dev', false]);
    console.log('[CB Reset] Logged reset event');
  } catch (e) {
    console.error('[CB Reset] Failed:', e.message);
  } finally {
    await pool.end();
  }
})();
