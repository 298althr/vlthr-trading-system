/**
 * VTC (Decision-Weighted Performance Asset) Engine
 * Ported from Python score_engine.py + valuation_engine.py
 */

// Metric normalizers: raw value -> 0-100 scale
const NORMALIZERS = {
  sharpe: (x) => Math.min(x * 10, 100),
  sortino: (x) => Math.min(x * 10, 100),
  drawdown: (x) => Math.max(100 - x, 0),
  expectancy: (x) => Math.min((x + 1) * 50, 100),
  win_rate: (x) => x,
  capital_growth: (x) => Math.min(x * 2, 100),
  stability: (x) => x,
  risk_score: (x) => Math.max(100 - x, 0),
};

function normalizeMetric(value, metric) {
  const fn = NORMALIZERS[metric];
  return fn ? fn(value) : value;
}

function calculatePerformanceLayer(vector) {
  const metrics = {
    sharpe: normalizeMetric(vector.sharpe, 'sharpe'),
    sortino: normalizeMetric(vector.sortino, 'sortino'),
    drawdown: normalizeMetric(vector.drawdown, 'drawdown'),
    expectancy: normalizeMetric(vector.expectancy, 'expectancy'),
  };
  const weights = { sharpe: 0.35, sortino: 0.25, drawdown: 0.25, expectancy: 0.15 };
  return Object.keys(metrics).reduce((sum, k) => sum + metrics[k] * weights[k], 0);
}

function calculateRiskLayer(vector) {
  const metrics = {
    stability: normalizeMetric(vector.stability, 'stability'),
    risk_score: normalizeMetric(vector.risk_score, 'risk_score'),
  };
  const volatility = Math.max(100 - normalizeMetric(vector.drawdown, 'drawdown'), 0);
  metrics.volatility = volatility;
  const weights = { stability: 0.40, risk_score: 0.35, volatility: 0.25 };
  return Object.keys(metrics).reduce((sum, k) => sum + metrics[k] * weights[k], 0);
}

function calculateGrowthLayer(vector) {
  const metrics = {
    capital_growth: normalizeMetric(vector.capital_growth, 'capital_growth'),
    win_rate: normalizeMetric(vector.win_rate, 'win_rate'),
  };
  const weights = { capital_growth: 0.50, win_rate: 0.50 };
  return Object.keys(metrics).reduce((sum, k) => sum + metrics[k] * weights[k], 0);
}

function calculateConfidence(normalizedMetrics) {
  const values = Object.values(normalizedMetrics);
  if (!values.length) return 0.5;
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const variance = values.reduce((sum, v) => sum + (v - mean) ** 2, 0) / values.length;
  return Math.max(0, Math.min(1 - (variance / 1000), 1));
}

function calculateScore(vector) {
  const perf = calculatePerformanceLayer(vector);
  const risk = calculateRiskLayer(vector);
  const growth = calculateGrowthLayer(vector);

  const layerWeights = { performance: 0.50, risk: 0.30, growth: 0.20 };
  const score = perf * layerWeights.performance + risk * layerWeights.risk + growth * layerWeights.growth;

  const normalized = {
    sharpe: normalizeMetric(vector.sharpe, 'sharpe'),
    sortino: normalizeMetric(vector.sortino, 'sortino'),
    drawdown: normalizeMetric(vector.drawdown, 'drawdown'),
    expectancy: normalizeMetric(vector.expectancy, 'expectancy'),
    win_rate: normalizeMetric(vector.win_rate, 'win_rate'),
    capital_growth: normalizeMetric(vector.capital_growth, 'capital_growth'),
    stability: normalizeMetric(vector.stability, 'stability'),
    risk_score: normalizeMetric(vector.risk_score, 'risk_score'),
  };

  return {
    score: Math.round(score * 100) / 100,
    confidence: Math.round(calculateConfidence(normalized) * 100) / 100,
    layers: {
      performance: Math.round(perf * 100) / 100,
      risk: Math.round(risk * 100) / 100,
      growth: Math.round(growth * 100) / 100,
    },
  };
}

function calculatePrice(previousPrice, score, kFactor = 1.0) {
  const deltaScore = (score - 50) / 50;
  const newPrice = previousPrice * (1 + deltaScore * kFactor);
  return Math.max(parseFloat(newPrice.toFixed(4)), 0.01);
}

