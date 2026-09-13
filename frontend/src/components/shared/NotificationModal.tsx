import { CheckCircle, AlertCircle } from 'lucide-react';

interface Props {
  notification: { type: 'success' | 'error'; title: string; message: string } | null;
  onDismiss: () => void;
}

export default function NotificationModal({ notification, onDismiss }: Props) {
  if (!notification) return null;
  const isSuccess = notification.type === 'success';
  const color = isSuccess ? 'var(--success)' : 'var(--danger)';

  return (
    <div className="modal-overlay" onClick={onDismiss}>
      <div className="modal-sheet" style={{ maxWidth: 400 }} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
          {isSuccess ? <CheckCircle size={48} color={color} /> : <AlertCircle size={48} color={color} />}
          <div>
            <div className="modal-title" style={{ fontSize: 20, marginBottom: 4 }}>{notification.title}</div>
            <div className="body-md" style={{ color: 'var(--on-surface-variant)' }}>{notification.message}</div>
          </div>
        </div>
        <div className="modal-footer">
          <button className="btn btn-secondary" onClick={onDismiss}>DISMISS</button>
        </div>
      </div>
    </div>
  );
}
