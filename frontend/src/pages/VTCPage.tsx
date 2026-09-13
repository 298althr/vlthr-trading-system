import { useState, useMemo } from 'react';
import {
  TrendingUp, TrendingDown, Activity, Shield, Zap,
  Coins, Plus, Minus, Clock, ChevronDown, ChevronUp,
  Loader2, AlertCircle, CheckCircle2
} from 'lucide-react';
import type { VTCState } from '../types';
import { API_BASE } from '../config';

interface Props {
  vtc: VTCState | null;
}

function formatUSD(n: number) {
  if (n >= 1e6) return `$${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `$${(n / 1e3).toFixed(2)}K`;
  return `$${n.toFixed(2)}`;
}

function formatNum(n: number) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(2)}K`;
  return n.toFixed(0);
}

/* ── Mini sparkline (SVG) ─────────────────────────────────────────── */
function Sparkline({ data, color, width = 280, height = 60 }: { data: number[]; color: string; width?: number; height?: number }) {
  if (data.length < 2) return <div style={{ height, display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-tertiary)', fontSize: 11 }}>No data</div>;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pts = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((v - min) / range) * height;
    return `${x},${y}`;
  }).join(' ');
  return (
    <svg width={width} height={height} style={{ overflow: 'visible' }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/* ── Progress bar ─────────────────────────────────────────────────── */
function ProgressBar({ value, color, label }: { value: number; color: string; label: string }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>
        <span>{label}</span>
        <span>{value.toFixed(1)}</span>
      </div>
      <div style={{ height: 6, borderRadius: 3, background: 'rgba(255,255,255,0.06)', overflow: 'hidden' }}>
        <div style={{ width: `${Math.max(0, Math.min(100, value))}%`, height: '100%', borderRadius: 3, background: color, transition: 'width 0.6s ease' }} />
      </div>
    </div>
  );
}

/* ── Layer card ───────────────────────────────────────────────────── */
function LayerCard({ title, score, icon: Icon, color, weight }: { title: string; score: number; icon: any; color: string; weight: string }) {
  return (
    <div className="glass" style={{ padding: 14, borderRadius: 'var(--radius-md)', flex: 1 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <div style={{ width: 28, height: 28, borderRadius: 8, background: `${color}20`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <Icon size={14} style={{ color }} />
        </div>
        <div>
          <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-secondary)' }}>{title}</div>
          <div style={{ fontSize: 9, color: 'var(--text-tertiary)' }}>{weight}</div>
        </div>
      </div>
      <div className="data-lg" style={{ color, fontSize: 24 }}>{score.toFixed(1)}</div>
      <div style={{ fontSize: 10, color: 'var(--text-tertiary)', marginTop: 2 }}>/ 100</div>
    </div>
  );
}

export default function VTCPage({ vtc }: Props) {
  const [showAudit, setShowAudit] = useState(false);
  const [mintAmount, setMintAmount] = useState('');
  const [burnAmount, setBurnAmount] = useState('');
  const [timeframe, setTimeframe] = useState<'1H' | '24H' | '7D' | '30D'>('24H');
  const [mintStatus, setMintStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [burnStatus, setBurnStatus] = useState<'idle' | 'loading' | 'success' | 'error'>('idle');
  const [statusMsg, setStatusMsg] = useState('');

  const priceData = useMemo(() => {
    if (!vtc?.history?.length) return [];
    const now = Date.now();
    const ms = { '1H': 60 * 60 * 1000, '24H': 24 * 60 * 60 * 1000, '7D': 7 * 24 * 60 * 60 * 1000, '30D': 30 * 24 * 60 * 60 * 1000 };
    const cutoff = now - (ms[timeframe] || ms['24H']);
    return vtc.history.filter(p => new Date(p.timestamp).getTime() >= cutoff).map(p => p.price);
  }, [vtc, timeframe]);

  const priceChange = priceData.length >= 2
    ? ((priceData[priceData.length - 1] - priceData[0]) / (priceData[0] || 1)) * 100
    : 0;

  if (!vtc) {
    return (
      <div className="content-max" style={{ paddingTop: 40, textAlign: 'center', opacity: 0.5 }}>
        <Coins size={40} style={{ marginBottom: 12 }} />
        <div className="headline-sm">VTC Loading…</div>
        <div className="body-md">Performance vector data will appear after the next sync cycle.</div>
      </div>
    );
  }

  const handleMint = async () => {
    const amt = parseFloat(mintAmount);
    if (!amt || amt <= 0) { setStatusMsg('Enter a positive amount'); setMintStatus('error'); return; }
    setMintStatus('loading');
    setStatusMsg('Minting…');
    try {
      const res = await fetch(`${API_BASE}/api/vtc/mint`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ amount: amt, reason: 'Manual mint' }) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setMintAmount('');
      setMintStatus('success');
      setStatusMsg(`Minted +${formatNum(amt)} VTC`);
    } catch (err: any) {
      setMintStatus('error');
      setStatusMsg(err.message || 'Mint failed');
    }
    setTimeout(() => { setMintStatus('idle'); setBurnStatus('idle'); setStatusMsg(''); }, 3000);
  };

  const handleBurn = async () => {
    const amt = parseFloat(burnAmount);
    if (!amt || amt <= 0) { setStatusMsg('Enter a positive amount'); setBurnStatus('error'); return; }
    setBurnStatus('loading');
    setStatusMsg('Burning…');
    try {
      const res = await fetch(`${API_BASE}/api/vtc/burn`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ amount: amt, reason: 'Manual burn' }) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setBurnAmount('');
      setBurnStatus('success');
      setStatusMsg(`Burned −${formatNum(amt)} VTC`);
    } catch (err: any) {
      setBurnStatus('error');
      setStatusMsg(err.message || 'Burn failed');
    }
    setTimeout(() => { setMintStatus('idle'); setBurnStatus('idle'); setStatusMsg(''); }, 3000);
  };

  return (
    <div className="content-max">
      <div className="section-title" style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <Coins size={18} style={{ color: 'var(--primary)' }} />
        VTC Token
      </div>
      <div className="body-sm" style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>
        Decision-Weighted Performance Asset. Price derived from trading performance.
      </div>

      {/* ── Headline Stats ───────────────────────────────────────────── */}
      <div className="kpi-grid" style={{ marginBottom: 20 }}>
        <div className="glass" style={{ padding: 16, borderRadius: 'var(--radius-md)' }}>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6 }}>VTC Price</div>
          <div className="data-lg" style={{ fontSize: 28 }}>${vtc.price.toFixed(4)}</div>
          <div style={{ fontSize: 11, marginTop: 4, color: priceChange >= 0 ? 'var(--success)' : 'var(--danger)', display: 'flex', alignItems: 'center', gap: 3 }}>
            {priceChange >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
            {priceChange >= 0 ? '+' : ''}{priceChange.toFixed(2)}% ({timeframe})
          </div>
        </div>

        <div className="glass" style={{ padding: 16, borderRadius: 'var(--radius-md)' }}>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6 }}>VTC Score</div>
          <div className="data-lg" style={{ fontSize: 28, color: vtc.score >= 60 ? 'var(--success)' : vtc.score >= 40 ? 'var(--warning)' : 'var(--danger)' }}>
            {vtc.score.toFixed(1)}
          </div>
          <div style={{ fontSize: 11, marginTop: 4, color: 'var(--text-tertiary)' }}>Confidence: {(vtc.confidence * 100).toFixed(0)}%</div>
        </div>

        <div className="glass" style={{ padding: 16, borderRadius: 'var(--radius-md)' }}>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6 }}>Market Cap</div>
          <div className="data-lg" style={{ fontSize: 28 }}>{formatUSD(vtc.marketCap)}</div>
          <div style={{ fontSize: 11, marginTop: 4, color: 'var(--text-tertiary)' }}>NAV: {formatUSD(vtc.nav)}</div>
        </div>

        <div className="glass" style={{ padding: 16, borderRadius: 'var(--radius-md)' }}>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 6 }}>Supply</div>
          <div className="data-lg" style={{ fontSize: 28 }}>{formatNum(vtc.supply)}</div>
          <div style={{ fontSize: 11, marginTop: 4, color: 'var(--text-tertiary)' }}>VTC</div>
        </div>
      </div>

      {/* ── Price Chart ──────────────────────────────────────────────── */}
      <div className="glass" style={{ padding: 16, borderRadius: 'var(--radius-md)', marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)' }}>Price History</div>
          <div style={{ display: 'flex', gap: 4 }}>
            {(['1H', '24H', '7D', '30D'] as const).map(tf => (
              <button
                key={tf}
                onClick={() => setTimeframe(tf)}
                style={{
                  padding: '3px 8px', borderRadius: 4, border: 'none', fontSize: 10, fontWeight: 600, cursor: 'pointer',
                  background: timeframe === tf ? 'var(--primary)' : 'rgba(255,255,255,0.06)',
                  color: timeframe === tf ? '#0B0B0F' : 'var(--text-secondary)',
                  fontFamily: 'inherit'
                }}
              >
                {tf}
              </button>
            ))}
          </div>
        </div>
        <div style={{ display: 'flex', justifyContent: 'center' }}>
          <Sparkline data={priceData} color="var(--primary)" />
        </div>
      </div>

      {/* ── Score Layers ─────────────────────────────────────────────── */}
      <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 10 }}>Score Breakdown</div>
      <div style={{ display: 'flex', gap: 10, marginBottom: 16 }}>
        <LayerCard title="Performance" score={vtc.layers.performance} icon={Activity} color="#81C995" weight="50%" />
        <LayerCard title="Risk" score={vtc.layers.risk} icon={Shield} color="#8AB4F8" weight="30%" />
        <LayerCard title="Growth" score={vtc.layers.growth} icon={Zap} color="#FBC02D" weight="20%" />
      </div>

      {/* ── Performance Vector ───────────────────────────────────────── */}
      <div className="glass" style={{ padding: 16, borderRadius: 'var(--radius-md)', marginBottom: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 12 }}>Performance Vector</div>
        {vtc.vector && (
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 16px' }}>
            <ProgressBar value={Math.min(vtc.vector.sharpe * 10, 100)} color="#81C995" label={`Sharpe ${vtc.vector.sharpe.toFixed(2)}`} />
            <ProgressBar value={Math.min(vtc.vector.sortino * 10, 100)} color="#81C995" label={`Sortino ${vtc.vector.sortino.toFixed(2)}`} />
            <ProgressBar value={Math.max(100 - vtc.vector.drawdown, 0)} color="#8AB4F8" label={`Drawdown ${vtc.vector.drawdown.toFixed(1)}%`} />
            <ProgressBar value={Math.min((vtc.vector.expectancy + 1) * 50, 100)} color="#8AB4F8" label={`Expectancy ${vtc.vector.expectancy.toFixed(3)}`} />
            <ProgressBar value={vtc.vector.win_rate} color="#FBC02D" label={`Win Rate ${vtc.vector.win_rate.toFixed(1)}%`} />
            <ProgressBar value={Math.min(vtc.vector.capital_growth * 2, 100)} color="#FBC02D" label={`Growth ${vtc.vector.capital_growth.toFixed(1)}%`} />
            <ProgressBar value={vtc.vector.stability} color="#BA68C8" label={`Stability ${vtc.vector.stability.toFixed(1)}`} />
            <ProgressBar value={Math.max(100 - vtc.vector.risk_score, 0)} color="#BA68C8" label={`Risk ${vtc.vector.risk_score.toFixed(1)}`} />
          </div>
        )}
      </div>

      {/* ── Treasury & Mint/Burn ─────────────────────────────────────── */}
      <div className="glass" style={{ padding: 16, borderRadius: 'var(--radius-md)', marginBottom: 16 }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 12, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Coins size={14} /> Treasury
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 14 }}>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Total Supply</div>
            <div className="data-md">{formatNum(vtc.treasury.total_supply)}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Circulating</div>
            <div className="data-md">{formatNum(vtc.treasury.circulating_supply)}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Minted</div>
            <div className="data-md" style={{ color: 'var(--success)' }}>+{formatNum(vtc.treasury.minted)}</div>
          </div>
          <div>
            <div style={{ fontSize: 10, color: 'var(--text-tertiary)' }}>Burned</div>
            <div className="data-md" style={{ color: 'var(--danger)' }}>-{formatNum(vtc.treasury.burned)}</div>
          </div>
        </div>

        {/* Status message */}
        {statusMsg && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 10px', borderRadius: 6, marginBottom: 10, fontSize: 11, fontWeight: 600,
            background: mintStatus === 'error' || burnStatus === 'error' ? 'rgba(255,100,100,0.12)' : mintStatus === 'success' || burnStatus === 'success' ? 'rgba(129,201,149,0.12)' : 'rgba(138,180,248,0.08)',
            color: mintStatus === 'error' || burnStatus === 'error' ? 'var(--danger)' : mintStatus === 'success' || burnStatus === 'success' ? 'var(--success)' : 'var(--primary)'
          }}>
            {mintStatus === 'loading' || burnStatus === 'loading' ? <Loader2 size={12} className="animate-spin" /> :
             mintStatus === 'error' || burnStatus === 'error' ? <AlertCircle size={12} /> :
             mintStatus === 'success' || burnStatus === 'success' ? <CheckCircle2 size={12} /> : null}
            {statusMsg}
          </div>
        )}

        {/* Mint/Burn controls — stacked vertically */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              type="number"
              placeholder="Mint amount"
              value={mintAmount}
              onChange={e => setMintAmount(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleMint()}
              disabled={mintStatus === 'loading'}
              style={{ flex: 1, padding: '8px 10px', background: 'rgba(255,255,255,0.04)', border: '1px solid var(--glass-border)', borderRadius: 6, color: 'var(--text-primary)', fontFamily: 'inherit', fontSize: 12 }}
            />
            <button
              onClick={handleMint}
              disabled={mintStatus === 'loading'}
              className="pressable"
              style={{ padding: '8px 14px', borderRadius: 6, border: 'none', background: 'var(--success)', color: '#0B0B0F', fontWeight: 700, fontSize: 11, cursor: mintStatus === 'loading' ? 'not-allowed' : 'pointer', opacity: mintStatus === 'loading' ? 0.6 : 1, display: 'flex', alignItems: 'center', gap: 4, minWidth: 70, justifyContent: 'center' }}
            >
              {mintStatus === 'loading' ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
              {mintStatus === 'loading' ? '…' : 'Mint'}
            </button>
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              type="number"
              placeholder="Burn amount"
              value={burnAmount}
              onChange={e => setBurnAmount(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleBurn()}
              disabled={burnStatus === 'loading'}
              style={{ flex: 1, padding: '8px 10px', background: 'rgba(255,255,255,0.04)', border: '1px solid var(--glass-border)', borderRadius: 6, color: 'var(--text-primary)', fontFamily: 'inherit', fontSize: 12 }}
            />
            <button
              onClick={handleBurn}
              disabled={burnStatus === 'loading'}
              className="pressable"
              style={{ padding: '8px 14px', borderRadius: 6, border: 'none', background: 'var(--danger)', color: '#fff', fontWeight: 700, fontSize: 11, cursor: burnStatus === 'loading' ? 'not-allowed' : 'pointer', opacity: burnStatus === 'loading' ? 0.6 : 1, display: 'flex', alignItems: 'center', gap: 4, minWidth: 70, justifyContent: 'center' }}
            >
              {burnStatus === 'loading' ? <Loader2 size={12} className="animate-spin" /> : <Minus size={12} />}
              {burnStatus === 'loading' ? '…' : 'Burn'}
            </button>
          </div>
        </div>
      </div>

      {/* ── Audit Log ────────────────────────────────────────────────── */}
      <div className="glass" style={{ padding: 16, borderRadius: 'var(--radius-md)', marginBottom: 16 }}>
        <button
          onClick={() => setShowAudit(v => !v)}
          style={{ display: 'flex', alignItems: 'center', gap: 6, width: '100%', background: 'none', border: 'none', color: 'var(--text-secondary)', fontSize: 12, fontWeight: 600, cursor: 'pointer', fontFamily: 'inherit' }}
        >
          <Clock size={14} /> Audit Log ({vtc.audit.length})
          {showAudit ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </button>
        {showAudit && (
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 8 }}>
            {vtc.audit.slice(0, 10).map(a => (
              <div key={a.id} style={{ padding: 10, borderRadius: 6, background: 'rgba(255,255,255,0.03)', fontSize: 11 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ color: 'var(--text-secondary)' }}>Score: <strong style={{ color: 'var(--text-primary)' }}>{a.score.toFixed(2)}</strong></span>
                  <span style={{ color: 'var(--text-tertiary)' }}>{new Date(a.timestamp).toLocaleTimeString()}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                  <span style={{ color: 'var(--text-tertiary)' }}>${a.old_price.toFixed(4)} → ${a.new_price.toFixed(4)}</span>
                  <span style={{ color: a.new_price >= a.old_price ? 'var(--success)' : 'var(--danger)' }}>
                    {a.new_price >= a.old_price ? '+' : ''}{((a.new_price - a.old_price) / a.old_price * 100).toFixed(2)}%
                  </span>
                </div>
                <div style={{ color: 'var(--text-tertiary)', marginTop: 3, fontSize: 10 }}>{a.reason}</div>
              </div>
            ))}
            {vtc.audit.length === 0 && <div style={{ textAlign: 'center', color: 'var(--text-tertiary)', padding: 12 }}>No audit events yet.</div>}
          </div>
        )}
      </div>
    </div>
  );
}
