import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { PackageCheck } from 'lucide-react';
import { dfmKeys, getReleaseChecklist, type Readiness } from '../../api/endpoints/dfmFindings';

/**
 * The words Create may show for making the product (section 16.2 item 7).
 *
 * "Ready for prototype" and "Ready for production" appear only when the
 * manufacturing release checklist backs them; otherwise the missing items are
 * the next steps. Never a percentage, never a completion it cannot back.
 */
export function readinessText(readiness: Readiness): string {
  if (readiness.production) return 'Ready for production';
  if (readiness.prototype) return 'Ready for prototype';
  const n = readiness.next_steps.length;
  return n ? `Not ready to make yet: ${n} next step${n === 1 ? '' : 's'}` : 'Not ready to make yet';
}

/**
 * Create's compact readiness chip (C3D slice 6). Hidden until a process with
 * exact manufacturing checks is chosen; selecting it lists what is missing in
 * plain words (the section 10 status wording).
 */
export function ReadinessStatus({ projectId }: { projectId: string }) {
  const checklist = useQuery({
    queryKey: dfmKeys.checklist(projectId),
    queryFn: ({ signal }) => getReleaseChecklist(projectId, signal),
  });
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  const data = checklist.data;
  if (!data || !data.applies) return null;
  const readiness = data.readiness;
  const steps = readiness.next_steps;
  return (
    <div className="cv-dfm" data-testid="readiness-status">
      <button
        type="button"
        className={`cv-mfg-note cv-dfm-badge cv-readiness is-${readiness.level}`}
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen((o) => !o)}
        title="From the manufacturing release checklist of the current revision"
      >
        <PackageCheck size={12} aria-hidden="true" /> {readinessText(readiness)}
      </button>
      {open && (
        <div className="cv-dfm-panel" role="dialog" aria-label="What is needed to make it">
          {steps.length ? (
            <>
              <p className="cv-dfm-intro">
                {readiness.prototype ? 'Before production:' : 'Before this can be made:'}
              </p>
              <ul className="cv-dfm-list" data-testid="readiness-next-steps">
                {steps.map((step) => (
                  <li key={step}>
                    <span className="cv-dfm-item">{step}</span>
                  </li>
                ))}
              </ul>
            </>
          ) : (
            <p className="cv-dfm-intro">Everything the release checklist asks for is in place.</p>
          )}
        </div>
      )}
    </div>
  );
}
