const { Router } = require('express');
const { getOrGenerateTOTPSecret, verifyTOTP, generateSessionId, sessions, SESSION_DURATION } = require('../auth/totp.cjs');

module.exports = function createAuthRouter(pool) {
  const router = Router();

  router.get('/api/auth/test', (req, res) => {
    res.json({ message: 'Auth code is running', timestamp: Date.now() });
  });

  router.get('/api/auth/setup', async (req, res) => {
    const setupToken = process.env.DASHBOARD_AUTH_KEYS;
    const providedToken = req.headers['x-setup-token'];
    if (!setupToken || providedToken !== setupToken) {
      return res.status(403).json({ error: 'Setup token required' });
    }
    try {
      const secret = await getOrGenerateTOTPSecret(pool);
      const qrCodeURL = `otpauth://totp/VLTHR%20Dashboard?secret=${secret}&issuer=VLTHR`;
      res.json({ secret, qrCodeURL, message: 'Scan this QR code in Google Authenticator app' });
    } catch (e) {
      console.error('[Auth] Setup error:', e);
      res.status(500).json({ error: 'Failed to generate setup data' });
    }
  });

  router.post('/api/auth/verify', async (req, res) => {
    const { token } = req.body;
    if (!token || token.length !== 6) {
      return res.status(400).json({ error: 'Invalid token format' });
    }
    try {
      const secret = await getOrGenerateTOTPSecret(pool);
      const isValid = verifyTOTP(token, secret);
      if (isValid) {
        const sessionId = generateSessionId();
        sessions.set(sessionId, { createdAt: Date.now() });
        console.log('[Auth] Session created:', sessionId);
        res.json({ success: true, sessionId });
      } else {
        res.status(401).json({ error: 'Invalid token' });
      }
    } catch (e) {
      console.error('[Auth] Verify error:', e);
      res.status(500).json({ error: 'Failed to verify token' });
    }
  });

  router.get('/api/auth/session/:sessionId', (req, res) => {
    const { sessionId } = req.params;
    const session = sessions.get(sessionId);
    if (!session) return res.status(401).json({ valid: false });
    if (Date.now() - session.createdAt > SESSION_DURATION) {
      sessions.delete(sessionId);
      return res.status(401).json({ valid: false, expired: true });
    }
    res.json({ valid: true });
  });

  router.post('/api/auth/logout', (req, res) => {
    const { sessionId } = req.body;
    if (sessionId) {
      sessions.delete(sessionId);
      console.log('[Auth] Session invalidated:', sessionId);
    }
    res.json({ success: true });
  });

  return router;
};
