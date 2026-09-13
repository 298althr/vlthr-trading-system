# Frontend Handoff: Bybit Demo Trades Tab

**Status:** READY FOR IMPLEMENTATION
**Date:** Jul 19, 2026
**Gate:** PASS (backend + DB + pipeline complete, frontend pending)

## Objective

Add a new "DEMO" tab to the VLTHR frontend that shows live Bybit demo trades running in parallel with paper trades. User can switch between monitoring paper trades and Bybit demo trades. All performance is shown in percentage terms since the two accounts have different balances.

## What Was Done (Backend + Pipeline)

### Pipeline (`portfolio_orchestrator.py`)
- When `EXECUTION_MODE=demo`, every approved signal now executes on BOTH paper and Bybit simultaneously.
- Paper trade sizing uses paper account balance.
- Bybit trade sizing uses Bybit demo account balance.
- Both use the SAME `risk_pct` (e.g., 3.0% for DQS 65-74 tier). This means position sizes differ in absolute dollar terms but are proportionally identical.
- SL/TP prices are identical (same entry, same ATR, same multipliers).
- Bybit reconciliation runs each pipeline cycle: detects positions closed by SL/TP on Bybit and records close price, PnL, and PnL%.

### Database (`paper_trades` table)
14 new columns added:

| Column | Type | Description |
|--------|------|-------------|
| `bybit_order_id` | VARCHAR(64) | Bybit order ID |
| `bybit_qty` | NUMERIC(18,4) | Position size on Bybit |
| `bybit_entry_price` | NUMERIC(18,8) | Entry price on Bybit |
| `bybit_sl_price` | NUMERIC(18,8) | Stop loss on Bybit |
| `bybit_tp_price` | NUMERIC(18,8) | Take profit on Bybit |
| `bybit_status` | VARCHAR(20) | PENDING / OPEN / CLOSED |
| `bybit_close_price` | NUMERIC(18,8) | Close price on Bybit |
| `bybit_pnl_usd` | NUMERIC(12,4) | PnL in USD on Bybit |
| `bybit_pnl_pct` | NUMERIC(8,4) | PnL as % of Bybit balance |
| `bybit_account_balance` | NUMERIC(12,2) | Bybit balance at trade open |
| `bybit_risk_pct` | NUMERIC(5,2) | Risk % used for this trade |
| `bybit_dollar_risk` | NUMERIC(12,2) | Dollar risk on Bybit |
| `bybit_close_time` | TIMESTAMPTZ | When Bybit position closed |
| `bybit_close_reason` | VARCHAR(32) | SL/TP or other reason |

### Backend API Endpoints (already live on port 3003)

1. **`GET /api/bybit/trades?status=OPEN|CLOSED|ALL`**
   Returns all trades that have Bybit execution data.
   ```json
   {
     "trades": [
       {
         "id": 123,
         "symbol": "BTCUSDT",
         "side": "LONG",
         "status": "OPEN",
         "bybit_order_id": "abc-123",
         "bybit_qty": 0.023,
         "bybit_entry_price": 64515.90,
         "bybit_sl_price": 63225.50,
         "bybit_tp_price": 66451.30,
         "bybit_status": "OPEN",
         "bybit_pnl_usd": null,
         "bybit_pnl_pct": null,
         "bybit_account_balance": 49985.83,
         "bybit_risk_pct": 3.0,
         "bybit_dollar_risk": 1499.57,
         "confidence": 72,
         "entry_strategy": "trend_following",
         "created_at": "2026-07-19T..."
       }
     ]
   }
   ```

2. **`GET /api/bybit/account`**
   Returns Bybit account summary + paper account for comparison.
   ```json
   {
     "bybit": {
       "balance": 49985.83,
       "open_trades": [...],
       "closed_count": 5,
       "wins": 3,
       "win_rate": "60.00",
       "total_pnl_usd": "125.50",
       "avg_pnl_pct": "0.2500",
       "best_trade": "80.00",
       "worst_trade": "-30.00"
     },
     "paper": {
       "balance": "10000.00",
       "equity": "10050.00",
       "margin_used": "500.00",
       "realized_pnl": "50.00"
     }
   }
   ```

3. **`GET /api/bybit/compare`**
   Side-by-side comparison of paper vs Bybit for each trade.
   ```json
   {
     "comparisons": [
       {
         "symbol": "BTCUSDT",
         "side": "LONG",
         "status": "CLOSED",
         "net_pnl_usd": 12.50,
         "net_pnl_pct": 0.25,
         "bybit_pnl_usd": 62.75,
         "bybit_pnl_pct": 0.25,
         "bybit_status": "CLOSED",
         "bybit_close_reason": "SL/TP",
         ...
       }
     ]
   }
   ```

## Frontend Implementation Spec

