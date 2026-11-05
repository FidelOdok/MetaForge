import { useQuery } from '@tanstack/react-query';
import {
  STOP_TEXT,
  cadLoopKeys,
  getCadLoops,
  loopImageUrl,
  type LoopIteration,
  type LoopRun,
} from '../../api/endpoints/cadLoop';

export interface DiffLine {
  kind: 'same' | 'added' | 'removed';
  text: string;
}

/** A line diff of two scripts (longest common subsequence; scripts are short). */
export function scriptDiff(before: string, after: string): DiffLine[] {
  const a = before.split('\n');
  const b = after.split('\n');
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      lcs[i]![j] = a[i] === b[j] ? lcs[i + 1]![j + 1]! + 1 : Math.max(lcs[i + 1]![j]!, lcs[i]![j + 1]!);
    }
  }
  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i]! });
      i++;
      j++;
    } else if (lcs[i + 1]![j]! >= lcs[i]![j + 1]!) {
      out.push({ kind: 'removed', text: a[i++]! });
    } else {
      out.push({ kind: 'added', text: b[j++]! });
    }
  }
  while (i < a.length) out.push({ kind: 'removed', text: a[i++]! });
  while (j < b.length) out.push({ kind: 'added', text: b[j++]! });
  return out;
}

const yesNo = (ok: boolean | undefined) => (ok ? 'yes' : 'no');

function IterationDetail({ run, it, previous }: { run: LoopRun; it: LoopIteration; previous: LoopIteration | null }) {
  const size = it.measurements?.size_mm;
  const diff = previous?.script && it.script ? scriptDiff(previous.script, it.script).filter((l) => l.kind !== 'same') : [];
  return (
    <li className="tw-fh-proposal" data-testid={`cad-loop-iteration-${it.n}`}>
      <p className="tw-fh-line">
        <strong>Iteration {it.n}</strong>
        {it.focus ? ` · ${it.focus}` : ''}
        {it.summary ? ` · ${it.summary}` : ''}
      </p>
      {it.render?.views && it.render.views.some((v) => v.file) && (
        <div className="tw-loop-views" style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {it.render.views
            .filter((v) => v.file)
            .map((v) => (
              <img
                key={v.view}
                src={loopImageUrl(run.node_id, v.file!)}
                alt={`${v.label ?? v.view}, iteration ${it.n}`}
                width={120}
                height={120}
              />
            ))}
        </div>
      )}
      {it.build && !it.build.ok && <p className="tw-fh-line">Build failed: {it.build.error}</p>}
      {it.render && !it.render.ok && <p className="tw-fh-line">Render failed: {it.render.error}</p>}
      <table className="tw-fh-params" aria-label={`Iteration ${it.n} checks`}>
        <tbody>
          {it.validity && (
            <tr>
              <th scope="row">Validity</th>
              <td>{it.validity.outcome}</td>
            </tr>
          )}
          {size && (
            <tr>
              <th scope="row">Size (mm)</th>
              <td>
                {size.x} x {size.y} x {size.z}
                {it.measurements?.volume_mm3 != null ? ` · ${Math.round(Number(it.measurements.volume_mm3))} mm3` : ''}
                {it.measurements?.dfm_measure?.min_wall_mm != null ? ` · thinnest wall ${it.measurements.dfm_measure.min_wall_mm} mm` : ''}
              </td>
            </tr>
          )}
          {(it.dimension_checks ?? []).map((d) => (
            <tr key={`${d.kind}-${d.axis}`}>
              <th scope="row">
                {d.axis.toUpperCase()} ({d.kind})
              </th>
              <td>
                {d.measured} mm, wanted {d.wanted} · {d.ok ? 'in tolerance' : 'out of tolerance'}
              </td>
            </tr>
          ))}
          {it.dfm && (
            <tr>
              <th scope="row">DFM</th>
              <td>
                {it.dfm.status}
                {it.dfm.profile ? ` · ${it.dfm.profile}` : ''}
                {it.dfm.findings?.length
                  ? ` · ${it.dfm.findings.map((f) => `${f.severity}: ${f.rule_title ?? f.rule_id ?? f.message}`).join('; ')}`
                  : ''}
              </td>
            </tr>
          )}
          {it.critique && (
            <tr>
              <th scope="row">Review</th>
              <td>
                {it.critique.verdict.replace(/_/g, ' ')} (advisory)
                {(it.critique.issues ?? []).map((i, k) => (
                  <span key={k} className="tw-fh-source">
                    {' '}
                    · [{i.severity}] {i.view ? `${i.view}: ` : ''}
                    {i.feature ? `${i.feature}: ` : ''}
                    {i.description}
                    {i.suggested_change ? ` (suggested: ${i.suggested_change})` : ''}
                  </span>
                ))}
              </td>
            </tr>
          )}
          {it.gate && (
            <tr>
              <th scope="row">Stop rule</th>
              <td>
                review {yesNo(it.gate.critic_satisfied)} · validity {yesNo(it.gate.validity_pass)} · no blocking DFM{' '}
                {yesNo(it.gate.no_blocking_dfm)} · sizes {yesNo(it.gate.dimensions_in_tolerance)}
              </td>
            </tr>
          )}
          {it.models && (
            <tr>
              <th scope="row">Models</th>
              <td>
                {Object.entries(it.models)
                  .map(([role, m]) => `${role}: ${m.provider ?? '?'} ${m.model ?? ''}${m.cost_usd != null ? ` $${m.cost_usd.toFixed(4)}` : ' (cost unknown)'}`)
                  .join(' · ')}
              </td>
            </tr>
          )}
        </tbody>
      </table>
      {diff.length > 0 && (
        <details>
          <summary>Script changes from iteration {previous!.n}</summary>
          <pre className="tw-loop-diff" data-testid={`cad-loop-diff-${it.n}`}>
            {diff.map((l, k) => (
              <div key={k} className={l.kind === 'added' ? 'is-added' : 'is-removed'}>
                {l.kind === 'added' ? '+ ' : '- '}
                {l.text}
              </div>
            ))}
          </pre>
        </details>
      )}
      {!previous && it.script && (
        <details>
          <summary>Script</summary>
          <pre className="tw-loop-diff">{it.script}</pre>
        </details>
      )}
    </li>
  );
}

