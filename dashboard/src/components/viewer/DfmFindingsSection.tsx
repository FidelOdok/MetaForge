import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { dfmKeys, evaluateDfm, getDfm, type DfmComponentFindings, type DfmFinding } from '../../api/endpoints/dfmFindings';

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

function ComponentBlock({ c, stale }: { c: DfmComponentFindings; stale?: boolean }) {
  return (
    <section className="tw-dfm-component" data-testid={`dfm-component-${c.component_id}`}>
      <header>
        <strong>{c.name}</strong>
        <span className={`tw-dfm-status is-${c.status}`}>{c.status_label}</span>
      </header>
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
    </section>
  );
}

/**
 * Engineering's DFM Findings, on demand in the contextual inspector (section 11).
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
        components.map((c) => <ComponentBlock key={c.finding_set_id} c={c} />)
      ) : (
        <p className="tw-dfm-none">Manufacturing not assessed for this selection yet.</p>
      )}
      <label className="tw-dfm-history">
        <input type="checkbox" checked={showHistory} onChange={(e) => setShowHistory(e.target.checked)} /> Show stale and earlier findings
      </label>
      {showHistory &&
        history.map((c) => <ComponentBlock key={c.finding_set_id} c={c} stale />)}
    </div>
  );
}
