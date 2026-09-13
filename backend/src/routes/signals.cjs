const { Router } = require('express');

module.exports = function createSignalsRouter(database, clients, sendSSEToAll) {
  const router = Router();

  // SSE stream
  router.get('/api/signals/stream', (req, res) => {
    res.writeHead(200, {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection': 'keep-alive'
    });
    const clientId = Date.now();
    clients.push({ id: clientId, res });
    res.write(`event: initial\ndata: ${JSON.stringify({
      signals: database.signals,
      watchlist: database.watchlist,
      powerTrades: database.powerTrades,
      winners: database.winners,
      history: database.history,
      paper: database.paper,
      livePrices: database.livePrices,
      marketIntel: database.marketIntel
    })}\n\n`);
    req.on('close', () => {
      const idx = clients.findIndex(c => c.id === clientId);
      if (idx >= 0) clients.splice(idx, 1);
    });
  });

  // REST data endpoints
  router.get('/api/signals', (req, res) => res.json(database.signals));
  router.get('/api/watchlist', (req, res) => res.json(database.watchlist));
  router.get('/api/power-trades', (req, res) => res.json(database.powerTrades));
  router.get('/api/winners', (req, res) => res.json(database.winners));
  router.get('/api/history', (req, res) => res.json(database.history));
  router.get('/api/prices', (req, res) => res.json(database.livePrices));
  router.get('/api/paper/account', (req, res) => res.json(database.paper.account));
  router.get('/api/paper/trades', (req, res) => res.json({
    pending: database.paper.pendingTrades,
    active: database.paper.activeTrades,
    closed: database.paper.closedTrades
  }));
  router.get('/api/market/:symbol/history', (req, res) => {
    const sym = req.params.symbol;
    const intel = database.marketIntel[sym];
    if (!intel) return res.status(404).json({ error: 'Symbol not tracked' });
    res.json({
      symbol: sym,
      history: intel.history || { prices: [], funding: [], oi: [] },
      orderbook: intel.orderbook || null,
      oi: intel.oi || null,
      funding: intel.funding || null
    });
  });

  // Signal action
  router.post('/api/signals/:id/action', async (req, res) => {
    const { id } = req.params;
    const { action } = req.body;
    if (!['Executed', 'Ignored'].includes(action)) {
      return res.status(400).json({ error: 'Invalid action type' });
    }
    const sig = database.signals.find(s => s.id === id);
    if (!sig) return res.status(404).json({ error: 'Signal not found' });
    res.json({ success: true });
  });

  return router;
};
