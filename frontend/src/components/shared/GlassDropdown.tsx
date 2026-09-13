import { useState, useRef, useEffect, useCallback } from 'react';
import { ChevronDown, Check } from 'lucide-react';

interface Option {
  value: string;
  label: string;
}

interface Props {
  label: string;
  value: string;
  options: Option[];
  onChange: (value: string) => void;
  disabled?: boolean;
}

export default function GlassDropdown({ label, value, options, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const selected = options.find((o) => o.value === value);

  const handleSelect = useCallback((val: string) => {
    onChange(val);
    setOpen(false);
  }, [onChange]);

  useEffect(() => {
    if (!open) return;
    const handleClick = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleKey);
    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleKey);
    };
  }, [open]);

  return (
    <div className="bt-form-group">
      <span className="bt-form-label">{label}</span>
      <div ref={containerRef} className="gd-container">
        <button
          type="button"
          className={`gd-trigger ${open ? 'open' : ''} ${disabled ? 'disabled' : ''}`}
          onClick={() => !disabled && setOpen((v) => !v)}
          disabled={disabled}
          aria-haspopup="listbox"
          aria-expanded={open}
        >
          <span className="gd-value">{selected?.label || value}</span>
          <ChevronDown size={14} className="gd-chevron" />
        </button>

        {open && (
          <ul className="gd-menu" role="listbox">
            {options.map((opt) => (
              <li
                key={opt.value}
                className={`gd-option ${opt.value === value ? 'active' : ''}`}
                onClick={() => handleSelect(opt.value)}
                role="option"
                aria-selected={opt.value === value}
              >
                <span className="gd-option-label">{opt.label}</span>
                {opt.value === value && <Check size={14} className="gd-check" />}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
