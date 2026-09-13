# VLTHR Frontend Refactor Plan

## Objective
Replace the current glassmorphism SPA with a minimal dark-mode app using the VLTHR design system. Two primary colours, flat surfaces, zero GPU layers, lazy-loaded pages.

---

## Design System

### Colour Palette

```
/* Surfaces */
--bg:           #000       /* App background */
--surface:      #0a0a0a    /* Cards, header, nav */
--surface-2:    #141414    /* Inputs, code blocks, secondary cards */
--surface-3:    #1a1a1a    /* Dashboard chrome, tertiary */
--border:       #222       /* All borders */
--border-light: #2a2a2a    /* Hover states */

/* Text */
--text:         #e8e8e8    /* Primary text */
--text-2:       #a0a0a0    /* Secondary text */
--text-muted:   #666       /* Labels, timestamps */

/* Primary Colours (two only) */
--accent:       #e82127    /* VLTHR red — brand, active states, CTAs */
--accent-dim:   rgba(232,33,39,.12)  /* Accent backgrounds */

/* Notification Colours */
--green:        #00e676    /* Success, pass, profit */
--red:          #ff5252    /* Failure, loss, error */
--yellow:       #ffd600    /* Warning, pending */
--orange:       #ff9100    /* Caution, degraded */
--blue:         #2d7ff9    /* Info, neutral data */
```

### Rules
- **Two primary colours**: `--accent` (red) for brand/CTAs, `--text` (white) for content
- **Notification colours** used only for status indicators, gate results, P&L values
- **No gradients.** No `backdrop-filter`. No `box-shadow` on cards (border only)
- **System font stack.** No external font downloads
- **2 keyframes max** (screen-in, pulse). No other animations

### Typography
```
--font:      -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif
--font-mono: 'SF Mono', 'Fira Code', 'JetBrains Mono', Consolas, monospace
--text-xs:   .72rem
--text-sm:   .82rem
--text-base: .94rem
--text-lg:   1.05rem
--text-xl:   1.25rem
--text-2xl:  1.6rem
```

### Spacing & Radius
```
--gap-xs: 6px    --gap-sm: 8px    --gap-md: 12px
--gap-lg: 16px   --gap-xl: 24px
--radius: 8px    --card-radius: 14px    --pill: 999px
```

---

## Current State

| Metric | Current | Target |
|---|---|---|
| CSS lines | 2,494 | ~300 |
| TS/TSX lines | ~5,900 | ~2,500 |
| backdrop-filter | 57 occurrences | 0 |
| @keyframes | 14 | 2 |
| External fonts | Inter + JetBrains Mono | 0 (system stack) |
| Page loading | All eager | React.lazy |
| Bundle (gzip) | ~200 KB | ~40 KB initial |

### Files to Modify
- `frontend/src/index.css` — **Replace entirely** with 300-line version
- `frontend/src/App.css` — **Delete** (merge useful rules into index.css)
- `frontend/src/App.tsx` — Add `React.lazy`, restructure tabs
- `frontend/src/main.tsx` — Remove font imports
- `frontend/index.html` — Remove external font `<link>` tags
- All page components — Wrap exports in `React.memo`
- `frontend/src/components/shared/GlassDropdown.tsx` — Replace with flat dropdown
- `frontend/src/components/shared/SignalCard.tsx` — Remove glassmorphism
- `frontend/src/components/shared/WatchlistDetailSheet.tsx` — Remove blur, use flat surface

### Files to Create
- `frontend/src/components/shared/Button.tsx` — 30-line reusable button (primary/ghost)
- `frontend/src/components/shared/Sparkline.tsx` — 60-line inline SVG sparkline
- `frontend/src/pages/FeaturesPage.tsx` — Feature manager (if feature engineering is built)

---

## Implementation Phases

### Phase 1: CSS Replacement (1 day)

1. **Delete `App.css`**. Move any non-duplicate rules into `index.css`
2. **Write new `index.css`** (~300 lines):
   - CSS reset (10 lines)
   - CSS variables / design tokens (40 lines)
   - Layout utilities: `.app-shell`, `.app-header`, `.app-main`, `.app-nav` (40 lines)
   - Card styles: `.card`, `.card-title`, `.badge` (30 lines)
   - Table styles: `.compare-table`, `.param-list` (30 lines)
   - Form inputs: `.input`, `.select`, `.toggle` (20 lines)
   - Notification styles: `.gate-banner`, `.gate-row`, `.note-box` (30 lines)
   - 2 keyframes: `screen-in`, `pulse` (10 lines)
   - Media queries (20 lines)
   - Page-specific overrides (70 lines max)
