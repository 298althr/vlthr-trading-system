import { useState, useEffect } from 'react';
import { X, TrendingUp, TrendingDown } from 'lucide-react';
import type { MarketIntel } from '../../types';
import Logo from './Logo';
import CopyTrigger from './CopyTrigger';
import { LineChart, OrderbookBars } from './Charts';

interface Props {
  symbol: string | null;
  intel: MarketIntel | null;
  onClose: () => void;
}

export default function SymbolDetailModal({ symbol, intel, onClose }: Props) {
  const [open, setOpen] = useState(false);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    if (symbol && intel) {
      setVisible(true);
      requestAnimationFrame(() => requestAnimationFrame(() => setOpen(true)));
    } else {
      setOpen(false);
    }
  }, [symbol, intel]);

  const handleClose = () => {
    setOpen(false);
    setTimeout(onClose, 320);
  };

  if (!visible || !symbol || !intel) return null;

  const isUp = intel.change24h >= 0;
  const priceColor = isUp ? 'var(--success)' : 'var(--danger)';
  const TrendIcon = isUp ? TrendingUp : TrendingDown;

  return (
    <div className={`modal-backdrop${open ? '' : ' closing'}`} onClick={handleClose}>
      <div className={`modal-glass${open ? '' : ' closing'}`} style={{ maxHeight: '85vh' }} onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <div className="modal-title">
            <Logo symbol={symbol || ''} size={24} />
            {symbol?.replace('USDT', '')}
          </div>
          <button className="modal-close pressable" onClick={handleClose}>
            <X size={20} />
          </button>
        </div>

        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 20 }}>
          <CopyTrigger text={intel.price.toFixed(2)}>
            <span className="data-xl" style={{ cursor: 'pointer' }}>
              ${intel.price.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
            </span>
          </CopyTrigger>
          <div style={{ color: priceColor, fontWeight: 700, fontSize: 14, display: 'flex', alignItems: 'center', gap: 4 }}>
            <TrendIcon size={14} />
            {isUp ? '+' : ''}{intel.change24h.toFixed(2)}%
          </div>
        </div>

        <div className="modal-inset" style={{ marginBottom: 20, borderLeft: '3px solid var(--primary)' }}>
          <div className="label" style={{ color: 'var(--primary)', marginBottom: 8 }}>Analyst Narrative</div>
          <div className="body-sm" style={{ color: 'var(--text-primary)', lineHeight: 1.6 }}>{intel.narrative}</div>
        </div>

        <div style={{ marginBottom: 20 }}>
          <div className="label" style={{ marginBottom: 8 }}>Price History</div>
          <div className="surface-dark" style={{ padding: 8, overflowX: 'auto' }}>
            <LineChart data={intel.history.prices.map(p => ({ time: p.time, value: p.price }))} width={340} height={120} color="var(--primary)" />
          </div>
        </div>

        {intel.funding && intel.history.funding.length > 1 && (
          <div style={{ marginBottom: 20 }}>
            <div className="label" style={{ marginBottom: 8 }}>Funding Rate (%)</div>
            <div className="surface-dark" style={{ padding: 8, overflowX: 'auto' }}>
              <LineChart data={intel.history.funding.map(f => ({ time: f.time, value: f.rate * 100 }))} width={340} height={80} color="#F28B82" />
            </div>
          </div>
        )}

        {intel.oi && intel.history.oi.length > 1 && (
          <div style={{ marginBottom: 20 }}>
            <div className="label" style={{ marginBottom: 8 }}>Open Interest</div>
            <div className="surface-dark" style={{ padding: 8, overflowX: 'auto' }}>
              <LineChart data={intel.history.oi.map(o => ({ time: o.time, value: o.oi }))} width={340} height={80} color="var(--success)" />
            </div>
          </div>
        )}

        {intel.orderbook && intel.orderbook.bids.length > 0 && (
          <div style={{ marginBottom: 20 }}>
            <div className="label" style={{ marginBottom: 8 }}>Orderbook Depth</div>
            <div className="surface-dark" style={{ padding: 8, overflowX: 'auto' }}>
              <OrderbookBars bids={intel.orderbook.bids} asks={intel.orderbook.asks} width={340} height={100} />
            </div>
            <div className="caption" style={{ display: 'flex', justifyContent: 'space-between', marginTop: 6, color: 'var(--text-tertiary)' }}>
              <span>Bids (Buy) — Depth: {intel.orderbook.depthBid.toFixed(2)}</span>
              <span>Spread: ${intel.orderbook.spread.toFixed(2)}</span>
              <span>Asks (Sell) — Depth: {intel.orderbook.depthAsk.toFixed(2)}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
