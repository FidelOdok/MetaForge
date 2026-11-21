import { useState } from 'react';
import type { ComponentThread, ThreadRelation } from '../../api/endpoints/components';
import { useAdoptComponent, useComponentThreads } from '../../hooks/use-components';
import { refusalOf } from '../../api/endpoints/create';

/**
 * The component thread (FORGE-903): one row per component, one column per
 * Create stage, so every stage shows where the same part stands in the others.
 * The column of the stage on screen is marked. Where Develop has moved past
 * the concept, **Use in Imagine** takes Develop's part into the studio.
 */

export type ThreadStage = 'imagine' | 'develop' | 'check' | 'make';

const STAGES: Array<[ThreadStage, string]> = [
  ['imagine', 'Imagine'],
  ['develop', 'Develop'],
  ['check', 'Check'],
  ['make', 'Make'],
];

const RELATION: Record<ThreadRelation, { label: string; help: string; tone: 'ok' | 'warn' | 'info' }> = {
  same: { label: 'Same', help: 'Imagine and Develop name the same part.', tone: 'ok' },
  refined: {
    label: 'Refined',
    help: 'Develop chose a more exact order code or a library entry for the part Imagine named.',
    tone: 'info',
  },
  replaced: { label: 'Replaced', help: 'Develop chose a different part for this role.', tone: 'warn' },
  develop_only: { label: 'Develop only', help: 'Develop added this; Imagine does not have it.', tone: 'info' },
  imagine_only: { label: 'Imagine only', help: 'Named in Imagine; Develop has not chosen a part yet.', tone: 'warn' },
};

function imagineCell(t: ComponentThread): string {
  const i = t.imagine;
  if (i.status === 'absent') return 'Not in the studio';
  const code = i.mpn && !i.mpn.includes('<') ? i.mpn : 'no order code yet';
  return `${code}${i.library ? ` · library ${i.library}` : ''}${i.status === 'candidate' ? ' · alternative' : ''}`;
}

function developCell(t: ComponentThread): string {
  const d = t.develop;
  const parts: string[] = [];
  const sel = d.selections[0];
  if (sel) {
    parts.push(`${sel.mpn}${sel.selection === 'candidate' ? ' (candidate)' : ''}`);
    if (sel.library?.name) parts.push(`${sel.library.shared ? 'shared' : 'your'} library ${sel.library.name}`);
  } else if (d.status === 'designed') {
    parts.push('Board designed');
  }
  const refs = d.boards.filter((b) => b.ref).map((b) => `${b.board} ${b.ref}`);
  if (refs.length) parts.push(refs.join(', '));
  return parts.length ? parts.join(' · ') : 'Nothing chosen yet';
}

function checkCell(t: ComponentThread): string {
  const c = t.check;
  if (c.status === 'violates') return `Misses ${c.violates.join(', ')}`;
  if (c.status === 'pass') return `Meets ${c.margins} requirement${c.margins === 1 ? '' : 's'}`;
  if (c.status === 'unchecked') return 'No requirement checks this part';
  return 'Nothing to check yet';
}

function makeCell(t: ComponentThread): string {
  const m = t.make;
  if (m.status === 'absent') return 'Not in a BOM or order';
  const orders = m.lines.filter((l) => l.kind === 'procurement');
  const bom = m.lines.filter((l) => l.kind === 'bom');
  const out: string[] = [];
  const refs = bom.flatMap((l) => l.refs);
  if (bom.length) out.push(`In the BOM${refs.length ? ` (${refs.join(', ')})` : ''}`);
  if (orders.length) out.push(`Ordered ${orders.reduce((n, l) => n + Number(l.quantity || 0), 0)}`);
  return out.join(' · ');
}

const CELL: Record<ThreadStage, (t: ComponentThread) => string> = {
  imagine: imagineCell,
  develop: developCell,
  check: checkCell,
  make: makeCell,
};

function tone(stage: ThreadStage, t: ComponentThread): string {
  const status = t[stage].status;
  if (status === 'absent') return 'is-absent';
  if (stage === 'check' && status === 'violates') return 'is-bad';
  if (stage === 'imagine' && (t.relation === 'replaced' || t.relation === 'refined')) return 'is-behind';
  return 'is-there';
}

export function ComponentThreadPanel({
  projectId,
  stage,
  open,
}: {
  projectId: string;
  stage: ThreadStage;
  /** Render as an open card (Imagine, Check) rather than collapsed. */
  open?: boolean;
}) {
  const threads = useComponentThreads(projectId);
  const adopt = useAdoptComponent(projectId);
  const [adopting, setAdopting] = useState<string | null>(null);
  const data = threads.data;
  if (threads.isLoading) return null;
  if (!data) {
    return threads.error ? (
      <p className="cv-muted" data-testid="component-thread-error">
        Components could not be read: {refusalOf(threads.error)}
      </p>
    ) : null;
  }
  if (data.threads.length === 0) return null;
  const behind = data.threads.filter((t) => t.adopt).length;
  const studioRevision = data.studio?.revision;

  return (
    <details className="cv-card cv-thread" data-testid="component-thread" open={open}>
      <summary>
        Components across the stages{' '}
        <span className="cv-muted">
          · {data.threads.length} component{data.threads.length === 1 ? '' : 's'}
          {behind > 0 && ` · ${behind} ahead of Imagine`}
        </span>
      </summary>
      <div className="cv-thread-scroll">
        <table className="cv-thread-table">
          <thead>
            <tr>
              <th scope="col">Component</th>
              {STAGES.map(([id, label]) => (
                <th key={id} scope="col" className={id === stage ? 'is-current' : undefined} aria-current={id === stage ? 'step' : undefined}>
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.threads.map((t) => {
              const rel = RELATION[t.relation];
              return (
                <tr key={t.key} data-testid={`thread-${t.key}`}>
                  <th scope="row">
                    <span className="cv-thread-name">{t.name}</span>
                    <span className={`cv-thread-rel is-${rel.tone}`} title={rel.help} data-testid="thread-relation">
                      {rel.label}
                    </span>
                    {t.adopt && studioRevision != null && (
                      t.adopt.needs_rework ? (
                        <span className="cv-muted cv-thread-note">Reopen Imagine to take this in</span>
                      ) : (
                        <button
                          type="button"
                          className="cv-link cv-thread-adopt"
                          disabled={adopt.isPending}
                          title={`Write ${t.adopt.mpn}${t.adopt.library ? ` (library ${t.adopt.library})` : ''} into the Imagine studio`}
                          onClick={() => {
                            setAdopting(t.key);
                            adopt.mutate({ revision: studioRevision, key: t.key });
                          }}
                        >
                          {adopt.isPending && adopting === t.key ? 'Taking into Imagine…' : 'Use in Imagine'}
                        </button>
                      )
                    )}
                    {adopt.isError && adopting === t.key && (
                      <span className="cv-error cv-thread-note" role="alert">
                        {refusalOf(adopt.error)}
                      </span>
                    )}
                  </th>
                  {STAGES.map(([id]) => (
                    <td key={id} className={`${tone(id, t)}${id === stage ? ' is-current' : ''}`}>
                      {CELL[id](t)}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {data.warnings.length > 0 && (
        <p className="cv-fineprint" data-testid="component-thread-warnings">
          {data.warnings.join(' ')}
        </p>
      )}
    </details>
  );
}