3. **Remove all `backdrop-filter`** occurrences (57 in current CSS)
4. **Remove all `box-shadow`** from cards. Use `border: 1px solid var(--border)` only
5. **Remove all gradients** (6 layers in current CSS)
6. **Remove external font `@import` or `<link>`** from `index.html` and `main.tsx`

### Phase 2: Component Cleanup (0.5 day)

7. **Rewrite `GlassDropdown.tsx`** → `Dropdown.tsx`:
   - Replace `backdrop-filter: blur(20px)` with `background: var(--surface-2)`
   - Replace glass border with `border: 1px solid var(--border)`
   - Keep functionality identical
8. **Update `SignalCard.tsx`**:
   - Remove glassmorphism classes
   - Use `.card` class from new CSS
   - Status indicators use `--green`/`--red`/`--yellow` only
9. **Update `WatchlistDetailSheet.tsx`**:
   - Replace bottom sheet blur with flat `background: var(--surface)`
   - Replace shadow with border
10. **Create `Button.tsx`** (30 lines):
    ```tsx
    interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
      variant?: 'primary' | 'ghost';
      size?: 'sm' | 'md';
    }
    export function Button({ variant = 'primary', size = 'md', ...props }: Props) {
      // inline styles, no CSS class needed
    }
    ```
11. **Create `Sparkline.tsx`** (60 lines):
    - Inline SVG, no dependency
    - Props: `data: number[]`, `color: string`, `width: number`, `height: number`

### Phase 3: Lazy Loading & Code Splitting (0.5 day)

12. **Update `App.tsx`**:
    ```tsx
    import { lazy, Suspense } from 'react';
    const DashboardPage = lazy(() => import('./pages/DashboardPage'));
    const SignalsPage   = lazy(() => import('./pages/SignalsPage'));
    const BacktestPage  = lazy(() => import('./pages/BacktestPage'));
    const PaperPage     = lazy(() => import('./pages/PaperPage'));
    const WatchlistPage = lazy(() => import('./pages/WatchlistPage'));
    const VTCPage       = lazy(() => import('./pages/VTCPage'));
    const PowerPage     = lazy(() => import('./pages/PowerPage'));
    ```
13. **Add `<Suspense fallback={null}>`** wrapper around the active page
14. **Update `vite.config.ts`**:
    ```ts
    build: {
      rollupOptions: {
        output: {
          manualChunks: { 'react-vendor': ['react', 'react-dom'] },
        },
      },
      target: 'es2020',
      minify: 'esbuild',
    }
    ```

### Phase 4: Render Optimization (0.5 day)

15. **Wrap all page exports in `React.memo`**:
    ```tsx
    export default memo(DashboardPage);
    ```
16. **Virtualize signal and trade lists**:
    - Cap visible items at 10
    - Render only viewport + 2 buffer rows
    - Use `IntersectionObserver` or simple slice
17. **SSE optimization**:
    - Only update state for the active tab
    - Debounce updates to 500ms minimum

### Phase 5: Verification (0.5 day)

18. **Build and measure**: `npm run build`
    - Initial chunk: target < 40 KB gzip
    - Each page chunk: target < 15 KB gzip
19. **Chrome DevTools audit**:
    - Record 30 seconds
    - Verify 0 GPU layers
    - JS heap < 20 MB
    - No layout thrashing
20. **Visual QA**:
    - All 7 pages render correctly
    - Dark mode is consistent
    - Notification colours appear on: gate results, P&L, trade status, toasts
    - No white flashes on tab switch

---

## Notification Colour Usage

| Colour | Hex | Where to use |
|---|---|---|
| **Success** | `#00e676` | Gate pass, profitable trade, positive P&L, "OPEN" status, WebSocket connected |
| **Failure** | `#ff5252` | Gate fail, losing trade, negative P&L, error toast, "SL hit" |
| **Warning** | `#ffd600` | Pending trade, degraded service, TP hit rate below threshold |
| **Caution** | `#ff9100` | Drawdown approaching limit, crowding detected, low confidence |
| **Info** | `#2d7ff9` | Neutral data values, file tags, informational toasts |
| **Brand** | `#e82127` | Active tab, CTA buttons, logo, section eyebrows, "VLTHR" branding |

---

## Do Not Touch

- Backend API endpoints (`server.cjs`)
- SSE event structure
- Database schema
- Pipeline orchestrator
- Any trading logic or risk parameters
- The `api/` and `hooks/` directories (functionality stays, only UI changes)

---

## Total Timeline: 3 days
- Phase 1: 1 day (CSS replacement — largest task)
- Phase 2: 0.5 day (component cleanup)
- Phase 3: 0.5 day (lazy loading)
- Phase 4: 0.5 day (render optimization)
- Phase 5: 0.5 day (verification)
