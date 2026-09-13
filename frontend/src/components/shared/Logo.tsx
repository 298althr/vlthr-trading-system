interface Props {
  symbol: string;
  size?: number;
}

export default function Logo({ symbol, size = 18 }: Props) {
  const base = symbol.replace('USDT', '').toUpperCase();
  return (
    <img
      src={`/logos/${base}.png`}
      alt={base}
      width={size}
      height={size}
      style={{ borderRadius: '50%', objectFit: 'cover', flexShrink: 0, display: 'inline-block', verticalAlign: 'middle', marginRight: 6 }}
      onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }}
    />
  );
}
