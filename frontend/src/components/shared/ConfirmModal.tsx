import { RefreshCw, X, TrendingUp, Lock } from 'lucide-react';
import type { ModalState } from '../../types';

interface Props {
  modal: ModalState;
  onConfirm: () => void;
  onDismiss: () => void;
  loading: { paper: boolean; power?: boolean; close: boolean };
  paperConfig: { leverage: number; riskPercent: number };
  onPaperConfigChange: (config: { leverage: number; riskPercent: number }) => void;
}

export default function ConfirmModal({ modal, onConfirm, onDismiss, loading, paperConfig, onPaperConfigChange }: Props) {
  if (!modal) return null;

  let title = '';
  let body = '';
  let asset = '';
  let btnLabel = 'CONFIRM';
  let btnVariant: 'primary' | 'danger' = 'primary';
  let icon = <TrendingUp size={20} />;
  const isLoading = modal.type === 'paper' ? loading.paper : modal.type === 'close' ? loading.close : false;

  if (modal.type === 'paper') {
    title = 'Execute Paper Trade';
    body = `Activate PENDING trade for ${modal.signal.asset} at $${modal.signal.price.toLocaleString()}.`;
    asset = modal.signal.asset;
    btnLabel = 'CONFIRM PAPER';
    btnVariant = 'primary';
    icon = <TrendingUp size={20} />;
  } else if (modal.type === 'close') {
    title = 'Close Trade';
    body = `Mark ${modal.trade.symbol} ${modal.trade.side} as closed. Exit recorded as MANUAL.`;
    asset = modal.trade.symbol;
    btnLabel = 'CLOSE TRADE';
    btnVariant = 'danger';
    icon = <Lock size={20} />;
  }

  return (
    <div className="modal-backdrop" onClick={onDismiss}>
      <div className="modal-glass" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">
            {icon}
            {title}
          </div>
          <button className="modal-close pressable" onClick={onDismiss}>
            <X size={20} />
          </button>
        </div>
        <div className="modal-body">
          <div className="modal-inset" style={{ marginBottom: 16, fontWeight: 600, fontSize: 16, fontFamily: 'var(--font-mono)' }}>
            {asset}
          </div>
          <p className="body-md" style={{ color: 'var(--text-secondary)' }}>{body}</p>

          {modal.type === 'paper' && (
            <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div style={{ display: 'flex', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <label className="label" style={{ marginBottom: 6, display: 'block' }}>Leverage (x)</label>
                  <input
                    type="number" min="1" max="100" step="1"
                    value={paperConfig.leverage}
                    onChange={e => onPaperConfigChange({ ...paperConfig, leverage: parseInt(e.target.value) || 4 })}
                    style={{
                      width: '100%', padding: 10, borderRadius: 12, border: '1px solid var(--glass-border)',
                      background: 'rgba(15,15,20,0.7)', color: 'var(--text-primary)', fontSize: 14,
                      outline: 'none', transition: 'border-color 0.15s ease'
                    }}
                    onFocus={e => e.currentTarget.style.borderColor = 'var(--primary)'}
                    onBlur={e => e.currentTarget.style.borderColor = 'var(--glass-border)'}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <label className="label" style={{ marginBottom: 6, display: 'block' }}>Risk Percent (%)</label>
                  <input
                    type="number" min="0.1" max="100" step="0.5"
                    value={paperConfig.riskPercent}
                    onChange={e => onPaperConfigChange({ ...paperConfig, riskPercent: parseFloat(e.target.value) || 1 })}
                    placeholder="1.0"
                    style={{
                      width: '100%', padding: 10, borderRadius: 12, border: '1px solid var(--glass-border)',
                      background: 'rgba(15,15,20,0.7)', color: 'var(--text-primary)', fontSize: 14,
                      outline: 'none', transition: 'border-color 0.15s ease'
                    }}
                    onFocus={e => e.currentTarget.style.borderColor = 'var(--primary)'}
                    onBlur={e => e.currentTarget.style.borderColor = 'var(--glass-border)'}
                  />
                </div>
              </div>
              <div className="caption" style={{ color: 'var(--text-tertiary)' }}>
                Leverage controls position size. Risk amount overrides auto-calculated position sizing if set.
              </div>
            </div>
          )}

          {isLoading && (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 16, color: 'var(--text-secondary)', fontSize: 13 }}>
              <RefreshCw size={16} className="animate-spin" />
              Processing...
            </div>
          )}
        </div>
        <div className="modal-footer">
          <button className="btn-secondary pressable" style={{ flex: 1 }} onClick={onDismiss} disabled={isLoading}>
            Cancel
          </button>
          <button className={`${btnVariant === 'danger' ? 'btn-danger' : 'btn-primary'} pressable`} style={{ flex: 1 }} onClick={onConfirm} disabled={isLoading}>
            {isLoading ? 'Processing…' : btnLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
