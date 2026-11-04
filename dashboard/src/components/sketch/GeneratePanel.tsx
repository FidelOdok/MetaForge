import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Check, Columns2, GitBranch, Sparkles, X } from 'lucide-react';
import {
  GENERATED_LABEL,
  SKETCH_STYLES,
  SKETCH_STYLE_LABELS,
  acceptGeneration,
  branchGeneration,
  compareGenerations,
  listGenerations,
  rejectGeneration,
  spendConfirmationOf,
  startGeneration,
  type GenerateSketchBody,
  type GeneratedConcept,
  type GenerationMode,
  type SketchStyle,
} from '../../api/endpoints/sketchGeneration';
import { referenceRecord, refusalOf } from '../../api/endpoints/create';
import { nodeFileUrl } from '../../api/endpoints/twin';
import { useCreation } from '../../hooks/use-creation';
import { useCreateStore } from '../../store/create-store';
import { useProjectOperations } from '../../store/operations-store';
import { debounceGenerate, useSketchGenerationStore } from '../../store/sketch-generation-store';
import { ReferenceChip } from '../create/ReferenceChip';

export const generationKeys = {
  all: ['sketch-generations'] as const,
  project: (projectId: string) => ['sketch-generations', projectId] as const,
};

const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'superseded', 'stale']);
const MODE_WORDS: Record<GenerationMode, string> = {
  text: 'From your description',
  reference: 'From your references',
  sketch: 'From your sketch',
};

/** The mode the gateway will choose, said before the person asks (FSG-001..003). */
export function modeFor(sketchKey: string | null, activeReferences: number): GenerationMode {
  if (sketchKey) return 'sketch';
  if (activeReferences > 0) return 'reference';
  return 'text';
}

interface GeneratePanelProps {
  projectId: string;
  /** The saved sketch to refine (`SKT-...`), if there is one. */
  sketchKey: string | null;
  sketchRevision: string | null;
  /** Unsaved strokes: the request waits for the save rather than using an old revision. */
  sketchUnsaved: boolean;
}

/**
 * Generate a concept from the description, the references or the sketch
 * (FORGE-794). A compact button in the sketch layer opens it; nothing else
 * changes on the stage. Each result is a proposal in the variants strip:
 * accept, reject, compare two, or branch. The accepted concept stays on the
 * stage while a new one is generated and when it fails, and every image is
 * labelled illustrative.
 */
