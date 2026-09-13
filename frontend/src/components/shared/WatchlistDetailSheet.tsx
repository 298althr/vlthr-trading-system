import { useState, useEffect } from 'react';
import { X, CheckCircle, XCircle, HelpCircle, Bell, Zap, Navigation } from 'lucide-react';
import type { WatchlistItem } from '../../types';
import Logo from './Logo';

interface Props {
  item: WatchlistItem | null;
  onClose: () => void;
  onNavigate: (tab: 'signals' | 'paper', symbol: string) => void;
  onPrestage?: (symbol: string) => void;
}

const statusColor = (st: string) => {
  switch (st) {
    case 'CONFIRMED':   return 'var(--success)';
    case 'APPROACHING': return 'var(--warning)';
    case 'WATCHING':    return 'var(--primary)';
    case 'OFF_SESSION': return 'var(--text-tertiary)';
    default:            return 'var(--text-secondary)';
  }
};

const statusBg = (st: string) => {
  switch (st) {
    case 'CONFIRMED':   return 'rgba(129,201,149,0.12)';
    case 'APPROACHING': return 'rgba(251,192,45,0.12)';
    case 'WATCHING':    return 'rgba(138,180,248,0.12)';
    case 'OFF_SESSION': return 'rgba(255,255,255,0.04)';
    default:            return 'rgba(255,255,255,0.04)';
  }
};

