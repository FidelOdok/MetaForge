import { useEffect, useRef, useState } from 'react';
import { ChevronDown } from 'lucide-react';

export interface StageAction {
  id: string;
  label: string;
  onClick: () => void;
  disabled?: boolean;
  title?: string;
}

/**
 * A stage's actions: exactly one primary action, and everything else in a
 * single More menu (UXS-002).
 */
export function StageActions({ primary, more = [] }: { primary: StageAction | null; more?: StageAction[] }) {
  return (
    <div className="cv-stage-actions">
      {primary && (
        <button
          type="button"
          className="cv-primary"
          data-testid="stage-primary"
          disabled={primary.disabled}
          title={primary.title}
          onClick={primary.onClick}
        >
          {primary.label}
        </button>
      )}
      {more.length > 0 && <MoreMenu items={more} />}
    </div>
  );
}

export function MoreMenu({ items }: { items: StageAction[] }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('mousedown', onDown);
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('mousedown', onDown);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);
  return (
    <div ref={root} className="cv-more">
      <button type="button" className="cv-chip-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        More <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open && (
        <div className="cv-more-menu" role="menu" aria-label="More actions">
          {items.map((item) => (
            <button
              key={item.id}
              type="button"
              role="menuitem"
              disabled={item.disabled}
              title={item.title}
              onClick={() => {
                setOpen(false);
                item.onClick();
              }}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