export function GeneratePanel({ projectId, sketchKey, sketchRevision, sketchUnsaved }: GeneratePanelProps) {
  const session = useSketchGenerationStore((s) => s.sessions[projectId]) ?? useSketchGenerationStore.getState().session(projectId);
  const patch = useSketchGenerationStore((s) => s.patch);
  const clearRegion = useSketchGenerationStore((s) => s.clearRegion);
  const toggleCompare = useSketchGenerationStore((s) => s.toggleCompare);
  const draft = useCreateStore((s) => s.sessions[projectId]?.draft ?? '');
  const creation = useCreation(session.open ? projectId : null);
  const ops = useProjectOperations(projectId).ops;
  const qc = useQueryClient();

  const pendingOp = session.pendingOperation ? ops.byId[session.pendingOperation] : undefined;
  const running = !!session.pendingOperation && (!pendingOp || !TERMINAL.has(pendingOp.state));
  const overview = useQuery({
    queryKey: generationKeys.project(projectId),
    queryFn: ({ signal }) => listGenerations(projectId, signal),
    // While a request runs, look again until its proposals arrive.
    refetchInterval: running ? 2_000 : false,
  });

  const brief = creation.data?.creation.brief;
  const references = useMemo(
    () => (brief?.references ?? []).map((id) => referenceRecord(brief, id)),
    [brief],
  );
  const active = references.filter((r) => r.active && r.role !== 'verification_evidence');
  const mode = modeFor(sketchKey, active.length);
  const line = overview.data?.lines.find((l) => !String(l.baseline_for ?? '').includes('#branch-')) ?? null;
  const baseline = line?.head ?? null;
  const proposals = (overview.data?.proposals ?? []).filter((p) => p.generation_status === 'proposed').slice(0, 6);
  const intent = session.intent || draft;

  const refresh = () => void qc.invalidateQueries({ queryKey: generationKeys.project(projectId) });

  const start = useMutation({
    mutationFn: (confirm: boolean) => {
      const body: GenerateSketchBody = {
        intent: intent.trim(),
        style: session.style,
        negative: session.negative
          .split(',')
          .map((n) => n.trim())
          .filter(Boolean),
        reference_ids: active.map((r) => r.work_product_id),
        sketch_key: mode === 'sketch' ? sketchKey : null,
        sketch_revision: mode === 'sketch' ? sketchRevision : null,
        mask: session.regionOnly ? session.mask : null,
        mask_instruction: session.regionOnly ? intent.trim() : '',
        n_variants: session.variants,
        expected_baseline: line?.head_ref ?? 'none',
        confirm_spend: confirm,
      };
      return startGeneration(projectId, body);
    },
    onSuccess: (started) => {
      patch(projectId, { pendingOperation: started.operation_id, error: null, spendPrompt: null });
      refresh();
    },
    onError: (error) => {
      const spend = spendConfirmationOf(error);
      patch(projectId, spend ? { spendPrompt: spend, error: null } : { error: refusalOf(error) });
    },
  });

  const decide = useMutation({
    mutationFn: async ({ action, node }: { action: 'accept' | 'reject' | 'branch'; node: GeneratedConcept }) => {
      if (action === 'accept') return acceptGeneration(projectId, node.node_id, line?.head_ref ?? 'none');
      if (action === 'reject') return rejectGeneration(projectId, node.node_id);
      return branchGeneration(projectId, node.node_id);
    },
    onSuccess: refresh,
    onError: (error) => {
      patch(projectId, { error: refusalOf(error) });
      refresh();
    },
  });

  const [a, b] = session.compare;
  const comparison = useQuery({
    queryKey: [...generationKeys.project(projectId), 'compare', a, b],
    queryFn: () => compareGenerations(projectId, a!, b!),
    enabled: !!a && !!b,
  });

  const canGenerate = !!intent.trim() && !(mode === 'sketch' && sketchUnsaved) && !start.isPending;
  const generate = (confirm = false) => {
    if (!canGenerate) return;
    debounceGenerate(projectId, () => start.mutate(confirm));
  };

  return (
    <div className="sk-generate" data-testid="generate-panel">
      <button
        type="button"
        className={`sk-save ${session.open ? 'is-on' : ''}`}
        aria-expanded={session.open}
        title="Generate a concept from your description, references or sketch"
        onClick={() => patch(projectId, { open: !session.open })}
      >
        <Sparkles size={14} aria-hidden="true" /> Generate
      </button>

      {session.open && (
        <div className="sk-generate-pop" role="group" aria-label="Generate a concept">
          <p className="sk-generate-mode" data-testid="generate-mode">
            {MODE_WORDS[mode]}
            {session.regionOnly && session.mask ? ' · selected region only' : ''}
          </p>
          <label className="sk-field">
            <span>Describe it</span>
            <textarea
              rows={2}
              value={intent}
              placeholder="A three legged stool with a round seat…"
              onChange={(e) => patch(projectId, { intent: e.target.value })}
            />
          </label>
          <div className="sk-generate-row">
            <label className="sk-field">
              <span>Style</span>
              <select value={session.style} onChange={(e) => patch(projectId, { style: e.target.value as SketchStyle })}>
                {SKETCH_STYLES.map((s) => (
                  <option key={s} value={s}>
                    {SKETCH_STYLE_LABELS[s]}
                  </option>
                ))}
              </select>
            </label>
            <label className="sk-field">
              <span>Alternatives</span>
              <select
                value={session.variants}
                onChange={(e) => patch(projectId, { variants: Number(e.target.value) })}
                title="Each alternative is a paid image"
              >
                {[1, 2, 3].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="sk-field">
            <span>Avoid</span>
            <input
              type="text"
              value={session.negative}
              placeholder="e.g. armrests, text"
              onChange={(e) => patch(projectId, { negative: e.target.value })}
            />
          </label>
          {session.mask ? (
            <label className="sk-check">
              <input
                type="checkbox"
                checked={session.regionOnly}
                onChange={(e) => patch(projectId, { regionOnly: e.target.checked })}
              />
              Change only the selected region
              <button type="button" className="cv-icon-btn" aria-label="Clear the region" onClick={() => clearRegion(projectId)}>
                <X size={12} />
              </button>
            </label>
          ) : (
            <p className="sk-hint">To change one part, draw a region with the region tool first.</p>
          )}
          {references.length > 0 && (
            <div className="sk-generate-refs" aria-label="References for this concept">
              {references.map((r) => (
                <ReferenceChip key={r.work_product_id} projectId={projectId} record={r} />
              ))}
            </div>
          )}
          {mode === 'sketch' && sketchUnsaved && <p className="sk-hint">Saving your sketch first…</p>}
          {session.spendPrompt ? (
            <div className="sk-generate-spend" role="alert">
              <span>{session.spendPrompt}</span>
              <button type="button" className="sk-save" onClick={() => generate(true)}>
                Generate anyway
              </button>
              <button type="button" className="sk-save" onClick={() => patch(projectId, { spendPrompt: null })}>
                Not now
              </button>
            </div>
          ) : (
            <button type="button" className="sk-save sk-generate-go" disabled={!canGenerate} onClick={() => generate(false)}>
              <Sparkles size={14} aria-hidden="true" /> {running ? 'Generate again' : 'Generate'}
            </button>
          )}
          {session.error && (
            <p className="sk-generate-error" role="alert">
              {session.error}
              {baseline ? ' The accepted concept is unchanged.' : ''}
            </p>
          )}
        </div>
      )}

      {(proposals.length > 0 || baseline) && (
        <div className="sk-variants" role="list" aria-label="Generated concepts" data-testid="variants-strip">
          {baseline && (
            <figure className="sk-variant is-accepted" role="listitem">
              <img src={nodeFileUrl(baseline.node_id)} alt="Accepted concept" />
              <figcaption>Accepted · {line?.head_ref}</figcaption>
            </figure>
          )}
          {proposals.map((p) => (
            <figure key={p.node_id} className={`sk-variant ${p.review_required ? 'is-flagged' : ''}`} role="listitem">
              <img src={nodeFileUrl(p.node_id)} alt={`Proposal: ${p.intent ?? p.name}`} />
              <figcaption>
                {p.review_required ? 'Check it: ' + (p.preservation?.failures[0] ?? 'it changed what it had to keep') : 'Proposal'}
              </figcaption>
              <div className="sk-variant-actions">
                <button type="button" className="cv-icon-btn" aria-label="Accept this concept" title="Accept" onClick={() => decide.mutate({ action: 'accept', node: p })}>
                  <Check size={13} />
                </button>
                <button type="button" className="cv-icon-btn" aria-label="Reject this concept" title="Reject" onClick={() => decide.mutate({ action: 'reject', node: p })}>
                  <X size={13} />
                </button>
                <button
                  type="button"
                  className="cv-icon-btn"
                  aria-pressed={session.compare.includes(p.node_id)}
                  aria-label="Compare this concept"
                  title="Compare (pick two)"
                  onClick={() => toggleCompare(projectId, p.node_id)}
                >
                  <Columns2 size={13} />
                </button>
                <button type="button" className="cv-icon-btn" aria-label="Branch from this concept" title="Branch" onClick={() => decide.mutate({ action: 'branch', node: p })}>
                  <GitBranch size={13} />
                </button>
              </div>
            </figure>
          ))}
          <span className="sk-generated-label">{GENERATED_LABEL}</span>
        </div>
      )}

      {a && b && (
        <div className="sk-compare" role="group" aria-label="Compare two concepts" data-testid="compare-view">
          <img src={nodeFileUrl(a)} alt="Concept A" />
          <img src={nodeFileUrl(b)} alt="Concept B" />
          <p>
            {comparison.data
              ? `${Math.round((comparison.data.pixel_difference ?? 0) * 100)}% of the image differs` +
                (comparison.data.changed.length ? ` · changed: ${comparison.data.changed.join(', ')}` : '')
              : 'Comparing…'}
          </p>
          <button type="button" className="sk-save" onClick={() => patch(projectId, { compare: [] })}>
            Close
          </button>
        </div>
      )}
    </div>
  );
}

/** The accepted concept, drawn under the strokes in sketch space (FSG-013, VIS-003). */
export function AcceptedConceptImage({ projectId }: { projectId: string }) {
  const overview = useQuery({
    queryKey: generationKeys.project(projectId),
    queryFn: ({ signal }) => listGenerations(projectId, signal),
  });
  const line = overview.data?.lines.find((l) => !String(l.baseline_for ?? '').includes('#branch-'));
  const head = line?.head;
  if (!head) return null;
  // A generated image's shorter side spans one sketch unit, centred on the
  // origin, so a region drawn on the stage lands on the same pixels.
  const [pw, ph] = (head.size ?? '1024x1024').split('x').map(Number);
  const s = Math.min(pw || 1, ph || 1);
  const w = (pw || 1) / s;
  const h = (ph || 1) / s;
  return (
    <image
      href={nodeFileUrl(head.node_id)}
      x={-w / 2}
      y={-h / 2}
      width={w}
      height={h}
      preserveAspectRatio="xMidYMid meet"
      className="sk-accepted-concept"
      data-testid="accepted-concept"
      aria-label={GENERATED_LABEL}
    />
  );
}
