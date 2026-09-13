import { useState, useRef } from 'react';
import type { PaperAccount, PaperTrade, Winner, HCSSignal } from '../types';
import Logo from '../components/shared/Logo';
import { X, ChevronLeft, ChevronRight, Wallet, TrendingUp, Shield, Trash2, Trophy } from 'lucide-react';

interface Props {
  account: PaperAccount | null;
  pendingTrades: PaperTrade[];
  activeTrades: PaperTrade[];
  closedTrades: PaperTrade[];
  winners: Winner[];
  signals: HCSSignal[];
  onActivate: (t: PaperTrade) => void;
  onClose: (t: PaperTrade) => void;
  onCancel?: (t: PaperTrade) => void;
}

function SwipeCard({ title, icon: Icon, metrics }: { title: string; icon: React.ElementType; metrics: { label: string; value: string; color?: string; sub?: string }[] }) {
  const [idx, setIdx] = useState(0);
  const touchStartX = useRef(0);
  const m = metrics[idx];
  const next = () => setIdx(i => (i + 1) % metrics.length);
  const prev = () => setIdx(i => (i - 1 + metrics.length) % metrics.length);
  return (
    <div className="glass" style={{ padding: 16, borderRadius: 'var(--radius-md)', position: 'relative', overflow: 'hidden', minHeight: 110, display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5 }}>
          <Icon size={14} /> {title}
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          <button onClick={prev} className="pressable" style={{ width: 22, height: 22, borderRadius: 4, border: 'none', background: 'rgba(255,255,255,0.06)', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><ChevronLeft size={12} /></button>
          <button onClick={next} className="pressable" style={{ width: 22, height: 22, borderRadius: 4, border: 'none', background: 'rgba(255,255,255,0.06)', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><ChevronRight size={12} /></button>
        </div>
      </div>
      <div
        onTouchStart={e => { touchStartX.current = e.changedTouches[0].screenX; }}
        onTouchEnd={e => {
          const diff = touchStartX.current - e.changedTouches[0].screenX;
          if (diff > 30) next(); else if (diff < -30) prev();
        }}
        style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}
      >
        <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginBottom: 4 }}>{m.label}</div>
        <div className="data-lg" style={{ fontSize: 26, color: m.color || 'var(--text-primary)', lineHeight: 1.1 }}>{m.value}</div>
        {m.sub && <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginTop: 4 }}>{m.sub}</div>}
      </div>
      <div style={{ display: 'flex', justifyContent: 'center', gap: 4, marginTop: 8 }}>
        {metrics.map((_, i) => (
          <div key={i} style={{ width: 6, height: 6, borderRadius: '50%', background: i === idx ? 'var(--primary)' : 'var(--glass-border)', transition: 'background 0.2s' }} />
        ))}
      </div>
    </div>
  );
}

const SYMBOL_NAMES: Record<string, string> = {
  BTCUSDT: 'Bitcoin',
  ETHUSDT: 'Ethereum',
  SOLUSDT: 'Solana',
  XRPUSDT: 'Ripple',
  BNBUSDT: 'BNB',
  DOGEUSDT: 'Dogecoin',
};

