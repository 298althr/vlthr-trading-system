import { Inbox } from 'lucide-react';

interface Props {
  title?: string;
  message: string;
}

export default function VlthrEmptyState({ title = 'Nothing here', message }: Props) {
  return (
    <div className="empty-widget">
      <Inbox className="empty-icon" />
      <div className="empty-title">{title}</div>
      <div className="empty-msg">{message}</div>
    </div>
  );
}
