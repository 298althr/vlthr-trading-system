/**
 * VTC Historical Backfill
 * Computes cumulative performance vectors from every closed trade,
 * rebuilding the full VTC price/score history from first trade to now.
 *
 * Usage: node backend/vtc-backfill.cjs
 */

const pool = require('./db.cjs');
const { calculateScore, calculatePrice } = require('./vtc-engine.cjs');

const SEED = 10000;
const BASE_PRICE = 1.0;

async function backfill() {
  console.log('[VTC Backfill] Starting historical reconstruction…');

  // 1. Get all closed trades in chronological order
  const tradesRes = await pool.query(`
    SELECT id, symbol, net_pnl_usd, exit_time_utc
    FROM paper_trades
    WHERE status = 'CLOSED'
      AND net_pnl_usd IS NOT NULL
    ORDER BY exit_time_utc ASC, id ASC
  `);

  const trades = tradesRes.rows.map(r => ({
    id: r.id,
    symbol: r.symbol,
    pnl: parseFloat(r.net_pnl_usd || 0),
    closedAt: new Date(r.exit_time_utc),
  }));

  console.log(`[VTC Backfill] Found ${trades.length} closed trades`);
  if (trades.length === 0) {
    console.log('[VTC Backfill] No trades found. Exiting.');
    await pool.end();
    return;
  }

  // Clear existing VTC data (we're rebuilding from scratch)
  console.log('[VTC Backfill] Clearing existing VTC data…');
  await pool.query(`TRUNCATE vtc_vectors, vtc_scores, vtc_prices, vtc_audit_log RESTART IDENTITY CASCADE`);

  // 2. Walk through trades cumulatively
  let cumulativePnL = [];
  let peakEquity = SEED;
  let runningEquity = SEED;
  let prevPrice = BASE_PRICE;

  for (let i = 0; i < trades.length; i++) {
    const trade = trades[i];
    cumulativePnL.push(trade.pnl);
    runningEquity += trade.pnl;
    if (runningEquity > peakEquity) peakEquity = runningEquity;

    // Compute metrics from cumulative trades up to this point
    const totalTrades = cumulativePnL.length;
    const wins = cumulativePnL.filter(p => p > 0).length;
    const winRate = totalTrades > 0 ? (wins / totalTrades) * 100 : 0;
    const avgReturn = cumulativePnL.reduce((a, b) => a + b, 0) / totalTrades;
    const totalPnl = cumulativePnL.reduce((a, b) => a + b, 0);

    // Sharpe / Sortino
    let sharpe = 0, sortino = 0;
    if (totalTrades > 1) {
      const variance = cumulativePnL.reduce((sum, r) => sum + (r - avgReturn) ** 2, 0) / totalTrades;
      const stddev = Math.sqrt(variance) || 1;
      const downsidePnL = cumulativePnL.filter(r => r < 0);
      const downsideVar = downsidePnL.length > 0
        ? downsidePnL.reduce((sum, r) => sum + r ** 2, 0) / downsidePnL.length
        : 0.01; // tiny non-zero to avoid div/0
      const downsideDev = Math.sqrt(downsideVar) || 0.01;
      sharpe = avgReturn / stddev;
      sortino = avgReturn / downsideDev;
    }

    // Drawdown from equity curve
    const maxDrawdown = peakEquity > 0 ? ((peakEquity - runningEquity) / peakEquity) * 100 : 0;

    // Expectancy
    const expectancy = avgReturn / 100;

    // Capital growth
    const capitalGrowth = ((runningEquity - SEED) / SEED) * 100;

    // Stability (inverse CV)
    let stability = 50;
    if (totalTrades > 1 && avgReturn !== 0) {
      const variance = cumulativePnL.reduce((sum, r) => sum + (r - avgReturn) ** 2, 0) / totalTrades;
      const stddev = Math.sqrt(variance);
      const cv = stddev / Math.abs(avgReturn);
      stability = Math.max(0, Math.min(100 - cv * 10, 100));
    }

    // Risk score
    const riskScore = Math.min(maxDrawdown * 1.5 + (totalTrades > 0 ? 10 : 0), 100);

    const vector = {
      timestamp: trade.closedAt.toISOString(),
      sharpe: Math.max(0, parseFloat(sharpe.toFixed(2))),
      sortino: Math.max(0, parseFloat(sortino.toFixed(2))),
      drawdown: parseFloat(maxDrawdown.toFixed(2)),
      expectancy: parseFloat(expectancy.toFixed(4)),
      win_rate: parseFloat(winRate.toFixed(2)),
      capital_growth: parseFloat(capitalGrowth.toFixed(2)),
      stability: parseFloat(stability.toFixed(2)),
      risk_score: parseFloat(Math.max(0, Math.min(riskScore, 100)).toFixed(2)),
    };

    // Compute score & price
    const scoreResult = calculateScore(vector);
    const newPrice = calculatePrice(prevPrice, scoreResult.score);

    // Insert vector
    const vecRes = await pool.query(`
      INSERT INTO vtc_vectors (timestamp, sharpe, sortino, drawdown, expectancy, win_rate, capital_growth, stability, risk_score)
      VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING id
    `, [vector.timestamp, vector.sharpe, vector.sortino, vector.drawdown, vector.expectancy, vector.win_rate, vector.capital_growth, vector.stability, vector.risk_score]);
    const vectorId = vecRes.rows[0].id;

    // Insert score
    await pool.query(`
      INSERT INTO vtc_scores (timestamp, score, confidence, performance_layer, risk_layer, growth_layer)
      VALUES ($1,$2,$3,$4,$5,$6)
    `, [vector.timestamp, scoreResult.score, scoreResult.confidence, scoreResult.layers.performance, scoreResult.layers.risk, scoreResult.layers.growth]);

    // Insert price
    await pool.query(`
      INSERT INTO vtc_prices (timestamp, price, score) VALUES ($1,$2,$3)
    `, [vector.timestamp, newPrice, scoreResult.score]);

    // Insert audit log
    await pool.query(`
      INSERT INTO vtc_audit_log (timestamp, vector_id, score, old_price, new_price, reason, vector_snapshot)
      VALUES ($1,$2,$3,$4,$5,$6,$7)
    `, [vector.timestamp, vectorId, scoreResult.score, prevPrice, newPrice,
        `Trade #${i + 1} closed: ${trade.symbol} ${trade.pnl >= 0 ? '+' : ''}${trade.pnl.toFixed(2)} USD. Score: ${scoreResult.score.toFixed(2)}`,
        JSON.stringify(vector)]);

    prevPrice = newPrice;

    if ((i + 1) % 10 === 0) {
      console.log(`[VTC Backfill] Processed ${i + 1}/${trades.length} trades. Price: ${newPrice.toFixed(4)} Score: ${scoreResult.score.toFixed(2)}`);
    }
  }

  // 3. Insert a final "current" vector at now() using all historical + current account state
  const now = new Date().toISOString();
  const finalPnLRes = await pool.query(`
    SELECT net_pnl_usd FROM paper_trades WHERE status = 'CLOSED' ORDER BY exit_time_utc DESC
  `);
  const allPnLs = finalPnLRes.rows.map(r => parseFloat(r.net_pnl_usd || 0));
  const finalTotal = allPnLs.length;
  const finalWins = allPnLs.filter(p => p > 0).length;
  const finalWinRate = finalTotal > 0 ? (finalWins / finalTotal) * 100 : 0;
  const finalAvg = allPnLs.reduce((a, b) => a + b, 0) / (finalTotal || 1);
  const finalEquity = SEED + allPnLs.reduce((a, b) => a + b, 0);

  const finalVector = {
    timestamp: now,
    sharpe: 0, sortino: 0, drawdown: 0, expectancy: 0,
    win_rate: finalWinRate,
    capital_growth: ((finalEquity - SEED) / SEED) * 100,
    stability: 50, risk_score: 0,
  };

  // Recompute with latest data using derivePerformanceVector logic
  const latestScore = calculateScore(finalVector);
  const latestPrice = calculatePrice(prevPrice, latestScore.score);

  await pool.query(`
    INSERT INTO vtc_vectors (timestamp, sharpe, sortino, drawdown, expectancy, win_rate, capital_growth, stability, risk_score)
    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
  `, [now, finalVector.sharpe, finalVector.sortino, finalVector.drawdown, finalVector.expectancy, finalVector.win_rate, finalVector.capital_growth, finalVector.stability, finalVector.risk_score]);

  await pool.query(`
    INSERT INTO vtc_scores (timestamp, score, confidence, performance_layer, risk_layer, growth_layer)
    VALUES ($1,$2,$3,$4,$5,$6)
  `, [now, latestScore.score, latestScore.confidence, latestScore.layers.performance, latestScore.layers.risk, latestScore.layers.growth]);

  await pool.query(`
    INSERT INTO vtc_prices (timestamp, price, score) VALUES ($1,$2,$3)
  `, [now, latestPrice, latestScore.score]);

  console.log(`[VTC Backfill] Complete! Final price: ${latestPrice.toFixed(4)}, Final score: ${latestScore.score.toFixed(2)}, Total vectors: ${trades.length + 1}`);

  await pool.end();
  process.exit(0);
}

backfill().catch(err => {
  console.error('[VTC Backfill Error]', err.message);
  process.exit(1);
});
