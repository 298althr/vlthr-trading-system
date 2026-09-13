import { useState, useEffect, useRef } from 'react';
import { API_BASE } from '../config';
import Logo from '../components/shared/Logo';
import { Wallet, X, Activity, Zap } from 'lucide-react';

interface DemoPageProps {
  livePrices: Record<string, { price: number; change24h: number }>;
}

interface BybitPosition {
  symbol: string;
  side: string;
  size: string;
  avgPrice: string;
  positionValue: string;
  unrealisedPnl: string;
  unrealisedPnlPercentage: string;
  takeProfit: string;
  stopLoss: string;
  leverage: string;
  liqPrice: string;
  createdTime: string;
  updatedTime: string;
  curRealisedPnl: string;
}

interface BybitOrder {
  orderId: string;
  symbol: string;
  side: string;
  orderType: string;
  qty: string;
  price: string;
  stopOrderType: string;
  orderStatus: string;
  createdTime: string;
}

interface BybitClosedPnl {
  symbol: string;
  side: string;
  qty: string;
  closedPnl: string;
  avgEntryPrice: string;
  avgExitPrice: string;
  createdTime: string;
  updatedTime: string;
  execType: string;
}

interface BybitAccountData {
  connected: boolean;
  mode: string;
  balance: string;
  equity: string;
  available: string;
  margin_used: string;
  unrealized_pnl: string;
  open_positions: BybitPosition[];
  open_orders: BybitOrder[];
  closed_count: number;
  wins: number;
  win_rate: string;
  total_pnl_usd: string;
  avg_pnl_pct: string;
  best_trade: string;
  worst_trade: string;
  closed_pnl_list: BybitClosedPnl[];
}

interface PaperSummary {
  balance: string;
  equity: string;
  margin_used: string;
  realized_pnl: string;
}

interface AccountResponse {
  bybit: BybitAccountData;
  paper: PaperSummary;
}

const BYBIT_ORANGE = '#F7A600';

