import { AlertTriangle, RotateCcw } from 'lucide-react';

interface Props {
  title?: string;
  message: string;
  onRetry?: () => void;
}

export default function VlthrErrorCard({ title = 'Something went wrong', message, onRetry }: Props) {
  return (
    <div className="error-widget">
      <AlertTriangle className="error-icon" />
      <div className="error-title">{title}</div>
      <div className="error-msg">{message}</div>
      {onRetry && (
        <button className="btn-primary pressable" onClick={onRetry}>
          <RotateCcw size={14} />
          Try Again
        </button>
      )}
    </div>
  );
}
