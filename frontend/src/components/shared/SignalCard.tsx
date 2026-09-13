import { TrendingUp, Zap, CircleDot } from 'lucide-react';
import type { HCSSignal } from '../../types';
import CopyTrigger from './CopyTrigger';

export function strengthColor(s: string): string {
  switch ((s || '').toUpperCase()) {
    case 'EXCELLENT': return '#81C995';
    case 'GOOD':      return '#81C995';
    case 'FAIR':      return '#FBC02D';
    case 'WEAK':      return '#FBC02D';
    default:          return 'var(--text-secondary)';
  }
}

export function strengthChipClass(s: string): string {
  switch ((s || '').toUpperCase()) {
    case 'EXCELLENT': return 'excellent';
    case 'GOOD':      return 'good';
    case 'FAIR':      return 'fair';
    case 'WEAK':      return 'weak';
    default:          return 'fair';
  }
}

function lifecycleColor(state: string): { bg: string; color: string; label: string } {
  switch (state) {
    case 'IN_TRADE': return { bg: 'var(--info)', color: '#fff', label: 'IN TRADE' };
    case 'TP_HIT':   return { bg: 'var(--success)', color: '#fff', label: 'TP HIT' };
    case 'SL_HIT':   return { bg: 'var(--danger)', color: '#fff', label: 'SL HIT' };
    case 'TIME_EXIT': return { bg: '#FF7043', color: '#fff', label: 'TIMEOUT' };
    case 'MANUAL':   return { bg: 'var(--warning)', color: '#0B0B0F', label: 'MANUAL' };
    case 'CANCELED': return { bg: '#78909C', color: '#fff', label: 'CANCELED' };
    case 'ABANDONED': return { bg: '#455A64', color: '#fff', label: 'ABANDONED' };
    case 'PENDING':  return { bg: 'var(--primary)', color: '#fff', label: 'PENDING' };
    default:         return { bg: 'var(--text-secondary)', color: '#fff', label: state };
  }
}

interface Props {
  signal: HCSSignal;
  compact?: boolean;
  onPaper?: () => void;
  onPower?: () => void;
}

