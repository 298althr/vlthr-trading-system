// ── Auth API calls ────────────────────────────────────────────────────────────

import { API_BASE, SESSION_KEY } from '../config';

export async function verifyToken(token: string): Promise<{ success: boolean; sessionId?: string; error?: string }> {
  const res = await fetch(`${API_BASE}/api/auth/verify`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ token }),
  });
  const data = await res.json();
  if (res.ok && data.success) {
    localStorage.setItem(SESSION_KEY, data.sessionId);
    localStorage.setItem(`${SESSION_KEY}_timestamp`, Date.now().toString());
    return { success: true, sessionId: data.sessionId };
  }
  return { success: false, error: data.error || 'Invalid code' };
}

export async function setupTOTP(): Promise<{ secret: string; qrCodeURL: string } | null> {
  const res = await fetch(`${API_BASE}/api/auth/setup`);
  const data = await res.json();
  if (res.ok) return { secret: data.secret, qrCodeURL: data.qrCodeURL };
  return null;
}

export async function checkSession(sessionId: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/api/auth/session/${sessionId}`);
    const data = await res.json();
    return data.valid === true;
  } catch {
    return false;
  }
}

export async function logoutSession(sessionId: string): Promise<void> {
  try {
    await fetch(`${API_BASE}/api/auth/logout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessionId }),
    });
  } catch {
    // ignore
  }
  localStorage.removeItem(SESSION_KEY);
  localStorage.removeItem(`${SESSION_KEY}_timestamp`);
}