const fmtUSD = (v: string | number | null | undefined, decimals = 2) => {
  if (v == null) return '\u2014';
  const n = typeof v === 'string' ? parseFloat(v) : v;
  if (isNaN(n)) return '\u2014';
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`;
};

const fmtNum = (v: string | number | null | undefined, decimals = 4) => {
  if (v == null) return '\u2014';
  const n = typeof v === 'string' ? parseFloat(v) : v;
  if (isNaN(n)) return '\u2014';
  return n.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
};

const fmtPrice = (v: string | number | null | undefined) => {
  if (v == null) return '\u2014';
  const n = typeof v === 'string' ? parseFloat(v) : v;
  if (isNaN(n)) return '\u2014';
  return `$${n.toLocaleString('en-US', { maximumFractionDigits: n < 1 ? 6 : 2 })}`;
};

const fmtTime = (s: string | null | undefined) => {
  if (!s) return '\u2014';
  try {
    const ts = parseInt(s);
    if (isNaN(ts)) return new Date(s).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    return new Date(ts).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch { return '\u2014'; }
};

const pnlColor = (v: number) => v >= 0 ? 'var(--success)' : 'var(--danger)';
const pnlSign = (v: number) => v >= 0 ? '+' : '';

function StatCard({ label, value, sub, color, icon: Icon }: {
  label: string;
  value: string;
  sub?: string;
  color?: string;
  icon?: React.ElementType;
}) {
  return (
    <div className="glass" style={{ padding: 14, borderRadius: 'var(--radius-md)', flex: 1, minWidth: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 10, fontWeight: 600, color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>
        {Icon && <Icon size={12} />} {label}
      </div>
      <div style={{ fontSize: 20, fontWeight: 700, fontFamily: 'var(--font-mono)', color: color || 'var(--text-primary)', lineHeight: 1.1 }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginTop: 4 }}>{sub}</div>}
    </div>
  );
}

export default function DemoPage({ livePrices }: DemoPageProps) {
  const [subTab, setSubTab] = useState<'positions' | 'orders' | 'closed' | 'compare'>('positions');
  const [account, setAccount] = useState<AccountResponse | null>(null);
  const [comparisons, setComparisons] = useState<any[]>([]);
  const [selectedTrade, setSelectedTrade] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const intervalsRef = useRef<ReturnType<typeof setInterval>[]>([]);

  useEffect(() => {
    const fetchAccount = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/bybit/account`);
        if (res.ok) {
          const data = await res.json();
          setAccount(data);
          setError(null);
        } else {
          const d = await res.json().catch(() => ({}));
          setError(d.error || `HTTP ${res.status}`);
        }
      } catch (e: any) {
        setError(e.message || 'Connection failed');
      }
    };

    const fetchCompare = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/bybit/compare`);
        if (res.ok) {
          const data = await res.json();
          setComparisons(data.comparisons ?? []);
        }
      } catch { /* ignore */ }
    };

    Promise.all([fetchAccount(), fetchCompare()]).finally(() => setLoading(false));

    const i1 = setInterval(fetchAccount, 5000);
    const i2 = setInterval(fetchCompare, 10000);
    intervalsRef.current = [i1, i2];

    return () => intervalsRef.current.forEach(clearInterval);
  }, []);

  const bybit = account?.bybit;
  const paper = account?.paper;

  const bybitBalance = bybit ? parseFloat(bybit.balance) : 0;
  const bybitEquity = bybit ? parseFloat(bybit.equity) : 0;
  const bybitUnrealized = bybit ? parseFloat(bybit.unrealized_pnl) : 0;
  const bybitMargin = bybit ? parseFloat(bybit.margin_used) : 0;
  const bybitAvailable = bybit ? parseFloat(bybit.available) : 0;
  const bybitTotalPnl = bybit ? parseFloat(bybit.total_pnl_usd) : 0;
  const paperPnl = paper ? parseFloat(paper.realized_pnl) : 0;

  const positions = bybit?.open_positions ?? [];
  const orders = bybit?.open_orders ?? [];
  const closedPnlList = bybit?.closed_pnl_list ?? [];

  if (loading) {
    return <div style={{ padding: 32, textAlign: 'center', opacity: 0.5 }}>Loading Bybit demo data...</div>;
  }

  return (
    <div className="content-max">
      <div className="section-title">Bybit Demo Trades</div>

      {/* Connection status bar */}
      <div className="glass" style={{ padding: '8px 14px', borderRadius: 'var(--radius-sm)', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8, fontSize: 11 }}>
        <span className={`sse-dot ${bybit?.connected ? 'connected' : 'disconnected'}`} />
        <span style={{ color: 'var(--text-secondary)', fontWeight: 600 }}>
          {bybit?.connected ? 'Bybit API Connected' : 'Bybit API Not Connected'}
        </span>
        <span style={{ color: 'var(--text-tertiary)' }}>.</span>
        <span style={{ color: BYBIT_ORANGE, fontWeight: 600, textTransform: 'uppercase' }}>{bybit?.mode || '\u2014'}</span>
        {error && (
          <>
            <span style={{ color: 'var(--text-tertiary)' }}>.</span>
            <span style={{ color: 'var(--danger)' }}>{error}</span>
          </>
        )}
      </div>

      {/* Account cards: Bybit vs Paper */}
      <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
        <div className="glass" style={{ padding: 16, borderRadius: 'var(--radius-md)', flex: 1, minWidth: 0, borderTop: `2px solid ${BYBIT_ORANGE}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 12 }}>
            <Wallet size={14} style={{ color: BYBIT_ORANGE }} /> Bybit Demo
          </div>
          <div style={{ fontSize: 26, fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', marginBottom: 8 }}>
            {fmtUSD(bybitBalance, 2)}
          </div>
          <div style={{ display: 'flex', gap: 16, fontSize: 11, flexWrap: 'wrap' }}>
            <div><div style={{ color: 'var(--text-tertiary)' }}>Equity</div><div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{fmtUSD(bybitEquity, 2)}</div></div>
            <div><div style={{ color: 'var(--text-tertiary)' }}>Available</div><div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{fmtUSD(bybitAvailable, 2)}</div></div>
            <div><div style={{ color: 'var(--text-tertiary)' }}>Margin</div><div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{fmtUSD(bybitMargin, 2)}</div></div>
            <div><div style={{ color: 'var(--text-tertiary)' }}>Unrealized</div><div style={{ color: pnlColor(bybitUnrealized), fontWeight: 600 }}>{pnlSign(bybitUnrealized)}{fmtUSD(bybitUnrealized, 2)}</div></div>
          </div>
        </div>
        <div className="glass" style={{ padding: 16, borderRadius: 'var(--radius-md)', flex: 1, minWidth: 0, borderTop: '2px solid var(--primary)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 12 }}>
            <Wallet size={14} style={{ color: 'var(--primary)' }} /> Paper Account
          </div>
          <div style={{ fontSize: 26, fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--text-primary)', marginBottom: 8 }}>
            {paper ? fmtUSD(parseFloat(paper.balance), 2) : '\u2014'}
          </div>
          <div style={{ display: 'flex', gap: 16, fontSize: 11, flexWrap: 'wrap' }}>
            <div><div style={{ color: 'var(--text-tertiary)' }}>Equity</div><div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{paper ? fmtUSD(parseFloat(paper.equity), 2) : '\u2014'}</div></div>
            <div><div style={{ color: 'var(--text-tertiary)' }}>Margin</div><div style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{paper ? fmtUSD(parseFloat(paper.margin_used), 2) : '\u2014'}</div></div>
            <div><div style={{ color: 'var(--text-tertiary)' }}>Realized PnL</div><div style={{ color: pnlColor(paperPnl), fontWeight: 600 }}>{pnlSign(paperPnl)}{fmtUSD(paperPnl, 2)}</div></div>
          </div>
        </div>
      </div>

      {/* Performance stats row */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 20, flexWrap: 'wrap' }}>
        <StatCard label="Open Positions" value={String(positions.length)} icon={Activity} color={positions.length > 0 ? BYBIT_ORANGE : 'var(--text-primary)'} />
        <StatCard label="Open Orders" value={String(orders.length)} icon={Zap} color={orders.length > 0 ? 'var(--warning)' : 'var(--text-primary)'} />
        <StatCard label="Closed Trades" value={String(bybit?.closed_count ?? 0)} color="var(--text-primary)" />
        <StatCard label="Win Rate" value={bybit ? `${bybit.win_rate}%` : '\u2014'} color={parseFloat(bybit?.win_rate || '0') >= 50 ? 'var(--success)' : 'var(--danger)'} />
        <StatCard label="Total PnL" value={bybit ? `${pnlSign(bybitTotalPnl)}${fmtUSD(bybitTotalPnl, 2)}` : '\u2014'} color={pnlColor(bybitTotalPnl)} />
        <StatCard label="Best Trade" value={bybit ? fmtUSD(parseFloat(bybit.best_trade), 2) : '\u2014'} color="var(--success)" />
        <StatCard label="Worst Trade" value={bybit ? fmtUSD(parseFloat(bybit.worst_trade), 2) : '\u2014'} color="var(--danger)" />
      </div>

      {/* Sub-tab navigation */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 20, borderBottom: '1px solid var(--glass-border)', paddingBottom: 12 }}>
        {([
          { key: 'positions' as const, label: `Positions (${positions.length})` },
          { key: 'orders' as const, label: `Orders (${orders.length})` },
          { key: 'closed' as const, label: `Closed (${closedPnlList.length})` },
          { key: 'compare' as const, label: `Compare (${comparisons.length})` },
        ]).map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setSubTab(key)}
            className={subTab === key ? 'btn-primary' : 'btn-secondary'}
            style={{ padding: '6px 14px', borderRadius: 20, fontSize: 12, fontWeight: 600 }}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Live Positions from Bybit API */}
      {subTab === 'positions' && (
        positions.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', opacity: 0.5 }}>No open positions on Bybit.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {positions.map((p, i) => {
              const size = parseFloat(p.size);
              const isLong = p.side === 'Buy';
              const entry = parseFloat(p.avgPrice);
              const unrealizedPnl = parseFloat(p.unrealisedPnl || '0');
              const unrealizedPct = parseFloat(p.unrealisedPnlPercentage || '0');
              const livePrice = livePrices[p.symbol]?.price;
              const tp = parseFloat(p.takeProfit || '0');
              const sl = parseFloat(p.stopLoss || '0');
              const leverage = parseFloat(p.leverage || '1');
              const liqPrice = parseFloat(p.liqPrice || '0');
              const posValue = parseFloat(p.positionValue || '0');
              const realisedPnl = parseFloat(p.curRealisedPnl || '0');

              const tpDist = tp > 0 ? Math.abs(tp - entry) : 0;
              const slDist = sl > 0 ? Math.abs(sl - entry) : 0;
              const priceMove = livePrice ? livePrice - entry : 0;
              const towardTP = isLong ? priceMove > 0 : priceMove < 0;
              const towardSL = isLong ? priceMove < 0 : priceMove > 0;
              let progress = 0;
              let barColor = '';
              let targetLabel = '';
              if (towardTP && tpDist > 0) {
                progress = Math.min(100, (Math.abs(priceMove) / tpDist) * 100);
                barColor = 'var(--success)';
                targetLabel = `${progress.toFixed(1)}% to TP`;
              } else if (towardSL && slDist > 0) {
                progress = Math.min(100, (Math.abs(priceMove) / slDist) * 100);
                barColor = 'var(--danger)';
                targetLabel = `${progress.toFixed(1)}% to SL`;
              }

              return (
                <div key={`${p.symbol}-${i}`} className={`trade-card ${isLong ? 'long' : 'short'}`} style={{ borderLeft: `3px solid ${BYBIT_ORANGE}` }}>
                  <div className="trade-card-header">
                    <div>
                      <div className="signal-asset"><Logo symbol={p.symbol} size={18} />{p.symbol}</div>
                      <div className="trade-card-meta">
                        {isLong ? 'LONG' : 'SHORT'} . {leverage}x . Entry {fmtPrice(entry)}
                        {livePrice && (
                          <span style={{ marginLeft: 6, color: 'var(--text-secondary)' }}>
                            {' \u2192 '} <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>{fmtPrice(livePrice)}</span>
                          </span>
                        )}
                        <span style={{ marginLeft: 8, opacity: 0.5 }}>{fmtTime(p.createdTime)}</span>
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 16, fontWeight: 700, fontFamily: 'var(--font-mono)', color: pnlColor(unrealizedPnl) }}>
                        {pnlSign(unrealizedPnl)}{fmtUSD(unrealizedPnl, 2)}
                      </div>
                      <div style={{ fontSize: 11, color: pnlColor(unrealizedPnl), fontFamily: 'var(--font-mono)' }}>
                        {pnlSign(unrealizedPct)}{unrealizedPct.toFixed(2)}%
                      </div>
                    </div>
                  </div>

                  {livePrice && (tpDist > 0 || slDist > 0) && (
                    <div style={{ margin: '0 var(--space-lg) var(--space-md)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', marginBottom: 3 }}>
                        <span>Entry {fmtPrice(entry)}</span>
                        <span style={{ color: barColor }}>{targetLabel}</span>
                      </div>
                      <div style={{ height: 4, borderRadius: 2, background: 'var(--glass-border)', overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${progress}%`, background: barColor, borderRadius: 2, transition: 'width 0.4s ease' }} />
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', marginTop: 2 }}>
                        <span style={{ color: 'var(--danger)' }}>SL {sl > 0 ? fmtPrice(sl) : '\u2014'}</span>
                        <span style={{ color: 'var(--success)' }}>TP {tp > 0 ? fmtPrice(tp) : '\u2014'}</span>
                      </div>
                    </div>
                  )}

                  <div className="trade-metric-row wide">
                    <div className="trade-metric-cell">
                      <div className="trade-metric-label">Size</div>
                      <div className="trade-metric-value">{fmtNum(size, 4)}</div>
                    </div>
                    <div className="trade-metric-cell">
                      <div className="trade-metric-label">Position Value</div>
                      <div className="trade-metric-value">{fmtUSD(posValue, 2)}</div>
                    </div>
                    <div className="trade-metric-cell">
                      <div className="trade-metric-label">Liq. Price</div>
                      <div className="trade-metric-value" style={{ color: liqPrice > 0 ? 'var(--danger)' : 'var(--text-tertiary)' }}>{liqPrice > 0 ? fmtPrice(liqPrice) : '\u2014'}</div>
                    </div>
                    <div className="trade-metric-cell">
                      <div className="trade-metric-label">Realised PnL</div>
                      <div className="trade-metric-value" style={{ color: pnlColor(realisedPnl) }}>{pnlSign(realisedPnl)}{fmtUSD(realisedPnl, 2)}</div>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )
      )}

      {/* Open Orders from Bybit API */}
      {subTab === 'orders' && (
        orders.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', opacity: 0.5 }}>No open orders on Bybit.</div>
        ) : (
          <div className="history-container">
            <table className="history-table">
              <thead>
                <tr><th>Symbol</th><th>Side</th><th>Type</th><th>Qty</th><th>Price</th><th>Stop Type</th><th>Status</th><th>Time</th></tr>
              </thead>
              <tbody>
                {orders.map((o, i) => (
                  <tr key={o.orderId || i} className="hover-lift">
                    <td style={{ fontWeight: 600 }}><Logo symbol={o.symbol} size={16} />{o.symbol}</td>
                    <td style={{ color: o.side === 'Buy' ? 'var(--success)' : 'var(--danger)' }}>{o.side}</td>
                    <td style={{ fontSize: 11, opacity: 0.7 }}>{o.orderType}</td>
                    <td style={{ fontSize: 11 }}>{fmtNum(parseFloat(o.qty), 4)}</td>
                    <td style={{ fontSize: 11, fontFamily: 'var(--font-mono)' }}>{o.price !== '0' ? fmtPrice(parseFloat(o.price)) : 'Market'}</td>
                    <td style={{ fontSize: 11, opacity: 0.7 }}>{o.stopOrderType || '\u2014'}</td>
                    <td style={{ fontSize: 11 }}>
                      <span style={{ padding: '2px 6px', borderRadius: 4, background: o.orderStatus === 'New' ? 'rgba(247,166,0,0.15)' : 'rgba(255,255,255,0.06)', color: o.orderStatus === 'New' ? BYBIT_ORANGE : 'var(--text-secondary)', fontWeight: 600 }}>{o.orderStatus}</span>
                    </td>
                    <td style={{ fontSize: 11, opacity: 0.7 }}>{fmtTime(o.createdTime)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}

      {/* Closed PnL from Bybit API */}
      {subTab === 'closed' && (
        closedPnlList.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', opacity: 0.5 }}>No closed trades on Bybit yet.</div>
        ) : (
          <div className="history-container">
            <table className="history-table">
              <thead>
                <tr><th>Symbol</th><th>Side</th><th>Qty</th><th>Entry</th><th>Exit</th><th style={{ textAlign: 'right' }}>PnL $</th><th>Time</th></tr>
              </thead>
              <tbody>
                {closedPnlList.map((t, i) => {
                  const pnl = parseFloat(t.closedPnl || '0');
                  const entry = parseFloat(t.avgEntryPrice || '0');
                  const exit = parseFloat(t.avgExitPrice || '0');
                  return (
                    <tr key={i} onClick={() => setSelectedTrade(t)} style={{ cursor: 'pointer' }} className="hover-lift">
                      <td style={{ fontWeight: 600 }}><Logo symbol={t.symbol} size={16} />{t.symbol}</td>
                      <td style={{ color: t.side === 'Buy' ? 'var(--success)' : 'var(--danger)' }}>{t.side === 'Buy' ? 'LONG' : 'SHORT'}</td>
                      <td style={{ fontSize: 11 }}>{fmtNum(parseFloat(t.qty), 4)}</td>
                      <td style={{ fontSize: 11, fontFamily: 'var(--font-mono)' }}>{fmtPrice(entry)}</td>
                      <td style={{ fontSize: 11, fontFamily: 'var(--font-mono)' }}>{fmtPrice(exit)}</td>
                      <td style={{ textAlign: 'right', color: pnlColor(pnl), fontWeight: 600 }}>{pnlSign(pnl)}{fmtUSD(pnl, 2)}</td>
                      <td style={{ fontSize: 11, opacity: 0.7 }}>{fmtTime(t.updatedTime)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )
      )}

      {/* Compare tab: DB-tracked dual execution */}
      {subTab === 'compare' && (
        <div className="history-container">
          <table className="history-table">
            <thead>
              <tr>
                <th>Symbol</th><th>Side</th><th>Status</th>
                <th style={{ textAlign: 'right' }}>Paper PnL %</th>
                <th style={{ textAlign: 'right' }}>Bybit PnL %</th>
                <th style={{ textAlign: 'right' }}>Delta</th>
              </tr>
            </thead>
            <tbody>
              {comparisons.length === 0 ? (
                <tr><td colSpan={6} style={{ textAlign: 'center', padding: 24, opacity: 0.5 }}>No comparison data. Trades will appear here once the pipeline executes on both accounts.</td></tr>
              ) : (
                comparisons.map((c, i) => {
                  const paperPnlPct = parseFloat(c.net_pnl_pct || 0);
                  const bybitPnlPct = parseFloat(c.bybit_pnl_pct || 0);
                  const delta = bybitPnlPct - paperPnlPct;
                  return (
                    <tr key={i} className="hover-lift">
                      <td style={{ fontWeight: 600 }}><Logo symbol={c.symbol} size={16} />{c.symbol}</td>
                      <td style={{ color: c.side === 'LONG' ? 'var(--success)' : 'var(--danger)' }}>{c.side}</td>
                      <td style={{ fontSize: 11, opacity: 0.7 }}>{c.status}</td>
                      <td style={{ textAlign: 'right', color: pnlColor(paperPnlPct), fontWeight: 600 }}>{pnlSign(paperPnlPct)}{paperPnlPct.toFixed(2)}%</td>
                      <td style={{ textAlign: 'right', color: pnlColor(bybitPnlPct), fontWeight: 600 }}>{pnlSign(bybitPnlPct)}{bybitPnlPct.toFixed(2)}%</td>
                      <td style={{ textAlign: 'right', color: Math.abs(delta) < 0.1 ? 'var(--text-tertiary)' : pnlColor(delta), fontWeight: 600 }}>{pnlSign(delta)}{delta.toFixed(2)}%</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Trade detail modal */}
      {selectedTrade && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 200,
            background: 'rgba(0,0,0,1)', backdropFilter: 'blur(20px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
          }}
          onClick={e => { if (e.target === e.currentTarget) setSelectedTrade(null); }}
        >
          <div className="glass" style={{ width: '100%', maxWidth: 480, maxHeight: '85vh', overflowY: 'auto', borderRadius: 'var(--radius-lg)', padding: 20, position: 'relative' }}>
            <button
              onClick={() => setSelectedTrade(null)}
              className="pressable"
              style={{ position: 'absolute', top: 12, right: 12, width: 28, height: 28, borderRadius: 6, border: 'none', background: 'rgba(255,255,255,0.08)', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            >
              <X size={14} />
            </button>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16, paddingRight: 36 }}>
              <Logo symbol={selectedTrade.symbol} size={28} />
              <div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{selectedTrade.symbol}</div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                  <span style={{ color: selectedTrade.side === 'Buy' ? 'var(--success)' : 'var(--danger)', fontWeight: 600 }}>{selectedTrade.side === 'Buy' ? 'LONG' : 'SHORT'}</span>
                  {' . '}Qty {fmtNum(parseFloat(selectedTrade.qty || 0), 4)}
                </div>
              </div>
              <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
                <div style={{ fontSize: 20, fontWeight: 700, color: pnlColor(parseFloat(selectedTrade.closedPnl || 0)) }}>
                  {pnlSign(parseFloat(selectedTrade.closedPnl || 0))}{fmtUSD(parseFloat(selectedTrade.closedPnl || 0), 2)}
                </div>
              </div>
            </div>
            <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: 12, marginBottom: 14 }}>
              <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>Trade Details</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div><div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Entry Price</div><div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{fmtPrice(parseFloat(selectedTrade.avgEntryPrice || 0))}</div></div>
                <div><div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Exit Price</div><div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{fmtPrice(parseFloat(selectedTrade.avgExitPrice || 0))}</div></div>
                <div><div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Exec Type</div><div style={{ fontSize: 12 }}>{selectedTrade.execType || '\u2014'}</div></div>
                <div><div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Closed</div><div style={{ fontSize: 12, fontFamily: 'var(--font-mono)' }}>{fmtTime(selectedTrade.updatedTime)}</div></div>
              </div>
            </div>
            <div style={{ marginTop: 16, display: 'flex', justifyContent: 'center' }}>
              <button onClick={() => setSelectedTrade(null)} className="btn-secondary" style={{ fontSize: 12, padding: '8px 24px' }}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
