import { useState, useRef, useCallback } from 'react';
import { verifyToken, setupTOTP } from '../api/auth';

interface Props {
  onAuthenticated: (sessionId: string) => void;
}

export default function AuthPage({ onAuthenticated }: Props) {
  const [digits, setDigits] = useState<string[]>(['', '', '', '', '', '']);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [showSetup, setShowSetup] = useState(false);
  const [setupData, setSetupData] = useState<{ secret: string; qrCodeURL: string } | null>(null);
  const inputsRef = useRef<(HTMLInputElement | null)[]>([]);

  const token = digits.join('');

  const handleDigitChange = useCallback((index: number, value: string) => {
    const v = value.replace(/\D/g, '').slice(0, 1);
    if (!v) return;
    setDigits(prev => {
      const next = [...prev];
      next[index] = v;
      return next;
    });
    setError('');
    // Auto-advance
    if (index < 5) {
      inputsRef.current[index + 1]?.focus();
    }
  }, []);

  const handleKeyDown = useCallback((index: number, e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Backspace') {
      if (digits[index]) {
        setDigits(prev => { const next = [...prev]; next[index] = ''; return next; });
      } else if (index > 0) {
        inputsRef.current[index - 1]?.focus();
        setDigits(prev => { const next = [...prev]; next[index - 1] = ''; return next; });
      }
    } else if (e.key === 'ArrowLeft' && index > 0) {
      inputsRef.current[index - 1]?.focus();
    } else if (e.key === 'ArrowRight' && index < 5) {
      inputsRef.current[index + 1]?.focus();
    }
  }, [digits]);

  const handlePaste = useCallback((e: React.ClipboardEvent) => {
    e.preventDefault();
    const text = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6);
    const next = ['', '', '', '', '', ''];
    for (let i = 0; i < text.length; i++) next[i] = text[i];
    setDigits(next);
    if (text.length < 6) {
      inputsRef.current[text.length]?.focus();
    }
  }, []);

  const handleVerify = async () => {
    if (token.length !== 6) {
      setError('Please enter a 6-digit code');
      return;
    }
    setLoading(true);
    setError('');
    const result = await verifyToken(token);
    setLoading(false);
    if (result.success && result.sessionId) {
      onAuthenticated(result.sessionId);
    } else {
      setError('Invalid code. Please try again.');
      setDigits(['', '', '', '', '', '']);
      inputsRef.current[0]?.focus();
    }
  };

  const handleSetup = async () => {
    const data = await setupTOTP();
    if (data) {
      setSetupData(data);
      setShowSetup(true);
    } else {
      setError('Failed to get setup data');
    }
  };

  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'radial-gradient(ellipse at 60% 40%, #0a1628 0%, #06101e 40%, #000000 100%)',
      padding: 20,
      position: 'relative',
      overflow: 'hidden',
    }}>
      {/* Glow orbs */}
      <div style={{
        position: 'absolute', top: '-10%', right: '-10%', width: 300, height: 300,
        borderRadius: '50%', background: 'radial-gradient(circle, rgba(0,174,77,0.08) 0%, transparent 70%)',
        pointerEvents: 'none',
      }} />
      <div style={{
        position: 'absolute', bottom: '-10%', left: '-10%', width: 300, height: 300,
        borderRadius: '50%', background: 'radial-gradient(circle, rgba(0,102,153,0.10) 0%, transparent 70%)',
        pointerEvents: 'none',
      }} />

      <div className="auth-glass-card" style={{
        maxWidth: 400,
        width: '100%',
        background: 'rgba(255, 255, 255, 0.03)',
        backdropFilter: 'blur(24px) saturate(180%)',
        WebkitBackdropFilter: 'blur(24px) saturate(180%)',
        border: '1px solid rgba(255, 255, 255, 0.08)',
        borderTop: '1px solid rgba(255, 255, 255, 0.12)',
        borderRadius: 24,
        padding: 36,
        boxShadow: '0 8px 32px rgba(0,0,0,0.6), 0 1px 0 rgba(255,255,255,0.06) inset, 0 -1px 0 rgba(0,0,0,0.3) inset',
        animation: 'fadeInUp 0.5s cubic-bezier(0.16, 1, 0.3, 1)',
        position: 'relative',
        zIndex: 1,
      }}>
        {/* Logo */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginBottom: 28 }}>
          <img
            src="/favicon.svg"
            alt="VLTHR"
            style={{
              width: 56, height: 56,
              filter: 'drop-shadow(0 0 12px rgba(0,174,77,0.4))',
              animation: 'breathe 2s ease-in-out infinite',
            }}
          />
          <div style={{ fontSize: 24, fontWeight: 700, color: '#adc6ff', marginTop: 12, fontFamily: "'JetBrains Mono', monospace", letterSpacing: '-0.5px' }}>
            VLTHR
          </div>
          <div style={{ fontSize: 13, color: '#c1c6d7', marginTop: 4, opacity: 0.7 }}>
            Two-Factor Authentication
          </div>
        </div>

        {showSetup && setupData ? (
          <div>
            <div style={{ marginBottom: 16, fontSize: 13, color: '#c1c6d7', textAlign: 'center' }}>
              Scan this QR code in Google Authenticator:
            </div>
            <div style={{
              background: 'white', padding: 16, borderRadius: 12,
              display: 'flex', justifyContent: 'center', marginBottom: 16,
            }}>
              <img
                src={`https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(setupData.qrCodeURL)}`}
                alt="QR Code"
                style={{ width: 200, height: 200 }}
              />
            </div>
            <div style={{ marginBottom: 20, fontSize: 12, color: '#c1c6d7', textAlign: 'center', wordBreak: 'break-all' }}>
              <strong>Secret Key:</strong><br/>{setupData.secret}
            </div>
            <button
              onClick={() => setShowSetup(false)}
              className="auth-primary-btn"
              style={{ width: '100%', padding: 14, borderRadius: 12, border: 'none', background: '#adc6ff', color: '#002e69', fontWeight: 700, cursor: 'pointer', fontSize: 14, transition: 'all 0.2s ease', }}
              onMouseEnter={e => (e.currentTarget.style.opacity = '0.9')}
              onMouseLeave={e => (e.currentTarget.style.opacity = '1')}
            >
              I've Added It - Enter Code
            </button>
          </div>
        ) : (
          <div>
            <div style={{ marginBottom: 20, fontSize: 13, color: '#c1c6d7', textAlign: 'center' }}>
              Enter the 6-digit code from Google Authenticator
            </div>

            {/* 6 individual digit inputs */}
            <div style={{ display: 'flex', gap: 8, justifyContent: 'center', marginBottom: 20 }} onPaste={handlePaste}>
              {digits.map((d, i) => (
                <input
                  key={i}
                  ref={el => { inputsRef.current[i] = el; }}
                  type="text"
                  inputMode="numeric"
                  maxLength={1}
                  value={d}
                  onChange={e => handleDigitChange(i, e.target.value)}
                  onKeyDown={e => handleKeyDown(i, e)}
                  style={{
                    width: 48,
                    height: 56,
                    fontSize: 24,
                    fontWeight: 700,
                    textAlign: 'center',
                    borderRadius: 12,
                    border: d ? '1px solid rgba(173,198,255,0.4)' : '1px solid rgba(255,255,255,0.12)',
                    background: 'rgba(255,255,255,0.05)',
                    color: '#e3e2e7',
                    outline: 'none',
                    transition: 'all 0.2s ease',
                    caretColor: '#adc6ff',
                    boxShadow: d ? '0 0 12px rgba(173,198,255,0.15)' : 'none',
                  }}
                  onFocus={e => { e.currentTarget.style.borderColor = 'rgba(173,198,255,0.4)'; e.currentTarget.style.transform = 'scale(1.05)'; }}
                  onBlur={e => { e.currentTarget.style.borderColor = d ? 'rgba(173,198,255,0.4)' : 'rgba(255,255,255,0.12)'; e.currentTarget.style.transform = 'scale(1)'; }}
                  autoFocus={i === 0}
                />
              ))}
            </div>

            {error && (
              <div style={{
                color: '#ff5252', fontSize: 13, marginBottom: 16, textAlign: 'center',
                animation: 'shake 0.4s ease',
              }}>
                {error}
              </div>
            )}

            <button
              onClick={handleVerify}
              disabled={loading || token.length !== 6}
              className="auth-primary-btn"
              style={{
                width: '100%',
                padding: 16,
                borderRadius: 12,
                border: 'none',
                background: loading || token.length !== 6 ? 'rgba(255,255,255,0.08)' : '#adc6ff',
                color: loading || token.length !== 6 ? '#888' : '#002e69',
                fontWeight: 700,
                cursor: loading || token.length !== 6 ? 'not-allowed' : 'pointer',
                fontSize: 15,
                marginBottom: 12,
                transition: 'all 0.2s ease',
                letterSpacing: '0.02em',
              }}
              onMouseDown={e => { if (!loading && token.length === 6) e.currentTarget.style.transform = 'scale(0.97)'; }}
              onMouseUp={e => { e.currentTarget.style.transform = 'scale(1)'; }}
              onMouseLeave={e => { e.currentTarget.style.transform = 'scale(1)'; }}
            >
              {loading ? 'Verifying...' : 'Verify & Enter'}
            </button>

            <button
              onClick={handleSetup}
              style={{
                width: '100%',
                padding: 12,
                borderRadius: 12,
                border: '1px solid rgba(255,255,255,0.12)',
                background: 'transparent',
                color: '#adc6ff',
                fontWeight: 600,
                cursor: 'pointer',
                fontSize: 13,
                transition: 'all 0.2s ease',
              }}
              onMouseEnter={e => { e.currentTarget.style.background = 'rgba(255,255,255,0.05)'; }}
              onMouseLeave={e => { e.currentTarget.style.background = 'transparent'; }}
            >
              First-time setup?
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
