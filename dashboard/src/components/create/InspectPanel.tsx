import { useState } from 'react';
import { ImagePlus, Plus, Sparkles, Trash2 } from 'lucide-react';
import {
  addEvidence,
  decideBuild,
  putInspectionPlan,
  refusalOf,
  setCheckResult,
  type Build,
  type CreationView,
  type InspectionCheck,
  type InspectionMethod,
  type ResultStatus,
} from '../../api/endpoints/create';
import { createUpload } from '../../api/endpoints/uploads';
import { useCreationWrite } from '../../hooks/use-creation';
import { useCreateStore } from '../../store/create-store';
import { CameraCapture } from './CameraCapture';
import { METHOD_COPY, RESULT_COPY } from './copy';

const RESULTS: ResultStatus[] = ['passed', 'failed', 'inconclusive', 'awaiting_review'];

let checkSeq = 0;
const newCheckId = () => `c${Date.now().toString(36)}${(checkSeq += 1)}`;

/** One check per success criterion line, as a starting point the person edits. */
export function checksFromCriteria(criteria: string, needs: string): InspectionCheck[] {
  const camera = /photo|camera|look|picture/i.test(needs);
  return criteria
    .split('\n')
    .map((line) => line.replace(/^[-*•\d.)\s]+/, '').trim())
    .filter(Boolean)
    .map((feature) => ({
      id: newCheckId(),
      feature,
      condition: '',
      evidence: camera ? 'Photo' : 'Your confirmation',
      method: /\d\s?(mm|cm|in|g|kg)\b/i.test(feature) ? 'measurement' : camera ? 'camera' : 'operator',
      reviewer: 'person',
      blocking: true,
    }));
}

interface InspectPanelProps {
  projectId: string;
  view: CreationView;
}

export function InspectPanel({ projectId, view }: InspectPanelProps) {
  const { creation } = view;
  const latest = [...creation.builds].reverse().find((b) => b.status !== 'preparing' && b.status !== 'cancelled');
  return (
    <div className="cv-panel-body">
      <PlanEditor projectId={projectId} view={view} />
      {latest ? (
        <BuildInspection key={latest.id} projectId={projectId} build={latest} version={creation.version} />
      ) : (
        <p className="cv-muted">Inspection opens when a build is released for making.</p>
      )}
    </div>
  );
}

function PlanEditor({ projectId, view }: InspectPanelProps) {
  const { creation } = view;
  const [checks, setChecks] = useState<InspectionCheck[]>(creation.inspection_plan);
  const [dirty, setDirty] = useState(false);
  const save = useCreationWrite(projectId, (next: InspectionCheck[]) =>
    putInspectionPlan(projectId, next, { expected_version: creation.version }),
  );
  const plan = dirty ? checks : creation.inspection_plan;
  const edit = (next: InspectionCheck[]) => {
    setChecks(next);
    setDirty(true);
  };
  const criteria = creation.brief.items.success_criteria?.value ?? '';

  return (
    <section className="cv-subsection" aria-labelledby="cv-plan">
      <h4 id="cv-plan">Inspection plan</h4>
      <p className="cv-fineprint">Decided before making: what must be true, how it is checked, and who checks it.</p>
      {plan.length === 0 && criteria.trim() && (
        <button type="button" className="cv-btn" onClick={() => edit(checksFromCriteria(criteria, creation.brief.items.inspection_needs?.value ?? ''))}>
          <Sparkles size={14} /> Turn the success criteria into checks
        </button>
      )}
      {plan.length === 0 && !criteria.trim() && (
        <p className="cv-muted">Add success criteria to the brief, or add checks here.</p>
      )}
      {plan.map((c) => (
        <div key={c.id} className="cv-check-row">
          <input
            className="cv-input"
            aria-label="What is checked"
            value={c.feature}
            onChange={(e) => edit(plan.map((x) => (x.id === c.id ? { ...x, feature: e.target.value } : x)))}
          />
          <input
            className="cv-input"
            aria-label={`${c.feature}: acceptance condition`}
            placeholder="Pass when… (tolerance)"
            value={c.condition}
            onChange={(e) => edit(plan.map((x) => (x.id === c.id ? { ...x, condition: e.target.value } : x)))}
          />
          <select
            className="cv-input"
            aria-label={`${c.feature}: method`}
            value={c.method}
            onChange={(e) => edit(plan.map((x) => (x.id === c.id ? { ...x, method: e.target.value as InspectionMethod } : x)))}
          >
            {(Object.keys(METHOD_COPY) as InspectionMethod[]).map((m) => (
              <option key={m} value={m}>
                {METHOD_COPY[m]}
              </option>
            ))}
          </select>
          <select
            className="cv-input"
            aria-label={`${c.feature}: reviewer`}
            value={c.reviewer}
            onChange={(e) => edit(plan.map((x) => (x.id === c.id ? { ...x, reviewer: e.target.value as 'person' | 'agent' } : x)))}
          >
            <option value="person">You review</option>
            <option value="agent">Agent may evaluate</option>
          </select>
          <label className="cv-check">
            <input
              type="checkbox"
              checked={c.blocking}
              onChange={(e) => edit(plan.map((x) => (x.id === c.id ? { ...x, blocking: e.target.checked } : x)))}
            />
            Required
          </label>
          <button type="button" className="cv-icon-btn" aria-label={`Remove ${c.feature}`} onClick={() => edit(plan.filter((x) => x.id !== c.id))}>
            <Trash2 size={13} />
          </button>
        </div>
      ))}
      <div className="cv-actions">
        <button
          type="button"
          className="cv-link"
          onClick={() =>
            edit([
              ...plan,
              { id: newCheckId(), feature: 'New check', condition: '', evidence: '', method: 'operator', reviewer: 'person', blocking: true },
            ])
          }
        >
          <Plus size={13} /> Add a check
        </button>
        {dirty && (
          <button
            type="button"
            className="cv-btn cv-btn-primary"
            disabled={save.isPending}
            onClick={() => save.mutate(checks, { onSuccess: () => setDirty(false) })}
          >
            Save plan
          </button>
        )}
      </div>
      {save.isError && <p className="cv-error">{refusalOf(save.error)}</p>}
      {creation.builds.some((b) => b.status === 'released' || b.status === 'making') && dirty && (
        <p className="cv-fineprint">The build already released keeps the checks it was released with.</p>
      )}
    </section>
  );
}