function TopContributorsCard({ closedTrades }: { closedTrades: PaperTrade[] }) {
  const [idx, setIdx] = useState(0);
  const touchStartX = useRef(0);

  const totalRealizedPnl = closedTrades.reduce((sum, t) => sum + (t.pnl || 0), 0);
  const symbolPnl = closedTrades.reduce((acc, t) => {
    acc[t.symbol] = (acc[t.symbol] || 0) + (t.pnl || 0);
    return acc;
  }, {} as Record<string, number>);

  const contributors = Object.entries(symbolPnl)
    .map(([symbol, pnl]) => ({
      symbol,
      pnl,
      share: totalRealizedPnl !== 0 ? (pnl / totalRealizedPnl) * 100 : 0,
    }))
    .sort((a, b) => b.pnl - a.pnl)
    .slice(0, 3);

  const next = () => setIdx(i => (i + 1) % Math.max(contributors.length, 1));
  const prev = () => setIdx(i => (i - 1 + Math.max(contributors.length, 1)) % Math.max(contributors.length, 1));
  const c = contributors[idx];
  const positionLabel = ['1st', '2nd', '3rd'][idx] ?? `${idx + 1}th`;

  return (
    <div className="glass" style={{ padding: 16, borderRadius: 'var(--radius-md)', position: 'relative', overflow: 'hidden', minHeight: 110, display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5 }}>
          <Trophy size={14} /> Top Contributors
        </div>
        <div style={{ display: 'flex', gap: 4 }}>
          <button onClick={prev} className="pressable" style={{ width: 22, height: 22, borderRadius: 4, border: 'none', background: 'rgba(255,255,255,0.06)', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><ChevronLeft size={12} /></button>
          <button onClick={next} className="pressable" style={{ width: 22, height: 22, borderRadius: 4, border: 'none', background: 'rgba(255,255,255,0.06)', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}><ChevronRight size={12} /></button>
        </div>
      </div>
      {contributors.length === 0 ? (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', fontSize: 12 }}>No closed trades yet</div>
      ) : (
        <div
          onTouchStart={e => { touchStartX.current = e.changedTouches[0].screenX; }}
          onTouchEnd={e => {
            const diff = touchStartX.current - e.changedTouches[0].screenX;
            if (diff > 30) next(); else if (diff < -30) prev();
          }}
          style={{ flex: 1, display: 'flex', flexDirection: 'column', justifyContent: 'center' }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <Logo symbol={c.symbol} size={28} />
            <div>
              <div className="data-lg" style={{ fontSize: 20, lineHeight: 1.1 }}>{SYMBOL_NAMES[c.symbol] ?? c.symbol}</div>
              <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>{c.symbol}</div>
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <div className="data-lg" style={{ fontSize: 22, color: c.share >= 0 ? 'var(--success)' : 'var(--danger)' }}>
              {c.share >= 0 ? '+' : ''}{c.share.toFixed(1)}%
            </div>
            <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>of total P&L</div>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-tertiary)', marginTop: 2 }}>
            {positionLabel} · {c.pnl >= 0 ? '+' : ''}${c.pnl.toFixed(2)} contribution
          </div>
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'center', gap: 4, marginTop: 8 }}>
        {contributors.map((_, i) => (
          <div key={i} style={{ width: 6, height: 6, borderRadius: '50%', background: i === idx ? 'var(--primary)' : 'var(--glass-border)', transition: 'background 0.2s' }} />
        ))}
      </div>
    </div>
  );
}

export default function PaperPage({ account, pendingTrades, activeTrades, closedTrades, winners, signals, onActivate, onClose, onCancel }: Props) {
  const [subTab, setSubTab] = useState<'pending' | 'active' | 'closed' | 'winners'>('pending');
  const [selectedTrade, setSelectedTrade] = useState<PaperTrade | null>(null);
  const [selectedWinner, setSelectedWinner] = useState<Winner | null>(null);
  const [hiddenPendingIds, setHiddenPendingIds] = useState<Set<number>>(new Set());
  const acc = account ?? { balance: 10000, wallet_balance: 10000, available: 10000, equity: 10000, margin_used: 0, total_pnl: 0, total_pnl_usd: 0, total_trades: 0, win_rate: 0, realized_pnl: 0, cumulative_funding_paid: 0 };

  // Filter out pending trades whose signal has expired OR were locally cancelled
  const expiredSignalIds = new Set(
    signals.filter(s => s.isExpired).map(s => s.dbId)
  );
  const visiblePending = pendingTrades.filter(t =>
    !expiredSignalIds.has(t.signalId ?? -1) && !hiddenPendingIds.has(t.id)
  );

  const handleCancel = (t: PaperTrade) => {
    // Local hide only — no API call, never affects win rate or closed trades
    setHiddenPendingIds(prev => new Set(prev).add(t.id));
    onCancel?.(t);
  };

  return (
    <div className="content-max">
      <div className="section-title">Paper Account</div>

      {/* ── Swipeable Metric Cards (MT5-style) ───────────────────────── */}
      <div className="kpi-grid" style={{ marginBottom: 20 }}>
        <SwipeCard
          title="Account"
          icon={Wallet}
          metrics={[
            { label: 'Wallet Balance', value: `$${parseFloat((acc.wallet_balance ?? acc.balance).toString()).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, color: 'var(--primary)', sub: 'Seed + realised PnL' },
            { label: 'Available Balance', value: `$${parseFloat(((acc as PaperAccount).available ?? acc.balance).toString()).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, color: 'var(--success)', sub: (acc as PaperAccount).cumulative_funding_paid > 0 ? `−${(acc as PaperAccount).cumulative_funding_paid.toFixed(2)} funding` : 'Free cash' },
            { label: 'Account Equity', value: `$${parseFloat((acc.equity ?? acc.balance).toString()).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`, color: ((acc as PaperAccount).equity ?? 0) >= 10000 ? 'var(--success)' : 'var(--danger)', sub: (acc as PaperAccount).live_floating_pnl != null ? `${(acc as PaperAccount).live_floating_pnl >= 0 ? '+' : ''}${(acc as PaperAccount).live_floating_pnl.toFixed(2)} floating` : 'Wallet + floating PnL' },
            { label: 'Total PnL', value: `${((acc as PaperAccount).live_total_pnl_usd ?? 0) >= 0 ? '+' : ''}$${((acc as PaperAccount).live_total_pnl_usd ?? 0).toFixed(2)}`, color: ((acc as PaperAccount).live_total_pnl_usd ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)', sub: `${acc.total_trades} closed + ${activeTrades.length} open` },
          ]}
        />
        <SwipeCard
          title="Performance"
          icon={TrendingUp}
          metrics={[
            { label: 'Win Rate', value: `${((acc as PaperAccount).live_win_rate ?? acc.win_rate).toFixed(1)}%`, color: 'var(--primary)', sub: `${acc.total_trades} closed trades` },
            { label: 'Realised PnL', value: `${(acc as PaperAccount).realized_pnl >= 0 ? '+' : ''}$${((acc as PaperAccount).realized_pnl ?? 0).toFixed(2)}`, color: ((acc as PaperAccount).realized_pnl ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)', sub: 'Closed trade total' },
            { label: 'Avg Return / Trade', value: `$${acc.total_trades > 0 ? ((acc as PaperAccount).total_pnl_usd ?? 0 / acc.total_trades).toFixed(2) : '0.00'}`, color: 'var(--primary)', sub: 'Mean net PnL' },
            { label: 'Total Trades', value: `${acc.total_trades}`, color: 'var(--text-primary)', sub: `${activeTrades.length} currently open` },
          ]}
        />
        <SwipeCard
          title="Risk"
          icon={Shield}
          metrics={[
            { label: 'Margin In Use', value: `$${parseFloat(acc.margin_used.toString()).toLocaleString('en-US', { maximumFractionDigits: 0 })}`, color: '#f5a623', sub: 'Locked (not spent)' },
            { label: 'Funding Paid', value: `−$${((acc as PaperAccount).cumulative_funding_paid ?? 0).toFixed(2)}`, color: 'var(--danger)', sub: 'Total funding cost' },
            { label: 'Floating PnL', value: `${(acc as PaperAccount).live_floating_pnl >= 0 ? '+' : ''}$${((acc as PaperAccount).live_floating_pnl ?? 0).toFixed(2)}`, color: ((acc as PaperAccount).live_floating_pnl ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)', sub: 'Open positions unrealized' },
            { label: 'Exposure', value: `${acc.margin_used > 0 ? ((acc.margin_used / (acc.equity || 1)) * 100).toFixed(1) : '0.0'}%`, color: acc.margin_used > (acc.equity || 1) * 0.5 ? 'var(--danger)' : 'var(--primary)', sub: 'Margin / Equity' },
          ]}
        />
        <TopContributorsCard closedTrades={closedTrades} />
      </div>

      {/* Sub-tab navigation */}
      <div style={{ display: 'flex', gap: 8, marginBottom: 20, borderBottom: '1px solid var(--glass-border)', paddingBottom: 12 }}>
        {([
          { key: 'pending' as const, label: `Pending (${visiblePending.length})` },
          { key: 'active' as const, label: `Open (${activeTrades.length})` },
          { key: 'closed' as const, label: `Closed (${closedTrades.length})` },
          { key: 'winners' as const, label: `Winners (${winners.length})` },
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

      {/* Pending trades */}
      {subTab === 'pending' && (
        visiblePending.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', opacity: 0.5 }}>No pending trades. Click PAPER on a signal to queue one.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {visiblePending.map(t => (
              <div key={t.id} className="trade-card" style={{ borderLeft: '3px solid var(--warning)' }}>
                <div className="trade-card-header">
                  <div>
                    <div className="signal-asset"><Logo symbol={t.symbol} size={18} />{t.symbol}</div>
                    <div className="trade-card-meta">PENDING · Conf {t.confidence}/100 · Planned entry ${t.entry_price_planned.toLocaleString()}</div>
                  </div>
                  <span className="chip chip-default" style={{ background: 'var(--warning)', color: '#0B0B0F', fontWeight: 700 }}>PENDING</span>
                </div>
                <div className="trade-metric-row">
                  <div className="trade-metric-cell"><div className="trade-metric-label">TP</div><div className="trade-metric-value" style={{ color: 'var(--success)' }}>${t.tp_price.toLocaleString()}</div></div>
                  <div className="trade-metric-cell"><div className="trade-metric-label">SL</div><div className="trade-metric-value" style={{ color: 'var(--danger)' }}>${t.sl_price.toLocaleString()}</div></div>
                  <div className="trade-metric-cell"><div className="trade-metric-label">Position Size</div><div className="trade-metric-value">${(t.leveraged_notional / (t.leverage || 4)).toLocaleString()}</div></div>
                  <div className="trade-metric-cell"><div className="trade-metric-label">Margin ({t.leverage || 4}x)</div><div className="trade-metric-value">${(t.margin || 0).toLocaleString()}</div></div>
                </div>
                {t.notes && <div className="body-sm" style={{ color: 'var(--text-secondary)', margin: '0 var(--space-lg) var(--space-md)' }}>{t.notes}</div>}
                <div className="trade-actions" style={{ gap: 8 }}>
                  <button className="btn-primary" style={{ flex: 1, fontSize: 12 }} onClick={() => onActivate(t)}>ACTIVATE</button>
                  <button
                    className="btn-secondary pressable"
                    style={{ fontSize: 12, padding: '8px 14px', color: 'var(--danger)', borderColor: 'var(--danger)', display: 'flex', alignItems: 'center', gap: 4, minWidth: 80, justifyContent: 'center' }}
                    onClick={() => handleCancel(t)}
                  >
                    <Trash2 size={12} /> Cancel
                  </button>
                </div>
              </div>
            ))}
          </div>
        )
      )}

      {/* Active trades */}
      {subTab === 'active' && (
        activeTrades.length === 0 ? (
          <div style={{ padding: '32px', textAlign: 'center', opacity: 0.5 }}>No open positions.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {activeTrades.map(t => {
              const livePnl = t.live_pnl ?? 0;
              const livePnlPct = t.live_pnl_pct ?? 0;
              const hasLive = t.live_price != null;
              const pnlColor = livePnl >= 0 ? 'var(--success)' : 'var(--danger)';
              // Margin is now correctly calculated by backend; use it directly
              const margin = t.margin || 0;
              // Progress bar: green toward TP, red toward SL (works for LONG & SHORT)
              const isLong = t.side === 'LONG';
              const tpDist = Math.abs(t.tp_price - t.entry_price);
              const slDist = Math.abs(t.sl_price - t.entry_price);
              const priceMove = hasLive ? t.live_price! - t.entry_price : 0;
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
                <div key={t.id} className={`trade-card ${t.side === 'LONG' ? 'long' : 'short'}`}>
                  <div className="trade-card-header">
                    <div>
                      <div className="signal-asset"><Logo symbol={t.symbol} size={18} />{t.symbol}</div>
                      <div className="trade-card-meta">
                        {t.side} · {t.leverage}x · Entry <span style={{ fontFamily: 'var(--font-mono)' }}>${t.entry_price.toLocaleString()}</span>
                        {hasLive && (
                          <span style={{ marginLeft: 6, color: 'var(--text-secondary)' }}>
                            → <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>${t.live_price!.toLocaleString()}</span>
                          </span>
                        )}
                        <span style={{ marginLeft: 8, opacity: 0.5 }}>{new Date(t.entry_time || '').toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                      </div>
                    </div>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: 16, fontWeight: 700, fontFamily: 'var(--font-mono)', color: pnlColor }}>
                        {livePnl >= 0 ? '+' : ''}{livePnl.toFixed(2)} USDT
                      </div>
                      <div style={{ fontSize: 11, color: pnlColor, fontFamily: 'var(--font-mono)' }}>
                        {livePnlPct >= 0 ? '+' : ''}{livePnlPct.toFixed(2)}%
                        {!hasLive && <span style={{ color: 'var(--text-secondary)', fontSize: 10 }}> (awaiting price)</span>}
                      </div>
                    </div>
                  </div>
                  {hasLive && (tpDist > 0 || slDist > 0) && (
                    <div style={{ margin: '0 var(--space-lg) var(--space-md)' }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', marginBottom: 3 }}>
                        <span>Entry ${t.entry_price.toLocaleString()}</span>
                        <span style={{ color: barColor }}>
                          {targetLabel} · {towardTP ? `TP $${t.tp_price.toLocaleString()}` : `SL $${t.sl_price.toLocaleString()}`}
                        </span>
                      </div>
                      <div style={{ height: 4, borderRadius: 2, background: 'var(--glass-border)', overflow: 'hidden' }}>
                        <div style={{ height: '100%', width: `${progress}%`, background: barColor, borderRadius: 2, transition: 'width 0.4s ease' }} />
                      </div>
                      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', marginTop: 2 }}>
                        <span style={{ color: 'var(--danger)' }}>SL ${t.sl_price.toLocaleString()}</span>
                        <span style={{ color: 'var(--success)' }}>TP ${t.tp_price.toLocaleString()}</span>
                      </div>
                    </div>
                  )}
                  <div className="trade-metric-row wide">
                    <div className="trade-metric-cell">
                      <div className="trade-metric-label">Margin</div>
                      <div className="trade-metric-value" style={{ color: '#f5a623' }}>${margin.toLocaleString('en-US', { maximumFractionDigits: 0 })}</div>
                    </div>
                    <div className="trade-metric-cell">
                      <div className="trade-metric-label">Qty / Fees</div>
                      <div className="trade-metric-value">{t.qty.toLocaleString('en-US', { maximumFractionDigits: 2 })}</div>
                    </div>
                    <div className="trade-metric-cell">
                      <div className="trade-metric-label">Confidence</div>
                      <div className="trade-metric-value">{t.confidence}/100</div>
                    </div>
                  </div>
                  <div className="trade-actions">
                    <button className="btn-secondary" style={{ fontSize: 12, color: 'var(--danger)', borderColor: 'var(--danger)' }} onClick={() => onClose(t)}>CLOSE TRADE</button>
                  </div>
                </div>
              );
            })}
          </div>
        )
      )}

      {/* Closed trades */}
      {subTab === 'closed' && (
        <div className="history-container">
          <table className="history-table">
            <thead>
              <tr><th>Symbol</th><th>Side</th><th>Exit</th><th>Hrs</th><th style={{ textAlign: 'right' }}>Net PnL</th></tr>
            </thead>
            <tbody>
              {closedTrades.length === 0 ? (
                <tr><td colSpan={5} style={{ textAlign: 'center', padding: 24, opacity: 0.5 }}>No closed trades.</td></tr>
              ) : (
                closedTrades.map(t => (
                  <tr key={t.id} onClick={() => setSelectedTrade(t)} style={{ cursor: 'pointer' }} className="hover-lift">
                    <td style={{ fontWeight: 600 }}><Logo symbol={t.symbol} size={16} />{t.symbol}</td>
                    <td style={{ color: t.side === 'LONG' ? 'var(--success)' : 'var(--danger)' }}>{t.side}</td>
                    <td style={{ fontSize: 11, opacity: 0.7 }}>{t.exit_reason || '—'}</td>
                    <td style={{ opacity: 0.7 }}>{t.hours_held != null ? `${t.hours_held.toFixed(1)}h` : '—'}</td>
                    <td style={{ textAlign: 'right', color: t.pnl >= 0 ? 'var(--success)' : 'var(--danger)', fontWeight: 600 }}>
                      {t.pnl >= 0 ? '+' : ''}${t.pnl.toFixed(2)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Winners */}
      {subTab === 'winners' && (
        <div className="history-container">
          <table className="history-table">
            <thead>
              <tr><th>Symbol</th><th>Conf</th><th>Strength</th><th>Hrs</th><th style={{ textAlign: 'right' }}>Net PnL</th></tr>
            </thead>
            <tbody>
              {winners.length === 0 ? (
                <tr><td colSpan={5} style={{ textAlign: 'center', padding: 24, opacity: 0.5 }}>No TP hits recorded yet.</td></tr>
              ) : (
                winners.map(w => (
                  <tr key={w.id} onClick={() => setSelectedWinner(w)} style={{ cursor: 'pointer' }} className="hover-lift">
                    <td style={{ fontWeight: 600 }}><Logo symbol={w.symbol} size={16} />{w.symbol}</td>
                    <td style={{ opacity: 0.8 }}>{w.confidence}/100</td>
                    <td><span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, background: w.signal_strength === 'EXCELLENT' ? '#4caf50' : w.signal_strength === 'GOOD' ? '#8bc34a' : '#ffc107', color: '#fff', fontWeight: 700 }}>{w.signal_strength}</span></td>
                    <td style={{ opacity: 0.7 }}>{w.hours_held.toFixed(1)}h</td>
                    <td style={{ textAlign: 'right', color: 'var(--success)', fontWeight: 700 }}>+${w.net_pnl_usd.toFixed(2)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Trade Detail Modal ─────────────────────────────────────────── */}
      {selectedTrade && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 200,
            background: 'rgba(0,0,0,1)', backdropFilter: 'blur(20px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
          }}
          onClick={e => { if (e.target === e.currentTarget) setSelectedTrade(null); }}
        >
          <div className="glass" style={{ width: '100%', maxWidth: 520, maxHeight: '85vh', overflowY: 'auto', borderRadius: 'var(--radius-lg)', padding: 20, position: 'relative' }}>
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
                  <span style={{ color: selectedTrade.side === 'LONG' ? 'var(--success)' : 'var(--danger)', fontWeight: 600 }}>{selectedTrade.side}</span>
                  {' · '}
                  {selectedTrade.leverage}x · Conf {selectedTrade.confidence}/100
                </div>
              </div>
              <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
                <div style={{ fontSize: 20, fontWeight: 700, color: (selectedTrade.pnl_pct ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                  {(selectedTrade.pnl_pct ?? 0) >= 0 ? '+' : ''}{(selectedTrade.pnl_pct ?? 0).toFixed(2)}%
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                  ${(selectedTrade.pnl ?? 0).toFixed(2)} net
                </div>
              </div>
            </div>

            {/* Timestamps */}
            <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: 12, marginBottom: 14 }}>
              <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>Timeline</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Entry Time</div>
                  <div style={{ fontSize: 12, fontFamily: 'var(--font-mono)' }}>{selectedTrade.entry_time ? new Date(selectedTrade.entry_time).toLocaleString() : '—'}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Exit Time</div>
                  <div style={{ fontSize: 12, fontFamily: 'var(--font-mono)' }}>{selectedTrade.exit_time ? new Date(selectedTrade.exit_time).toLocaleString() : '—'}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Duration</div>
                  <div style={{ fontSize: 12, fontFamily: 'var(--font-mono)' }}>{selectedTrade.hours_held != null ? `${selectedTrade.hours_held.toFixed(1)} hours` : '—'}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Exit Reason</div>
                  <div style={{ fontSize: 12 }}>{selectedTrade.exit_reason || '—'}</div>
                </div>
              </div>
            </div>

            {/* Prices */}
            <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: 12, marginBottom: 14 }}>
              <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>Prices</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Entry</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>${selectedTrade.entry_price.toLocaleString()}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Exit</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>${selectedTrade.exit_price?.toLocaleString() ?? '—'}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Planned</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>${selectedTrade.entry_price_planned.toLocaleString()}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>TP</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--success)' }}>${selectedTrade.tp_price.toLocaleString()}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>SL</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--danger)' }}>${selectedTrade.sl_price.toLocaleString()}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Funding</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{(selectedTrade.funding_rate ?? 0).toFixed(4)}%</div>
                </div>
              </div>
            </div>

            {/* Position */}
            <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: 12, marginBottom: 14 }}>
              <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>Position</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Qty</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{selectedTrade.qty.toLocaleString('en-US', { maximumFractionDigits: 2 })}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Leverage</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>{selectedTrade.leverage}x</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Notional</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>${selectedTrade.leveraged_notional.toLocaleString('en-US', { maximumFractionDigits: 0 })}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Margin</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>${(selectedTrade.margin ?? 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Fees</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>${(selectedTrade.fees_total ?? 0).toFixed(2)}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Status</div>
                  <div style={{ fontSize: 14, fontWeight: 700 }}>{selectedTrade.status}</div>
                </div>
              </div>
            </div>

            {/* PnL */}
            <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: 12, marginBottom: 14 }}>
              <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>P&L</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Net PnL ($)</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)', color: (selectedTrade.pnl ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                    {(selectedTrade.pnl ?? 0) >= 0 ? '+' : ''}${(selectedTrade.pnl ?? 0).toFixed(2)}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Net PnL (%)</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)', color: (selectedTrade.pnl_pct ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                    {(selectedTrade.pnl_pct ?? 0) >= 0 ? '+' : ''}{(selectedTrade.pnl_pct ?? 0).toFixed(2)}%
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Fees</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--danger)' }}>
                    −${(selectedTrade.fees_total ?? 0).toFixed(2)}
                  </div>
                </div>
              </div>
            </div>

            {selectedTrade.notes && (
              <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: 12 }}>
                <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 6 }}>Notes</div>
                <div style={{ fontSize: 12, color: 'var(--text-primary)', lineHeight: 1.5 }}>{selectedTrade.notes}</div>
              </div>
            )}

            <div style={{ marginTop: 16, display: 'flex', justifyContent: 'center' }}>
              <button onClick={() => setSelectedTrade(null)} className="btn-secondary" style={{ fontSize: 12, padding: '8px 24px' }}>Close</button>
            </div>
          </div>
        </div>
      )}

      {/* ── Winner Detail Modal ────────────────────────────────────────── */}
      {selectedWinner && (
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 200,
            background: 'rgba(0,0,0,1)', backdropFilter: 'blur(20px)',
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
          }}
          onClick={e => { if (e.target === e.currentTarget) setSelectedWinner(null); }}
        >
          <div className="glass" style={{ width: '100%', maxWidth: 460, maxHeight: '85vh', overflowY: 'auto', borderRadius: 'var(--radius-lg)', padding: 20, position: 'relative' }}>
            <button
              onClick={() => setSelectedWinner(null)}
              className="pressable"
              style={{ position: 'absolute', top: 12, right: 12, width: 28, height: 28, borderRadius: 6, border: 'none', background: 'rgba(255,255,255,0.08)', color: 'var(--text-secondary)', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center' }}
            >
              <X size={14} />
            </button>

            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16, paddingRight: 36 }}>
              <Logo symbol={selectedWinner.symbol} size={28} />
              <div>
                <div style={{ fontSize: 18, fontWeight: 700 }}>{selectedWinner.symbol}</div>
                <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                  <span style={{ color: 'var(--success)', fontWeight: 600 }}>WINNER</span>
                  {' · '}
                  Conf {selectedWinner.confidence}/100 · {selectedWinner.session}
                </div>
              </div>
              <div style={{ marginLeft: 'auto', textAlign: 'right' }}>
                <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--success)' }}>
                  +{selectedWinner.net_pnl_pct.toFixed(2)}%
                </div>
                <div style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>
                  +${selectedWinner.net_pnl_usd.toFixed(2)}
                </div>
              </div>
            </div>

            <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: 12, marginBottom: 14 }}>
              <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>Trade Details</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Entry Price</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>${selectedWinner.entry_price.toLocaleString()}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Exit Price</div>
                  <div style={{ fontSize: 14, fontWeight: 700, fontFamily: 'var(--font-mono)' }}>${selectedWinner.exit_price.toLocaleString()}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Duration</div>
                  <div style={{ fontSize: 12, fontFamily: 'var(--font-mono)' }}>{selectedWinner.hours_held.toFixed(1)} hours</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Exit Reason</div>
                  <div style={{ fontSize: 12 }}>{selectedWinner.exit_reason || '—'}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Signal Strength</div>
                  <div style={{ fontSize: 12, fontWeight: 700 }}>{selectedWinner.signal_strength}</div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Recorded</div>
                  <div style={{ fontSize: 12, fontFamily: 'var(--font-mono)' }}>{selectedWinner.recorded_at ? new Date(selectedWinner.recorded_at).toLocaleString() : '—'}</div>
                </div>
              </div>
            </div>

            <div style={{ background: 'rgba(255,255,255,0.03)', borderRadius: 8, padding: 12, marginBottom: 14 }}>
              <div style={{ fontSize: 10, fontWeight: 600, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>P&L Breakdown</div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Net PnL ($)</div>
                  <div style={{ fontSize: 16, fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--success)' }}>
                    +${selectedWinner.net_pnl_usd.toFixed(2)}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Net PnL (%)</div>
                  <div style={{ fontSize: 16, fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--success)' }}>
                    +{selectedWinner.net_pnl_pct.toFixed(2)}%
                  </div>
                </div>
              </div>
            </div>

            <div style={{ marginTop: 16, display: 'flex', justifyContent: 'center' }}>
              <button onClick={() => setSelectedWinner(null)} className="btn-secondary" style={{ fontSize: 12, padding: '8px 24px' }}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
