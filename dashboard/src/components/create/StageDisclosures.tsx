import type { ReactNode } from 'react';
import { ChevronDown } from 'lucide-react';

export interface StageDisclosure {
  id: string;
  label: string;
  /** A short summary on the button itself, so it says something while closed. */
  badge?: string;
  tone?: 'ok' | 'warn' | 'bad';
  icon?: ReactNode;
  content: ReactNode;
}

interface StageDisclosuresProps {
  items: StageDisclosure[];
  /** The open item's id, or null; one opens at a time so the model keeps its room. */
  open: string | null;
  onOpen: (id: string | null) => void;
  /** Shown first in the button row: the stage's own actions. */
  lead?: ReactNode;
}

/**
 * Under the model, everything else is a button that opens its detail in
 * place and closes it again. Nothing is open by default.
 */
export function StageDisclosures({ items, open, onOpen, lead }: StageDisclosuresProps) {
  const shown = items.find((i) => i.id === open);
  return (
    <>
      <div className="cv-stage-toolbar">
        {lead}
        {items.length > 0 && (
          <div className="cv-disclosures" role="group" aria-label="Details">
            {items.map((item) => {
              const isOpen = item.id === open;
              return (
                <button
                  key={item.id}
                  type="button"
                  className={`cv-disclose${isOpen ? ' is-open' : ''}${item.tone ? ` is-${item.tone}` : ''}`}
                  aria-expanded={isOpen}
                  aria-controls={`cv-disclose-${item.id}`}
                  data-testid={`disclose-${item.id}`}
                  onClick={() => onOpen(isOpen ? null : item.id)}
                >
                  {item.icon}
                  <span>{item.label}</span>
                  {item.badge && <span className="cv-disclose-badge">{item.badge}</span>}
                  <ChevronDown size={14} className="cv-disclose-chevron" aria-hidden="true" />
                </button>
              );
            })}
          </div>
        )}
      </div>
      {shown && (
        <section id={`cv-disclose-${shown.id}`} className="cv-card cv-disclose-panel" aria-label={shown.label}>
          {shown.content}
        </section>
      )}
    </>
  );
}
