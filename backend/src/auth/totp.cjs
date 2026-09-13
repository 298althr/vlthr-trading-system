const speakeasy = require('speakeasy');
const crypto = require('crypto');

const SESSION_DURATION = 1 * 60 * 60 * 1000; // 1 hour in milliseconds
const sessions = new Map(); // sessionId -> { createdAt: timestamp }

function generateSessionId() {
  return crypto.randomUUID();
}

function cleanExpiredSessions() {
  const now = Date.now();
  for (const [sessionId, session] of sessions.entries()) {
    if (now - session.createdAt > SESSION_DURATION) {
      sessions.delete(sessionId);
      console.log('[Auth] Session expired:', sessionId);
    }
  }
}

async function getOrGenerateTOTPSecret(pool) {
  const accRes = await pool.query('SELECT id, totp_secret FROM paper_account LIMIT 1');
  const account = accRes.rows[0];

  if (account && account.totp_secret) {
    return account.totp_secret;
  }

  const newSecret = speakeasy.generateSecret({ name: 'VLTHR Dashboard', issuer: 'VLTHR' }).base32;

  if (account) {
    await pool.query('UPDATE paper_account SET totp_secret = $1 WHERE id = $2', [newSecret, account.id]);
  } else {
    await pool.query('INSERT INTO paper_account (balance, equity, totp_secret) VALUES (10000.00, 10000.00, $1)', [newSecret]);
  }

  return newSecret;
}

function verifyTOTP(token, secret) {
  return speakeasy.totp.verify({ secret, token, encoding: 'base32', window: 1 });
}

// Clean sessions every hour
setInterval(cleanExpiredSessions, 60 * 60 * 1000);

module.exports = {
  SESSION_DURATION,
  sessions,
  generateSessionId,
  cleanExpiredSessions,
  getOrGenerateTOTPSecret,
  verifyTOTP,
};
