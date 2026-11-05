import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { dfmKeys, evaluateDfm, getDfm, type DfmComponentFindings, type DfmFinding } from '../../api/endpoints/dfmFindings';
import { FaceHighlight, ReleaseChecklistPanel, VerificationItems } from './DfmExactPanels';

function measuredText(f: DfmFinding): string {
  const unit = f.unit && f.unit !== 'ratio' ? ` ${f.unit}` : '';
  return `${f.measured ?? 'unknown'}${unit} against ${f.comparison} ${f.limit ?? 'unknown'}${unit}`;
}

function anchorText(f: DfmFinding): string {
  const parts = [f.anchor.component_id];
  if (f.anchor.feature) parts.push(`feature ${f.anchor.feature}`);
  if (f.anchor.face_signature) parts.push(`face ${f.anchor.face_signature}`);
  if (f.anchor.faces.length > 1) parts.push(`${f.anchor.faces.length} faces`);
  return parts.join(' · ');
}

function measurementText(c: DfmComponentFindings): string | null {
  const e = c.exact_evidence ?? {};
  if (c.kind !== 'exact' || !e.wall_method) return null;
  const dir = Array.isArray(e.build_direction) ? (e.build_direction as number[]).join(', ') : '0, 0, 1';
  return `Measured on the exact model with ${String(e.tool)}: wall thickness by ${String(e.wall_method)} (${String(
    e.wall_samples ?? 0,
  )} samples); build direction (${dir}), ${String(e.build_direction_source ?? '')}.`;
}

function FindingFaces({ projectId, c, f }: { projectId: string; c: DfmComponentFindings; f: DfmFinding }) {
  const [show, setShow] = useState(false);
  if (c.kind !== 'exact' || !f.anchor.faces.length || !c.geometry_refs[0]) return null;
  return (
    <div>
      <button type="button" className="tw-dfm-show-faces" onClick={() => setShow((v) => !v)} aria-expanded={show}>
        {show ? 'Hide faces' : `Show ${f.anchor.faces.length} face${f.anchor.faces.length === 1 ? '' : 's'} on the model`}
      </button>
      {show && <FaceHighlight projectId={projectId} geometryRef={c.geometry_refs[0]} faces={f.anchor.faces} />}
    </div>
  );
}

function ComponentBlock({ c, stale, projectId }: { c: DfmComponentFindings; stale?: boolean; projectId: string }) {
  const measured = measurementText(c);
  return (
    <section className="tw-dfm-component" data-testid={`dfm-component-${c.component_id}`}>
      <header>
        <strong>{c.name}</strong>
        <span className="tw-dfm-kind" data-testid={`dfm-kind-${c.component_id}`}>
          {c.kind === 'exact' ? 'Exact checks' : 'Preliminary guidance'}
        </span>
        <span className={`tw-dfm-status is-${c.status}`}>{c.status_label}</span>
      </header>
      {c.kind === 'exact' && (
        <p className="tw-dfm-numbers">
          {c.complete ? 'Every exact check ran' : 'Incomplete: not every exact check ran'} ({(c.rules_run ?? []).length} of{' '}
          {(c.checks_required ?? []).length})
          {c.guidance_only_rules?.length ? `; guidance only: ${c.guidance_only_rules.join(', ')}` : ''}
        </p>
      )}
      {measured && <p className="tw-dfm-numbers">{measured}</p>}
      <dl className="tw-dfm-meta">
        <dt>Rule profile</dt>
        <dd>{c.profile ? `${c.profile.id} ${c.profile.version}` : 'none (process unknown)'}</dd>
        <dt>Process</dt>
        <dd>
          {c.process ?? 'unknown'} ({c.process_authority.replace('_', ' ')})
        </dd>
        <dt>Geometry</dt>
        <dd>
          {c.geometry_level} · {c.geometry_refs.join(', ') || 'none'}
        </dd>
        <dt>Context</dt>
        <dd>{c.context_ref}</dd>
        <dt>Evidence</dt>
        <dd className={stale || c.staleness.status === 'stale' ? 'is-stale' : ''}>
          {c.staleness.status}
          {c.staleness.reason ? `: ${c.staleness.reason}` : ''}
        </dd>
      </dl>
      {c.findings.length ? (
        <ul className="tw-dfm-findings">
          {c.findings.map((f) => (
            <li key={f.id} className={`is-${f.severity}`}>
              <div className="tw-dfm-finding-head">
                <span className="tw-dfm-severity">{f.severity}</span>
                <span>
                  {f.rule_title} <code>{f.rule_id}</code>
                </span>
              </div>
              <p>{f.message}</p>
              <p className="tw-dfm-numbers">
                Measured {measuredText(f)} · confidence {f.confidence.replace('_', ' ')} · {f.rule_kind.replace('_', ' ')}
              </p>
              <p className="tw-dfm-numbers">Anchor: {anchorText(f)}</p>
              <p className="tw-dfm-numbers">Limit source: {f.limit_source}</p>
              {f.evidence_refs.length > 0 && <p className="tw-dfm-numbers">Evidence: {f.evidence_refs.join(', ')}</p>}
              <p className="tw-dfm-fix">Suggested fix: {f.suggested_fix}</p>
              {f.decision && <p className="tw-dfm-numbers">Decision: {f.decision.decision.replace('_', ' ')}</p>}
              <FindingFaces projectId={projectId} c={c} f={f} />
            </li>
          ))}
        </ul>
      ) : (
        <p className="tw-dfm-none">No findings from the checks that ran.</p>
      )}
      {c.not_assessed.length > 0 && (
        <details className="tw-dfm-gaps">
          <summary>{c.not_assessed.length} not assessed</summary>
          <ul>
            {c.not_assessed.map((n) => (
              <li key={n.rule_id}>{n.text}</li>
            ))}
          </ul>
        </details>
      )}
      {!stale && <VerificationItems projectId={projectId} items={c.verification_items ?? []} />}
    </section>
  );
}

