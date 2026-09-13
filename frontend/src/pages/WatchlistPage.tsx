import { EyeOff } from 'lucide-react';
import type { WatchlistItem } from '../types';
import Logo from '../components/shared/Logo';

interface Props {
  watchlist: WatchlistItem[];
  onSelectItem?: (item: WatchlistItem) => void;
}

export default function WatchlistPage({ watchlist, onSelectItem }: Props) {
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
  return (
    <div className="content-max">
      <div className="section-title">Market Watchlist</div>

      <div className="body-sm" style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>
        Live edge per symbol — RSI 15m, ADX 4h, trend direction, and gate status.
      </div>

      {watchlist.length === 0 ? (
        <div style={{ padding: '60px 20px', textAlign: 'center', opacity: 0.4 }}>
          <EyeOff size={48} style={{ marginBottom: 16 }} />
          <div className="headline-sm">Watchlist empty</div>
          <div className="body-md">Watchlist updates automatically via SSE.</div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {watchlist.map(item => (
            <div
              key={item.id}
              className="trade-card"
              style={{ borderLeft: `3px solid ${statusColor(item.status)}`, cursor: onSelectItem ? 'pointer' : 'default' }}
              onClick={() => onSelectItem?.(item)}
            >
              <div className="trade-card-header">
                <div>
                  <div className="signal-asset"><Logo symbol={item.symbol} size={18} />{item.symbol}</div>
                  <div className="trade-card-meta">{item.session?.replace('_', ' ').toUpperCase()} · {item.h4_direction} · Scanned {new Date(item.scan_time_utc).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
                </div>
                <span style={{ fontSize: 10, padding: '3px 10px', borderRadius: 4, background: statusBg(item.status), color: statusColor(item.status), fontWeight: 700, letterSpacing: '0.05em', border: `1px solid ${statusColor(item.status)}33` }}>
                  {item.status}
                </span>
              </div>
              <div className="trade-metric-row">
                <div className="trade-metric-cell">
                  <div className="trade-metric-label">Price</div>
                  <div className="trade-metric-value">${item.current_price.toLocaleString()}</div>
                </div>
                <div className="trade-metric-cell">
                  <div className="trade-metric-label">RSI 15m</div>
                  <div className="trade-metric-value" style={{ color: item.rsi < 40 ? 'var(--success)' : item.rsi < 55 ? 'var(--warning)' : 'var(--danger)' }}>{item.rsi.toFixed(1)}</div>
                </div>
                <div className="trade-metric-cell">
                  <div className="trade-metric-label">ADX 4h</div>
                  <div className="trade-metric-value" style={{ color: item.adx >= 25 ? 'var(--success)' : 'var(--danger)' }}>{item.adx.toFixed(1)}</div>
                </div>
                <div className="trade-metric-cell">
                  <div className="trade-metric-label">Gates</div>
                  <div className="trade-metric-value">{item.gates_passed}</div>
                </div>
              </div>
              {item.notes && (
                <div style={{ fontSize: 11, color: 'var(--text-secondary)', margin: '0 var(--space-lg) var(--space-md)', padding: '8px 10px', background: 'rgba(255,255,255,0.03)', borderRadius: 6 }}>
                  {item.notes}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
