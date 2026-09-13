interface Props {
  message?: string;
}

const MESSAGES = [
  'Scanning markets…',
  'Loading signals…',
  'Syncing with database…',
  'Warming up engines…',
  'Almost there…',
];

export default function VlthrLoading({ message }: Props) {
  const displayMessage = message ?? MESSAGES[Math.floor(Math.random() * MESSAGES.length)];

  return (
    <div className="vlthr-loading">
      <img src="/favicon.svg" alt="VLTHR" className="loading-logo" />
      <div className="loading-dots">
        <span />
        <span />
        <span />
      </div>
      <span className="loading-text">{displayMessage}</span>
    </div>
  );
}
