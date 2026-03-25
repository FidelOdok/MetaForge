import { useState, useRef } from 'react';
import { useSubmitRequest, useRunStatus, useProposals, useDecideProposal } from '../hooks/use-assistant';
import { useProjects } from '../hooks/use-projects';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { StatusBadge } from '../components/shared/StatusBadge';
import { SkeletonCard } from '../components/ui/Skeleton';
import { useToast } from '../components/ui/Toast';
import { formatRelativeTime } from '../utils/format-time';
import type { RunStatusResponse, Proposal } from '../api/endpoints/assistant';

// ── Constants ────────────────────────────────────────────────────────────────

const ACTIONS = [
  { value: 'validate_stress', label: 'Validate Stress', needsTarget: true },
  { value: 'generate_mesh', label: 'Generate Mesh', needsTarget: true },
  { value: 'check_tolerances', label: 'Check Tolerances', needsTarget: true },
  { value: 'generate_cad', label: 'Generate CAD', needsTarget: false },
  { value: 'generate_cad_script', label: 'Generate CAD Script (LLM)', needsTarget: false },
  { value: 'run_erc', label: 'Run ERC', needsTarget: true },
  { value: 'run_drc', label: 'Run DRC', needsTarget: true },
  { value: 'full_validation', label: 'Full Validation', needsTarget: true },
] as const;