export default function SignalCard({ signal, compact = false, onPaper, onPower }: Props) {
  void onPower; // acknowledged but unused — Power feature removed
  const typeClass = signal.type.toLowerCase() === 'long' ? 'long' : signal.type.toLowerCase() === 'short' ? 'short' : 'wait';

  return (
    <div className={`signal-widget hover-lift ${typeClass}`} style={{ position: 'relative' }}>
      <div className={`signal-strip ${typeClass}`} />

      {/* Badges */}
      <div style={{ position: 'absolute', top: 14, right: 16, display: 'flex', gap: 6, zIndex: 2, flexWrap: 'wrap', maxWidth: '60%', justifyContent: 'flex-end' }}>
        {signal.isPowerTrade && <span className="chip chip-active" style={{ background: 'var(--warning)', color: '#0B0B0F' }}><Zap size={10} /> POWER</span>}
        {(() => {
          const lc = lifecycleColor(signal.lifecycle_state);
          return <span className="chip" style={{ background: lc.bg, color: lc.color, fontSize: 10, fontWeight: 700 }}>{lc.label}</span>;
        })()}
      </div>

      <div className="signal-widget-header">
        <div>
          <div className="signal-asset">{signal.asset}</div>
          <div className="signal-meta">{signal.session?.replace('_', ' ')} · {signal.time}</div>
        </div>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'flex-end' }}>
          <span className={`confidence-chip ${strengthChipClass(signal.signal_strength)}`}>
            {signal.signal_strength || signal.grade}
          </span>
          <span className="chip chip-default" style={{ fontFamily: 'var(--font-mono)', fontWeight: 700 }}>
            {signal.confidence}/100
          </span>
        </div>
      </div>

      {/* Price row with copy */}
      <div className="signal-price-row">
        <CopyTrigger text={String(signal.price)}>
          <span className="signal-price-value">${signal.price.toLocaleString()}</span>
        </CopyTrigger>
        <span className="caption" style={{ color: 'var(--text-secondary)' }}>{signal.type}</span>
      </div>

      {/* Metrics grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 'var(--space-sm)', padding: '0 var(--space-lg)', marginBottom: 'var(--space-md)' }}>
        <div className="modal-inset" style={{ padding: 'var(--space-sm)', textAlign: 'center' }}>
          <div className="label" style={{ marginBottom: 2 }}>TP</div>
          <div className="data-sm" style={{ color: 'var(--success)' }}>${signal.tp.toLocaleString()}</div>
        </div>
        <div className="modal-inset" style={{ padding: 'var(--space-sm)', textAlign: 'center' }}>
          <div className="label" style={{ marginBottom: 2 }}>SL</div>
          <div className="data-sm" style={{ color: 'var(--danger)' }}>${signal.sl.toLocaleString()}</div>
        </div>
        <div className="modal-inset" style={{ padding: 'var(--space-sm)', textAlign: 'center' }}>
          <div className="label" style={{ marginBottom: 2 }}>R:R</div>
          <div className="data-sm">1:{signal.rr.toFixed(1)}</div>
        </div>
      </div>

      {/* Indicator row — RSI / ADX / OI / Fund */}
      <div style={{ display: 'flex', gap: 'var(--space-md)', padding: '0 var(--space-lg) var(--space-md)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span className="caption">RSI</span>
          <span className="data-sm" style={{ color: signal.rsi < 40 ? 'var(--success)' : 'var(--warning)' }}>{signal.rsi.toFixed(1)}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span className="caption">ADX</span>
          <span className="data-sm" style={{ color: signal.adx >= 25 ? 'var(--success)' : 'var(--danger)' }}>{signal.adx.toFixed(1)}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span className="caption">OI</span>
          <span className="data-sm" style={{ color: signal.oi_delta_pct > 0 ? 'var(--success)' : 'var(--danger)' }}>{signal.oi_delta_pct > 0 ? '+' : ''}{signal.oi_delta_pct.toFixed(1)}%</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <span className="caption">Fund</span>
          <span className="data-sm" style={{ color: signal.funding_rate < 0 ? 'var(--success)' : 'var(--warning)' }}>{(signal.funding_rate * 100).toFixed(4)}%</span>
        </div>
      </div>

      {/* Score bars */}
      <div className="score-grid">
        <div className="score-item">
          <span className="score-label">Tech</span>
          <div className="score-track"><div className="score-fill tech" style={{ width: `${Math.min(signal.technical_score, 100)}%` }} /></div>
        </div>
        <div className="score-item">
          <span className="score-label">Struct</span>
          <div className="score-track"><div className="score-fill struct" style={{ width: `${Math.min(signal.market_structure_score, 100)}%` }} /></div>
        </div>
        <div className="score-item">
          <span className="score-label">F/OI</span>
          <div className="score-track"><div className="score-fill foi" style={{ width: `${Math.min(signal.funding_oi_score, 100)}%` }} /></div>
        </div>
        <div className="score-item">
          <span className="score-label">Session</span>
          <div className="score-track"><div className="score-fill sess" style={{ width: `${Math.min(signal.session_symbol_score, 100)}%` }} /></div>
        </div>
      </div>

      {/* AI Summary — truncated */}
      {signal.summary && (
        <div style={{ padding: '0 var(--space-lg) var(--space-md)' }}>
          <div className="modal-inset" style={{ fontSize: 12, lineHeight: 1.5, color: 'var(--text-secondary)' }}>
            <span style={{ fontWeight: 600, color: 'var(--primary)' }}>AI: </span>
            <span style={{ display: '-webkit-box', WebkitLineClamp: 1, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>{signal.summary}</span>
          </div>
        </div>
      )}

      {/* Actions */}
      {!compact && onPaper && (
        <div className="signal-actions">
          {(() => {
            const state = signal.lifecycle_state;
            if (state === 'PENDING') {
              return (
                <button className="btn-primary pressable" style={{ flex: 1 }} onClick={onPaper}>
                  <TrendingUp size={14} />
                  PAPER
                </button>
              );
            }
            if (state === 'IN_TRADE') {
              return (
                <button className="btn-secondary pressable" disabled style={{ opacity: 0.6, cursor: 'not-allowed', flex: 1, borderColor: 'var(--info)', color: 'var(--info)' }}>
                  <CircleDot size={14} />
                  IN TRADE
                </button>
              );
            }
            // Terminal states
            const terminalLabels: Record<string, string> = {
              TP_HIT: 'TP HIT',
              SL_HIT: 'SL HIT',
              TIME_EXIT: 'TIMEOUT',
              MANUAL: 'MANUAL',
              CANCELED: 'CANCELED',
              ABANDONED: 'ABANDONED',
            };
            const label = terminalLabels[state] || state;
            return (
              <button className="btn-secondary pressable" disabled style={{ opacity: 0.5, cursor: 'not-allowed', flex: 1 }}>
                {label} {signal.ageMinutes > 0 ? `(${signal.ageMinutes}m ago)` : ''}
              </button>
            );
          })()}
          {onPower && signal.lifecycle_state === 'PENDING' && (
            <button className="btn-secondary pressable" style={{ flex: 1, borderColor: 'var(--warning)', color: 'var(--warning)' }} onClick={onPower}>
              <Zap size={14} />
              POWER
            </button>
          )}
        </div>
      )}
    </div>
  );
}
