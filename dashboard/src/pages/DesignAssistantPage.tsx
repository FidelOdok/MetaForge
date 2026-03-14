import { useState } from 'react';
import { useSubmitRequest, useRunStatus } from '../hooks/use-assistant';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { StatusBadge } from '../components/shared/StatusBadge';
import { formatRelativeTime } from '../utils/format-time';
import type { RunStatusResponse } from '../api/endpoints/assistant';

const ACTIONS = [
  { value: 'validate_stress', label: 'Validate Stress' },
  { value: 'generate_mesh', label: 'Generate Mesh' },
  { value: 'check_tolerances', label: 'Check Tolerances' },
  { value: 'generate_cad', label: 'Generate CAD' },
  { value: 'run_erc', label: 'Run ERC' },
  { value: 'run_drc', label: 'Run DRC' },
  { value: 'full_validation', label: 'Full Validation' },
] as const;

const EVENT_ICONS: Record<string, string> = {
  agent_started: '▶',
  agent_completed: '✓',
  skill_started: '◇',
  skill_completed: '◆',
  change_proposed: '◈',
  twin_updated: '↻',
  task_started: '▶',
  task_completed: '✓',
  task_failed: '✗',
};

const EVENT_COLORS: Record<string, string> = {
  agent_started: 'text-blue-500',
  agent_completed: 'text-green-500',
  skill_started: 'text-indigo-400',
  skill_completed: 'text-indigo-600',
  change_proposed: 'text-amber-500',
  twin_updated: 'text-teal-500',
  task_started: 'text-blue-500',
  task_completed: 'text-green-500',
  task_failed: 'text-red-500',
};

interface StepInfo {
  status: string;
  agent_code: string;
  task_type: string;
  result: Record<string, unknown>;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
}

