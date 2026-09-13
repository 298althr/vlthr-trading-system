import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './components/ErrorBoundary'

// ── Service Worker update detection ──────────────────────────────────────
// Installs new SW immediately and reloads once activated (no logout broadcast)
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').then(reg => {
    reg.addEventListener('updatefound', () => {
      const newWorker = reg.installing;
      if (!newWorker) return;
      newWorker.addEventListener('statechange', () => {
        if (newWorker.state === 'installed' && navigator.serviceWorker.controller) {
          // New version waiting — force activate now
          newWorker.postMessage('skipWaiting');
        }
        if (newWorker.state === 'activated') {
          console.log('[SW] New version activated — reloading');
          window.location.reload();
        }
      });
    });
  }).catch(() => {});
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
