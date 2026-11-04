import { useNodeArtifacts, useExportStep, useExactBuild } from '../../hooks/use-cad-artifacts';
import { stepFileHref, type ExportStateView } from '../../api/endpoints/cad-artifacts';
import { GeometryStatusBadge } from '../geometry/GeometryStatusBadge';
import type { TwinNode } from '../../types/twin';

/**
 * CDE slice 6 (UI-010, DEF-UI-002/003): the export controls of the selected
 * CAD revision. GLB, native CAD and STEP each show READY, STALE, QUEUED or
 * FAILED for this very revision; "Export STEP" queues the export of a
 * validated revision. The last validated revision stays named next to the
 * one on screen, so a newer unvalidated revision is never mistaken for it.
 */

const FORMAT_LABEL: Record<ExportStateView['format'], string> = {
  glb: 'GLB',
  native: 'Native CAD',
  step: 'STEP',
};

const STATE_LABEL: Record<ExportStateView['state'], string> = {
  READY: 'READY',
  STALE: 'STALE',
  QUEUED: 'QUEUED',
  FAILED: 'FAILED',
  NONE: 'NOT MADE',
};

const STATE_TONE: Record<ExportStateView['state'], string> = {
  READY: '#3dd68c',
  STALE: '#f59e0b',
  QUEUED: '#86cfff',
  FAILED: '#ff5a5a',
  NONE: '#9a9aaa',
};

interface RevisionExportControlsProps {
  node: TwinNode;
  projectId?: string | null;
}

export function RevisionExportControls({ node, projectId }: RevisionExportControlsProps) {
  const artifacts = useNodeArtifacts(node.id);
  const exportStep = useExportStep(node.id);
  const exactBuild = useExactBuild(node.id);
  const data = artifacts.data;

  if (artifacts.isLoading) {
    return (
      <div className="px-3 py-2 font-mono" style={{ fontSize: 10 }} role="status">
        Reading export states…
      </div>
    );
  }
  if (!data) return null;

  const step = data.exports.find((e) => e.format === 'step');
  const stepBusy = step?.state === 'QUEUED' || exportStep.isPending;
  const ref = data.item_key && data.revision != null ? `${data.item_key}@${data.revision}` : null;
  const canValidate =
    !data.can_export_step && data.is_head && Boolean(data.item_key) && Boolean(projectId ?? node.projectId);
  const lastValidated = data.last_validated;
  const attemptFailed = data.latest_attempt?.state === 'failed';

  return (
    <section
      aria-label="Revision exports"
      className="px-3 py-2 flex-shrink-0"
      style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}
    >
      <div className="flex items-center justify-between gap-2" style={{ marginBottom: 6 }}>
        <GeometryStatusBadge state={data.geometry_state} revision={data.revision} />
        {ref && (
          <span className="font-mono" style={{ fontSize: 10 }}>
            {ref}
            {!data.is_head && data.head_revision != null ? ` · current is @${data.head_revision}` : ''}
          </span>
        )}
      </div>
      {lastValidated && !(lastValidated.revision === data.revision) && (
        <p className="font-mono" style={{ fontSize: 10, margin: '0 0 6px' }} data-testid="last-validated">
          Last validated: {lastValidated.item_ref}
        </p>
      )}
      {attemptFailed && (
        <p className="font-mono" style={{ fontSize: 10, margin: '0 0 6px' }} role="note">
          The last engineering check from this revision failed; this revision is unchanged.
        </p>
      )}
      <ul style={{ listStyle: 'none', margin: 0, padding: 0 }} aria-label="Export states for this revision">
        {data.exports.map((e) => (
          <li
            key={e.format}
            className="flex items-center justify-between gap-2 font-mono"
            style={{ fontSize: 11, padding: '2px 0' }}
            data-export={e.format}
          >
            <span>{FORMAT_LABEL[e.format]}</span>
            <span title={e.detail || undefined}>
              <span style={{ color: STATE_TONE[e.state] }} data-state={e.state}>
                {STATE_LABEL[e.state]}
              </span>
              {e.format === 'step' && e.schema ? ` · ${e.schema}` : ''}
              {e.format === 'step' && e.round_trip ? ` · re-import ${e.round_trip}` : ''}
            </span>
          </li>
        ))}
      </ul>
      <div className="flex gap-1.5" style={{ marginTop: 6 }}>
        {step && (step.state === 'READY' || step.state === 'STALE') && step.url ? (
          <a className="text-xs" href={stepFileHref(node.id)} download>
            Download STEP{step.state === 'STALE' ? ' (older revision)' : ''}
          </a>
        ) : (
          <button
            type="button"
            className="text-xs"
            disabled={!data.can_export_step || stepBusy}
            title={data.can_export_step ? undefined : 'A STEP is exported only from validated geometry'}
            onClick={() => exportStep.mutate(false)}
          >
            {stepBusy ? 'STEP queued…' : 'Export STEP'}
          </button>
        )}
        {canValidate && (
          <button
            type="button"
            className="text-xs"
            disabled={exactBuild.isPending}
            onClick={() =>
              exactBuild.mutate({
                itemKey: data.item_key as string,
                projectId: (projectId ?? node.projectId) as string,
                baseRevision: data.revision,
              })
            }
          >
            {exactBuild.isPending ? 'Checking…' : 'Validate geometry'}
          </button>
        )}
      </div>
    </section>
  );
}