function StepTimeline({ steps }: { steps: Record<string, StepInfo> }) {
  const entries = Object.entries(steps);

  if (entries.length === 0) {
    return (
      <p className="text-sm text-zinc-500">No steps recorded yet.</p>
    );
  }

  return (
    <div className="relative space-y-0 border-l-2 border-zinc-200 pl-6 dark:border-zinc-700">
      {entries.map(([stepId, step]) => {
        const eventType =
          step.status === 'completed'
            ? 'task_completed'
            : step.status === 'failed'
              ? 'task_failed'
              : 'task_started';
        return (
          <div key={stepId} className="relative pb-6 last:pb-0">
            <span
              className={`absolute -left-[1.625rem] flex h-5 w-5 items-center justify-center rounded-full bg-white text-xs dark:bg-zinc-900 ${EVENT_COLORS[eventType] ?? 'text-zinc-400'}`}
            >
              {EVENT_ICONS[eventType] ?? '?'}
            </span>
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                {step.agent_code} — {step.task_type.replace(/_/g, ' ')}
              </span>
              <StatusBadge status={step.status} />
            </div>
            {step.error && (
              <p className="mt-1 text-sm text-red-600 dark:text-red-400">
                {step.error}
              </p>
            )}
            {step.started_at && (
              <div className="text-xs text-zinc-400">
                Started {formatRelativeTime(step.started_at)}
                {step.completed_at &&
                  ` · Completed ${formatRelativeTime(step.completed_at)}`}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function ResultSection({ data }: { data: RunStatusResponse }) {
  if (data.status !== 'completed') return null;

  // Collect work_product URLs from step results
  const work_products: { name: string; url: string }[] = [];
  for (const [, step] of Object.entries(data.steps as Record<string, StepInfo>)) {
    const result = step.result ?? {};
    if (typeof result.deliverable_url === 'string') {
      work_products.push({
        name: (result.deliverable_name as string) ?? 'Download deliverable',
        url: result.deliverable_url,
      });
    }
    if (typeof result.download_url === 'string') {
      work_products.push({
        name: (result.file_name as string) ?? 'Download file',
        url: result.download_url,
      });
    }
  }

  return (
    <Card className="space-y-3">
      <h3 className="text-lg font-medium text-zinc-900 dark:text-zinc-100">
        Results
      </h3>
      <div className="flex items-center gap-2">
        <StatusBadge status="completed" />
        <span className="text-sm text-zinc-600 dark:text-zinc-300">
          Run completed
          {data.completed_at && ` ${formatRelativeTime(data.completed_at)}`}
        </span>
      </div>

      {work_products.length > 0 ? (
        <div className="space-y-2">
          {work_products.map((work_product, idx) => (
            <a
              key={idx}
              href={work_product.url}
              download
              className="inline-flex items-center gap-2 rounded-md border border-blue-200 bg-blue-50 px-3 py-2 text-sm font-medium text-blue-700 hover:bg-blue-100 dark:border-blue-800 dark:bg-blue-900/20 dark:text-blue-400 dark:hover:bg-blue-900/40"
            >
              ↓ {work_product.name}
            </a>
          ))}
        </div>
      ) : (
        <p className="text-sm text-zinc-500">
          No downloadable work_products were produced by this run.
        </p>
      )}
    </Card>
  );
}

export function DesignAssistantPage() {
  const [prompt, setPrompt] = useState('');
  const [action, setAction] = useState<string>(ACTIONS[0].value);
  const [runId, setRunId] = useState<string | undefined>(undefined);

  const submitRequest = useSubmitRequest();
  const { data: runStatus } = useRunStatus(runId);

  const isRunning =
    runStatus?.status === 'running' || runStatus?.status === 'pending';

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!prompt.trim()) return;

    submitRequest.mutate(
      {
        action,
        target_id: prompt.trim(),
        parameters: { prompt: prompt.trim() },
      },
      {
        onSuccess: (response) => {
          const id =
            (response.result?.run_id as string) ??
            response.request_id;
          setRunId(id);
        },
      },
    );
  }

  function handleReset() {
    setPrompt('');
    setRunId(undefined);
    submitRequest.reset();
  }

  return (
    <div>
      <div className="mb-6">
        <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
          Design Assistant
        </h2>
        <p className="mt-1 text-sm text-zinc-500">
          Submit a request to an agent, track progress in real-time, and
          download results.
        </p>
      </div>

      {/* --- Prompt form --- */}
      <Card className="mb-6 space-y-4">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label
              htmlFor="action-select"
              className="mb-1 block text-sm font-medium text-zinc-700 dark:text-zinc-300"
            >
              Action
            </label>
            <select
              id="action-select"
              value={action}
              onChange={(e) => setAction(e.target.value)}
              disabled={!!runId}
              className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100"
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
              htmlFor="prompt-input"
              className="mb-1 block text-sm font-medium text-zinc-700 dark:text-zinc-300"
            >
              Target work_product ID or description
            </label>
            <input
              id="prompt-input"
              type="text"
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              disabled={!!runId}
              placeholder="e.g. bracket-assembly-v2 or a UUID"
              className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 shadow-sm placeholder:text-zinc-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100 dark:placeholder:text-zinc-500"
            />
          </div>

          <div className="flex gap-2">
            <Button
              type="submit"
              variant="primary"
              disabled={
                !prompt.trim() || submitRequest.isPending || !!runId
              }
            >
              {submitRequest.isPending ? 'Submitting...' : 'Submit request'}
            </Button>
            {runId && (
              <Button
                type="button"
                variant="secondary"
                onClick={handleReset}
                disabled={isRunning}
              >
                New request
              </Button>
            )}
          </div>
        </form>
      </Card>

      {/* --- Submission error --- */}
      {submitRequest.isError && (
        <Card className="mb-6 border-red-300 dark:border-red-700">
          <p className="text-sm font-medium text-red-600 dark:text-red-400">
            Request failed
          </p>
          <p className="mt-1 text-sm text-red-500">
            {(submitRequest.error as Error)?.message ?? 'Unknown error'}
          </p>
        </Card>
      )}

      {/* --- Progress section --- */}
      {runId && runStatus && (
        <div className="space-y-6">
          <Card>
            <div className="mb-4 flex items-center justify-between">
              <h3 className="text-lg font-medium text-zinc-900 dark:text-zinc-100">
                Progress
              </h3>
              <StatusBadge status={runStatus.status} />
            </div>

            <div className="mb-4 grid gap-4 sm:grid-cols-2">
              <div>
                <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                  {runStatus.run_id}
                </div>
                <div className="text-xs text-zinc-500">Run ID</div>
              </div>
              <div>
                <div className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                  {runStatus.completed_at
                    ? formatRelativeTime(runStatus.completed_at)
                    : isRunning
                      ? 'In progress...'
                      : '--'}
                </div>
                <div className="text-xs text-zinc-500">Completed</div>
              </div>
            </div>

            <h4 className="mb-3 text-sm font-medium text-zinc-700 dark:text-zinc-300">
              Steps
            </h4>
            <StepTimeline
              steps={runStatus.steps as Record<string, StepInfo>}
            />
          </Card>

          {/* --- Error display for failed runs --- */}
          {runStatus.status === 'failed' && (
            <Card className="border-red-300 dark:border-red-700">
              <p className="text-sm font-medium text-red-600 dark:text-red-400">
                Run failed
              </p>
              {Object.entries(
                runStatus.steps as Record<string, StepInfo>,
              ).map(
                ([stepId, step]) =>
                  step.error && (
                    <p
                      key={stepId}
                      className="mt-1 text-sm text-red-500"
                    >
                      [{step.agent_code}] {step.error}
                    </p>
                  ),
              )}
            </Card>
          )}

          {/* --- Result / download section --- */}
          <ResultSection data={runStatus} />
        </div>
      )}

      {/* --- Loading state while waiting for first status poll --- */}
      {runId && !runStatus && (
        <Card>
          <p className="text-sm text-zinc-500">
            Waiting for run status...
          </p>
        </Card>
      )}
    </div>
  );
}
