const { Router } = require('express');

module.exports = function createPowerRouter(pool, syncFromPostgres) {
  const router = Router();

  router.post('/api/power-trades/promote', async (req, res) => {
    const { signalId, analystNote, urgency } = req.body;
    if (!signalId) return res.status(400).json({ error: 'signalId required' });
    try {
      const numId = parseInt(String(signalId).replace('HCS-', ''));
      const sigRes = await pool.query('SELECT * FROM high_confidence_signals WHERE id = $1', [numId]);
      const sig = sigRes.rows[0];
      if (!sig) return res.status(404).json({ error: 'Signal not found' });

      const result = await pool.query(`INSERT INTO power_trades (signal_id, symbol, confidence, signal_strength, price_at_signal, sl_price, tp_price, risk_reward, dollar_risk, leveraged_notional, qty_contracts, session, h4_direction, trade_decision, action, summary, urgency, analyst_note, status, promoted_by) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,'ACTIVE','MANUAL') ON CONFLICT (signal_id) DO UPDATE SET urgency = EXCLUDED.urgency, analyst_note = EXCLUDED.analyst_note, status = 'ACTIVE' RETURNING *`,
        [numId, sig.symbol, sig.confidence, sig.signal_strength, sig.price_at_signal, sig.sl_price, sig.tp_price, sig.risk_reward, sig.dollar_risk, sig.leveraged_notional, sig.qty_contracts, sig.session, sig.h4_direction, sig.trade_decision, sig.action, sig.summary, urgency || 'NORMAL', analystNote || '']);

      await syncFromPostgres();
      res.json({ success: true, powerTrade: result.rows[0] });
    } catch (err) {
      console.error('[Promote Power Trade Error]', err);
      res.status(500).json({ error: err.message });
    }
  });

  router.post('/api/power-trades/:id/dismiss', async (req, res) => {
    const { id } = req.params;
    try {
      await pool.query(`UPDATE power_trades SET status = 'DISMISSED' WHERE id = $1`, [id]);
      await syncFromPostgres();
      res.json({ success: true });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
};
