export default function LoadingScreen() {
  return (
    <div style={{
      minHeight: '100vh',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      background: 'radial-gradient(ellipse at 60% 40%, #0a1628 0%, #06101e 40%, #000000 100%)',
      color: '#adc6ff',
      fontFamily: 'Inter, sans-serif',
      position: 'fixed',
      inset: 0,
      zIndex: 9999,
    }}>
      <img
        src="/favicon.svg"
        alt="VLTHR"
        style={{
          width: 80,
          height: 80,
          animation: 'breathe 1.5s ease-in-out infinite',
          filter: 'drop-shadow(0 0 20px rgba(0,174,77,0.3))',
        }}
      />
      <div style={{ marginTop: 24, display: 'flex', gap: 6, alignItems: 'center' }}>
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#adc6ff', animation: 'dotBounce 0.6s ease-in-out infinite' }} />
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#adc6ff', animation: 'dotBounce 0.6s ease-in-out 0.15s infinite' }} />
        <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#adc6ff', animation: 'dotBounce 0.6s ease-in-out 0.3s infinite' }} />
      </div>
      <div style={{
        marginTop: 16,
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 10,
        letterSpacing: '0.2em',
        textTransform: 'uppercase',
        opacity: 0.5,
        color: '#c1c6d7',
      }}>
        VLTHR TERMINAL
      </div>
    </div>
  );
}
