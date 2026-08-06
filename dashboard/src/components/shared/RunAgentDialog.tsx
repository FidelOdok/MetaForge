import { useEffect, useState } from 'react';
import { Button } from '../ui/Button';
import { StatusBadge } from './StatusBadge';
import { useSubmitRequest, useRunStatus } from '../../hooks/use-assistant';
import { useActiveProject } from '../../hooks/use-active-project';

const ACTIONS = [
  { value: 'validate_stress', label: 'Validate Stress' },
  { value: 'generate_mesh', label: 'Generate Mesh' },
  { value: 'check_tolerances', label: 'Check Tolerances' },
  { value: 'run_erc', label: 'Run ERC' },
  { value: 'run_drc', label: 'Run DRC' },
  { value: 'full_validation', label: 'Full Validation' },
];

interface RunAgentDialogProps {
  onClose: () => void;
}

export function RunAgentDialog({ onClose }: RunAgentDialogProps) {
  const [action, setAction] = useState(ACTIONS[0]!.value);
  const [targetId, setTargetId] = useState('');
  const [runId, setRunId] = useState<string | undefined>();
  const { activeProjectId, projects } = useActiveProject();
  // Pre-fill from the Topbar's active-project context (Context UI), but
  // keep it a local, overridable field — the same pattern as the Design
  // Assistant's project field, since this needs one specific project.
  const [projectId, setProjectId] = useState(activeProjectId ?? '');
  const selectedProject = projects.find((p) => p.id === projectId);
  const workProducts = selectedProject?.work_products ?? [];
  const submit = useSubmitRequest();
  const { data: runStatus } = useRunStatus(runId);

  // Lock body scroll and support Escape-to-close while the dialog is open,
  // so it actually behaves like a blocking modal.
  useEffect(() => {
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.body.style.overflow = prevOverflow;
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [onClose]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!targetId.trim() || !projectId) return;

    submit.mutate(
      { action, target_id: targetId.trim(), project_id: projectId },
      {
        onSuccess: (resp) => {
          const id = resp.result['run_id'];
          if (typeof id === 'string') {
            setRunId(id);
          }
        },
      },
    );
  }

  const isDone = runStatus?.status === 'completed' || runStatus?.status === 'failed';

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="run-agent-dialog-title"
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md rounded-lg border border-zinc-200 bg-white p-6 shadow-xl dark:border-zinc-700 dark:bg-zinc-900"
      >
        <div className="mb-4 flex items-center justify-between">
          <h3 id="run-agent-dialog-title" className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">
            Run Agent
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
          >
            &times;
          </button>
        </div>

        {!runId ? (
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="dialog-project"
                className="mb-1 block text-sm font-medium text-zinc-700 dark:text-zinc-300"
              >
                Project
              </label>
              <select
                id="dialog-project"
                value={projectId}
                onChange={(e) => { setProjectId(e.target.value); setTargetId(''); }}
                className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-200"
              >
                <option value="">Select a project...</option>
                {projects?.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>

            <div>
              <label
                htmlFor="action"
                className="mb-1 block text-sm font-medium text-zinc-700 dark:text-zinc-300"
              >
                Action
              </label>
              <select
                id="action"
                value={action}
                onChange={(e) => setAction(e.target.value)}
                className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-200"
              >
                {ACTIONS.map((a) => (
                  <option key={a.value} value={a.value}>
                    {a.label}
                  </option>
                ))}
              </select>
            </div>

            <div>
              <label
                htmlFor="target"
                className="mb-1 block text-sm font-medium text-zinc-700 dark:text-zinc-300"
              >
                Target work product
              </label>
              <select
                id="target"
                value={targetId}
                onChange={(e) => setTargetId(e.target.value)}
                disabled={!projectId}
                className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm disabled:opacity-50 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-200"
              >
                <option value="">
                  {!projectId
                    ? 'Select a project first...'
                    : workProducts.length === 0
                      ? 'No work products in this project'
                      : 'Select a work product...'}
                </option>
                {workProducts.map((wp) => (
                  <option key={wp.id} value={wp.id}>
                    {wp.name} ({wp.type.replace(/_/g, ' ')})
                  </option>
                ))}
              </select>
            </div>

            <div className="flex justify-end gap-2">
              <Button variant="secondary" size="sm" type="button" onClick={onClose}>
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                type="submit"
                disabled={!targetId.trim() || !projectId || submit.isPending}
              >
                {submit.isPending ? 'Submitting...' : 'Submit'}
              </Button>
            </div>
          </form>
        ) : (
          <div className="space-y-4">
            <div className="flex items-center gap-2">
              <span className="text-sm text-zinc-600 dark:text-zinc-400">Status:</span>
              <StatusBadge status={runStatus?.status ?? 'pending'} />
            </div>

            {runStatus?.steps && Object.keys(runStatus.steps).length > 0 && (
              <div className="space-y-2">
                <div className="text-sm font-medium text-zinc-700 dark:text-zinc-300">Steps</div>
                {Object.entries(runStatus.steps).map(([stepId, step]) => (
                  <div
                    key={stepId}
                    className="flex items-center justify-between rounded-md border border-zinc-200 px-3 py-2 dark:border-zinc-700"
                  >
                    <div className="text-sm text-zinc-800 dark:text-zinc-200">
                      <span className="font-medium">{step.agent_code}</span>{' '}
                      <span className="text-zinc-500">{step.task_type}</span>
                    </div>
                    <StatusBadge status={step.status} />
                  </div>
                ))}
              </div>
            )}

            <div className="flex justify-end">
              <Button variant="secondary" size="sm" onClick={onClose}>
                {isDone ? 'Close' : 'Running...'}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
