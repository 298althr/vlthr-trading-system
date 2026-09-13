// ── Configuration ────────────────────────────────────────────────────────────

// If VITE_API_BASE is set, use it (e.g. Vercel deployment pointing to ngrok).
// Otherwise: relative paths in production (nginx proxies /api/), localhost in dev.
export const API_BASE = import.meta.env.VITE_API_BASE
  ?? (import.meta.env.PROD ? '' : 'http://localhost:3002');

export const SESSION_KEY = 'vlthr_session_id';
export const SESSION_DURATION = 1 * 60 * 60 * 1000; // 1 hour
