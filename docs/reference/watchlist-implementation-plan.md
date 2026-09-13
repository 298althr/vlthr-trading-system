# Watchlist Interactive Funnel — Implementation Plan

**Scope:** Transform the passive Watchlist into a state-driven discovery funnel that feeds Signals → Paper trades, per the product blueprint in `watchlist update.md`.

**System Context:**
- Frontend: React + TypeScript (Vite) | Backend: Node.js/Express (CJS) | DB: PostgreSQL (Supabase)
- Data flow: SSE stream (`/api/signals/stream`) pushes DB state every ~15s
- Pipeline: `scan_signals.py` → `sync_high_confidence.py` → Supabase → backend SSE
- Gate logic: 4 gates (Session, ADX≥25, 4h Direction, RSI<40) + 1 bonus (EMA8>EMA21)
- Current max daily trades: 6 (`portfolio_config.py`)

---

## Phase 0: CRITICAL FIX — Push Notifications (Mobile Crash)

**Problem:** On mobile (iOS Safari / Android PWA), allowing notifications crashes the app with `TypeError: Failed to construct 'Notification': Illegal constructor`. Reloading does nothing because the crash re-occurs on every new signal.

**Root Cause:** `src/hooks/useSSE.ts` used `new Notification(...)` directly. iOS Safari and some Android PWAs do not support the `Notification` constructor — they only support `self.registration.showNotification()` via the service worker.

**Fix Applied:**

**File:** `src/hooks/useSSE.ts`

1. Added `isNotificationSupported()` helper that checks `typeof Notification === 'function'`.
2. Added `safeNotify()` async helper:
   - **Path A:** Try `new Notification()` (desktop Chrome/FF).
   - **Path B:** If that throws, fall back to `navigator.serviceWorker.ready.then(reg => reg.showNotification(...))` (iOS PWA + Android).
   - **Path C:** If both fail, silently drop — app never crashes.
3. Wrapped `Notification.requestPermission()` in `try/catch`.
4. **Filtered to important signals only:** Only EXCELLENT, GOOD, or confidence ≥75 signals trigger a push. Prevents notification spam.

```typescript
const important = ['EXCELLENT', 'GOOD'].includes(sig.signal_strength?.toUpperCase()) || sig.confidence >= 75;
if (important) {
  safeNotify('VLTHR Signal', {
    body: `${sig.asset} — ${sig.signal_strength} (${sig.confidence}/100)`,
    tag: `signal-${sig.id}`,
  });
}
```

**Build verified:** `npm run build` passes with zero TS errors.

---

## Phase 1: Backend — Expose Per-Gate Breakdown

**Goal:** The frontend needs individual gate pass/fail status, not just `gates_passed: "3/4"`.

### 1.1 Update `watchlist_current` schema

**File:** `departments/strategy_research/BACKTESTER/strategy/signals/sync_high_confidence.py`

The `watchlist_current` table already has these columns (confirmed from schema):
- `gate_session_ok`, `gate_adx_ok`, `gate_direction_ok`, `gate_rsi_ok`, `gate_bull_4h_ok`

The backend `mapWatchlistItem` currently **ignores** these. Fix:

**File:** `backend/server.cjs` — `mapWatchlistItem()`

```javascript
function mapWatchlistItem(row) {
  // ... existing code ...
  return {
    // ... existing fields ...
    gates: {
      session: { pass: row.gate_session_ok, label: 'Session' },
      adx:     { pass: row.gate_adx_ok,     label: 'ADX ≥25' },
      direction:{ pass: row.gate_direction_ok, label: '4h Direction' },
      rsi:     { pass: row.gate_rsi_ok,     label: 'RSI <40' },
      bull:    { pass: row.gate_bull_4h_ok, label: 'EMA Trend' },
    },
    gates_passed: row.gates_passed || '0/4',
    // ...
  };
}
```

**Also update** `src/types.ts` — extend `WatchlistItem`:

```typescript
export interface WatchlistGate {
  pass: boolean;
  label: string;
}

export interface WatchlistItem {
  // ... existing fields ...
  gates?: Record<'session'|'adx'|'direction'|'rsi'|'bull', WatchlistGate>;
}
```

---

## Phase 2: Frontend — Gate-Analysis Detail Sheet

**Goal:** Clicking a Watchlist card opens a sliding bottom sheet (not the existing `SymbolDetailModal` which only shows market intel).

### 2.1 New Component: `WatchlistDetailSheet`

**File:** `src/components/shared/WatchlistDetailSheet.tsx`

**Layout (bottom sheet, 70vh height):**
```
┌─────────────────────────────────────┐
│  Handle bar                          │
│  [BTCUSDT]  CONFIRMED                │
│  $67,420  ·  LONDON  ·  BULLISH      │
├─────────────────────────────────────┤
│  GATE CHECKLIST                      │
│  ✓ Session    ✓ ADX ≥25              │
│  ✓ Direction  ✗ RSI <40              │
│  ✓ EMA Trend                         │
├─────────────────────────────────────┤
│  MICRO-CHARTS                        │
│  [RSI 15m sparkline]  [ADX 4h spark] │
├─────────────────────────────────────┤
│  CONTEXTUAL ACTION (state-driven)    │
│  [ "Go to Live Signal" button ]      │
└─────────────────────────────────────┘
```

