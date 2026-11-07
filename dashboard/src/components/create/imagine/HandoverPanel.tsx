import { useState } from 'react';
import { CircleAlert, CircleCheck, CircleDashed, Lock } from 'lucide-react';
import { handoverMissing } from '../../../api/endpoints/imagine';
import { refusalOf } from '../../../api/endpoints/create';
import { useHandover, useHandoverChecklist, useRework } from '../../../hooks/use-imagine';
import type { HandoverItem, ImagineStudio } from '../../../types/imagine';

const STATUS_WORD: Record<HandoverItem['status'], string> = {
  done: 'Done',
  missing: 'Missing',
  assumed: 'Assumed',
};

function ItemIcon({ status }: { status: HandoverItem['status'] }) {
  if (status === 'done') return <CircleCheck size={14} aria-hidden="true" />;
  if (status === 'assumed') return <CircleDashed size={14} aria-hidden="true" />;
  return <CircleAlert size={14} aria-hidden="true" />;
}

function ItemList({ items, label }: { items: HandoverItem[]; label: string }) {
  return (
    <ul className="cv-checklist" aria-label={label}>
      {items.map((item) => (
        <li key={item.id} className={`is-${item.status}`}>
          <ItemIcon status={item.status} />
          <span className="cv-checklist-label">
            {item.label}
            {item.detail && <span className="cv-muted"> · {item.detail}</span>}
          </span>
          <span className="cv-checklist-state">{STATUS_WORD[item.status]}</span>
        </li>
      ))}
    </ul>
  );
}

/**
 * The gate from Imagine to Develop: the checklist, and one primary action,
 * "Hand over to engineering". A refused handover (409) lists exactly what is
 * missing. After handover the studio is read only; reopening it needs a
 * reason and a confirmation, and marks dependent evidence stale.
 */
export function HandoverPanel({ projectId, studio }: { projectId: string; studio: ImagineStudio }) {
  const handedOver = studio.status === 'handed_over';
  const checklist = useHandoverChecklist(projectId, !handedOver);
  const handover = useHandover(projectId);
  const rework = useRework(projectId);
  const [reopening, setReopening] = useState(false);
  const [reason, setReason] = useState('');

  if (handedOver) {
    return (
      <section className="cv-card cv-handover" aria-label="Handover">
        <p className="cv-handover-done" data-testid="imagine-readonly">
          <Lock size={14} aria-hidden="true" /> Handed over to engineering
          {studio.revision !== null ? ` · revision ${studio.revision}` : ''} · read only
        </p>
        {reopening ? (
          <form
            className="cv-reopen"
            onSubmit={(e) => {
              e.preventDefault();
              if (!reason.trim()) return;
              rework.mutate(reason.trim(), {
                onSuccess: () => {
                  setReopening(false);
                  setReason('');
                },
              });
            }}
          >
            <label className="cv-label" htmlFor="cv-reopen-reason">
              Why reopen Imagine? Develop and Check results that depend on it will need checking again.
            </label>
            <textarea
              id="cv-reopen-reason"
              className="cv-input"
              rows={2}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
            <div className="cv-actions">
              <button type="submit" className="cv-btn" disabled={!reason.trim() || rework.isPending}>
                {rework.isPending ? 'Reopening…' : 'Confirm and reopen'}
              </button>
              <button type="button" className="cv-link" onClick={() => setReopening(false)}>
                Cancel
              </button>
            </div>
            {rework.isError && (
              <p className="cv-error" role="alert">
                {refusalOf(rework.error)}
              </p>
            )}
          </form>
        ) : (
          <button type="button" className="cv-btn" onClick={() => setReopening(true)}>
            Reopen Imagine
          </button>
        )}
      </section>
    );
  }

  const missing = handover.isError ? handoverMissing(handover.error) : null;
  const items = checklist.data?.items ?? [];

  return (
    <section className="cv-card cv-handover" aria-labelledby="cv-handover-title">
      <h3 id="cv-handover-title">Ready for engineering?</h3>
      {items.length > 0 ? (
        <ItemList items={items} label="Handover checklist" />
      ) : checklist.isError ? (
        <p className="cv-muted">The checklist could not be loaded: {refusalOf(checklist.error)}</p>
      ) : (
        <p className="cv-muted">Loading the checklist…</p>
      )}
      <button
        type="button"
        className="cv-primary"
        data-testid="stage-primary"
        disabled={handover.isPending}
        onClick={() => handover.mutate()}
      >
        {handover.isPending ? 'Handing over…' : 'Hand over to engineering'}
      </button>
      {missing && (
        <div className="cv-handover-missing" role="alert" data-testid="handover-missing">
          <p>Not yet. These are still missing:</p>
          {missing.length > 0 ? (
            <ItemList items={missing} label="Missing for handover" />
          ) : (
            <p className="cv-muted">See the checklist above.</p>
          )}
        </div>
      )}
      {handover.isError && !missing && (
        <p className="cv-error" role="alert">
          {refusalOf(handover.error)}
        </p>
      )}
    </section>
  );
}
