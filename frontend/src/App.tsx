import { useState, useEffect, useRef } from 'react';
import {
  Activity, Wallet, BarChart3, Coins, FlaskConical, Bot, TrendingUp,
} from 'lucide-react';

import { API_BASE } from './config';
import type { Tab, ModalState, HCSSignal } from './types';

import { useSSE } from './hooks/useSSE';

import DashboardPage from './pages/DashboardPage';
import SignalsPage from './pages/SignalsPage';
import VTCPage from './pages/VTCPage';
import BacktestPage from './pages/BacktestPage';
import PaperPage from './pages/PaperPage';
import DemoPage from './pages/DemoPage';

import ConfirmModal from './components/shared/ConfirmModal';
import VlthrToast, { type ToastItem } from './components/shared/VlthrToast';
import SymbolDetailModal from './components/shared/SymbolDetailModal';
import WatchlistDetailSheet from './components/shared/WatchlistDetailSheet';

export default function App() {
  const sse = useSSE();

  const [activeTab, setActiveTab] = useState<Tab>('dashboard');
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [selectedWatchlistItem, setSelectedWatchlistItem] = useState<import('./types').WatchlistItem | null>(null);
  const [modal, setModal] = useState<ModalState>(null);
  const [loading, setLoading] = useState({ paper: false, close: false });
  const [paperConfig, setPaperConfig] = useState({ leverage: 4, riskPercent: 1 });
  const [autoExecute, setAutoExecute] = useState(() => localStorage.getItem('vlthr_auto_execute') === 'true');
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const autoExecutedIds = useRef<Set<string>>(new Set());

  const addToast = (type: ToastItem['type'], title: string, message: string) => {
    const id = `${Date.now()}-${Math.random()}`;
    setToasts(prev => [...prev.slice(-2), { id, type, title, message }]);
  };

  const dismissToast = (id: string) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  };

  // Persist autoExecute toggle
  useEffect(() => {
    localStorage.setItem('vlthr_auto_execute', String(autoExecute));
  }, [autoExecute]);

  // Auto-execute EXCELLENT signals (only fresh, high-confidence, non-expired)
  useEffect(() => {
    if (!autoExecute) return;
    sse.signals.forEach((sig: HCSSignal) => {
      const isFresh = !sig.isExpired && sig.ageMinutes < 120;
      const isHighConfidence = sig.confidence >= 65;
      const isActionable = sig.lifecycle_state !== 'CANCELED' && sig.lifecycle_state !== 'ABANDONED';
      const isExcellent = sig.signal_strength === 'EXCELLENT';

      if (isExcellent && isFresh && isHighConfidence && isActionable && !autoExecutedIds.current.has(sig.id)) {
        // Mark as attempted immediately to prevent duplicate firing while request is in flight
        autoExecutedIds.current.add(sig.id);
        fetch(`${API_BASE}/api/paper/trade`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            signalId: sig.dbId,
            symbol: sig.asset,
            side: sig.type,
            entryPrice: sig.price,
            qty: sig.qty_contracts,
            leverage: paperConfig.leverage,
            riskPercent: paperConfig.riskPercent,
          }),
        }).then(async res => {
          if (res.ok) {
            addToast('success', 'Auto-Executed', `${sig.asset} EXCELLENT signal opened automatically.`);
          } else {
            // Remove from attempted set so we can retry if signal conditions improve
            autoExecutedIds.current.delete(sig.id);
            let msg = `${sig.asset} auto-execution failed.`;
            try {
              const data = await res.json();
              msg = data.error || msg;
            } catch {
              msg = `${sig.asset} auto-execution failed (HTTP ${res.status}).`;
            }
            addToast('error', 'Auto-Execute Failed', msg);
          }
        }).catch(err => {
          // Remove from attempted set so we can retry on next sync
          autoExecutedIds.current.delete(sig.id);
          addToast('error', 'Auto-Execute Failed', `${sig.asset} network error: ${err?.message || 'connection refused'}`);
        });
      }
    });
  }, [sse.signals, autoExecute, paperConfig]);

  // Reset paper config when paper modal opens
  useEffect(() => {
    if (modal?.type === 'paper') setPaperConfig({ leverage: 4, riskPercent: 1 });
  }, [modal?.type]);

  // ── Actions ──

  const handlePaperConfirm = async () => {
    if (!modal || modal.type !== 'paper') return;
    const sig = modal.signal;
    const timer = setTimeout(() => setLoading(prev => ({ ...prev, paper: true })), 600);
    try {
      const res = await fetch(`${API_BASE}/api/paper/trade`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ signalId: sig.id, symbol: sig.asset, side: sig.type, entryPrice: sig.price, qty: sig.qty_contracts, leverage: paperConfig.leverage, riskPercent: paperConfig.riskPercent }),
      });
      const data = await res.json();
      clearTimeout(timer);
      setLoading(prev => ({ ...prev, paper: false }));
      if (res.ok) {
        addToast('success', 'Trade Activated', `${sig.asset} ${sig.type} position opened successfully.`);
        setModal(null);
        setPaperConfig({ leverage: 4, riskPercent: 1 });
        setActiveTab('paper');
      } else {
        addToast('error', 'Trade Failed', data.error || 'Failed to activate paper trade.');
      }
    } catch (err) {
      clearTimeout(timer);
      setLoading(prev => ({ ...prev, paper: false }));
      addToast('error', 'Trade Failed', 'Network error or server unavailable.');
    }
  };

  const handleCloseConfirm = async () => {
    if (!modal || modal.type !== 'close') return;
    const trade = modal.trade;
    const timer = setTimeout(() => setLoading(prev => ({ ...prev, close: true })), 600);
    try {
      const live = sse.livePrices[trade.symbol];
      const exitPrice = live ? live.price : (trade.tp_price || trade.entry_price);
      const res = await fetch(`${API_BASE}/api/paper/trade/${trade.id}/close`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ exitPrice, exitReason: 'MANUAL' }),
      });
      const data = await res.json();
      clearTimeout(timer);
      setLoading(prev => ({ ...prev, close: false }));
      if (res.ok) {
        addToast('success', 'Trade Closed', `${trade.symbol} ${trade.side} position closed at $${exitPrice.toFixed(2)}.`);
        setModal(null);
      } else {
        addToast('error', 'Close Failed', data.error || 'Failed to close trade.');
      }
    } catch (err) {
      clearTimeout(timer);
      setLoading(prev => ({ ...prev, close: false }));
      addToast('error', 'Close Failed', 'Network error or server unavailable.');
    }
  };

  const handleModalConfirm = () => {
    if (!modal) return;
    if (modal.type === 'paper') handlePaperConfirm();
    else if (modal.type === 'close') handleCloseConfirm();
  };

  const handlePaperFromTrade = (t: import('./types').PaperTrade) => {
    const linked = sse.signals.find(s => s.dbId === t.signalId);
    const fallback: HCSSignal = {
      id: `HCS-${t.signalId}`, dbId: t.signalId ?? 0, asset: t.symbol, type: 'LONG',
      confidence: t.confidence, grade: '', signal_strength: '', price: t.entry_price_planned,
      tp: t.tp_price, sl: t.sl_price, rr: 0, dollar_risk: 0, actual_dollar_risk: 0, leveraged_notional: t.leveraged_notional,
      qty_contracts: t.qty, rsi: 0, adx: 0, funding_rate: t.funding_rate, oi_delta_pct: 0,
      ob_imbalance_pct: 0, technical_score: 0, market_structure_score: 0, funding_oi_score: 0,
      session_symbol_score: 0, session: '', trade_decision: '', action: '', summary: '',
      time: '', scan_time_utc: '', signal_bar_utc: '', trafficLight: 'GREEN', timestamp: 0, isPowerTrade: false,
      isExpired: false, expiryReason: '', ageMinutes: 0, lifecycle_state: 'PENDING'
    };
    setModal({ type: 'paper', signal: linked ?? fallback });
  };

  // ── Render ──
  return (
    <div className="app-bg">
      <div className="app-container">
        <div className="main-wrapper">
          {/* Top Navigation */}
          <header className="topbar-glass">
            <div className="topbar-brand">
              <img src="/favicon.svg" alt="VLTHR" />
              <h1>VLTHR</h1>
            </div>
            <div className="topbar-actions">
              <button
                className={`topbar-btn pressable ${autoExecute ? 'active' : ''}`}
                onClick={() => setAutoExecute(v => !v)}
                title={autoExecute ? 'Auto-Execute EXCELLENT: ON' : 'Auto-Execute EXCELLENT: OFF'}
                style={{ color: autoExecute ? '#81C995' : undefined }}
              >
                <Bot size={14} />
              </button>
              <div className="sse-status">
                <span className={`sse-dot ${sse.sseConnected}`} />
                <span className="sse-label">LIVE</span>
              </div>
            </div>
          </header>

          {/* Live Price Strip — marquee ticker (dashboard only) */}
          {activeTab === 'dashboard' && Object.keys(sse.livePrices).length > 0 && (() => {
            const entries = Object.entries(sse.livePrices);
            const renderItem = ([sym, p]: [string, typeof entries[0][1]], i: number) => (
              <div key={`${sym}-${i}`} className="live-strip-item">
                <span className="live-strip-sym">{sym.replace('USDT', '')}</span>
                <span className="live-strip-price">{p.price.toLocaleString(undefined, { minimumFractionDigits: p.price < 1000 ? 2 : 0, maximumFractionDigits: p.price < 1000 ? 2 : 0 })}</span>
                <span className={p.change24h >= 0 ? 'live-strip-chg-up' : 'live-strip-chg-down'}>
                  {p.change24h >= 0 ? '+' : ''}{p.change24h.toFixed(1)}%
                </span>
              </div>
            );
            return (
              <div className="live-strip-outer">
                <div className="live-strip" aria-hidden="false">
                  {entries.map((e, i) => renderItem(e, i))}
                  {entries.map((e, i) => renderItem(e, entries.length + i))}
                </div>
              </div>
            );
          })()}

          {/* Main Content */}
          <main className="content-wrap page-enter">
            {activeTab === 'dashboard' && (
              <DashboardPage signals={sse.signals} paper={{ account: sse.paper.account, activeTrades: sse.paper.activeTrades }} marketIntel={sse.marketIntel} vtc={sse.vtc} onSelectSymbol={setSelectedSymbol} onClose={t => setModal({ type: 'close', trade: t })} />
            )}
            {activeTab === 'signals' && (
              <SignalsPage
                signals={sse.signals}
                watchlist={sse.watchlist}
                onPaper={s => setModal({ type: 'paper', signal: s })}
                searchQuery={searchQuery}
                onSearch={setSearchQuery}
                onSelectWatchlistItem={setSelectedWatchlistItem}
              />
            )}
            {activeTab === 'vtc' && (
              <VTCPage vtc={sse.vtc} />
            )}
            {activeTab === 'backtest' && <BacktestPage />}
            {activeTab === 'paper' && (
              <PaperPage
                account={sse.paper.account}
                pendingTrades={sse.paper.pendingTrades}
                activeTrades={sse.paper.activeTrades}
                closedTrades={sse.paper.closedTrades}
                winners={sse.winners}
                signals={sse.signals}
                onActivate={handlePaperFromTrade}
                onClose={t => setModal({ type: 'close', trade: t })}
                onCancel={() => addToast('info', 'Cancelled', 'Pending trade removed.')}
              />
            )}
            {activeTab === 'demo' && (
              <DemoPage livePrices={sse.livePrices} />
            )}
          </main>
        </div>

        {/* Bottom Navigation */}
        <nav className="bottom-nav-glass">
          {[
            { tab: 'dashboard' as Tab, Icon: BarChart3, label: 'DASH' },
            { tab: 'signals' as Tab, Icon: Activity, label: 'SIGNALS' },
            { tab: 'vtc' as Tab, Icon: Coins, label: 'VTC' },
            { tab: 'backtest' as Tab, Icon: FlaskConical, label: 'BACKTEST' },
            { tab: 'paper' as Tab, Icon: Wallet, label: 'PAPER' },
            { tab: 'demo' as Tab, Icon: TrendingUp, label: 'DEMO' },
          ].map(({ tab, Icon, label }) => (
            <button
              key={tab}
              className={`nav-item pressable ${activeTab === tab ? 'active' : ''}`}
              onClick={() => setActiveTab(tab)}
            >
              <Icon size={20} className="nav-icon" />
              <span>{label}</span>
              <span className="nav-indicator" />
            </button>
          ))}
        </nav>

        <SymbolDetailModal symbol={selectedSymbol} intel={selectedSymbol ? sse.marketIntel[selectedSymbol] : null} onClose={() => setSelectedSymbol(null)} />
        <WatchlistDetailSheet
          item={selectedWatchlistItem}
          onClose={() => setSelectedWatchlistItem(null)}
          onNavigate={(tab, symbol) => { setActiveTab(tab); setSearchQuery(symbol); setSelectedWatchlistItem(null); }}
          onPrestage={(symbol) => addToast('info', 'Pre-Staged', `${symbol} will be queued when CONFIRMED.`)}
        />
        <ConfirmModal modal={modal} onConfirm={handleModalConfirm} onDismiss={() => setModal(null)} loading={loading} paperConfig={paperConfig} onPaperConfigChange={setPaperConfig} />
        <VlthrToast toasts={toasts} onDismiss={dismissToast} />
      </div>
    </div>
  );
}
