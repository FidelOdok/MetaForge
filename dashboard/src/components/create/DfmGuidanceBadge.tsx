import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Factory } from 'lucide-react';
import {
  decideDfmFinding,
  dfmKeys,
  getDfm,
  openFindings,
  type DfmDecisionKind,
  type DfmDecisionResult,
} from '../../api/endpoints/dfmFindings';
import { useComponentSelection } from '../../store/component-selection-store';

/** The badge's words: a count of suggestions, never a readiness percentage (section 10). */
export function guidanceBadgeText(count: number): string {
  return `Manufacturing guidance: ${count} suggestion${count === 1 ? '' : 's'}`;
}

const ACTIONS: { id: DfmDecisionKind; label: string }[] = [
  { id: 'keep_design', label: 'Keep design' },
  { id: 'try_fix', label: 'Try suggested fix' },
  { id: 'explore_process', label: 'Explore another process' },
];

/**
 * The Create stage's compact manufacturing guidance badge (C3D slice 4).
 *
 * Shown only when the preliminary checks found something for this project.
 * Selecting it lists each suggestion in plain words; selecting a suggestion
 * highlights the affected part (the shared component selection) and offers
 * **Keep design**, **Try suggested fix** or **Explore another process**.
 * Nothing here changes the design: a fix comes back as a proposal, or waits
 * for approval when it would trade away something protected.
 */
export function DfmGuidanceBadge({ projectId }: { projectId: string }) {
  const qc = useQueryClient();
  const view = useQuery({
    queryKey: dfmKeys.project(projectId),
    queryFn: ({ signal }) => getDfm(projectId, { signal }),
  });
  const select = useComponentSelection((s) => s.select);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<DfmDecisionResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const items = openFindings(view.data);
  if (!items.length) return null;

  const choose = (findingId: string, componentId: string, name: string) => {
    setActive(findingId);
    setResult(null);
    setError(null);
    select(projectId, { componentId, name, from: 'create' });
  };

  const decide = async (findingId: string, decision: DfmDecisionKind) => {
    setBusy(true);
    setError(null);
    try {
      const out = await decideDfmFinding(projectId, findingId, decision);
      setResult(out);
      if (decision === 'keep_design') {
        setActive(null);
        await qc.invalidateQueries({ queryKey: ['dfm', projectId] });
      }
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'That did not work. Nothing was changed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div ref={root} className="cv-dfm" data-testid="dfm-guidance">
      <button
        type="button"
        className="cv-mfg-note cv-dfm-badge"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((o) => !o)}
        title="Preliminary manufacturing guidance from quick checks. Not an analysis result."
      >
        <Factory size={12} aria-hidden="true" /> {guidanceBadgeText(items.length)}
      </button>
      {open && (
        <div className="cv-dfm-panel" role="dialog" aria-label="Manufacturing guidance">
          <p className="cv-dfm-intro">Quick, preliminary checks. Your design is not changed unless you choose to.</p>
          <ul className="cv-dfm-list">
            {items.map(({ component, finding }) => (
              <li key={finding.id} className={active === finding.id ? 'is-on' : ''}>
                <button
                  type="button"
                  className="cv-dfm-item"
                  aria-pressed={active === finding.id}
                  onClick={() => choose(finding.id, component.component_id, component.name)}
                >
                  <strong>{component.name}</strong>
                  <span>{finding.message}</span>
                </button>
                {active === finding.id && (
                  <div className="cv-dfm-detail">
                    <p>{finding.suggested_fix}</p>
                    <div className="cv-dfm-actions" role="group" aria-label="What to do">
                      {ACTIONS.map((a) => (
                        <button
                          key={a.id}
                          type="button"
                          className="cv-chip-btn"
                          disabled={busy}
                          onClick={() => void decide(finding.id, a.id)}
                        >
                          {a.label}
                        </button>
                      ))}
                    </div>
                    {result && result.finding_id === finding.id && (
                      <div className="cv-dfm-result" role="status">
                        <p>{result.message}</p>
                        {result.alternatives && (
                          <ul>
                            {result.alternatives.map((alt) => (
                              <li key={alt.profile.id}>
                                {alt.process}: {alt.note}
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    )}
                    {error && (
                      <p className="cv-dfm-error" role="alert">
                        {error}
                      </p>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
