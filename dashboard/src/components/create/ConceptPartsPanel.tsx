import { useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  completePart,
  confirmMapping,
  conceptPartsKeys,
  decideRevision,
  getConceptParts,
  regeneratePart,
  segmentConcept,
  type ConceptRevision,
  type MappedPart,
  type Started,
} from '../../api/endpoints/conceptParts';
import { refusalOf } from '../../api/endpoints/create';
import { spendConfirmationOf } from '../../api/endpoints/sketchGeneration';
import { twinKeys } from '../../hooks/use-twin';

/** What a paid step is asking the person before it runs. */
interface PendingSpend {
  message: string;
  retry: () => Promise<Started>;
}

const NOT_A_COMPONENT = '__none__';

/** The revision the person is working on: the newest proposal in parts, else the accepted one. */
export function workingRevision(current: ConceptRevision | null, proposals: ConceptRevision[]): ConceptRevision | null {
  const inParts = proposals.find((p) => p.mapping && p.mapping.status !== 'not_segmented');
  if (inParts) return inParts;
  if (current?.mapping && current.mapping.status !== 'not_segmented') return current;
  return null;
}

/** The part a person confirmed as `componentId`, if exactly one. */
export function confirmedPartFor(rev: ConceptRevision | null, componentId: string | null | undefined): MappedPart | null {
  if (!rev?.mapping || !componentId) return null;
  const hits = rev.mapping.parts.filter((p) => p.state === 'confirmed' && p.confirmed_component_id === componentId);
  return hits.length === 1 ? hits[0]! : null;
}

function readFile(file: File): Promise<{ data: string; mime: string }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const url = String(reader.result ?? '');
      resolve({ data: url.slice(url.indexOf(',') + 1), mime: file.type });
    };
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(file);
  });
}

interface Props {
  projectId: string;
  /** The component picked on the stage (3D-RC-009). */
  selectedComponent: { componentId: string; name?: string | null } | null;
  /** Show a revision on the stage without accepting it. */
  onShow?: (nodeId: string) => void;
}

/**
 * Concept parts on Create (L0): split the 3D concept into parts, confirm which
 * component each part is (just in time, only where it is unclear), then
 * *Redo this part* or *Seal this part*. Each paid step asks before it spends
 * past the hourly cap, and every result is a proposal: the accepted concept
 * stays until the person picks *Use this version*.
 */
