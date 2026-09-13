import { useState, useEffect, useCallback } from 'react';
import { SESSION_KEY, SESSION_DURATION } from '../config';
import { checkSession, logoutSession } from '../api/auth';

export function useAuth() {
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [authChecking, setAuthChecking] = useState(true);

  // Check stored session on mount
  useEffect(() => {
    const check = async () => {
      const storedId = localStorage.getItem(SESSION_KEY);
      const storedTs = localStorage.getItem(`${SESSION_KEY}_timestamp`);
      if (!storedId || !storedTs) {
        setAuthChecking(false);
        return;
      }
      const age = Date.now() - parseInt(storedTs);
      if (age > SESSION_DURATION) {
        localStorage.removeItem(SESSION_KEY);
        localStorage.removeItem(`${SESSION_KEY}_timestamp`);
        setAuthChecking(false);
        return;
      }
      const valid = await checkSession(storedId);
      if (valid) {
        setSessionId(storedId);
        setIsAuthenticated(true);
      } else {
        localStorage.removeItem(SESSION_KEY);
        localStorage.removeItem(`${SESSION_KEY}_timestamp`);
      }
      setAuthChecking(false);
    };
    check();
  }, []);

  // Periodic session validation every 5 minutes
  useEffect(() => {
    if (!isAuthenticated || !sessionId) return;
    const interval = setInterval(async () => {
      const valid = await checkSession(sessionId);
      if (!valid) {
        setIsAuthenticated(false);
        setSessionId(null);
        localStorage.removeItem(SESSION_KEY);
        localStorage.removeItem(`${SESSION_KEY}_timestamp`);
      }
    }, 5 * 60 * 1000);
    return () => clearInterval(interval);
  }, [isAuthenticated, sessionId]);

  const handleAuthenticated = useCallback((newSessionId: string) => {
    setSessionId(newSessionId);
    setIsAuthenticated(true);
  }, []);

  const handleLogout = useCallback(async () => {
    if (sessionId) await logoutSession(sessionId);
    setSessionId(null);
    setIsAuthenticated(false);
  }, [sessionId]);

  return { isAuthenticated, sessionId, authChecking, handleAuthenticated, handleLogout };
}
