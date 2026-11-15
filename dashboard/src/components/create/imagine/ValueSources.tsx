import { useMemo } from 'react';
import { valueSources, type Origin } from './sources';

const ORIGIN_WORD: Record<Origin, string> = {
  library: 'Library',
  typed: 'Typed in',
  estimate: 'Estimate',
};

/**
 * Where each studio value came from (2026-11-15): a part's size and price, the machine and
 * the material's figures, each marked as the library's (with its entry and version), typed
 * in with its own source, or an estimate. Collapsed by default; the summary says how many
 * values the library gave.
 */
export function ValueSources({ config }: { config: Record<string, unknown> | null }) {
  const rows = useMemo(() => valueSources(config), [config]);
  if (rows.length === 0) return null;
  const fromLibrary = rows.filter((r) => r.origin === 'library').length;
  return (
    <details className="cv-card cv-sources" data-testid="value-sources">
      <summary>
        Where the values come from{' '}
        <span className="cv-muted">
          · {fromLibrary} of {rows.length} from the library
        </span>
      </summary>
      <ul className="cv-sources-list">
        {rows.map((row, i) => (
          <li key={`${row.subject}-${row.what}-${i}`}>
            <span className={`cv-source-origin is-${row.origin}`}>{ORIGIN_WORD[row.origin]}</span>
            <span className="cv-sources-text">
              <strong>{row.subject}</strong> · {row.what}
              {row.value && <> · {row.value}</>}
              {row.detail && <span className="cv-muted"> ({row.detail})</span>}
              <span className="cv-muted cv-sources-ref">{row.source}</span>
            </span>
          </li>
        ))}
      </ul>
    </details>
  );
}
