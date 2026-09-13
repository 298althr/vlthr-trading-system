import { useState } from 'react';
import { Search, Activity, Clock, Eye } from 'lucide-react';
import type { HCSSignal, WatchlistItem } from '../types';
import SignalCard from '../components/shared/SignalCard';
import WatchlistPage from './WatchlistPage';

interface Props {
  signals: HCSSignal[];
  watchlist: WatchlistItem[];
  onPaper: (s: HCSSignal) => void;
  onPower?: (s: HCSSignal) => void;
  searchQuery: string;
  onSearch: (q: string) => void;
  onSelectWatchlistItem?: (item: WatchlistItem) => void;
}

export default function SignalsPage({ signals, watchlist, onPaper, searchQuery, onSearch, onSelectWatchlistItem }: Props) {
  const [sigSubTab, setSigSubTab] = useState<'active' | 'expired' | 'watchlist'>('active');
  const activeSignals = signals.filter(s => !s.isExpired);
  const expiredSignals = signals.filter(s => s.isExpired);
  const displaySignals = sigSubTab === 'active' ? activeSignals : sigSubTab === 'expired' ? expiredSignals : [];
  const filtered = displaySignals.filter(s => s.asset.toLowerCase().includes(searchQuery.toLowerCase()));

  return (
    <div className="content-max">
      <div className="section-title">High-Confidence Signal Feed</div>

      <div style={{ display: 'flex', gap: 8, marginBottom: 16, borderBottom: '1px solid var(--glass-border)', paddingBottom: 10 }}>
        {[
          { key: 'active' as const, label: `Active (${activeSignals.length})`, icon: Activity },
          { key: 'expired' as const, label: `Expired (${expiredSignals.length})`, icon: Clock },
          { key: 'watchlist' as const, label: `Watchlist (${watchlist.length})`, icon: Eye },
        ].map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setSigSubTab(key)}
            style={{
              padding: '6px 14px', borderRadius: 20, border: 'none',
              cursor: 'pointer', fontFamily: 'inherit', fontSize: 12, fontWeight: 600,
              background: sigSubTab === key ? 'var(--primary)' : 'rgba(255,255,255,0.04)',
              color: sigSubTab === key ? '#0B0B0F' : 'var(--text-secondary)',
              display: 'flex', alignItems: 'center', gap: 4
            }}
          >
            <Icon size={12} /> {label}
          </button>
        ))}
      </div>

      {sigSubTab === 'watchlist' ? (
        <WatchlistPage watchlist={watchlist} onSelectItem={onSelectWatchlistItem} />
      ) : (
        <>
          <div style={{ position: 'relative', marginBottom: 20 }}>
            <Search size={18} style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', opacity: 0.4 }} />
            <input
              type="text"
              placeholder="Filter by symbol…"
              style={{ width: '100%', padding: '12px 12px 12px 40px', background: 'rgba(255,255,255,0.04)', border: '1px solid var(--glass-border)', borderRadius: 'var(--radius-md)', color: 'var(--text-primary)', outline: 'none', fontFamily: 'inherit' }}
              value={searchQuery}
              onChange={e => onSearch(e.target.value)}
            />
          </div>
          {filtered.length === 0 ? (
            <div style={{ padding: '60px 20px', textAlign: 'center', opacity: 0.4 }}>
              {sigSubTab === 'active' ? <Activity size={48} style={{ marginBottom: 16 }} /> : <Clock size={48} style={{ marginBottom: 16 }} />}
              <div className="headline-sm">No {sigSubTab} signals</div>
              <div className="body-md">
                {sigSubTab === 'active' ? 'New signals appear automatically via SSE.' : 'Expired signals are automatically removed from Active.'}
              </div>
            </div>
          ) : (
            <div className="signals-grid">
              {filtered.map(s => (
                <SignalCard key={s.id} signal={s} onPaper={() => onPaper(s)} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
