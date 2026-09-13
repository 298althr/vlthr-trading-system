import { useState, useEffect, useRef } from 'react';
import type { HCSSignal, WatchlistItem, PowerTrade, Winner, HistoryItem, PaperAccount, PaperTrade, MarketIntel, VTCState } from '../types';
import { API_BASE } from '../config';

function isNotificationSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window && typeof Notification === 'function';
}

async function safeNotify(title: string, options?: NotificationOptions) {
  if (!isNotificationSupported()) return;
  if (Notification.permission !== 'granted') return;

  // 1. Try the legacy Notification constructor (works on desktop Chrome/FF)
  try {
    // eslint-disable-next-line no-new
    new Notification(title, { icon: '/favicon.svg', ...options });
    return;
  } catch {
    // constructor not supported (e.g. iOS Safari, some Android PWAs)
  }

  // 2. Fall back to service-worker showNotification (works on iOS PWA + Android)
  try {
    if ('serviceWorker' in navigator && navigator.serviceWorker.controller) {
      const reg = await navigator.serviceWorker.ready;
      await reg.showNotification(title, { icon: '/favicon.svg', badge: '/favicon.svg', ...options });
    }
  } catch {
    // silently drop if SW path also fails
  }
}

export function useSSE() {
  const [signals, setSignals]         = useState<HCSSignal[]>([]);
  const [watchlist, setWatchlist]     = useState<WatchlistItem[]>([]);
  const [powerTrades, setPowerTrades] = useState<PowerTrade[]>([]);
  const [winners, setWinners]         = useState<Winner[]>([]);
  const [history, setHistory]         = useState<HistoryItem[]>([]);
  const [paper, setPaper]             = useState<{ account: PaperAccount | null; pendingTrades: PaperTrade[]; activeTrades: PaperTrade[]; closedTrades: PaperTrade[] }>({
    account: null, pendingTrades: [], activeTrades: [], closedTrades: [],
  });
  const [livePrices, setLivePrices]     = useState<Record<string, { price: number; change24h: number }>>({});
  const [marketIntel, setMarketIntel]   = useState<Record<string, MarketIntel>>({});
  const [sseConnected, setSseConnected] = useState<'connected' | 'disconnected' | 'connecting'>('connecting');
  const [refreshProgress, setRefreshProgress] = useState<{ step: number; total: number; message: string } | null>(null);
  const [vtc, setVtc] = useState<VTCState | null>(null);

  // ── Refs for change-detection (notifications only fire on actual changes) ──
  const prevPendingRef = useRef<Set<number>>(new Set());
  const prevClosedRef  = useRef<Set<number>>(new Set());
  const notifiedClosedRef = useRef<Set<number>>(new Set());
  const prevWatchlistRef = useRef<Record<string, WatchlistItem['status']>>({});

  useEffect(() => {
    let reconnectDelay = 1000;
    let es: EventSource | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout>;

    const SSE_URL = `${API_BASE}/api/signals/stream`;

    function connect() {
      setSseConnected('connecting');
      es = new EventSource(SSE_URL);

      es.onopen = () => { setSseConnected('connected'); reconnectDelay = 1000; };
      es.onerror = () => {
        setSseConnected('disconnected');
        if (es) { es.close(); es = null; }
        reconnectDelay = Math.min(reconnectDelay * 2, 30000);
        reconnectTimer = setTimeout(connect, reconnectDelay);
      };

      const hydrate = (d: Record<string, unknown>) => {
        if (d.signals)     setSignals(d.signals as HCSSignal[]);
        if (d.watchlist)   setWatchlist(d.watchlist as WatchlistItem[]);
        if (d.powerTrades) setPowerTrades(d.powerTrades as PowerTrade[]);
        if (d.winners)     setWinners(d.winners as Winner[]);
        if (d.history)     setHistory(d.history as HistoryItem[]);
        if (d.paper)       setPaper(d.paper as typeof paper);
        if (d.livePrices)  setLivePrices(d.livePrices as typeof livePrices);
        if (d.marketIntel) setMarketIntel(d.marketIntel as Record<string, MarketIntel>);
        if (d.vtc)         setVtc(d.vtc as VTCState);
      };

      es.addEventListener('initial', (e: MessageEvent) => hydrate(JSON.parse(e.data)));
      es.addEventListener('update',  (e: MessageEvent) => hydrate(JSON.parse(e.data)));
      es.addEventListener('prices',  (e: MessageEvent) => {
        const d = JSON.parse(e.data);
        if (d.livePrices) setLivePrices(d.livePrices);
      });
      es.addEventListener('intel',   (e: MessageEvent) => {
        const d = JSON.parse(e.data);
        if (d.marketIntel) setMarketIntel(d.marketIntel);
      });
      es.addEventListener('refreshProgress', (e: MessageEvent) => {
        const d = JSON.parse(e.data);
        setRefreshProgress({ step: d.step, total: d.total, message: d.message });
      });
      es.addEventListener('refreshComplete', () => {
        setRefreshProgress(null);
      });
    }

    connect();
    if (isNotificationSupported() && Notification.permission === 'default') {
      try { Notification.requestPermission(); } catch { /* ignore */ }
    }
    return () => { if (es) es.close(); clearTimeout(reconnectTimer); };
  }, []);

  // ── 1. New pending paper trade (signal detected awaiting execution) ──
  useEffect(() => {
    const prev = prevPendingRef.current;
    paper.pendingTrades.forEach(t => {
      if (!prev.has(t.id)) {
        safeNotify('⏳ Pending Execution', {
          body: `${t.symbol} ${t.side} @ $${t.entry_price_planned?.toFixed(2) ?? t.entry_price.toFixed(2)} — tap to confirm`,
          tag: `pending-${t.id}`,
        });
      }
    });
    prevPendingRef.current = new Set(paper.pendingTrades.map(t => t.id));
  }, [paper.pendingTrades]);

  // ── 2. Trade hit TP or SL (newly closed trades) ──
  useEffect(() => {
    const prevClosed = prevClosedRef.current;
    const notified = notifiedClosedRef.current;
    paper.closedTrades.forEach(t => {
      if (!prevClosed.has(t.id) && !notified.has(t.id)) {
        notified.add(t.id);
        const isWin = (t.pnl ?? 0) >= 0;
        const reason = t.exit_reason?.toUpperCase();
        const isTP = isWin || reason === 'TP';
        const title = isTP ? `🎯 TP Hit: ${t.symbol}` : `🛑 SL Hit: ${t.symbol}`;
        const body = isTP
          ? `+$${(t.pnl ?? 0).toFixed(2)} · closed at $${(t.exit_price ?? 0).toFixed(2)}`
          : `-$${Math.abs(t.pnl ?? 0).toFixed(2)} · closed at $${(t.exit_price ?? 0).toFixed(2)}`;
        safeNotify(title, { body, tag: `closed-${t.id}` });
      }
    });
    prevClosedRef.current = new Set(paper.closedTrades.map(t => t.id));
  }, [paper.closedTrades]);

  // ── 3. Watchlist state changes (only status changes, not every refresh) ──
  useEffect(() => {
    const prevMap = prevWatchlistRef.current;
    watchlist.forEach(item => {
      const oldStatus = prevMap[item.symbol];
      if (oldStatus && oldStatus !== item.status) {
        safeNotify(`👁️ Watchlist: ${item.symbol}`, {
          body: `State changed: ${oldStatus} → ${item.status}`,
          tag: `watchlist-${item.symbol}`,
        });
      }
      prevMap[item.symbol] = item.status;
    });
    // Remove stale symbols
    Object.keys(prevMap).forEach(sym => {
      if (!watchlist.find(w => w.symbol === sym)) delete prevMap[sym];
    });
    prevWatchlistRef.current = prevMap;
  }, [watchlist]);

  return {
    signals, watchlist, powerTrades, winners, history, paper,
    livePrices, marketIntel, sseConnected, refreshProgress, vtc,
  };
}