### 1. Add Tab Type

In `types.ts`:
```typescript
export type Tab = 'dashboard' | 'signals' | 'vtc' | 'backtest' | 'paper' | 'demo';
```

### 2. Create `DemoPage.tsx`

Create `/frontend/src/pages/DemoPage.tsx`. This page shows Bybit demo trades.

**Layout requirements:**
- **Top section:** Two account cards side by side (Bybit Demo vs Paper). Show balance, open trades count, win rate, total PnL. All PnL shown in BOTH absolute USD and percentage.
- **Middle section:** Tab toggle within the page: "OPEN" | "CLOSED" | "COMPARE"
  - **OPEN tab:** List of open Bybit positions with symbol, side, qty, entry price, SL, TP, risk %, DQS, strategy. Show live PnL if available (use live prices from SSE).
  - **CLOSED tab:** List of closed Bybit trades with close price, PnL USD, PnL %, close reason, hold time.
  - **COMPARE tab:** Side-by-side table showing each trade on both paper and Bybit. Columns: Symbol, Side, Paper PnL %, Bybit PnL %, Delta %, Paper Status, Bybit Status. This is the key view since percentages should match.
- **Bottom section:** Performance metrics chart (optional, can use simple bars). Show cumulative PnL % over time for both accounts.

**Data fetching:**
- Poll `GET /api/bybit/account` every 5 seconds for account summary.
- Poll `GET /api/bybit/trades?status=OPEN` every 5 seconds for open trades.
- Poll `GET /api/bybit/trades?status=CLOSED` every 10 seconds for closed trades.
- Poll `GET /api/bybit/compare` every 10 seconds for comparison view.
- Use `API_BASE` from `../config` for all fetch calls.
- Reuse `sse.livePrices` from `useSSE` hook for live price injection into open trades (same pattern as PaperPage).

**Styling:**
- Follow existing design system: glass cards, `var(--text-primary)`, `var(--text-secondary)`, `var(--text-tertiary)`, `var(--primary)`, etc.
- Use lucide-react icons (e.g., `TrendingUp`, `TrendingDown`, `Wallet`, `Activity`).
- Green for profit, red for loss, same as PaperPage.
- Bybit-specific accent color: use a distinct color (e.g., `#F7A600` Bybit brand orange) for Bybit-specific elements to visually distinguish from paper trades.

### 3. Add Tab to `App.tsx`

In `App.tsx`:
- Import `DemoPage`.
- Add to the `activeTab` conditional rendering:
  ```tsx
  {activeTab === 'demo' && <DemoPage livePrices={sse.livePrices} />}
  ```
- Add to bottom navigation array:
  ```tsx
  { tab: 'demo' as Tab, Icon: TrendingUp, label: 'DEMO' },
  ```
- Import `TrendingUp` from lucide-react.

### 4. Props Interface for DemoPage

```typescript
interface DemoPageProps {
  livePrices: Record<string, { price: number; change24h: number }>;
}
```

The page manages its own state via polling. No need to modify the SSE hook.

### 5. Key Display Rules

- **All PnL shown as percentage first, USD second.** Since accounts have different balances, percentage is the primary comparison metric.
- **Risk % is the same for both accounts.** Display it prominently.
- **Entry/SL/TP prices are identical.** Only qty and dollar amounts differ.
- **Bybit qty will be larger** because Bybit demo balance ($49,985) is larger than paper balance ($10,000). This is expected and correct.
- **Status badges:** Use "OPEN" (green), "CLOSED" (gray), "PENDING" (amber) for Bybit status.

### 6. Existing Patterns to Follow

- `PaperPage.tsx` is the closest reference. It has SwipeCard for metrics, trade tables, and account display.
- `DashboardPage.tsx` shows the live price strip pattern.
- All pages use `className="glass"` for card backgrounds.
- Use `className="pressable"` for interactive elements.

## Files to Create/Modify

| File | Action |
|------|--------|
| `frontend/src/types.ts` | Add `'demo'` to `Tab` type |
| `frontend/src/pages/DemoPage.tsx` | **CREATE** - new page component |
| `frontend/src/App.tsx` | Import DemoPage, add tab rendering + nav button |

## Do NOT Modify

- `portfolio_orchestrator.py` - already updated
- `db_migration.py` - already updated
- `server.cjs` - API endpoints already added
- `bybit_executor.py` - no changes needed
- `useSSE.ts` - DemoPage handles its own polling

## Testing

After implementation:
1. `docker compose up -d --build frontend`
2. Navigate to `http://localhost:5175`
3. Click "DEMO" tab in bottom nav
4. Verify account cards show Bybit balance and paper balance
5. Verify open/closed trades load from API
6. Verify comparison view shows matching PnL percentages
7. Verify live prices update for open trades
