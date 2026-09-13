import { useState, useCallback } from 'react';
import { Check } from 'lucide-react';

interface Props {
  text: string;
  children: React.ReactNode;
  className?: string;
}

export default function CopyTrigger({ text, children, className = '' }: Props) {
  const [showCopied, setShowCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text);
      setShowCopied(true);
      setTimeout(() => setShowCopied(false), 1500);
    } catch {
      // Ignore copy failure
    }
  }, [text]);

  return (
    <span className={`copy-trigger ${className}`} onClick={handleCopy} role="button" tabIndex={0}>
      {children}
      {showCopied && (
        <span className="copy-popup">
          <Check size={10} style={{ display: 'inline', marginRight: 4 }} />
          Copied
        </span>
      )}
    </span>
  );
}
