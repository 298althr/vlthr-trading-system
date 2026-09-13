import type { HistoryItem } from '../../types';

interface Props {
  history: HistoryItem[];
}

export default function HistoryTable({ history }: Props) {
  return (
    <div className="history-container">
      <table className="history-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Asset</th>
            <th>Type</th>
            <th style={{ textAlign: 'right' }}>Status</th>
          </tr>
        </thead>
        <tbody>
          {history.length === 0 ? (
            <tr>
              <td colSpan={4} style={{ textAlign: 'center', padding: '32px', opacity: 0.3 }}>No records found.</td>
            </tr>
          ) : (
            history.map((item, idx) => (
              <tr key={`${item.id}-${idx}`}>
                <td style={{ color: 'var(--on-surface-variant)' }}>{item.time}</td>
                <td style={{ fontWeight: 600 }}>{item.asset}</td>
                <td style={{ color: item.type === 'LONG' ? 'var(--success)' : 'var(--on-surface-variant)' }}>{item.type}</td>
                <td style={{ textAlign: 'right' }}>
                  <span style={{ fontSize: 10, padding: '2px 6px', background: 'var(--surface-container-highest)', borderRadius: 4, color: 'var(--on-surface-variant)' }}>
                    {item.status?.toUpperCase()}
                  </span>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
}
