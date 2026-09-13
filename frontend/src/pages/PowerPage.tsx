import { Zap, TrendingUp, X } from 'lucide-react';
import type { HCSSignal, PowerTrade } from '../types';
import Logo from '../components/shared/Logo';
import { strengthColor } from '../components/shared/SignalCard';

interface Props {
  powerTrades: PowerTrade[];
  signals: HCSSignal[];
  onDismiss: (id: number) => void;
  onPaper: (s: HCSSignal) => void;
}

export default function PowerPage({ powerTrades, signals, onDismiss, onPaper }: Props) {
  const urgencyColor = (u: string) => u === 'CRITICAL' ? 'var(--danger)' : u === 'HIGH' ? 'var(--warning)' : 'var(--success)';
  const urgencyBg = (u: string) => u === 'CRITICAL' ? 'rgba(242,139,130,0.15)' : u === 'HIGH' ? 'rgba(251,192,45,0.15)' : 'rgba(129,201,149,0.15)';

  return (
    <div className="content-max">
      <div className="section-title">Power Trades</div>
      <div className="body-sm" style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>
        Manually promoted high-conviction setups. Promote any signal from the SIGNALS tab.
      </div>
      {powerTrades.length === 0 ? (
        <div style={{ padding: '60px 20px', textAlign: 'center', opacity: 0.4 }}>
          <Zap size={48} style={{ marginBottom: 16 }} />
          <div className="headline-sm">No power trades</div>
          <div className="body-md">Go to SIGNALS and click POWER on a high-conviction setup.</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {powerTrades.map(pt => {
            const linkedSig = signals.find(s => s.dbId === pt.signalId);
            return (
              <div key={pt.id} className="trade-card" style={{ borderTop: `3px solid ${urgencyColor(pt.urgency)}` }}>
                <div className="trade-card-header">
                  <div>
                    <div className="signal-asset">
                      <Logo symbol={pt.symbol} size={18} />{pt.symbol}
                      <span style={{ marginLeft: 8, fontSize: 10, padding: '2px 8px', background: urgencyBg(pt.urgency), color: urgencyColor(pt.urgency), borderRadius: 4, fontWeight: 700, border: `1px solid ${urgencyColor(pt.urgency)}33` }}>{pt.urgency}</span>
                    </div>
                    <div className="trade-card-meta">{pt.session?.replace('_', ' ')} · {pt.h4_direction} · Conf {pt.confidence}/100</div>
                  </div>
                  <span className="chip chip-default" style={{ color: strengthColor(pt.signal_strength), fontWeight: 700 }}>
                    {pt.signal_strength}
                  </span>
                </div>
                <div className="trade-metric-row">
                  <div className="trade-metric-cell">
                    <div className="trade-metric-label">Entry</div>
                    <div className="trade-metric-value">${pt.price.toLocaleString()}</div>
                  </div>
                  <div className="trade-metric-cell">
                    <div className="trade-metric-label">TP</div>
                    <div className="trade-metric-value" style={{ color: 'var(--success)' }}>${pt.tp.toLocaleString()}</div>
                  </div>
                  <div className="trade-metric-cell">
                    <div className="trade-metric-label">SL</div>
                    <div className="trade-metric-value" style={{ color: 'var(--danger)' }}>${pt.sl.toLocaleString()}</div>
                  </div>
                  <div className="trade-metric-cell">
                    <div className="trade-metric-label">R:R</div>
                    <div className="trade-metric-value">1:{pt.rr.toFixed(1)}</div>
                  </div>
                </div>
                {pt.analyst_note && (
                  <div style={{ fontSize: 12, margin: '0 var(--space-lg) var(--space-md)', padding: '8px 10px', background: 'rgba(138,180,248,0.06)', borderRadius: 6, borderLeft: '3px solid var(--primary)', color: 'var(--text-secondary)' }}>
                    <strong style={{ color: 'var(--primary)' }}>Note:</strong> {pt.analyst_note}
                  </div>
                )}
                <div className="trade-actions">
                  {linkedSig && (
                    <button className="btn-primary" style={{ flex: 1, fontSize: 12 }} onClick={() => onPaper(linkedSig)}>
                      <TrendingUp size={14} /> PAPER
                    </button>
                  )}
                  <button className="btn-secondary" style={{ flex: 1, fontSize: 12, color: 'var(--danger)', borderColor: 'var(--danger)' }} onClick={() => onDismiss(pt.dbId)}>
                    <X size={14} /> DISMISS
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
