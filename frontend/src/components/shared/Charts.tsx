export function Sparkline({ data, width = 60, height = 24 }: { data: { time: number; price: number }[]; width?: number; height?: number }) {
  if (data.length < 2) return <svg width={width} height={height} />;
  const prices = data.map(d => d.price);
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const range = max - min || 1;
  const pts = prices.map((p, i) => {
    const x = (i / (prices.length - 1)) * width;
    const y = height - ((p - min) / range) * (height - 2) - 1;
    return `${x},${y}`;
  }).join(' ');
  const trend = prices[prices.length - 1] - prices[0];
  const color = trend >= 0 ? 'var(--success)' : 'var(--danger)';
  return (
    <svg width={width} height={height} style={{ overflow: 'visible' }}>
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function LineChart({ data, width = 340, height = 100, color = 'var(--primary)' }: { data: { time: number; value: number }[]; width?: number; height?: number; color?: string }) {
  if (data.length < 2) {
    return (
      <div style={{ width, height, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, color: 'var(--on-surface-variant)', opacity: 0.5, fontStyle: 'italic' }}>
        {data.length === 0 ? 'No data — run signal scan to populate' : 'Collecting data…'}
      </div>
    );
  }
  const values = data.map(d => d.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const pad = 4;
  const chartH = height - pad * 2;
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * width;
    const y = pad + chartH - ((v - min) / range) * chartH;
    return `${x},${y}`;
  }).join(' ');
  const gridY = [0, 0.25, 0.5, 0.75, 1].map(p => pad + chartH - p * chartH);
  return (
    <svg width={width} height={height} style={{ overflow: 'visible' }}>
      {gridY.map((y, i) => (
        <line key={i} x1={0} y1={y} x2={width} y2={y} stroke="var(--outline-variant)" strokeWidth={0.5} strokeDasharray="2,2" />
      ))}
      <polyline points={pts} fill="none" stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export function OrderbookBars({ bids, asks, width = 340, height = 100 }: { bids: [number, number][]; asks: [number, number][]; width?: number; height?: number }) {
  if (!bids?.length && !asks?.length) return null;
  const allQty = [...bids.map(b => b[1]), ...asks.map(a => a[1])];
  const maxQty = Math.max(...allQty, 1);
  const barH = 7;
  const gap = 2;
  const maxBars = Math.min(10, Math.floor(height / (barH + gap)));
  const showBids = bids.slice(0, maxBars);
  const showAsks = asks.slice(0, maxBars);
  const halfW = width / 2 - 4;
  return (
    <svg width={width} height={height}>
      {showBids.map(([, qty], i) => (
        <rect key={`b${i}`} x={halfW - (qty / maxQty) * halfW} y={i * (barH + gap)} width={(qty / maxQty) * halfW} height={barH} rx={2} fill="var(--success)" opacity={0.7} />
      ))}
      {showAsks.map(([, qty], i) => (
        <rect key={`a${i}`} x={halfW + 4} y={i * (barH + gap)} width={(qty / maxQty) * halfW} height={barH} rx={2} fill="var(--danger)" opacity={0.7} />
      ))}
      <line x1={halfW + 2} y1={0} x2={halfW + 2} y2={height} stroke="var(--outline-variant)" strokeWidth={0.5} strokeDasharray="2,2" />
    </svg>
  );
}
