import { useState, useCallback } from 'react';
import { Activity, Wallet, Coins, TrendingUp, Brain, Loader2 } from 'lucide-react';
import { API_BASE } from '../config';
import type { HCSSignal, PaperAccount, PaperTrade, MarketIntel, VTCState } from '../types';
import Logo from '../components/shared/Logo';
import SignalCard from '../components/shared/SignalCard';
import VlthrEmptyState from '../components/shared/VlthrEmptyState';
import { Sparkline } from '../components/shared/Charts';
import { ChevronRight } from 'lucide-react';

interface Props {
  signals: HCSSignal[];
  paper: { account: PaperAccount | null; activeTrades: PaperTrade[] };
  marketIntel: Record<string, MarketIntel>;
  vtc: VTCState | null;
  onSelectSymbol: (sym: string) => void;
  onClose: (t: PaperTrade) => void;
}

export default function DashboardPage({ signals, paper, marketIntel, vtc, onSelectSymbol, onClose }: Props) {
  const acc = paper.account;
  const [overviewLoading, setOverviewLoading] = useState(false);
  const [overviewResult, setOverviewResult] = useState<string | null>(null);
  const [overviewError, setOverviewError] = useState<string | null>(null);

  const generateOverview = useCallback(async () => {
    setOverviewLoading(true);
    setOverviewError(null);
    setOverviewResult(null);
    try {
      const res = await fetch(`${API_BASE}/api/overview/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.error || `HTTP ${res.status}`);
      }
      setOverviewResult(data.report?.ai_analysis || 'No analysis returned.');
    } catch (err: any) {
      setOverviewError(err.message || 'Failed to generate overview');
    } finally {
      setOverviewLoading(false);
    }
  }, []);

  return (
    <div className="content-max">
      <div className="section-title">
        <span className="status-dot"></span>
        Portfolio Overview
      </div>

      {/* KPI Grid */}
      <div className="kpi-grid">
        <div className="kpi-widget hover-lift">
          <Wallet size={20} color="var(--primary)" className="kpi-icon" />
          <div className="kpi-label-text">Paper Balance</div>
          <div className="kpi-value">${(acc?.wallet_balance ?? acc?.balance ?? 10000).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
          <div className="kpi-trend">Wallet (seed + realised)</div>
          <div className="data-sm" style={{ marginTop: 4, color: 'var(--success)' }}>
            Avail <span style={{ fontWeight: 600 }}>${(acc?.available ?? acc?.balance ?? 10000).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
          </div>
          <div className="data-sm" style={{ marginTop: 2, color: 'var(--warning)' }}>
            Equity <span style={{ fontWeight: 600 }}>${(acc?.equity ?? 10000).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</span>
          </div>
          {acc && acc.live_total_pnl_usd != null && acc.live_total_pnl_usd !== 0 && (
            <div className="data-sm" style={{ marginTop: 2, color: acc.live_total_pnl_usd >= 0 ? 'var(--success)' : 'var(--danger)' }}>
              {acc.live_total_pnl_usd >= 0 ? '+' : ''}{acc.live_total_pnl_usd.toFixed(2)} total
              {acc.live_floating_pnl != null && acc.live_floating_pnl !== 0 && (
                <span style={{ opacity: 0.6 }}> ({acc.live_floating_pnl >= 0 ? '+' : ''}{acc.live_floating_pnl.toFixed(2)} open)</span>
              )}
            </div>
          )}
        </div>
        <div className="kpi-widget hover-lift">
          <Coins size={20} color="var(--primary)" className="kpi-icon" />
          <div className="kpi-label-text">VTC</div>
          <div className="kpi-value">${vtc?.price.toFixed(4) ?? '1.0000'}</div>
          <div className="kpi-trend">Score: {vtc?.score.toFixed(1) ?? '50.0'}</div>
          {vtc && (
            <div className="data-sm" style={{ marginTop: 2, color: vtc.score >= 60 ? 'var(--success)' : vtc.score >= 40 ? 'var(--warning)' : 'var(--danger)' }}>
              {vtc.marketCap >= 1e6 ? `$${(vtc.marketCap / 1e6).toFixed(2)}M` : `$${vtc.marketCap.toFixed(0)}`} market cap
            </div>
          )}
        </div>
        <div className="kpi-widget hover-lift">
          <Activity size={20} color="var(--primary)" className="kpi-icon" />
          <div className="kpi-label-text">Live Signals</div>
          <div className="kpi-value">{signals.length}</div>
          <div className="kpi-trend">High-confidence</div>
        </div>
        <div className="kpi-widget hover-lift">
          <TrendingUp size={20} color={((acc?.live_total_pnl_usd ?? acc?.total_pnl ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)')} className="kpi-icon" />
          <div className="kpi-label-text">Overall Performance</div>
          <div className="kpi-value" style={{ color: ((acc?.live_total_pnl_usd ?? acc?.total_pnl ?? 0) >= 0 ? 'var(--success)' : 'var(--danger)') }}>
            {((acc?.live_total_pnl_usd ?? 0) >= 0 ? '+' : '')}${((acc?.live_total_pnl_usd ?? 0)).toFixed(2)}
          </div>
          <div className="kpi-trend">{acc?.total_trades ?? 0} trades · {(acc?.live_win_rate ?? acc?.win_rate ?? 0).toFixed(1)}% win rate</div>
        </div>
      </div>

      {/* AI Market Overview */}
      <div style={{ marginTop: 20, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <button
          className="btn-primary pressable"
          onClick={generateOverview}
          disabled={overviewLoading}
          style={{ display: 'flex', alignItems: 'center', gap: 8 }}
        >
          {overviewLoading ? <Loader2 size={16} className="spin" /> : <Brain size={16} />}
          {overviewLoading ? 'Generating...' : 'AI Market Overview'}
        </button>
        <span className="caption" style={{ color: 'var(--text-secondary)' }}>
          Run every ~12 hours. Probes all 6 symbols + AI alignment check.
        </span>
      </div>

      {overviewError && (
        <div className="modal-inset" style={{ marginTop: 12, color: 'var(--danger)', fontSize: 13 }}>
          {overviewError}
        </div>
      )}

      {overviewResult && (
        <div className="modal-inset" style={{ marginTop: 12, padding: 16, fontSize: 13, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
          <div style={{ fontWeight: 700, marginBottom: 8, color: 'var(--primary)', fontSize: 14 }}>
            <Brain size={14} style={{ display: 'inline', marginRight: 6, verticalAlign: 'middle' }} />
            AI Market Overview — {new Date().toLocaleString()}
          </div>
          {overviewResult}
        </div>
      )}

      {/* Live Market Intel */}
      {Object.keys(marketIntel).length > 0 && (
        <div style={{ marginTop: 24 }}>
          <div className="section-title">Live Market Intel</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {Object.keys(marketIntel).sort().map(sym => {
              const item = marketIntel[sym];
              const isUp = item.change24h >= 0;
              const priceColor = isUp ? 'var(--success)' : 'var(--danger)';
              return (
                <button
                  key={sym}
                  onClick={() => onSelectSymbol(sym)}
                  className="trade-card"
                  style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '14px 16px',
                    textAlign: 'left', width: '100%', fontFamily: 'inherit',
                    cursor: 'pointer', border: 'none'
                  }}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 700, color: item.change24h > 0 ? 'var(--success)' : item.change24h < 0 ? 'var(--danger)' : 'var(--text-primary)' }}><Logo symbol={sym} size={16} />{sym.replace('USDT', '')}</span>
                      <span style={{ fontSize: 10, padding: '2px 6px', borderRadius: 4, background: isUp ? 'rgba(83,225,111,0.15)' : 'rgba(255,180,170,0.15)', color: priceColor, fontWeight: 700 }}>
                        {isUp ? '+' : ''}{item.change24h.toFixed(1)}%
                      </span>
                    </div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 600, color: 'var(--text-primary)' }}>
                      ${item.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </div>
                    <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
                      {item.funding && (
                        <span className="chip chip-default" style={{ fontSize: 9, padding: '2px 5px' }}>
                          Fund {(item.funding.rate * 100).toFixed(4)}%
                        </span>
                      )}
                      {item.oi && (
                        <span className="chip chip-default" style={{ fontSize: 9, padding: '2px 5px', color: item.oi.oiDeltaPct >= 0 ? 'var(--success)' : 'var(--danger)' }}>
                          OI {item.oi.oiDeltaPct >= 0 ? '+' : ''}{item.oi.oiDeltaPct.toFixed(1)}%
                        </span>
                      )}
                      {item.orderbook && (
                        <span className="chip chip-default" style={{ fontSize: 9, padding: '2px 5px', color: item.orderbook.imbalance > 55 ? 'var(--success)' : item.orderbook.imbalance < 45 ? 'var(--danger)' : 'var(--text-secondary)' }}>
                          OB {item.orderbook.imbalance.toFixed(0)}%
                        </span>
                      )}
                    </div>
                  </div>
                  <Sparkline data={item.history.prices} width={60} height={28} />
                  <ChevronRight size={18} style={{ color: 'var(--text-secondary)', opacity: 0.5, flexShrink: 0 }} />
                </button>
              );
            })}
          </div>
        </div>
      )}

      <div className="section-title" style={{ marginTop: 24 }}>
        <Activity size={18} style={{ marginRight: 8, verticalAlign: 'middle' }} />
        Latest Signals
      </div>
      {signals.length === 0 ? (
        <VlthrEmptyState title="No signals yet" message="Monitoring market for new signals…" />
      ) : (
        <div className="signals-grid">
          {signals.slice(0, 2).map(s => (
            <SignalCard key={s.id} signal={s} compact />
          ))}
        </div>
      )}

      <div className="section-title" style={{ marginTop: 24 }}>
        <Wallet size={18} style={{ marginRight: 8, verticalAlign: 'middle' }} />
        Open Positions ({paper.activeTrades.length})
      </div>
      {paper.activeTrades.length === 0 ? (
        <VlthrEmptyState title="No open positions" message="No active paper positions." />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {paper.activeTrades.map(t => {
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
                    <div className="signal-meta">
                      {t.side} · {t.leverage}x · Entry <span style={{ fontFamily: 'var(--font-mono)' }}>${t.entry_price.toLocaleString()}</span>
                      {hasLive && (
                        <span style={{ marginLeft: 6, color: 'var(--text-secondary)' }}>
                          → <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-primary)' }}>${t.live_price!.toLocaleString()}</span>
                        </span>
                      )}
                    </div>
                  </div>
                  <div style={{ textAlign: 'right' }}>
                    <div style={{ fontSize: 16, fontWeight: 700, fontFamily: 'var(--font-mono)', color: pnlColor }}>
                      {livePnl >= 0 ? '+' : ''}{livePnl.toFixed(2)} USDT
                    </div>
                    <div style={{ fontSize: 11, color: pnlColor, fontFamily: 'var(--font-mono)' }}>
                      {livePnlPct >= 0 ? '+' : ''}{livePnlPct.toFixed(2)}%
                      {!hasLive && <span style={{ color: 'var(--text-secondary)', fontSize: 10 }}> (pending price)</span>}
                    </div>
                  </div>
                </div>
                {hasLive && (tpDist > 0 || slDist > 0) && (
                  <div style={{ marginTop: 8 }}>
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
                <div style={{ display: 'flex', gap: 16, marginTop: 8, flexWrap: 'wrap', fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)' }}>
                  <span><span style={{ opacity: 0.6 }}>Margin </span><span style={{ color: '#f5a623', fontWeight: 600 }}>${margin.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span></span>
                  <span><span style={{ opacity: 0.6 }}>Qty </span>{t.qty.toLocaleString('en-US', { maximumFractionDigits: 2 })}</span>
                  <span><span style={{ opacity: 0.6 }}>Fees </span>${t.fees_total.toFixed(2)}</span>
                </div>
                <div style={{ marginTop: 8, display: 'flex', justifyContent: 'flex-end' }}>
                  <button className="btn-secondary" style={{ fontSize: 11, padding: '4px 10px', color: 'var(--danger)', borderColor: 'var(--danger)' }} onClick={() => onClose(t)}>CLOSE TRADE</button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <div className="section-title" style={{ marginTop: 24 }}>Signal History</div>
      {(() => {
        const recentSignals = signals
          .filter(s => s.confidence > 50 && !(s.isExpired && s.ageMinutes > 24 * 60))
          .sort((a, b) => b.timestamp - a.timestamp)
          .slice(0, 5);

        const statusOf = (s: HCSSignal) => {
          if (s.isExpired) return { label: 'EXPIRED', color: 'var(--danger)', bg: 'rgba(244,67,54,0.12)' };
          if (s.trade_decision === 'EXECUTE') return { label: 'EXECUTED', color: 'var(--success)', bg: 'rgba(129,201,149,0.12)' };
          if (s.trade_decision === 'SKIP') return { label: 'SKIPPED', color: 'var(--text-tertiary)', bg: 'rgba(255,255,255,0.06)' };
          return { label: 'ACTIVE', color: 'var(--primary)', bg: 'rgba(138,180,248,0.12)' };
        };

        if (recentSignals.length === 0) {
          return <div style={{ padding: '24px', textAlign: 'center', opacity: 0.4, fontSize: 13 }}>No recent signals above 50% confidence.</div>;
        }

        return (
          <div className="history-container">
            <table className="history-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Asset</th>
                  <th>Strength</th>
                  <th style={{ textAlign: 'right' }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {recentSignals.map((s, idx) => {
                  const st = statusOf(s);
                  return (
                    <tr key={`${s.id}-${idx}`}>
                      <td style={{ color: 'var(--on-surface-variant)', whiteSpace: 'nowrap' }}>{s.time}</td>
                      <td style={{ fontWeight: 600 }}>{s.asset}</td>
                      <td>
                        <span style={{
                          fontSize: 10, padding: '2px 8px', borderRadius: 4, fontWeight: 700,
                          background: s.signal_strength === 'EXCELLENT' ? 'rgba(129,201,149,0.15)' : s.signal_strength === 'GOOD' ? 'rgba(129,201,149,0.10)' : 'rgba(251,192,45,0.10)',
                          color: s.signal_strength === 'EXCELLENT' || s.signal_strength === 'GOOD' ? 'var(--success)' : 'var(--warning)'
                        }}>
                          {s.signal_strength}
                        </span>
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 4, fontWeight: 700, background: st.bg, color: st.color }}>
                          {st.label}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        );
      })()}
    </div>
  );
}