**State-driven action mapping:**

| Status | Action Button | Handler |
|--------|--------------|---------|
| `CONFIRMED` | "Go to Live Signal" | `onNavigate('signals', symbol)` + filter |
| `APPROACHING` | "Pre-Stage Paper Trade" | `onPrestage(symbol)` → POST `/api/paper/prestage` |
| `WATCHING` | "Set Volatility Alert" | `onSetAlert(symbol, condition)` → (Phase 4) |
| `OFF_SESSION` | "Notify on Session Open" | `onNotify(symbol)` → (Phase 4) |

### 2.2 Wire into `WatchlistPage`

**File:** `src/pages/WatchlistPage.tsx`

Add click handler to each card:
```typescript
interface Props {
  watchlist: WatchlistItem[];
  onSelectItem: (item: WatchlistItem) => void;  // NEW
}
```

Wrap each card in a `<div onClick={() => onSelectItem(item)} style={{cursor:'pointer'}}>`.

### 2.3 Wire into `App.tsx`

**File:** `src/App.tsx`

Add state:
```typescript
const [selectedWatchlistItem, setSelectedWatchlistItem] = useState<WatchlistItem | null>(null);
```

Render `WatchlistDetailSheet` alongside existing `SymbolDetailModal`:
```typescript
<WatchlistDetailSheet
  item={selectedWatchlistItem}
  onClose={() => setSelectedWatchlistItem(null)}
  onNavigate={(tab, symbol) => { setActiveTab(tab); setSearchQuery(symbol); }}
  onPrestage={(symbol) => handlePrestage(symbol)}
/>
```

---

## Phase 3: Backend — Pre-Stage API

**Goal:** Allow users to pre-commit a paper trade while a symbol is in `APPROACHING` state.

### 3.1 New Table: `prestaged_trades`

```sql
CREATE TABLE prestaged_trades (
  id SERIAL PRIMARY KEY,
  symbol TEXT NOT NULL,
  desired_side TEXT,        -- 'LONG' or 'SHORT'
  desired_risk_pct NUMERIC, -- e.g. 1.5
  desired_leverage INT DEFAULT 4,
  watchlist_gate_snapshot JSONB, -- snapshot of gates at prestage time
  status TEXT DEFAULT 'WAITING', -- 'WAITING' | 'TRIGGERED' | 'EXPIRED'
  created_at TIMESTAMPTZ DEFAULT NOW(),
  triggered_at TIMESTAMPTZ,
  UNIQUE(symbol, status) WHERE status = 'WAITING'
);
```

### 3.2 New API Endpoints

**File:** `backend/server.cjs`

```javascript
// POST /api/paper/prestage
// Body: { symbol, side?, riskPct?, leverage? }
// Creates a prestaged trade. If symbol later hits CONFIRMED,
// the pipeline auto-promotes it to a real PENDING paper trade.

// GET /api/paper/prestaged
// Returns all WAITING prestaged trades for the dashboard.

// DELETE /api/paper/prestage/:id
// Cancel a prestaged trade.
```

### 3.3 Pipeline Hook: Auto-Promote on CONFIRMED

**File:** `paper_trade_unzipped/vlthr-signal-dashboard/engine/run_pipeline.py`

In Step 5 (after upserting approved signals), check for prestaged trades:
```python
# After: n_approved = len(approved_signals)
# Check prestaged_trades for symbols that just became CONFIRMED
conn.execute("""
  SELECT * FROM prestaged_trades
  WHERE status = 'WAITING' AND symbol = ANY(%s)
""", ([s.symbol for s in approved_signals],))
# For each match: create real paper trade, update status to 'TRIGGERED'
```

---

## Phase 4: Signals Page Enhancements

**Goal:** Add trace-gates, editable R:R, and auto-pilot toggle.

### 4.1 "Trace Gates" Flip Card

**File:** `src/components/shared/SignalCard.tsx`

Add a small (i) icon next to the confidence score. On click:
- Flip the card (CSS 3D transform) to show gate breakdown
- Show which specific gate failed (from `technical_score`, `market_structure_score`, etc.)
- Map scores back to gates: Tech→RSI/ADX, Struct→Direction, F/OI→OI/Funding, Session→Session

**Data already available** in `HCSSignal`: `technical_score`, `market_structure_score`, `funding_oi_score`, `session_symbol_score`.

### 4.2 Auto-Pilot Toggle

**File:** `src/pages/SignalsPage.tsx`

Already partially implemented in `App.tsx` (auto-executes EXCELLENT). Extend:
```typescript
const [autoThreshold, setAutoThreshold] = useState(75); // user-defined
```

Add UI toggle in SignalsPage header:
```
[Auto-Pilot] [threshold slider: 50———————100]
```

When enabled + signal confidence ≥ threshold → auto-POST `/api/paper/trade`.

### 4.3 Pre-Staged Ghost Cards

**File:** `src/pages/SignalsPage.tsx`