function BuildInspection({ projectId, build, version }: { projectId: string; build: Build; version: number }) {
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');
  const [reason, setReason] = useState('');
  const setDraft = useCreateStore((s) => s.setDraft);
  const setIntent = useCreateStore((s) => s.setIntent);
  const submit = useCreateStore((s) => s.submit);

  const evidence = useCreationWrite(projectId, (vars: Parameters<typeof addEvidence>[2]) =>
    addEvidence(projectId, build.id, { ...vars, expected_version: version }),
  );
  const result = useCreationWrite(projectId, (vars: { checkId: string; status: ResultStatus }) =>
    setCheckResult(projectId, build.id, vars.checkId, { status: vars.status, expected_version: version }),
  );
  const decide = useCreationWrite(projectId, (decision: 'accept' | 'reject') =>
    decideBuild(projectId, build.id, { decision, reason, expected_version: version }),
  );
  const error = evidence.error ?? result.error ?? decide.error;

  const photo = async (file: File, checkId: string | null) => {
    setUploading(true);
    setUploadError('');
    try {
      const upload = await createUpload([file], { projectId, approve: true, description: `Inspection of build ${build.number}` });
      const staged = upload.files[0];
      if (!staged?.work_product_id) throw new Error(staged?.error ?? 'The photo was not imported');
      evidence.mutate({ kind: 'photo', check_id: checkId, work_product_id: staged.work_product_id, upload_id: upload.id });
    } catch (e) {
      setUploadError(refusalOf(e));
    } finally {
      setUploading(false);
    }
  };

  const askAgent = () => {
    const lines = build.checks.map((c) => `- ${c.feature}${c.condition ? ` (pass when ${c.condition})` : ''}`).join('\n');
    const photos = build.evidence.filter((e) => e.work_product_id).map((e) => e.work_product_id);
    setIntent(projectId, 'ask');
    setDraft(
      projectId,
      `Inspect build ${build.number} of ${build.revision.label || build.revision.work_product_id}. Compare the evidence ` +
        `(${photos.length ? photos.join(', ') : 'none yet'}) with the design and these checks:\n${lines}\n` +
        'For each check say what you observe, a confidence, what is uncertain, and whether you need another view, a ' +
        'measurement or an operator check. Recommend a correction for anything that fails.',
    );
    submit(projectId, { ask: [], change: [], design: [] });
  };

  const improve = () => {
    const failing = build.checks
      .filter((c) => (build.results[c.id]?.status ?? 'awaiting_review') !== 'passed')
      .map((c) => `${c.feature}: ${RESULT_COPY[build.results[c.id]?.status ?? 'awaiting_review']}${build.results[c.id]?.note ? ` (${build.results[c.id]!.note})` : ''}`);
    setIntent(projectId, 'change');
    setDraft(projectId, `Build ${build.number} needs a correction.\n${failing.join('\n')}\nPropose the design change that fixes this.`);
  };

  const decided = build.acceptance;
  return (
    <section className="cv-subsection" aria-labelledby="cv-inspect-build">
      <h4 id="cv-inspect-build">
        Build {build.number} · {build.revision.label || build.revision.work_product_id}
      </h4>
      <CameraCapture onSnapshot={(f) => void photo(f, null)} disabled={uploading || !!decided} />
      <label className="cv-btn cv-upload-btn">
        <ImagePlus size={14} /> Add photos
        <input
          type="file"
          accept="image/*"
          capture="environment"
          multiple
          hidden
          disabled={uploading || !!decided}
          onChange={(e) => {
            Array.from(e.target.files ?? []).forEach((f) => void photo(f, null));
            e.target.value = '';
          }}
        />
      </label>
      {uploading && <p className="cv-muted">Uploading…</p>}
      {uploadError && <p className="cv-error">{uploadError}</p>}
      {build.evidence.length > 0 && (
        <p className="cv-muted">
          {build.evidence.length} piece{build.evidence.length === 1 ? '' : 's'} of evidence recorded.
        </p>
      )}
      <button type="button" className="cv-btn" onClick={askAgent} disabled={!!decided}>
        <Sparkles size={14} /> Ask MetaForge to compare the evidence with the design
      </button>
      <p className="cv-fineprint">The agent reports findings with its confidence. A check you review is passed only by you.</p>

      <ul className="cv-results">
        {build.checks.map((c) => {
          const r = build.results[c.id];
          const status = r?.status ?? 'awaiting_review';
          return (
            <li key={c.id} className={`cv-result is-${status}`}>
              <div>
                <strong>{c.feature}</strong>
                {!c.blocking && <span className="cv-tag">Not required</span>}
                <p className="cv-muted">
                  {METHOD_COPY[c.method]}
                  {c.condition ? ` · pass when ${c.condition}` : ''}
                  {r?.note ? ` · ${r.note}` : ''}
                  {r?.confidence != null ? ` · confidence ${Math.round(r.confidence * 100)}%` : ''}
                </p>
              </div>
              <div className="cv-result-actions" role="radiogroup" aria-label={`${c.feature} result`}>
                {RESULTS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    role="radio"
                    aria-checked={status === s}
                    className={`cv-pill is-${s} ${status === s ? 'is-on' : ''}`}
                    disabled={!!decided || result.isPending}
                    onClick={() => result.mutate({ checkId: c.id, status: s })}
                  >
                    {RESULT_COPY[s]}
                  </button>
                ))}
              </div>
            </li>
          );
        })}
      </ul>

      {decided ? (
        <div className={`cv-banner ${decided.decision === 'accept' ? 'is-success' : 'is-warning'}`}>
          Build {build.number} {decided.decision === 'accept' ? 'accepted' : 'rejected'} by {decided.approver}
          {decided.reason ? `: ${decided.reason}` : ''}
          {decided.limitations.length > 0 && (
            <ul>
              {decided.limitations.map((l) => (
                <li key={l}>Open limitation: {l}</li>
              ))}
            </ul>
          )}
        </div>
      ) : build.status === 'completed' ? (
        <div className="cv-decide">
          <input className="cv-input" aria-label="Note on your decision" placeholder="Note (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="cv-actions">
            <button type="button" className="cv-btn cv-btn-primary" disabled={decide.isPending} onClick={() => decide.mutate('accept')}>
              Accept the build
            </button>
            <button type="button" className="cv-btn" disabled={decide.isPending} onClick={() => decide.mutate('reject')}>
              Reject
            </button>
          </div>
          <p className="cv-fineprint">Finished making is not acceptance. Every required check must pass first.</p>
        </div>
      ) : (
        <p className="cv-muted">Accepting opens when making is finished.</p>
      )}
      {(decided?.decision === 'reject' || build.status === 'failed') && (
        <button type="button" className="cv-btn cv-btn-primary" onClick={improve}>
          Describe the correction
        </button>
      )}
      {error && <p className="cv-error">{refusalOf(error)}</p>}
    </section>
  );
}
