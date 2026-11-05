import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getReconstruction,
  proposeReconstruction,
  reconstructionKeys,
  type ProposalView,
  type ProposedFeature,
  type ProposedParameter,
} from '../../api/endpoints/reconstruction';

function valueText(p: ProposedParameter): string {
  if (p.value === null || p.value === undefined) return 'unknown';
  return `${p.value}${p.unit && p.unit !== 'deg' ? ` ${p.unit}` : p.unit === 'deg' ? '°' : ''}`;
}

function sourceText(p: ProposedParameter): string {
  if (!p.source) return '';
  return [p.source.kind.replace(/_/g, ' '), p.source.ref].filter(Boolean).join(' · ');
}

function FeatureRow({ f, index }: { f: ProposedFeature; index: number }) {
  const params = Object.entries(f.parameters);
  return (
    <li className="tw-fh-feature" data-testid={`fh-feature-${f.id}`}>
      <div className="tw-fh-feature-head">
        <span className="tw-fh-index">{index + 1}</span>
        <strong>{f.name}</strong>
        <span className="tw-fh-kind">{f.kind.replace('_', ' ')}</span>
        {f.suggested_by === 'llm' && (
          <span className="tw-fh-badge is-llm" title={f.provenance[0]?.detail}>
            suggested by a model
          </span>
        )}
        {f.status !== 'proposed' && <span className={`tw-fh-badge is-${f.status}`}>{f.status}</span>}
        {f.existing_ref && <span className="tw-fh-badge">from {f.existing_ref}</span>}
      </div>
      {f.placement && (
        <p className="tw-fh-line">
          Plane {f.placement.plane}, offset {f.placement.offset} mm ({f.placement.status})
        </p>
      )}
      {params.length > 0 && (
        <table className="tw-fh-params">
          <tbody>
            {params.map(([name, p]) => (
              <tr key={name} data-testid={`fh-param-${f.id}-${name}`}>
                <th scope="row">{name}</th>
                <td>{valueText(p)}</td>
                <td>
                  <span className={`tw-fh-status is-${p.status}`}>{p.status}</span>
                </td>
                <td className="tw-fh-source">
                  {sourceText(p)}
                  {p.minimum !== null && p.minimum !== undefined ? ` · at least ${p.minimum} ${p.unit ?? ''} (${p.minimum_rule})` : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {f.dfm_rules.length > 0 && <p className="tw-fh-line">Rules: {f.dfm_rules.join(', ')}</p>}
      {f.note && <p className="tw-fh-line">{f.note}</p>}
    </li>
  );
}

function ProposalBlock({ v }: { v: ProposalView }) {
  const p = v.proposal;
  const inputs = p.inputs;
  return (
    <section className="tw-fh-component" data-testid={`fh-component-${v.component_id}`}>
      <header>
        <strong>{v.component_name}</strong>
        <span className="tw-fh-ref">{v.item_ref ?? v.item_key}</span>
        <span className={`tw-fh-badge is-${v.status}`}>{v.status.replace('_', ' ')}</span>
        {v.staleness !== 'current' && <span className="tw-fh-badge is-stale">{v.staleness}: propose again</span>}
      </header>
      <ol className="tw-fh-features">
        {p.features.map((f, i) => (
          <FeatureRow key={f.id} f={f} index={i} />
        ))}
      </ol>
      {p.assumptions.length > 0 && (
        <div className="tw-fh-block">
          <div className="tw-fh-label">Assumptions</div>
          <ul>
            {p.assumptions.map((a) => (
              <li key={a.id}>{a.text}</li>
            ))}
          </ul>
        </div>
      )}
      {p.ambiguities.length > 0 && (
        <div className="tw-fh-block">
          <div className="tw-fh-label">Needs a person</div>
          <ul>
            {p.ambiguities.map((a) => (
              <li key={a.id}>
                {a.question} {a.choice ? `Answered: ${a.choice}.` : a.consequential ? '(open)' : `(default: ${a.default})`}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="tw-fh-block" data-testid={`fh-dfm-${v.component_id}`}>
        <div className="tw-fh-label">DFM strategy</div>
        <p>{p.dfm_strategy.text}</p>
        {p.dfm_strategy.rules.length > 0 && (
          <ul>
            {p.dfm_strategy.rules.map((r) => (
              <li key={r.rule_id}>
                {r.rule_id} ({r.kind.replace(/_/g, ' ')}): {r.applied_as}. Source: {r.source}
              </li>
            ))}
          </ul>
        )}
      </div>
      <dl className="tw-fh-provenance" data-testid={`fh-provenance-${v.component_id}`}>
        <dt>Concept</dt>
        <dd>{inputs.assembly_decision_id ?? 'none'}</dd>
        <dt>Layout</dt>
        <dd>{inputs.layout_ref ?? 'none'}</dd>
        <dt>References</dt>
        <dd>{inputs.reference_ids.join(', ') || 'none'}</dd>
        <dt>Sketches</dt>
        <dd>{inputs.sketch_refs.join(', ') || 'none'}</dd>
        <dt>Context</dt>
        <dd>{inputs.context_ref ?? 'none'}</dd>
        <dt>Model pass</dt>
        <dd>
          {p.llm.outcome.replace('_', ' ')}
          {p.llm.model ? ` (${p.llm.model})` : ''}
        </dd>
        {Object.keys(p.accepted_sketches).length > 0 && (
          <>
            <dt>Accepted as</dt>
            <dd>{Object.values(p.accepted_sketches).join(', ')}</dd>
          </>
        )}
      </dl>
    </section>
  );
}

/**
 * Engineering's "Feature history (proposed)" (spec 4B): on demand, the
 * proposed features of each component with every parameter's value, status
 * (estimated, confirmed, unknown) and source, the assumptions, the questions
 * a person must settle, the DFM strategy note and its rules, and provenance.
 */
export function FeatureHistorySection({ projectId, componentId }: { projectId: string; componentId?: string | null }) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const qc = useQueryClient();
  const view = useQuery({
    queryKey: reconstructionKeys.project(projectId),
    queryFn: ({ signal }) => getReconstruction(projectId, signal),
    enabled: open,
  });
  const proposals = (view.data?.components ?? [])
    .filter((c) => !componentId || c.component_id === componentId)
    .map((c) => c.proposal)
    .filter((p): p is ProposalView => !!p);

  const propose = async () => {
    setBusy(true);
    setError(null);
    try {
      await proposeReconstruction(projectId, componentId ? { component_ids: [componentId] } : {});
      await qc.invalidateQueries({ queryKey: reconstructionKeys.project(projectId) });
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
      setError(typeof detail === 'string' ? detail : 'The feature history could not be proposed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="tw-fh" data-testid="feature-history-section">
      <button type="button" className="tw-fh-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        Feature history (proposed)
      </button>
      {open && (
        <div className="tw-fh-body">
          {view.isLoading ? (
            <p>Loading…</p>
          ) : !view.data?.concept_accepted ? (
            <p>No concept is accepted yet, so there is nothing to reconstruct.</p>
          ) : proposals.length ? (
            proposals.map((v) => <ProposalBlock key={v.item_key} v={v} />)
          ) : (
            <p>No feature history is proposed{componentId ? ' for this component' : ''} yet.</p>
          )}
          {view.data?.concept_accepted && (
            <button type="button" className="tw-fh-propose" disabled={busy} onClick={() => void propose()}>
              {busy ? 'Proposing…' : proposals.length ? 'Propose again' : 'Propose a feature history'}
            </button>
          )}
          {error && <p role="alert">{error}</p>}
        </div>
      )}
    </div>
  );
}