Fetch prestaged trades via `GET /api/paper/prestaged`.
Render them as "ghost" cards (50% opacity, dashed border):
```
[ BTCUSDT ]  PRE-STAGED
Waiting for 4th gate...
[Cancel] [Edit]
```

When the symbol hits CONFIRMED, the ghost card animates to full opacity and a toast fires.

---

## Phase 5: Cross-Page Flywheel

### 5.1 Watchlist → Signals (Flow A)

Already partially works: CONFIRMED signals appear in `high_confidence_signals` which feeds the Signals page.

**Enhancement:** When a CONFIRMED signal arrives via SSE, if the user is on the Watchlist tab, fire a toast:
```typescript
useEffect(() => {
  if (activeTab === 'watchlist') {
    const newConfirmed = sse.signals.filter(s => s.confidence >= 50 && !seenIds.has(s.id));
    newConfirmed.forEach(s => addToast('success', 'Signal Confirmed', `${s.asset} is now live on Signals.`));
  }
}, [sse.signals, activeTab]);
```

### 5.2 Signals → Paper (Flow B)

Already works: clicking PAPER on a signal card opens the paper trade, then `App.tsx` does `setActiveTab('paper')`.

**Enhancement:** Add a micro-animation. When PAPER is clicked:
1. Button transforms into a "flying" chip that animates toward the Paper tab icon
2. After 300ms, navigate to Paper tab with the new trade highlighted

**File:** `src/App.tsx` — track `lastCreatedTradeId`:
```typescript
const [highlightTradeId, setHighlightTradeId] = useState<number | null>(null);
// On paper success: setHighlightTradeId(data.tradeId); setActiveTab('paper');
```

**File:** `src/pages/PaperPage.tsx` — scroll-to/highlight the new trade.

### 5.3 Paper → Watchlist Historical View (Flow C)

**New endpoint:** `GET /api/watchlist/historical?symbol=X&timestamp=Y`

Query `watchlist_current` (or a new `watchlist_history` table) for the exact scan row that existed when the trade was created.

**File:** `src/pages/PaperPage.tsx`

Add a small "View Setup" link on each closed/active trade card. Clicking it opens `WatchlistDetailSheet` in **read-only historical mode**, showing the gates as they were at entry time.

---

## Phase 6: Alert System (Deferred — High Complexity)

The blueprint asks for:
- "Notify Me on Session Open"
- "Alert me if RSI crosses 60"

**Decision:** Defer to Phase 6. Requires:
- New `user_alerts` table (symbol, condition, triggered, user_id)
- Background worker to evaluate conditions every 15m scan
- Push notification infra (Telegram bot already exists, could reuse)

**Quick-win alternative:** Use the `safeNotify` helper (from Phase 0) so the notification never crashes the app:
```typescript
safeNotify('BTCUSDT Approaching', { body: '3/4 gates now passing', tag: 'watchlist-btc' });
```

---

## Implementation Order (Priority)

| # | Task | Effort | Impact | Status |
|---|------|--------|--------|--------|
| 0 | **Fix mobile push-notification crash** (`safeNotify` + service-worker fallback) | 20 min | **Critical** | **DONE** |
| 1 | Backend: expose per-gate booleans in `mapWatchlistItem` | 30 min | High | Pending |
| 2 | Frontend: `WatchlistDetailSheet` component + gate checklist | 2h | High | Pending |
| 3 | Wire click handler in `WatchlistPage` → open sheet | 30 min | High | Pending |
| 4 | State-driven action buttons (CONFIRMED→Signals, APPROACHING→Prestage) | 1h | High | Pending |
| 5 | Backend: `prestaged_trades` table + API | 1.5h | Medium | Pending |
| 6 | Signals page: ghost cards for prestaged trades | 1h | Medium | Pending |
| 7 | Auto-pilot threshold slider + logic | 1h | Medium | Pending |
| 8 | SignalCard: "Trace Gates" flip view | 1.5h | Medium | Pending |
| 9 | Paper→Watchlist historical link | 1h | Low | Pending |
| 10 | Alert system (browser notifications) | 1h | Low | Pending |

**Total estimated effort: ~11 hours (Phase 0 already complete)**

---

## Files to Touch

### Backend
- `backend/server.cjs` — `mapWatchlistItem`, new endpoints
- `backend/routes/paper.cjs` (or inline in server.cjs) — prestage CRUD

### Frontend
- `src/types.ts` — extend `WatchlistItem`
- `src/App.tsx` — state, handlers, sheet rendering
- `src/pages/WatchlistPage.tsx` — click handlers, pass-through
- `src/pages/SignalsPage.tsx` — ghost cards, auto-pilot UI
- `src/components/shared/SignalCard.tsx` — trace-gates flip
- `src/components/shared/WatchlistDetailSheet.tsx` — **NEW**

### Pipeline
- `engine/run_pipeline.py` — auto-promote prestaged on CONFIRMED
- `sync_high_confidence.py` — confirm `gate_*_ok` columns exist

### DB
- `prestaged_trades` table (new)
- `watchlist_history` table (optional, for Flow C)