const AGENT_OPTIONS = [
  { value: 'any', label: 'Any Agent', prefix: null },
  { value: 'ME', label: 'ME — Mechanical', prefix: '[@ME]' },
  { value: 'EE', label: 'EE — Electronics', prefix: '[@EE]' },
  { value: 'FW', label: 'FW — Firmware', prefix: '[@FW]' },
  { value: 'SIM', label: 'SIM — Simulation', prefix: '[@SIM]' },
  { value: 'SC', label: 'SC — Supply Chain', prefix: '[@SC]' },
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

// Domain keyword → badge color mapping
const DOMAIN_COLORS: Record<string, string> = {
  mechanical: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400',
  electronics: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  firmware: 'bg-purple-100 text-purple-800 dark:bg-purple-900/30 dark:text-purple-400',
  simulation: 'bg-teal-100 text-teal-800 dark:bg-teal-900/30 dark:text-teal-400',
  supply_chain: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  bom: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400',
  schematic: 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-400',
  pcb: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
  cad: 'bg-orange-100 text-orange-800 dark:bg-orange-900/30 dark:text-orange-400',
  gerber: 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400',
};

function getDomainColor(domain: string): string {
  const lower = domain.toLowerCase();
  for (const [key, cls] of Object.entries(DOMAIN_COLORS)) {
    if (lower.includes(key)) return cls;
  }
  return 'bg-zinc-100 text-zinc-800 dark:bg-zinc-700 dark:text-zinc-200';
}

function deriveConfidence(proposal: Proposal): { label: string; variant: 'success' | 'warning' | 'error' } {
  // Heuristic: fewer affected work products + smaller diff → higher confidence
  const diffKeys = Object.keys(proposal.diff ?? {}).length;
  const affected = (proposal.work_products_affected ?? []).length;
  const score = diffKeys + affected;
  if (score <= 2) return { label: 'High', variant: 'success' };
  if (score <= 5) return { label: 'Medium', variant: 'warning' };
  return { label: 'Low', variant: 'error' };
}

function exportProposalAsMarkdown(proposal: Proposal): void {
  const confidence = deriveConfidence(proposal);
  const affected = (proposal.work_products_affected ?? []).join(', ') || 'None';
  const decision = proposal.status === 'approved'
    ? 'Accepted'
    : proposal.status === 'rejected'
      ? 'Rejected'
      : 'Pending';

  const md = [
    `# Proposal: ${proposal.description.slice(0, 80)}`,
    '',
    `**Agent**: ${proposal.agent_code}`,
    `**Confidence**: ${confidence.label}`,
    `**Status**: ${decision}`,
    `**Created**: ${proposal.created_at}`,
    proposal.decided_at ? `**Decided**: ${proposal.decided_at}` : '',
    '',
    '## Description',
    '',
    proposal.description,
    '',
    '## Affected Work Products',
    '',
    affected,
    '',
    proposal.decision_reason ? `## Decision Reason\n\n${proposal.decision_reason}` : '',
  ]
    .filter((line) => line !== undefined)
    .join('\n');

  const blob = new Blob([md], { type: 'text/markdown' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `proposal-${proposal.change_id.slice(0, 8)}.md`;
  a.click();
  URL.revokeObjectURL(url);
}

// ── ProposalCard ─────────────────────────────────────────────────────────────

interface ProposalCardProps {
  proposal: Proposal;
  onAskMore: (title: string) => void;
}

function ProposalCard({ proposal, onAskMore }: ProposalCardProps) {
  const toast = useToast();
  const decide = useDecideProposal();
  const confidence = deriveConfidence(proposal);
  const title = proposal.description.slice(0, 80) + (proposal.description.length > 80 ? '…' : '');
  const isPending = proposal.status === 'pending';

  function handleDecide(decision: 'approve' | 'reject') {
    decide.mutate(
      {
        changeId: proposal.change_id,
        decision,
        reason: decision === 'approve' ? 'Accepted via dashboard' : 'Rejected via dashboard',
        reviewer: 'dashboard-user',
      },
      {
        onSuccess: () => {
          toast.success(decision === 'approve' ? 'Proposal accepted.' : 'Proposal rejected.');
        },
        onError: (err) => {
          toast.error((err as Error)?.message ?? 'Failed to decide proposal.');
        },
      },
    );
  }

  return (
    <Card className="space-y-3">
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{title}</p>
          <p className="mt-0.5 text-xs text-zinc-500">
            {proposal.agent_code} · {formatRelativeTime(proposal.created_at)}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge variant={confidence.variant}>{confidence.label} confidence</Badge>
          <StatusBadge status={proposal.status} />
        </div>
      </div>

      {/* Affected work products */}
      {proposal.work_products_affected && proposal.work_products_affected.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          <span className="text-xs text-zinc-500">Affects:</span>
          {proposal.work_products_affected.map((wp) => (
            <span
              key={wp}
              className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${getDomainColor(wp)}`}
            >
              {wp}
            </span>
          ))}
        </div>
      )}

      {/* Action buttons */}
      <div className="flex flex-wrap items-center gap-2 border-t border-zinc-100 pt-3 dark:border-zinc-700">
        {isPending && (
          <>
            <Button
              size="sm"
              variant="primary"
              onClick={() => handleDecide('approve')}
              disabled={decide.isPending}
            >
              Accept
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => handleDecide('reject')}
              disabled={decide.isPending}
            >
              Reject
            </Button>
          </>
        )}
        <Button
          size="sm"
          variant="ghost"
          onClick={() => onAskMore(proposal.description.slice(0, 80))}
        >
          Ask more
        </Button>
        <Button
          size="sm"
          variant="secondary"
          onClick={() => exportProposalAsMarkdown(proposal)}
        >
          Export ↓
        </Button>
      </div>
    </Card>
  );
}

// ── StepTimeline ─────────────────────────────────────────────────────────────

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

// ── ResultSection ─────────────────────────────────────────────────────────────

function ResultSection({ data }: { data: RunStatusResponse }) {
  if (data.status !== 'completed') return null;

  // Collect work_product URLs from step results (check both top-level and skill_results)
  const work_products: { name: string; url: string }[] = [];
  for (const [, step] of Object.entries(data.steps as Record<string, StepInfo>)) {
    const result = step.result ?? {};
    // Check top-level result keys
    const sources: Record<string, unknown>[] = [result];
    // Also check nested skill_results array (TaskResult.skill_results)
    if (Array.isArray(result.skill_results)) {
      for (const sr of result.skill_results) {
        if (sr && typeof sr === 'object') {
          sources.push(sr as Record<string, unknown>);
        }
      }
    }
    for (const src of sources) {
      if (typeof src.deliverable_url === 'string') {
        work_products.push({
          name: (src.deliverable_name as string) ?? 'Download deliverable',
          url: src.deliverable_url,
        });
      }
      if (typeof src.download_url === 'string') {
        work_products.push({
          name: (src.file_name as string) ?? 'Download file',
          url: src.download_url,
        });
      }
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

      {/* CAD generation details */}
      {(() => {
        const cadResults: Record<string, unknown>[] = [];
        for (const [, step] of Object.entries(data.steps as Record<string, StepInfo>)) {
          const r = step.result ?? {};
          if (Array.isArray(r.skill_results)) {
            for (const sr of r.skill_results) {
              if (sr && typeof sr === 'object' && (sr as Record<string, unknown>).script_text) {
                cadResults.push(sr as Record<string, unknown>);
              }
            }
          }
        }
        if (cadResults.length === 0) return null;
        return cadResults.map((sr, idx) => (
          <div key={idx} className="space-y-2 border-t border-zinc-200 pt-3 dark:border-zinc-700">
            <h4 className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
              CAD Generation Details
            </h4>
            <div className="grid grid-cols-3 gap-3 text-sm">
              <div className="rounded bg-zinc-50 p-2 dark:bg-zinc-800">
                <span className="text-zinc-500 dark:text-zinc-400">Volume</span>
                <p className="font-mono font-medium text-zinc-900 dark:text-zinc-100">
                  {typeof sr.volume_mm3 === 'number' ? `${sr.volume_mm3.toLocaleString()} mm\u00B3` : 'N/A'}
                </p>
              </div>
              <div className="rounded bg-zinc-50 p-2 dark:bg-zinc-800">
                <span className="text-zinc-500 dark:text-zinc-400">Surface Area</span>
                <p className="font-mono font-medium text-zinc-900 dark:text-zinc-100">
                  {typeof sr.surface_area_mm2 === 'number' ? `${sr.surface_area_mm2.toLocaleString()} mm\u00B2` : 'N/A'}
                </p>
              </div>
              <div className="rounded bg-zinc-50 p-2 dark:bg-zinc-800">
                <span className="text-zinc-500 dark:text-zinc-400">Output</span>
                <p className="truncate font-mono font-medium text-zinc-900 dark:text-zinc-100">
                  {(sr.cad_file as string) ?? 'N/A'}
                </p>
              </div>
            </div>
            <details className="group">
              <summary className="cursor-pointer text-sm font-medium text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300">
                View CadQuery Script
              </summary>
              <pre className="mt-2 max-h-80 overflow-auto rounded-md bg-zinc-900 p-3 text-xs text-green-400">
                <code>{sr.script_text as string}</code>
              </pre>
            </details>
          </div>
        ));
      })()}
    </Card>
  );
}

// ── RequirementsPanel ─────────────────────────────────────────────────────────

interface RequirementsPanelProps {
  proposal: Proposal | null;
  onClose: () => void;
}

function RequirementsPanel({ proposal, onClose }: RequirementsPanelProps) {
  // The Proposal type doesn't have a requirements field yet; gracefully handle
  const requirements = (proposal as (Proposal & { requirements?: string[] }) | null)?.requirements;

  return (
    <div className="flex h-full flex-col rounded-lg border border-zinc-200 bg-white dark:border-zinc-700 dark:bg-zinc-800">
      <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3 dark:border-zinc-700">
        <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
          Requirement Traceability
        </h3>
        <button
          type="button"
          aria-label="Close requirements panel"
          onClick={onClose}
          className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
        >
          ✕
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-4">
        {!proposal ? (
          <p className="text-sm text-zinc-500">Select a proposal to see linked requirements.</p>
        ) : requirements && requirements.length > 0 ? (
          <ul className="space-y-2">
            {requirements.map((req) => (
              <li
                key={req}
                className="rounded-md border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
              >
                {req}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-zinc-500">No requirements linked to this proposal.</p>
        )}
      </div>
    </div>
  );
}

// ── DesignAssistantPage ───────────────────────────────────────────────────────

export function DesignAssistantPage() {
  const [prompt, setPrompt] = useState('');
  const [action, setAction] = useState<string>(ACTIONS[0].value);
  const [projectId, setProjectId] = useState<string>('');
  const [targetId, setTargetId] = useState<string>('');
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const [selectedAgent, setSelectedAgent] = useState<string>('any');
  const [showHistory, setShowHistory] = useState(false);
  const [showRequirementsPanel, setShowRequirementsPanel] = useState(false);
  const [tracedProposal, setTracedProposal] = useState<Proposal | null>(null);
  const promptInputRef = useRef<HTMLInputElement>(null);

  const { data: projects } = useProjects();
  const submitRequest = useSubmitRequest();
  const { data: runStatus } = useRunStatus(runId);
  const { data: proposalsData, isLoading: proposalsLoading } = useProposals();
  const toast = useToast();

  const isRunning =
    runStatus?.status === 'running' || runStatus?.status === 'pending';

  const selectedAction = ACTIONS.find((a) => a.value === action);
  const needsTarget = selectedAction?.needsTarget ?? true;

  // Get work products for the selected project
  const selectedProject = projects?.find((p) => p.id === projectId);
  const workProducts = selectedProject?.work_products ?? [];

  // Split proposals into active (pending) and history (decided)
  const allProposals = proposalsData?.proposals ?? [];
  const activeProposals = allProposals.filter((p) => p.status === 'pending');
  const historyProposals = allProposals.filter((p) => p.status !== 'pending');

  function handleAskMore(title: string) {
    setPrompt(`Tell me more about: ${title}`);
    setTimeout(() => promptInputRef.current?.focus(), 50);
  }

  function buildPrefixedPrompt(raw: string): string {
    const agentOpt = AGENT_OPTIONS.find((a) => a.value === selectedAgent);
    if (agentOpt && agentOpt.prefix) {
      return `${agentOpt.prefix} ${raw}`;
    }
    return raw;
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!projectId) return;
    if (needsTarget && !targetId) return;
    if (!needsTarget && !prompt.trim()) return;

    const finalPrompt = buildPrefixedPrompt(prompt.trim());

    submitRequest.mutate(
      {
        action,
        target_id: needsTarget ? targetId : undefined,
        project_id: projectId,
        prompt: finalPrompt || undefined,
        parameters: finalPrompt ? { prompt: finalPrompt } : {},
      },
      {
        onSuccess: (response) => {
          const id =
            (response.result?.run_id as string) ??
            response.request_id;
          setRunId(id);
          toast.info('Request submitted — tracking progress below.');
        },
        onError: (err) => {
          toast.error((err as Error)?.message ?? 'Failed to submit request.');
        },
      },
    );
  }

  function handleReset() {
    setPrompt('');
    setTargetId('');
    setRunId(undefined);
    submitRequest.reset();
  }

  return (
    <div className="flex gap-6">
      {/* Main content column */}
      <div className="min-w-0 flex-1">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
              Design Assistant
            </h2>
            <p className="mt-1 text-sm text-zinc-500">
              Submit a request to an agent, track progress in real-time, and
              download results.
            </p>
          </div>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => setShowRequirementsPanel((v) => !v)}
          >
            {showRequirementsPanel ? 'Hide Traceability' : 'Show Traceability'}
          </Button>
        </div>

        {/* --- Prompt form --- */}
        <Card className="mb-6 space-y-4">
          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label
                htmlFor="project-select"
                className="mb-1 block text-sm font-medium text-zinc-700 dark:text-zinc-300"
              >
                Project
              </label>
              <select
                id="project-select"
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                disabled={!!runId}
                className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100"
              >
                <option value="">Select a project...</option>
                {projects?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>

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

            {needsTarget && (
              <div>
                <label
                  htmlFor="target-select"
                  className="mb-1 block text-sm font-medium text-zinc-700 dark:text-zinc-300"
                >
                  Target work product
                </label>
                <select
                  id="target-select"
                  value={targetId}
                  onChange={(e) => setTargetId(e.target.value)}
                  disabled={!!runId || !projectId}
                  className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100"
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
            )}

            {/* Agent selector */}
            <div>
              <label
                htmlFor="agent-select"
                className="mb-1 block text-sm font-medium text-zinc-700 dark:text-zinc-300"
              >
                Direct to agent
              </label>
              <select
                id="agent-select"
                value={selectedAgent}
                onChange={(e) => setSelectedAgent(e.target.value)}
                disabled={!!runId}
                className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 shadow-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100"
              >
                {AGENT_OPTIONS.map((a) => (
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
                {needsTarget ? 'Additional instructions (optional)' : 'Description / prompt'}
              </label>
              <input
                id="prompt-input"
                ref={promptInputRef}
                type="text"
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
                disabled={!!runId}
                placeholder={
                  needsTarget
                    ? 'e.g. focus on thermal stress at mounting points'
                    : 'e.g. simple bracket with two mounting holes'
                }
                className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 shadow-sm placeholder:text-zinc-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:opacity-50 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100 dark:placeholder:text-zinc-500"
              />
              {selectedAgent !== 'any' && prompt.trim() && (
                <p className="mt-1 text-xs text-zinc-400">
                  Will be sent as:{' '}
                  <span className="font-mono text-zinc-600 dark:text-zinc-300">
                    {buildPrefixedPrompt(prompt.trim())}
                  </span>
                </p>
              )}
            </div>

            <div className="flex gap-2">
              <Button
                type="submit"
                variant="primary"
                disabled={
                  (!needsTarget && !prompt.trim()) ||
                  (needsTarget && !targetId) ||
                  !projectId ||
                  submitRequest.isPending ||
                  !!runId
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

        {/* --- Active proposals --- */}
        <section className="mb-6">
          <h3 className="mb-3 text-base font-semibold text-zinc-900 dark:text-zinc-100">
            Active Proposals
            {activeProposals.length > 0 && (
              <span className="ml-2 text-sm font-normal text-zinc-500">
                ({activeProposals.length})
              </span>
            )}
          </h3>

          {proposalsLoading ? (
            <div className="space-y-4">
              <SkeletonCard />
              <SkeletonCard />
            </div>
          ) : activeProposals.length === 0 ? (
            <p className="text-sm text-zinc-500">No pending proposals.</p>
          ) : (
            <div className="space-y-4">
              {activeProposals.map((proposal) => (
                <div
                  key={proposal.change_id}
                  onClick={() => {
                    setTracedProposal(proposal);
                    setShowRequirementsPanel(true);
                  }}
                  className="cursor-pointer"
                >
                  <ProposalCard proposal={proposal} onAskMore={handleAskMore} />
                </div>
              ))}
            </div>
          )}
        </section>

        {/* --- Progress section --- */}
        {runId && runStatus && (
          <div className="mb-6 space-y-6">
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
          <Card className="mb-6">
            <p className="text-sm text-zinc-500">
              Waiting for run status...
            </p>
          </Card>
        )}

        {/* --- Proposal history --- */}
        <section>
          <button
            type="button"
            onClick={() => setShowHistory((v) => !v)}
            className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-700 hover:text-zinc-900 dark:text-zinc-300 dark:hover:text-zinc-100"
          >
            <span>{showHistory ? '▾' : '▸'}</span>
            Previous Proposals
            {historyProposals.length > 0 && (
              <span className="font-normal text-zinc-500">
                ({historyProposals.length})
              </span>
            )}
          </button>

          {showHistory && (
            <div className="space-y-3">
              {historyProposals.length === 0 ? (
                <p className="text-sm text-zinc-500">No previous proposals.</p>
              ) : (
                historyProposals.map((proposal) => (
                  <Card key={proposal.change_id} className="space-y-2 opacity-80">
                    <div className="flex items-start justify-between gap-3">
                      <p className="min-w-0 flex-1 truncate text-sm text-zinc-700 dark:text-zinc-300">
                        {proposal.description.slice(0, 80)}
                        {proposal.description.length > 80 ? '…' : ''}
                      </p>
                      <div className="flex shrink-0 items-center gap-2">
                        <StatusBadge status={proposal.status} />
                        <span className="text-xs text-zinc-400">
                          {proposal.decided_at
                            ? formatRelativeTime(proposal.decided_at)
                            : formatRelativeTime(proposal.created_at)}
                        </span>
                      </div>
                    </div>
                    {proposal.work_products_affected && proposal.work_products_affected.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {proposal.work_products_affected.map((wp) => (
                          <span
                            key={wp}
                            className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${getDomainColor(wp)}`}
                          >
                            {wp}
                          </span>
                        ))}
                      </div>
                    )}
                    <div className="flex items-center gap-2 pt-1">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => exportProposalAsMarkdown(proposal)}
                      >
                        Export ↓
                      </Button>
                    </div>
                  </Card>
                ))
              )}
            </div>
          )}
        </section>
      </div>

      {/* Requirements traceability side panel */}
      {showRequirementsPanel && (
        <div className="w-72 shrink-0">
          <RequirementsPanel
            proposal={tracedProposal}
            onClose={() => setShowRequirementsPanel(false)}
          />
        </div>
      )}
    </div>
  );
}