/**
 * Engineering's DFM Findings, on demand in the contextual inspector (section 11).
 *
 * Exact checks on a validated model (C3D slice 6) show their measured values,
 * the faces they are anchored to (highlighted on the model on request), the
 * physical verification items and the manufacturing release checklist with
 * the DFM report.
 *
 * Per component: the rule profile and its version, measured against limit with
 * units and confidence, the anchor (component, feature, face), the evidence it
 * was read from and whether that evidence is still current. Filtered to the
 * selected component when one is selected.
 */
export function DfmFindingsSection({ projectId, componentId }: { projectId: string; componentId?: string | null }) {
  const qc = useQueryClient();
  const [showHistory, setShowHistory] = useState(false);
  const [running, setRunning] = useState(false);
  const view = useQuery({
    queryKey: dfmKeys.project(projectId, showHistory),
    queryFn: ({ signal }) => getDfm(projectId, { history: showHistory, signal }),
  });

  const run = async () => {
    setRunning(true);
    try {
      await evaluateDfm(projectId);
      await qc.invalidateQueries({ queryKey: ['dfm', projectId] });
    } finally {
      setRunning(false);
    }
  };

  if (view.isLoading) return <p className="tw-dfm-none">Loading manufacturing findings…</p>;
  if (view.isError || !view.data) return <p className="tw-dfm-none">The manufacturing findings could not be read.</p>;
  const data = view.data;
  const components = componentId ? data.components.filter((c) => c.component_id === componentId) : data.components;
  const history = componentId ? data.history.filter((c) => c.component_id === componentId) : data.history;

  return (
    <div className="tw-dfm" data-testid="dfm-findings">
      <div className="tw-dfm-top">
        <span className={`tw-dfm-status is-${data.status}`}>{data.status_label}</span>
        <button type="button" onClick={() => void run()} disabled={running}>
          {running ? 'Checking…' : 'Run checks'}
        </button>
      </div>
      <p className="tw-dfm-disclaimer">{data.disclaimer}</p>
      {data.profiles.length > 0 && (
        <p className="tw-dfm-numbers">Profiles: {data.profiles.map((p) => `${p.id} ${p.version}`).join(', ')}</p>
      )}
      {components.length ? (
        components.map((c) => <ComponentBlock key={c.finding_set_id} c={c} projectId={projectId} />)
      ) : (
        <p className="tw-dfm-none">Manufacturing not assessed for this selection yet.</p>
      )}
      <label className="tw-dfm-history">
        <input type="checkbox" checked={showHistory} onChange={(e) => setShowHistory(e.target.checked)} /> Show stale and earlier findings
      </label>
      {showHistory &&
        history.map((c) => <ComponentBlock key={c.finding_set_id} c={c} stale projectId={projectId} />)}
      <ReleaseChecklistPanel projectId={projectId} />
    </div>
  );
}