export function ConceptPartsPanel({ projectId, selectedComponent, onShow }: Props) {
  const qc = useQueryClient();
  const [error, setError] = useState<string | null>(null);
  const [spend, setSpend] = useState<PendingSpend | null>(null);
  const [busy, setBusy] = useState(false);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [redo, setRedo] = useState<{ source: 'text' | 'image'; prompt: string; file: File | null } | null>(null);

  const state = useQuery({
    queryKey: conceptPartsKeys.project(projectId),
    queryFn: ({ signal }) => getConceptParts(projectId, signal),
    refetchInterval: (q) => ((q.state.data?.active_jobs.length ?? 0) > 0 ? 4_000 : false),
  });
  const data = state.data;
  const working = useMemo(() => (data ? workingRevision(data.current, data.proposals) : null), [data]);
  const part = confirmedPartFor(working, selectedComponent?.componentId);
  const running = (data?.active_jobs.length ?? 0) > 0;

  const refresh = async () => {
    await qc.invalidateQueries({ queryKey: conceptPartsKeys.project(projectId) });
    await qc.invalidateQueries({ queryKey: twinKeys.all });
  };

  /** Run a paid step; on a spend question, keep the retry for *Run it anyway*. */
  const paid = async (first: () => Promise<Started>, retry: () => Promise<Started>) => {
    setBusy(true);
    setError(null);
    try {
      await first();
      setSpend(null);
      setRedo(null);
      await refresh();
    } catch (err) {
      const message = spendConfirmationOf(err);
      if (message) setSpend({ message, retry });
      else setError(refusalOf(err));
    } finally {
      setBusy(false);
    }
  };

  const free = async (action: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      await refresh();
    } catch (err) {
      setError(refusalOf(err));
    } finally {
      setBusy(false);
    }
  };

  if (!data) return null;
  const mapping = working?.mapping ?? null;
  const pending = mapping?.parts.filter((p) => p.state === 'proposed' || p.state === 'question') ?? [];
  const notSegmented = data.proposals.find((p) => p.segmentation?.status === 'not_segmented');

  const confirmParts = () => {
    if (!working) return;
    const assignments = Object.entries(answers)
      .filter(([, v]) => v !== '')
      .map(([part, v]) => ({ part, component_id: v === NOT_A_COMPONENT ? null : v }));
    void free(async () => {
      await confirmMapping(projectId, working.node_id, { assignments, accept_proposals: true });
      setAnswers({});
    });
  };

  const startRedo = async (confirm: boolean): Promise<Started> => {
    if (!working || !part || !redo || !selectedComponent) throw new Error('nothing selected');
    if (redo.source === 'text') {
      return regeneratePart(projectId, working.node_id, {
        component_id: selectedComponent.componentId,
        source: 'text',
        prompt: redo.prompt,
        confirm_spend: confirm,
      });
    }
    if (!redo.file) throw new Error('Choose a picture of the part first.');
    const { data: image, mime } = await readFile(redo.file);
    return regeneratePart(projectId, working.node_id, {
      component_id: selectedComponent.componentId,
      source: 'image',
      image_base64: image,
      image_mime: mime,
      confirm_spend: confirm,
    });
  };

  const seal = (confirm: boolean) =>
    completePart(projectId, working!.node_id, {
      component_id: selectedComponent!.componentId,
      completion_mode: 'quick_cap',
      confirm_spend: confirm,
    });

  return (
    <section className="cv-parts" aria-label="Concept parts" data-testid="concept-parts">
      {running && (
        <p className="cv-parts-line" role="status">
          Working on the 3D concept. The accepted one stays on screen until you choose.
        </p>
      )}

      {!working && !running && data.current && (
        <button
          type="button"
          className="cv-chip-btn"
          disabled={busy}
          title="A paid step. The result is still an illustrative concept."
          onClick={() =>
            void paid(
              () => segmentConcept(projectId),
              () => segmentConcept(projectId, { confirm_spend: true }),
            )
          }
        >
          Split into parts
        </button>
      )}

      {notSegmented && (
        <p className="cv-parts-line" data-testid="parts-not-segmented">
          The concept came back whole, so it could not be split into parts. {notSegmented.segmentation?.note}
        </p>
      )}

      {working && mapping && pending.length > 0 && (
        <div className="cv-parts-mapping" data-testid="parts-mapping">
          <p className="cv-parts-line">Which part is which? We filled in the ones we are sure of.</p>
          <ul>
            {pending.map((p) => (
              <li key={p.part}>
                <label>
                  <span>{p.label ?? p.part}</span>
                  <select
                    aria-label={`Component for ${p.part}`}
                    value={answers[p.part] ?? (p.state === 'proposed' ? p.proposed_component_id ?? '' : '')}
                    onChange={(e) => setAnswers((a) => ({ ...a, [p.part]: e.target.value }))}
                  >
                    <option value="">{p.state === 'question' ? 'Choose…' : 'Keep the suggestion'}</option>
                    {data.components.map((c) => (
                      <option key={c.component_id} value={c.component_id}>
                        {c.name}
                      </option>
                    ))}
                    <option value={NOT_A_COMPONENT}>Not a component</option>
                  </select>
                </label>
                {p.state === 'proposed' && (
                  <span className="cv-parts-confidence">
                    {p.proposed_name} · {Math.round(p.confidence * 100)}% sure
                  </span>
                )}
                {p.state === 'question' && <span className="cv-parts-question">Not sure: please choose</span>}
              </li>
            ))}
          </ul>
          <button type="button" className="cv-chip-btn" disabled={busy} onClick={confirmParts}>
            Confirm parts
          </button>
        </div>
      )}

      {working && selectedComponent && part && (
        <div className="cv-parts-actions" data-testid="part-actions">
          <span className="cv-parts-line">{selectedComponent.name ?? selectedComponent.componentId}</span>
          <button type="button" className="cv-chip-btn" disabled={busy} onClick={() => setRedo({ source: 'text', prompt: '', file: null })}>
            Redo this part
          </button>
          <button
            type="button"
            className="cv-chip-btn"
            disabled={busy}
            title="Close the part's open edges. A paid step."
            onClick={() => void paid(() => seal(false), () => seal(true))}
          >
            Seal this part
          </button>
        </div>
      )}

      {redo && part && (
        <form
          className="cv-parts-redo"
          data-testid="part-redo"
          onSubmit={(e) => {
            e.preventDefault();
            void paid(
              () => startRedo(false),
              () => startRedo(true),
            );
          }}
        >
          <div role="group" aria-label="Redo from">
            <label>
              <input type="radio" checked={redo.source === 'text'} onChange={() => setRedo({ ...redo, source: 'text' })} /> Words
            </label>
            <label>
              <input type="radio" checked={redo.source === 'image'} onChange={() => setRedo({ ...redo, source: 'image' })} /> A picture
            </label>
          </div>
          {redo.source === 'text' ? (
            <input
              aria-label="How should this part look?"
              value={redo.prompt}
              maxLength={1024}
              onChange={(e) => setRedo({ ...redo, prompt: e.target.value })}
            />
          ) : (
            <input
              aria-label="A picture of the part"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={(e) => setRedo({ ...redo, file: e.target.files?.[0] ?? null })}
            />
          )}
          <button type="submit" className="cv-chip-btn" disabled={busy || (redo.source === 'text' ? !redo.prompt.trim() : !redo.file)}>
            Make it
          </button>
          <button type="button" className="cv-chip-btn" onClick={() => setRedo(null)}>
            Cancel
          </button>
        </form>
      )}

      {data.proposals.length > 0 && (
        <ul className="cv-parts-proposals" aria-label="New versions to choose from">
          {data.proposals.map((p) => (
            <li key={p.node_id} data-testid={`concept-proposal-${p.node_id}`}>
              <span>
                {p.name} · revision {p.concept_revision} · illustrative
              </span>
              {onShow && (
                <button type="button" className="cv-chip-btn" onClick={() => onShow(p.node_id)}>
                  Show
                </button>
              )}
              <button type="button" className="cv-chip-btn" disabled={busy} onClick={() => void free(() => decideRevision(projectId, p.node_id, 'accept'))}>
                Use this version
              </button>
              <button type="button" className="cv-chip-btn" disabled={busy} onClick={() => void free(() => decideRevision(projectId, p.node_id, 'reject'))}>
                Keep the previous one
              </button>
            </li>
          ))}
        </ul>
      )}

      {spend && (
        <div className="cv-stage-notice is-spend" role="alert" data-testid="parts-spend">
          <span>{spend.message}</span>
          <button type="button" className="cv-chip-btn" onClick={() => void paid(spend.retry, spend.retry)}>
            Run it anyway
          </button>
          <button type="button" className="cv-chip-btn" onClick={() => setSpend(null)}>
            Not now
          </button>
        </div>
      )}
      {error && (
        <div className="cv-stage-notice is-error" role="alert">
          <span>{error}</span>
        </div>
      )}
    </section>
  );
}