// Derive performance vector from paper trading account + closed trades
async function derivePerformanceVector(pool) {
  // Get closed trades for metrics
  const closedRes = await pool.query(`
    SELECT COUNT(*) as total,
           COUNT(CASE WHEN net_pnl_usd > 0 THEN 1 END) as wins,
           SUM(net_pnl_usd) as total_pnl,
           AVG(net_pnl_usd) as avg_pnl,
           MAX(ABS(net_pnl_usd)) as max_pnl
    FROM paper_trades
    WHERE status = 'CLOSED'
  `).catch(() => ({ rows: [{}] }));

  const closed = closedRes.rows[0] || {};
  const totalTrades = parseInt(closed.total || 0);
  const winCount = parseInt(closed.wins || 0);

  // Get paper account for equity/growth
  const acctRes = await pool.query(`
    SELECT balance, equity, wallet_balance, total_pnl_usd, total_trades
    FROM paper_account LIMIT 1
  `).catch(() => ({ rows: [{}] }));
  const acct = acctRes.rows[0] || {};

  // Get all closed trade PnLs for Sharpe/Sortino estimation
  const pnlRes = await pool.query(`
    SELECT net_pnl_usd FROM paper_trades WHERE status = 'CLOSED' ORDER BY exit_time_utc DESC LIMIT 50
  `).catch(() => ({ rows: [] }));
  const pnls = pnlRes.rows.map(r => parseFloat(r.net_pnl_usd || 0));

  // Calculate metrics
  const winRate = totalTrades > 0 ? (winCount / totalTrades) * 100 : 0;
  const avgReturn = pnls.length > 0 ? pnls.reduce((a, b) => a + b, 0) / pnls.length : 0;

  // Simple Sharpe approximation: mean / stddev of returns (annualized-ish)
  let sharpe = 0;
  let sortino = 0;
  if (pnls.length > 1) {
    const mean = avgReturn;
    const variance = pnls.reduce((sum, r) => sum + (r - mean) ** 2, 0) / pnls.length;
    const stddev = Math.sqrt(variance) || 1;
    const downsideVariance = pnls.filter(r => r < 0).reduce((sum, r) => sum + r ** 2, 0) / (pnls.filter(r => r < 0).length || 1);
    const downsideDev = Math.sqrt(downsideVariance) || 1;
    sharpe = mean / stddev;
    sortino = mean / downsideDev;
  }

  // Drawdown: estimate from max peak-to-trough
  let maxDrawdown = 0;
  if (pnls.length > 0) {
    let peak = 0;
    let running = 0;
    for (const pnl of pnls) {
      running += pnl;
      if (running > peak) peak = running;
      const dd = peak > 0 ? ((peak - running) / peak) * 100 : 0;
      if (dd > maxDrawdown) maxDrawdown = dd;
    }
  }

  // Expectancy: average return per trade (normalized roughly)
  const expectancy = avgReturn / 100; // rough scale

  // Capital growth from starting balance
  const seed = 10000;
  const equity = parseFloat(acct.equity || acct.balance || seed);
  const capitalGrowth = ((equity - seed) / seed) * 100;

  // Stability: inverse of coefficient of variation
  let stability = 50;
  if (pnls.length > 1 && avgReturn !== 0) {
    const variance = pnls.reduce((sum, r) => sum + (r - avgReturn) ** 2, 0) / pnls.length;
    const stddev = Math.sqrt(variance);
    const cv = stddev / Math.abs(avgReturn);
    stability = Math.max(0, Math.min(100 - cv * 10, 100));
  }

  // Risk score based on drawdown + trade frequency
  const riskScore = Math.min(maxDrawdown * 1.5 + (totalTrades > 0 ? 10 : 0), 100);

  return {
    timestamp: new Date().toISOString(),
    sharpe: Math.max(0, parseFloat(sharpe.toFixed(2))),
    sortino: Math.max(0, parseFloat(sortino.toFixed(2))),
    drawdown: parseFloat(maxDrawdown.toFixed(2)),
    expectancy: parseFloat(expectancy.toFixed(4)),
    win_rate: parseFloat(winRate.toFixed(2)),
    capital_growth: parseFloat(capitalGrowth.toFixed(2)),
    stability: parseFloat(stability.toFixed(2)),
    risk_score: parseFloat(Math.max(0, Math.min(riskScore, 100)).toFixed(2)),
  };
}

module.exports = {
  calculateScore,
  calculatePrice,
  derivePerformanceVector,
};
