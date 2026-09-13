import { useEffect, useState } from 'react';
import { CheckCircle, AlertCircle, AlertTriangle, Info, X } from 'lucide-react';

export interface ToastItem {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  title: string;
  message: string;
}

interface Props {
  toasts: ToastItem[];
  onDismiss: (id: string) => void;
}

const ICONS = {
  success: CheckCircle,
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
};

const COLORS = {
  success: '#81C995',
  error: '#F28B82',
  warning: '#FBC02D',
  info: '#8AB4F8',
};

function ToastItemComponent({ toast, onDismiss }: { toast: ToastItem; onDismiss: () => void }) {
  const [exiting, setExiting] = useState(false);
  const Icon = ICONS[toast.type];
  const color = COLORS[toast.type];

  useEffect(() => {
    const timer = setTimeout(() => {
      setExiting(true);
      setTimeout(onDismiss, 250);
    }, 4000);
    return () => clearTimeout(timer);
  }, [onDismiss]);

  return (
    <div className={`vlthr-toast ${toast.type} ${exiting ? 'toast-exit' : ''}`}>
      <Icon size={18} className="toast-icon" style={{ color }} />
      <div className="toast-content">
        <div className="toast-title">{toast.title}</div>
        {toast.message && <div className="toast-message">{toast.message}</div>}
      </div>
      <button className="toast-close pressable" onClick={() => { setExiting(true); setTimeout(onDismiss, 250); }}>
        <X size={14} />
      </button>
    </div>
  );
}

export default function VlthrToast({ toasts, onDismiss }: Props) {
  if (toasts.length === 0) return null;

  return (
    <div className="toast-container">
      {toasts.map(t => (
        <ToastItemComponent key={t.id} toast={t} onDismiss={() => onDismiss(t.id)} />
      ))}
    </div>
  );
}
