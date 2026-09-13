const { Router } = require('express');

module.exports = function createPaperRouter(pool, syncFromPostgres, sendTelegramAlert, database) {
  const router = Router();

  // Helper: mark signal_state as TRADING when a live trade opens
  async function markSignalTrading(symbol) {
    try {
      await pool.query(
        `UPDATE signal_state SET status = 'TRADING', updated_at = NOW() WHERE symbol = $1 AND status IN ('ACTIVE', 'BOOSTED')`,
        [symbol]
      );
    } catch (e) {
      // signal_state may not have a row for this symbol yet
    }
  }

  // Helper: record trade in risk_ledger for portfolio tracking
  async function recordRiskLedger(symbol, tradeId, entryPrice, slPrice, qty, balance) {
    if (!slPrice || slPrice <= 0 || qty <= 0 || !balance || balance <= 0) return;
    const dollarRisk = Math.abs(entryPrice - slPrice) * qty;
    const riskPct = (dollarRisk / balance) * 100;
    try {
      // Check if already exists
      const existing = await pool.query('SELECT 1 FROM risk_ledger WHERE trade_id = $1 LIMIT 1', [tradeId]);
      if (existing.rows.length === 0) {
        await pool.query(`
          INSERT INTO risk_ledger (symbol, trade_id, entry_price, sl_price, qty_contracts, dollar_risk, risk_pct_of_account, is_open)
          VALUES ($1, $2, $3, $4, $5, $6, $7, TRUE)
        `, [symbol, tradeId, entryPrice, slPrice, qty, dollarRisk.toFixed(2), riskPct.toFixed(4)]);
        console.log(`[RiskLedger] Recorded trade ${tradeId} — ${symbol} risk=$${dollarRisk.toFixed(2)}`);
      }
    } catch (e) {
      console.error('[RiskLedger] Failed to record:', e.message);
    }
  }

  router.post('/api/paper/trade', async (req, res) => {
    const { signalId, symbol, side, entryPrice, qty, leverage, riskPercent } = req.body;
    try {
      const accRes = await pool.query('SELECT * FROM paper_account LIMIT 1');
      const account = accRes.rows[0];
      if (!account) return res.status(400).json({ error: 'No paper account' });

      const actualLev = parseFloat(leverage) || 4;
      let actualQty = parseFloat(qty) || 0.1;

      const rp = parseFloat(riskPercent);
      // Only recalculate qty from riskPercent if no valid qty was explicitly provided
      if ((!qty || actualQty <= 0) && riskPercent && !isNaN(rp) && rp > 0) {
        const riskAmount = parseFloat(account.balance) * (rp / 100);
        const slDist = 0.01;
        actualQty = riskAmount / (entryPrice * slDist);
      }

      let marginRequired = (entryPrice * actualQty) / actualLev;
      let notional = entryPrice * actualQty;
      let openFee = notional * 0.00055;
      const freeBalance = parseFloat(account.balance) - parseFloat(account.margin_used || 0);

      // ── Enforce per-symbol allocation cap ($1,667) ──
      const PER_SYMBOL_MARGIN_CAP = 1667;
      const symOpenRes = await pool.query(
        `SELECT COALESCE(SUM(margin_required), 0)::float as total FROM paper_trades WHERE symbol = $1 AND status = 'OPEN'`,
        [symbol]
      );
      const symOpenMargin = parseFloat(symOpenRes.rows[0]?.total || 0);
      const remainingCap = PER_SYMBOL_MARGIN_CAP - symOpenMargin;
      const maxAllowedMargin = Math.min(remainingCap, freeBalance);

      // Resize qty to fit within cap + free balance
      if (marginRequired > maxAllowedMargin && entryPrice > 0 && actualLev > 0) {
        actualQty = (maxAllowedMargin * actualLev) / entryPrice;
        notional = entryPrice * actualQty;
        marginRequired = notional / actualLev;
        openFee = notional * 0.00055;
      }

      if (marginRequired <= 0 || marginRequired > freeBalance) {
        return res.status(400).json({ error: `Insufficient free balance. Margin required: $${marginRequired.toFixed(2)}, Free: $${freeBalance.toFixed(2)}` });
      }

      if (parseFloat(account.balance) < openFee) {
        return res.status(400).json({ error: `Insufficient balance for open fee ($${openFee.toFixed(2)})` });
      }

      const symCountRes = await pool.query(`SELECT COUNT(*) as open_count FROM paper_trades WHERE status = 'OPEN' AND symbol = $1`, [symbol]);
      const openCount = parseInt(symCountRes.rows[0]?.open_count || 0);
      if (openCount >= 2) {
        return res.status(400).json({ error: `Max 2 open positions on ${symbol} reached. Close one first.` });
      }

      if (signalId) {
        const sigNumId = parseInt(String(signalId).replace('HCS-', ''));
        const pendingRes = await pool.query(`SELECT id, entry_price_planned FROM paper_trades WHERE signal_id = $1 AND status = 'PENDING' LIMIT 1`, [sigNumId]);

        if (pendingRes.rows.length > 0) {
          const pendingId = pendingRes.rows[0].id;
          const plannedPrice = parseFloat(pendingRes.rows[0].entry_price_planned || 0);

          // Drift guard on promotion: if market moved >0.3% from planned entry, reject
          const livePrice = database?.livePrices?.[symbol]?.price || 0;
          if (livePrice > 0 && plannedPrice > 0) {
            const drift = Math.abs(livePrice - plannedPrice) / plannedPrice;
            if (drift > 0.003) {
              return res.status(400).json({
                error: `Entry stale: market moved ${(drift*100).toFixed(2)}% from planned price ($${plannedPrice}). Live: $${livePrice.toFixed(4)}. Signal too old — re-scan for fresh entry.`,
                livePrice, plannedPrice, driftPct: (drift*100).toFixed(2)
              });
            }
          }

          const pendingRow = await pool.query(`SELECT entry_fee, margin_required, qty_contracts, leverage, sl_price, tp_price FROM paper_trades WHERE id = $1`, [pendingId]);
          const pt = pendingRow.rows[0] || {};
          let openFee2 = parseFloat(pt.entry_fee || entryPrice * actualQty * 0.00055);
          let marginReq = parseFloat(pt.margin_required || marginRequired);
          let qty2 = parseFloat(pt.qty_contracts || actualQty);

          // Re-apply per-symbol cap on promotion too
          if (marginReq > maxAllowedMargin && entryPrice > 0 && actualLev > 0) {
            qty2 = (maxAllowedMargin * actualLev) / entryPrice;
            const notional2 = entryPrice * qty2;
            marginReq = notional2 / actualLev;
            openFee2 = notional2 * 0.00055;
          }
          const lev2 = parseFloat(pt.leverage || actualLev);
          const notional2 = entryPrice * qty2;
          const slPrice = parseFloat(pt.sl_price || 0);
          const tpPrice = parseFloat(pt.tp_price || 0);

          await pool.query(`UPDATE paper_trades SET status = 'OPEN', side = $1, entry_price_actual = $2, entry_fee = $3, margin_required = $4, leveraged_notional = $5, sl_price = $6, tp_price = $7, updated_at = NOW() WHERE id = $8`,
            [side, entryPrice, openFee2, marginReq, notional2 * lev2, slPrice, tpPrice, pendingId]);
          await pool.query(`UPDATE paper_account SET balance = balance - $1, margin_used = margin_used + $2 WHERE id = $3`,
            [openFee2, marginReq, account.id]);
          const newBal = parseFloat(account.balance) - openFee2;
          await pool.query(`INSERT INTO paper_trade_statements (trade_id, action, amount, balance_after, note) VALUES ($1, 'ENTRY_FEE', $2, $3, $4)`,
            [pendingId, -openFee2, newBal, `${side} ${symbol} activated (fee=${openFee2.toFixed(2)})`]);
          // Track in risk ledger
          await recordRiskLedger(symbol, pendingId, entryPrice, slPrice, qty2, parseFloat(account.balance));
          await markSignalTrading(symbol);
          await syncFromPostgres();
          return res.json({ success: true, promoted: true, tradeId: pendingId });
        }
      }

      // ── Live price validation: reject if market moved >0.3% from signal entry ──
      const livePrice = database?.livePrices?.[symbol]?.price || 0;
      if (livePrice > 0 && entryPrice > 0) {
        const drift = Math.abs(livePrice - entryPrice) / entryPrice;
        if (drift > 0.003) {
          return res.status(400).json({
            error: `Entry stale: market moved ${(drift*100).toFixed(2)}% from signal price ($${entryPrice}). Live: $${livePrice.toFixed(4)}. Signal too old — re-scan for fresh entry.`,
            livePrice,
            signalPrice: entryPrice,
            driftPct: (drift*100).toFixed(2)
          });
        }
      }

      let sigRow;
      if (signalId) {
        const sigNumId = parseInt(String(signalId).replace('HCS-', ''));
        const specificRes = await pool.query(`SELECT id, signal_bar_utc, scan_time_utc, is_expired, expiry_reason, sl_price, tp_price, confidence, signal_strength FROM high_confidence_signals WHERE id = $1 LIMIT 1`, [sigNumId]);
        sigRow = specificRes.rows[0];
      }
      if (!sigRow) {
        const fallbackRes = await pool.query(`SELECT id, signal_bar_utc, scan_time_utc, is_expired, expiry_reason, sl_price, tp_price, confidence, signal_strength FROM high_confidence_signals WHERE symbol = $1 ORDER BY signal_bar_utc DESC LIMIT 1`, [symbol]);
        sigRow = fallbackRes.rows[0];
      }
      if (sigRow && sigRow.is_expired) {
        return res.status(400).json({ error: `Signal expired: ${sigRow.expiry_reason || 'Market conditions changed'}` });
      }
      const signalDbId = sigRow ? sigRow.id : null;
      const barTs = sigRow ? sigRow.signal_bar_utc : new Date();
      const scanTs = sigRow ? sigRow.scan_time_utc : new Date();

      const sigSl = sigRow ? parseFloat(sigRow.sl_price || 0) : 0;
      const sigTp = sigRow ? parseFloat(sigRow.tp_price || 0) : 0;

      // ── Per-symbol margin cap ──
      const PER_SYMBOL_ALLOCATION = 1667;
      const symMarginRes = await pool.query(
        `SELECT COALESCE(SUM(margin_required), 0)::float as total FROM paper_trades WHERE symbol = $1 AND status = 'OPEN'`,
        [symbol]
      );
      const symMargin = parseFloat(symMarginRes.rows[0]?.total || 0);
      if (symMargin + marginRequired > PER_SYMBOL_ALLOCATION) {
        return res.status(400).json({
          error: `Per-symbol allocation exceeded for ${symbol}. Current $${symMargin.toFixed(2)} + new $${marginRequired.toFixed(2)} > cap $${PER_SYMBOL_ALLOCATION}. Close an existing ${symbol} trade first.`
        });
      }

      const sigConfidence = sigRow ? parseInt(sigRow.confidence || 0) : 0;
      const tradeRes = await pool.query(`INSERT INTO paper_trades (signal_id, symbol, signal_bar_utc, scan_time_utc, side, entry_price_planned, entry_price_actual, sl_price, tp_price, qty_contracts, leveraged_notional, confidence, status, notes) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'OPEN',$13) RETURNING *`,
        [signalDbId, symbol, barTs, scanTs, side, entryPrice, entryPrice, sigSl, sigTp, actualQty, entryPrice * actualQty * actualLev, sigConfidence, `Paper ${side} on ${symbol}`]);

      const newTrade = tradeRes.rows[0];
      await pool.query(`UPDATE paper_trades SET margin_required = $1, entry_fee = $2 WHERE id = $3`, [marginRequired, openFee, newTrade.id]);
      await pool.query(`INSERT INTO paper_trade_statements (trade_id, action, amount, balance_after, note) VALUES ($1,'ENTRY_FEE',$2,$3,$4)`,
        [newTrade.id, -openFee, parseFloat(account.balance) - openFee, `Open fee ${side} ${symbol}`]);
      await pool.query(`UPDATE paper_account SET balance = balance - $1, margin_used = margin_used + $2 WHERE id = $3`,
        [openFee, marginRequired, account.id]);
      // Track in risk ledger
      await recordRiskLedger(symbol, newTrade.id, entryPrice, sigSl, actualQty, parseFloat(account.balance));
      await markSignalTrading(symbol);
      await syncFromPostgres();
      res.json({ success: true, promoted: false, trade: newTrade });
    } catch (err) {
      console.error('[Paper Trade Error]', err);
      res.status(500).json({ error: err.message });
    }
  });

  router.post('/api/paper/trade/:id/close', async (req, res) => {
    const { id } = req.params;
    const { exitPrice, exitReason } = req.body;
    try {
      const tradeRes = await pool.query('SELECT * FROM paper_trades WHERE id = $1', [id]);
      const trade = tradeRes.rows[0];
      if (!trade || trade.status !== 'OPEN') {
        return res.status(400).json({ error: 'Trade not found or already closed' });
      }

      const entry = parseFloat(trade.entry_price_actual || trade.entry_price_planned);
      const qty = parseFloat(trade.qty_contracts || 0);
      const notional = entry * qty;
      const exitNotional = exitPrice * qty;
      const lev = 4;
      const isLong = (trade.side || '').toUpperCase() === 'LONG';
      const grossPct = isLong ? ((exitPrice - entry) / entry) * 100 : ((entry - exitPrice) / entry) * 100;
      const grossUsd = isLong ? (exitPrice - entry) * qty : (entry - exitPrice) * qty;
      const openFee = parseFloat(trade.entry_fee || notional * 0.00055);
      const closeFee = exitNotional * 0.00055;
      const cumulativeFunding = parseFloat(trade.funding_total || trade.cumulative_funding || trade.funding_cost || 0);
      const totalFees = openFee + closeFee + cumulativeFunding;
      const netUsd = grossUsd - totalFees;
      const marginReleased = parseFloat(trade.margin_required) || (notional / lev);
      const netPct = (netUsd / marginReleased) * 100;
      const reason = exitReason || 'MANUAL';

      const entryTs = new Date(trade.created_at);
      const now = new Date();
      const hoursHeld = (now - entryTs) / 3600000;

      await pool.query(`UPDATE paper_trades SET exit_price=$1, exit_reason=$2, exit_time_utc=NOW(), gross_pnl_pct=$3, gross_pnl_usd=$4, exit_fee=$5, fees_total=$6, net_pnl_pct=$7, net_pnl_usd=$8, hours_held=$9, status='CLOSED', updated_at=NOW() WHERE id=$10`,
        [exitPrice, reason, grossPct, grossUsd, closeFee, totalFees, netPct, netUsd, hoursHeld.toFixed(2), id]);

      const accRes = await pool.query('SELECT * FROM paper_account LIMIT 1');
      const account = accRes.rows[0];
      await pool.query(`UPDATE paper_account SET balance = balance + $1, margin_used = GREATEST(0, margin_used - $2), realized_pnl = realized_pnl + $3 WHERE id = $4`,
        [netUsd, marginReleased, netUsd, account.id]);

      // Close risk ledger entry
      try {
        await pool.query(`UPDATE risk_ledger SET is_open = FALSE, closed_at = NOW() WHERE trade_id = $1`, [id]);
      } catch (e) {
        console.error('[RiskLedger] Failed to close:', e.message);
      }

      if (reason === 'TP_HIT') {
        const sigRes = await pool.query(`SELECT * FROM high_confidence_signals WHERE id = $1`, [trade.signal_id]);
        const sig = sigRes.rows[0];
        await pool.query(`INSERT INTO winners (trade_id, signal_id, symbol, confidence, signal_strength, entry_price, exit_price, tp_price, sl_price, gross_pnl_pct, net_pnl_pct, net_pnl_usd, hours_held, exit_reason, session, h4_direction) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16) ON CONFLICT (trade_id) DO NOTHING`,
          [id, trade.signal_id, trade.symbol, sig ? sig.confidence : null, sig ? sig.signal_strength : null, entry, exitPrice, sig ? sig.tp_price : null, sig ? sig.sl_price : null, grossPct, netPct, netUsd, hoursHeld.toFixed(2), reason, sig ? sig.session : null, sig ? sig.h4_direction : null]);
        console.log(`[Winners] Recorded TP hit for trade ${id} — ${trade.symbol} +${netPct.toFixed(2)}%`);
      }

      const leverage = parseInt(trade.leverage || 4);
      const emoji = reason === 'TP_HIT' ? '🎯' : reason === 'SL_HIT' ? '🛑' : '🔒';
      const pnlEmoji = netUsd >= 0 ? '🟢' : '🔴';
      sendTelegramAlert(
        `${emoji} <b>VLTHR Trade Closed — ${reason}</b><br>` +
        `<pre>Symbol:  ${trade.symbol}\n` +
        `Side:    ${trade.side}\n` +
        `Entry:   ${entry.toFixed(2)}\n` +
        `Exit:    ${exitPrice.toFixed(2)}\n` +
        `Qty:     ${parseFloat(trade.qty_contracts || 0).toFixed(4)}\n` +
        `Leverage: ${leverage}x\n` +
        `Hours:   ${hoursHeld.toFixed(1)}\n\n` +
        `P&L %:   ${netPct.toFixed(2)}%\n` +
        `P&L $:   ${pnlEmoji} ${netUsd.toFixed(2)} USD</pre>`
      );

      await syncFromPostgres();
      res.json({ success: true });
    } catch (err) {
      console.error('[Close Trade Error]', err);
      res.status(500).json({ error: err.message });
    }
  });

  router.delete('/api/paper/trade/:id', async (req, res) => {
    const { id } = req.params;
    try {
      const tradeRes = await pool.query('SELECT status, signal_id FROM paper_trades WHERE id = $1', [id]);
      const trade = tradeRes.rows[0];
      if (!trade) return res.status(404).json({ error: 'Trade not found' });
      if (trade.status !== 'PENDING') {
        return res.status(400).json({ error: 'Only PENDING trades can be cancelled. Close OPEN trades instead.' });
      }
      // Soft dismiss: keep the row but mark cancelled, and dismiss the source signal
      await pool.query("UPDATE paper_trades SET status='CANCELLED', updated_at=NOW() WHERE id = $1", [id]);
      if (trade.signal_id) {
        await pool.query(
          "UPDATE high_confidence_signals SET is_expired=TRUE, expiry_reason='Manually cancelled by user', updated_at=NOW() WHERE id=$1",
          [trade.signal_id]
        );
      }
      await syncFromPostgres();
      res.json({ success: true, cancelled: true });
    } catch (err) {
      console.error('[Cancel Pending Trade Error]', err);
      res.status(500).json({ error: err.message });
    }
  });

  return router;
};
