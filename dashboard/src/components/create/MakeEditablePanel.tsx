import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Wand2 } from 'lucide-react';
import {
  acceptReconstruction,
  defaultFeatureIds,
  getReconstruction,
  openQuestions,
  proposeReconstruction,
  reconstructionKeys,
  type ProposalView,
} from '../../api/endpoints/reconstruction';
import { twinKeys } from '../../hooks/use-twin';

function errorText(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === 'string' ? detail : fallback;
}

/**
 * Create's "Make it editable" (spec 4B in plain words): how we would build
 * the accepted concept, the questions that need the person now, and one
 * accept. No feature tree, no parameters: Engineering shows those.
 */
export function MakeEditablePanel({ projectId, onClose }: { projectId: string; onClose: () => void }) {
  const qc = useQueryClient();
  const view = useQuery({
    queryKey: reconstructionKeys.project(projectId),
    queryFn: ({ signal }) => getReconstruction(projectId, signal),
  });
  const [proposing, setProposing] = useState(false);
  const [accepting, setAccepting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Record<string, Record<string, string>>>({});
  const asked = useRef(false);

  const pending: ProposalView[] = useMemo(
    () =>
      (view.data?.components ?? [])
        .map((c) => c.proposal)
        .filter((p): p is ProposalView => !!p && p.status === 'proposed'),
    [view.data],
  );
  const needsProposal =
    !!view.data?.concept_accepted &&
    (view.data.components ?? []).some((c) => c.source === 'made' && (!c.proposal || c.proposal.staleness === 'stale'));

  // Opening the panel asks for the proposal once, when there is none yet.
  useEffect(() => {
    if (!view.data || asked.current || !needsProposal) return;
    asked.current = true;
    setProposing(true);
    proposeReconstruction(projectId)
      .then(() => qc.invalidateQueries({ queryKey: reconstructionKeys.project(projectId) }))
      .catch((err) => setError(errorText(err, 'We could not work out how to build it yet.')))
      .finally(() => setProposing(false));
  }, [view.data, needsProposal, projectId, qc]);

  const questions = pending.flatMap((p) => openQuestions(p.proposal).map((q) => ({ p, q })));
  const unanswered = questions.filter(({ p, q }) => q.consequential && !answers[p.item_key]?.[q.id]);

  const accept = async () => {
    setAccepting(true);
    setError(null);
    try {
      let sketches = 0;
      for (const p of pending) {
        const ids = defaultFeatureIds(p.proposal);
        if (!ids.length) continue;
        const reply = await acceptReconstruction(projectId, p.item_key, {
          feature_ids: ids,
          choices: answers[p.item_key] ?? {},
          base_revision: p.revision,
        });
        sketches += reply.sketches.length;
      }
      await qc.invalidateQueries({ queryKey: reconstructionKeys.project(projectId) });
      await qc.invalidateQueries({ queryKey: twinKeys.all });
      setDone(
        sketches
          ? `Editable. ${sketches} part${sketches === 1 ? '' : 's'} wait for your sign-off; sizes we only estimated are asked again before the exact model.`
          : 'Editable.',
      );
    } catch (err) {
      setError(errorText(err, 'The proposal could not be accepted.'));
    } finally {
      setAccepting(false);
    }
  };

  const summary = view.data?.summary;
  return (
    <div className="cv-editable" role="region" aria-label="Make it editable" data-testid="make-editable">
      <div className="cv-editable-head">
        <strong>
          <Wand2 size={14} aria-hidden="true" /> Make it editable
        </strong>
        <button type="button" className="cv-chip-btn" onClick={onClose}>
          Close
        </button>
      </div>
      {view.isLoading || proposing ? (
        <p className="cv-editable-status" role="status">
          <Loader2 size={14} className="cv-spin" aria-hidden="true" /> Working out how to build it…
        </p>
      ) : view.data && !view.data.concept_accepted ? (
        <p className="cv-editable-status">Choose a concept first; then it can be made editable.</p>
      ) : done ? (
        <p className="cv-editable-status" role="status" data-testid="make-editable-done">
          {done}
        </p>
      ) : (
        <>
          {summary && (
            <p className="cv-editable-summary" data-testid="make-editable-summary">
              {summary}
            </p>
          )}
          {questions.length > 0 && (
            <div className="cv-editable-questions">
              {questions.map(({ p, q }) => (
                <fieldset key={`${p.item_key}:${q.id}`} className="cv-editable-question">
                  <legend>{q.question}</legend>
                  <div className="cv-editable-options">
                    {q.options.map((o) => {
                      const on = (answers[p.item_key]?.[q.id] ?? (q.consequential ? null : q.default)) === o.id;
                      return (
                        <button
                          key={o.id}
                          type="button"
                          className={`cv-chip-btn ${on ? 'is-on' : ''}`}
                          aria-pressed={on}
                          onClick={() =>
                            setAnswers((a) => ({ ...a, [p.item_key]: { ...(a[p.item_key] ?? {}), [q.id]: o.id } }))
                          }
                        >
                          {o.label}
                        </button>
                      );
                    })}
                  </div>
                </fieldset>
              ))}
            </div>
          )}
          {pending.length > 0 && (
            <div className="cv-editable-actions">
              <button
                type="button"
                className="cv-primary"
                data-testid="make-editable-accept"
                disabled={accepting || unanswered.length > 0}
                title={unanswered.length ? 'Answer the questions above first' : undefined}
                onClick={() => void accept()}
              >
                {accepting ? 'Making it editable…' : 'Accept and make editable'}
              </button>
              {unanswered.length > 0 && (
                <span className="cv-editable-hint">
                  {unanswered.length} question{unanswered.length === 1 ? '' : 's'} to answer first
                </span>
              )}
            </div>
          )}
          {!pending.length && view.data?.accepted && <p className="cv-editable-status">It is already editable.</p>}
        </>
      )}
      {error && (
        <p className="cv-editable-status is-error" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
