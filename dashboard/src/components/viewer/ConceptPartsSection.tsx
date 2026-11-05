import { useQuery } from '@tanstack/react-query';
import { conceptPartsKeys, getConceptParts, type ConceptRevision } from '../../api/endpoints/conceptParts';

/** One revision's segmentation provenance and part mapping, as recorded. */
function RevisionDetail({ rev }: { rev: ConceptRevision }) {
  const seg = rev.segmentation;
  return (
    <div className="tw-fh-proposal" data-testid={`concept-parts-${rev.node_id}`}>
      <p className="tw-fh-line">
        <strong>{rev.name}</strong> · revision {rev.concept_revision} · {rev.concept_status} · {rev.operation.replace(/_/g, ' ')} ·{' '}
        {rev.authority}
      </p>
      {seg && (
        <table className="tw-fh-params" aria-label="Segmentation provenance">
          <tbody>
            <tr>
              <th scope="row">Provider</th>
              <td>
                {seg.provider} {seg.model_version}
                {seg.granularity ? ` · ${seg.granularity}` : ''}
              </td>
            </tr>
            <tr>
              <th scope="row">Task</th>
              <td>
                {seg.task_id} (input: {seg.input_kind})
              </td>
            </tr>
            <tr>
              <th scope="row">Credits</th>
              <td>{seg.credits ?? 'not reported'}</td>
            </tr>
            <tr>
              <th scope="row">Result</th>
              <td>
                {seg.status} · {seg.note}
                {seg.labelled ? ' · labelled' : ''}
              </td>
            </tr>
          </tbody>
        </table>
      )}
      {rev.mapping && rev.mapping.parts.length > 0 && (
        <table className="tw-fh-params" aria-label="Part mapping">
          <thead>
            <tr>
              <th>Part</th>
              <th>Component</th>
              <th>State</th>
              <th>Basis</th>
            </tr>
          </thead>
          <tbody>
            {rev.mapping.parts.map((p) => (
              <tr key={p.part} data-testid={`part-row-${p.part}`}>
                <th scope="row">{p.label ? `${p.part} (${p.label})` : p.part}</th>
                <td>{p.confirmed_name ?? p.proposed_name ?? '·'}</td>
                <td>
                  <span className={`tw-fh-status is-${p.state}`}>{p.state.replace(/_/g, ' ')}</span>
                </td>
                <td className="tw-fh-source">
                  {p.basis.join(', ')}
                  {p.state === 'proposed' ? ` · ${Math.round(p.confidence * 100)}%` : ''}
                  {p.confirmed_by ? ` · ${p.confirmed_by}` : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/**
 * Engineering: the concept's segmentation provenance (provider, model, task,
 * credits) and the part to component mapping, for the accepted 3D concept and
 * each proposal beside it. Read-only; the person maps parts on Create.
 */
export function ConceptPartsSection({ projectId }: { projectId: string | null | undefined }) {
  const state = useQuery({
    queryKey: conceptPartsKeys.project(projectId ?? ''),
    queryFn: ({ signal }) => getConceptParts(projectId!, signal),
    enabled: !!projectId,
    retry: false,
  });
  const revs = [state.data?.current, ...(state.data?.proposals ?? [])].filter(
    (r): r is ConceptRevision => !!r && (!!r.segmentation || !!r.mapping),
  );
  if (!revs.length) return null;
  return (
    <section className="tw-fh" aria-label="Concept parts" data-testid="concept-parts-section">
      <h4>Concept parts (illustrative)</h4>
      {revs.map((r) => (
        <RevisionDetail key={r.node_id} rev={r} />
      ))}
    </section>
  );
}