export default function WatchlistDetailSheet({ item, onClose, onNavigate, onPrestage }: Props) {
  const [open, setOpen] = useState(false);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (item) {
      setVisible(true);
      requestAnimationFrame(() => requestAnimationFrame(() => setOpen(true)));
    } else {
      setOpen(false);
    }
  }, [item]);

  const handleClose = () => {
    setOpen(false);
    setTimeout(onClose, 320);
  };

  if (!visible || !item) return null;

  const gates = item.gates;
  const gateEntries = gates
    ? Object.entries(gates).map(([key, g]) => ({ key, ...g }))
    : [];

  const actionConfig: Record<string, { label: string; icon: typeof Zap; color: string; action: () => void }> = {
    CONFIRMED: {
      label: 'Go to Live Signal',
      icon: Navigation,
      color: 'var(--success)',
      action: () => { onNavigate('signals', item.symbol); handleClose(); }
    },
    APPROACHING: {
      label: 'Pre-Stage Paper Trade',
      icon: Zap,
      color: 'var(--warning)',
      action: () => { if (onPrestage) onPrestage(item.symbol); handleClose(); }
    },
    WATCHING: {
      label: 'Set Volatility Alert',
      icon: Bell,
      color: 'var(--primary)',
      action: () => { handleClose(); }
    },
    OFF_SESSION: {
      label: 'Notify on Session Open',
      icon: Bell,
      color: 'var(--text-tertiary)',
      action: () => { handleClose(); }
    },
  };

  const act = actionConfig[item.status] || actionConfig.WATCHING;
  const ActionIcon = act.icon;

  // Separate main gates (4) from bonus EMA gate
  const mainGates = gateEntries.filter(g => g.key !== 'bull');
  const emaGate = gateEntries.find(g => g.key === 'bull');

  return (
    <div
      className={`modal-backdrop${open ? '' : ' closing'}`}
      style={{ background: 'rgba(0, 0, 0, 0.92)', backdropFilter: 'blur(16px)' }}
      onClick={handleClose}
    >
      <div
        className={`modal-sheet${open ? '' : ' closing'}`}
        style={{
          maxHeight: '80vh',
          borderRadius: '20px 20px 0 0',
          background: 'linear-gradient(180deg, rgba(26,26,46,0.98) 0%, rgba(15,15,26,0.98) 100%)',
          border: '1px solid rgba(138, 180, 248, 0.15)',
          boxShadow: '0 -12px 40px rgba(0, 0, 0, 0.6)',
          padding: '20px 24px 28px',
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Handle bar */}
        <div style={{ display: 'flex', justifyContent: 'center', padding: '4px 0 16px' }}>
          <div style={{ width: 36, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.15)' }} />
        </div>

        {/* Header */}
        <div className="modal-header" style={{ paddingBottom: 20, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div className="modal-title" style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <Logo symbol={item.symbol} size={28} />
            <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span style={{ fontSize: 20, fontWeight: 700 }}>{item.symbol.replace('USDT', '')}</span>
              <span style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
                {item.session?.replace('_', ' ').toUpperCase()} · {item.h4_direction}
              </span>
            </div>
            <span style={{
              fontSize: 10, padding: '4px 12px', borderRadius: 6,
              background: statusBg(item.status), color: statusColor(item.status),
              fontWeight: 700, letterSpacing: '0.05em',
              border: `1px solid ${statusColor(item.status)}40`,
              marginLeft: 4,
            }}>
              {item.status}
            </span>
          </div>
          <button 
            className="modal-close pressable" 
            onClick={handleClose}
            style={{ width: 32, height: 32, borderRadius: 8, display: 'flex', alignItems: 'center', justifyContent: 'center', background: 'rgba(255,255,255,0.06)' }}
          >
            <X size={18} />
          </button>
        </div>

        {/* Price row */}
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 16, marginBottom: 24, padding: '12px 16px', background: 'rgba(138,180,248,0.06)', borderRadius: 12, border: '1px solid rgba(138,180,248,0.1)' }}>
          <span className="data-xl" style={{ fontSize: 32 }}>${item.current_price.toLocaleString()}</span>
        </div>

        {/* Gate Checklist — 4 Main Gates */}
        <div className="modal-inset" style={{ marginBottom: 16, background: 'rgba(138, 180, 248, 0.04)', border: '1px solid rgba(138, 180, 248, 0.12)', borderRadius: 12, padding: 16 }}>
          <div className="label" style={{ color: 'var(--primary)', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            <span>Main Gates</span>
            <span style={{ fontSize: 11, padding: '3px 8px', borderRadius: 4, background: 'rgba(138,180,248,0.15)', color: 'var(--primary)' }}>
              {item.gates_passed}
            </span>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
            {mainGates.map(g => {
              const isPass = g.pass === true;
              const isFail = g.pass === false;
              const bg = isPass ? 'rgba(129,201,149,0.1)' : isFail ? 'rgba(244,67,54,0.1)' : 'rgba(255,255,255,0.05)';
              const border = isPass ? 'rgba(129,201,149,0.25)' : isFail ? 'rgba(244,67,54,0.25)' : 'rgba(255,255,255,0.12)';
              const color = isPass ? 'var(--success)' : isFail ? 'var(--danger)' : 'var(--text-tertiary)';
              return (
                <div
                  key={g.key}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 10,
                    padding: '10px 12px', borderRadius: 10,
                    background: bg,
                    border: `1px solid ${border}`,
                  }}
                >
                  {isPass ? (
                    <CheckCircle size={16} style={{ color, flexShrink: 0 }} />
                  ) : isFail ? (
                    <XCircle size={16} style={{ color, flexShrink: 0 }} />
                  ) : (
                    <HelpCircle size={16} style={{ color, flexShrink: 0 }} />
                  )}
                  <span style={{ fontSize: 13, color, fontWeight: 500 }}>
                    {g.label}
                  </span>
                </div>
              );
            })}
          </div>
          {mainGates.length === 0 && (
            <div className="body-sm" style={{ color: 'var(--text-secondary)', padding: '8px 0' }}>
              Gate breakdown not available for this scan.
            </div>
          )}
        </div>

        {/* Micro-metrics */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 12, marginBottom: 20 }}>
          <div className="modal-inset" style={{ textAlign: 'center', padding: 14, borderRadius: 12, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
            <div className="label" style={{ marginBottom: 6, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>RSI 15m</div>
            <div className="data-sm" style={{ fontSize: 18, fontWeight: 700, color: item.rsi < 40 ? 'var(--success)' : item.rsi < 55 ? 'var(--warning)' : 'var(--danger)' }}>
              {item.rsi.toFixed(1)}
            </div>
          </div>
          <div className="modal-inset" style={{ textAlign: 'center', padding: 14, borderRadius: 12, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
            <div className="label" style={{ marginBottom: 6, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>ADX 4h</div>
            <div className="data-sm" style={{ fontSize: 18, fontWeight: 700, color: item.adx >= 25 ? 'var(--success)' : 'var(--danger)' }}>
              {item.adx.toFixed(1)}
            </div>
          </div>
          <div className="modal-inset" style={{ textAlign: 'center', padding: 14, borderRadius: 12, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
            <div className="label" style={{ marginBottom: 6, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em' }}>ATR 15m</div>
            <div className="data-sm" style={{ fontSize: 18, fontWeight: 700 }}>${item.atr.toFixed(2)}</div>
          </div>
        </div>

        {/* Bonus: EMA Trend Quality Gate */}
        {emaGate && (
          <div className="modal-inset" style={{ marginBottom: 16, background: emaGate.pass ? 'rgba(129,201,149,0.08)' : 'rgba(255,255,255,0.04)', border: `1px solid ${emaGate.pass ? 'rgba(129,201,149,0.25)' : 'rgba(255,255,255,0.1)'}`, borderRadius: 12, padding: 14 }}>
            <div className="label" style={{ color: emaGate.pass ? 'var(--success)' : 'var(--text-secondary)', marginBottom: 10, fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>
              Quality Modifier (Bonus)
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', borderRadius: 8, background: emaGate.pass ? 'rgba(129,201,149,0.1)' : 'transparent' }}>
              {emaGate.pass ? (
                <CheckCircle size={16} style={{ color: 'var(--success)' }} />
              ) : (
                <XCircle size={16} style={{ color: 'var(--text-secondary)' }} />
              )}
              <span style={{ fontSize: 13, fontWeight: 500, color: emaGate.pass ? 'var(--success)' : 'var(--text-secondary)' }}>
                {emaGate.label} {emaGate.pass ? '— Quality: HIGH' : '— Quality: MEDIUM'}
              </span>
            </div>
          </div>
        )}

        {/* SL / TP preview */}
        {(item.sl_level > 0 || item.tp_level > 0) && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 24 }}>
            <div className="modal-inset" style={{ textAlign: 'center', padding: 14, borderRadius: 12, background: 'rgba(244,67,54,0.08)', border: '1px solid rgba(244,67,54,0.2)' }}>
              <div className="label" style={{ marginBottom: 6, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--danger)' }}>SL Level</div>
              <div className="data-sm" style={{ fontSize: 18, fontWeight: 700, color: 'var(--danger)' }}>${item.sl_level.toLocaleString()}</div>
            </div>
            <div className="modal-inset" style={{ textAlign: 'center', padding: 14, borderRadius: 12, background: 'rgba(129,201,149,0.08)', border: '1px solid rgba(129,201,149,0.2)' }}>
              <div className="label" style={{ marginBottom: 6, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--success)' }}>TP Level</div>
              <div className="data-sm" style={{ fontSize: 18, fontWeight: 700, color: 'var(--success)' }}>${item.tp_level.toLocaleString()}</div>
            </div>
          </div>
        )}

        {/* Contextual Action Button */}
        <div style={{ marginBottom: 12 }}>
          <button
            className="btn-primary pressable"
            style={{
              width: '100%',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 10,
              padding: '14px 20px',
              borderRadius: 12,
              fontSize: 14,
              fontWeight: 600,
              background: act.color,
              color: item.status === 'APPROACHING' ? '#0B0B0F' : '#fff',
              boxShadow: '0 4px 16px rgba(0,0,0,0.3)',
            }}
            onClick={act.action}
          >
            <ActionIcon size={18} />
            {act.label}
          </button>
        </div>

        {item.notes && (
          <div className="body-sm" style={{ color: 'var(--text-secondary)', marginTop: 16, padding: '12px 14px', background: 'rgba(255,255,255,0.04)', borderRadius: 10, border: '1px solid rgba(255,255,255,0.06)', lineHeight: 1.5 }}>
            {item.notes}
          </div>
        )}
      </div>
    </div>
  );
}
