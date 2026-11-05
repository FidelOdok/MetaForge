import type { ReactNode } from 'react';

interface PanelProps {
  /** The section's name, shown as the Files page's small uppercase label. */
  title: ReactNode;
  /** A count or short status beside the title, in monospace. */
  meta?: ReactNode;
  /** Buttons on the right of the header. */
  actions?: ReactNode;
  children?: ReactNode;
  testId?: string;
}

/**
 * A section of an Engineering page, drawn like the Files page's panels: a
 * glass card whose header carries a small uppercase label, an optional count
 * and the section's buttons. A table or list placed in the body with the
 * `mf-bleed` class runs edge to edge, as the Files rows do.
 */
export function Panel({ title, meta, actions, children, testId }: PanelProps) {
  return (
    <section className="mf-panel" data-testid={testId}>
      <header className="mf-panel-head">
        <h2 className="mf-panel-label">{title}</h2>
        {meta !== undefined && meta !== null && meta !== false && meta !== '' && (
          <span className="mf-panel-meta">{meta}</span>
        )}
        {actions && <div className="mf-actions mf-panel-actions">{actions}</div>}
      </header>
      {children !== undefined && children !== null && children !== false && (
        <div className="mf-panel-body">{children}</div>
      )}
    </section>
  );
}

interface SearchFieldProps {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
  /** The accessible name, when it differs from the placeholder. */
  label?: string;
}

/** The Files page's search: a glass pill with a search icon. */
export function SearchField({ value, onChange, placeholder, label }: SearchFieldProps) {
  return (
    <label className="mf-search">
      <span className="material-symbols-outlined" aria-hidden="true">
        search
      </span>
      <input
        type="search"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-label={label ?? placeholder}
      />
    </label>
  );
}

/** Loading rows inside a panel, as on the Files page. */
export function PanelLoading({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="mf-panel-loading" role="status">
      <span className="material-symbols-outlined" aria-hidden="true">
        progress_activity
      </span>
      {label}
    </div>
  );
}