function RunDetail({ run }: { run: LoopRun }) {
  return (
    <div className="tw-fh-proposal" data-testid={`cad-loop-run-${run.node_id}`}>
      <p className="tw-fh-line">
        <strong>{run.name}</strong> · {run.status === 'running' ? 'running' : run.stop_reason ? STOP_TEXT[run.stop_reason] : ''}
        {run.stop_detail && run.stop_reason !== 'satisfied' ? ` (${run.stop_detail})` : ''}
      </p>
      <p className="tw-fh-line">
        From {run.context?.sketch_ref ?? 'the parametric plan'} · base {run.context?.cad_key ?? ''}
        {run.context?.base_revision ? `@${run.context.base_revision}` : ' (no exact model yet)'}
        {run.budget
          ? ` · ${run.budget.model_calls} of ${run.budget.max_model_calls} model calls, $${run.budget.spend_usd.toFixed(4)} priced${
              run.budget.unpriced_calls ? `, ${run.budget.unpriced_calls} unpriced` : ''
            }`
          : ''}
        {run.result ? ` · result: iteration ${run.result.iteration}, ${run.result.status} (a proposal, not validated)` : ''}
        {run.decision ? ` · ${run.decision.decision} by ${run.decision.by}` : ''}
      </p>
      <ol className="tw-loop-timeline" aria-label="Iterations">
        {run.iterations.map((it, k) => (
          <IterationDetail key={it.n} run={run} it={it} previous={k > 0 ? run.iterations[k - 1]! : null} />
        ))}
      </ol>
    </div>
  );
}

/**
 * Engineering: the CAD design loop's iteration timeline, one run at a time:
 * the rendered views, the review, the measurements and checks, the DFM
 * findings and the script diff per iteration. Read-only; the person decides
 * on Create (or here through the same proposal).
 */
export function CadLoopSection({ projectId, componentId }: { projectId: string | null | undefined; componentId?: string | null }) {
  const state = useQuery({
    queryKey: cadLoopKeys.project(projectId ?? '', componentId),
    queryFn: ({ signal }) => getCadLoops(projectId!, componentId, signal),
    enabled: !!projectId,
    retry: false,
  });
  const runs = state.data?.runs ?? [];
  if (!runs.length) return null;
  return (
    <section className="tw-fh" aria-label="CAD design loop" data-testid="cad-loop-section">
      <h4>CAD design loop (proposals)</h4>
      {runs.slice(0, 5).map((r) => (
        <RunDetail key={r.node_id} run={r} />
      ))}
    </section>
  );
}
