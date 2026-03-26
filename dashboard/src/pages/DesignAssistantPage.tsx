import { useState } from 'react';
import { useSubmitRequest, useRunStatus } from '../hooks/use-assistant';
import { useProjects } from '../hooks/use-projects';
import { Button } from '../components/ui/Button';
import { StatusBadge } from '../components/shared/StatusBadge';
import { formatRelativeTime } from '../utils/format-time';
import type { RunStatusResponse } from '../api/endpoints/assistant';

// ---------------------------------------------------------------------------
// KC color tokens (as inline style values)
// ---------------------------------------------------------------------------
// surface         #111319
// surface-low     #191b22
// surface-high    #282a30
// surface-lowest  #0c0e14
// on-surface      #e2e2eb
// on-surface-variant #9a9aaa
// primary         #ffb783
// primary-container #e67e22
// error           #ffb4ab
// success         #3dd68c

const KC = {
  surface: '#111319',
  surfaceLow: '#191b22',
  surfaceHigh: '#282a30',
  surfaceLowest: '#0c0e14',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  primary: '#ffb783',
  primaryContainer: '#e67e22',
  border: 'rgba(65,72,90,0.2)',
  borderStrong: 'rgba(65,72,90,0.3)',
  error: '#ffb4ab',
  errorBg: 'rgba(255,180,171,0.1)',
  success: '#3dd68c',
};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

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
  agent_started: KC.primary,
  agent_completed: KC.success,
  skill_started: KC.primary,
  skill_completed: KC.primaryContainer,
  change_proposed: KC.primary,
  twin_updated: KC.success,
  task_started: KC.primary,
  task_completed: KC.success,
  task_failed: KC.error,
};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface StepInfo {
  status: string;
  agent_code: string;
  task_type: string;
  result: Record<string, unknown>;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function GlassPanel({
  children,
  className,
  style,
}: {
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}) {
  return (
    <div
      className={className}
      style={{
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        background: 'rgba(30,31,38,0.85)',
        border: `1px solid ${KC.border}`,
        borderRadius: '6px',
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/** KC-styled select element */
function KCSelect({
  id,
  value,
  onChange,
  disabled,
  children,
}: {
  id?: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      style={{
        width: '100%',
        background: KC.surfaceHigh,
        border: `1px solid ${KC.borderStrong}`,
        borderRadius: '4px',
        color: KC.onSurface,
        padding: '6px 10px',
        fontSize: '13px',
        fontFamily: 'Inter, sans-serif',
        outline: 'none',
        opacity: disabled ? 0.5 : 1,
        cursor: disabled ? 'not-allowed' : 'default',
      }}
    >
      {children}
    </select>
  );
}

/** KC-styled text input */
function KCInput({
  id,
  value,
  onChange,
  disabled,
  placeholder,
  type = 'text',
}: {
  id?: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
  placeholder?: string;
  type?: string;
}) {
  return (
    <input
      id={id}
      type={type}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      disabled={disabled}
      placeholder={placeholder}
      style={{
        width: '100%',
        background: KC.surfaceHigh,
        border: `1px solid ${KC.borderStrong}`,
        borderRadius: '4px',
        color: KC.onSurface,
        padding: '6px 10px',
        fontSize: '13px',
        fontFamily: 'Inter, sans-serif',
        outline: 'none',
        opacity: disabled ? 0.5 : 1,
        cursor: disabled ? 'not-allowed' : 'default',
      }}
    />
  );
}

/** KC-styled label */
function KCLabel({ htmlFor, children }: { htmlFor?: string; children: React.ReactNode }) {
  return (
    <label
      htmlFor={htmlFor}
      style={{
        display: 'block',
        marginBottom: '4px',
        fontSize: '11px',
        fontWeight: 500,
        color: KC.onSurfaceVariant,
        letterSpacing: '0.04em',
        textTransform: 'uppercase',
        fontFamily: 'Inter, sans-serif',
      }}
    >
      {children}
    </label>
  );
}

function StepTimeline({ steps }: { steps: Record<string, StepInfo> }) {
  const entries = Object.entries(steps);

  if (entries.length === 0) {
    return (
      <p style={{ fontSize: '13px', color: KC.onSurfaceVariant }}>No steps recorded yet.</p>
    );
  }

  return (
    <div style={{ position: 'relative', paddingLeft: '20px', borderLeft: `2px solid ${KC.border}` }}>
      {entries.map(([stepId, step]) => {
        const eventType =
          step.status === 'completed'
            ? 'task_completed'
            : step.status === 'failed'
              ? 'task_failed'
              : 'task_started';
        const color = EVENT_COLORS[eventType] ?? KC.onSurfaceVariant;
        return (
          <div key={stepId} style={{ position: 'relative', paddingBottom: '16px' }}>
            <span
              style={{
                position: 'absolute',
                left: '-26px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                width: '20px',
                height: '20px',
                borderRadius: '50%',
                background: KC.surfaceHigh,
                fontSize: '10px',
                color,
              }}
            >
              {EVENT_ICONS[eventType] ?? '?'}
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              <span style={{ fontSize: '13px', fontWeight: 500, color: KC.onSurface }}>
                {step.agent_code} — {step.task_type.replace(/_/g, ' ')}
              </span>
              <StatusBadge status={step.status} />
            </div>
            {step.error && (
              <p style={{ marginTop: '4px', fontSize: '12px', color: KC.error }}>
                {step.error}
              </p>
            )}
            {step.started_at && (
              <div style={{ fontSize: '11px', color: KC.onSurfaceVariant }}>
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
    const sources: Record<string, unknown>[] = [result];
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
    <GlassPanel style={{ padding: '16px', marginTop: '12px' }}>
      <h3 style={{ margin: '0 0 12px 0', fontSize: '14px', fontWeight: 500, color: KC.onSurface }}>
        Results
      </h3>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
        <StatusBadge status="completed" />
        <span style={{ fontSize: '13px', color: KC.onSurfaceVariant }}>
          Run completed
          {data.completed_at && ` ${formatRelativeTime(data.completed_at)}`}
        </span>
      </div>

      {work_products.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {work_products.map((wp, idx) => (
            <a
              key={idx}
              href={wp.url}
              download
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '6px',
                borderRadius: '4px',
                border: `1px solid rgba(230,126,34,0.3)`,
                background: 'rgba(230,126,34,0.1)',
                padding: '6px 10px',
                fontSize: '13px',
                fontWeight: 500,
                color: KC.primaryContainer,
                textDecoration: 'none',
              }}
            >
              ↓ {wp.name}
            </a>
          ))}
        </div>
      ) : (
        <p style={{ fontSize: '13px', color: KC.onSurfaceVariant }}>
          No downloadable work products were produced by this run.
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
          <div
            key={idx}
            style={{
              marginTop: '12px',
              paddingTop: '12px',
              borderTop: `1px solid ${KC.border}`,
            }}
          >
            <h4 style={{ margin: '0 0 8px 0', fontSize: '12px', fontWeight: 500, color: KC.onSurfaceVariant }}>
              CAD Generation Details
            </h4>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: '8px' }}>
              {[
                { label: 'Volume', value: typeof sr.volume_mm3 === 'number' ? `${sr.volume_mm3.toLocaleString()} mm³` : 'N/A' },
                { label: 'Surface Area', value: typeof sr.surface_area_mm2 === 'number' ? `${sr.surface_area_mm2.toLocaleString()} mm²` : 'N/A' },
                { label: 'Output', value: (sr.cad_file as string) ?? 'N/A' },
              ].map((item) => (
                <div
                  key={item.label}
                  style={{ background: KC.surfaceLowest, borderRadius: '4px', padding: '8px' }}
                >
                  <span style={{ fontSize: '11px', color: KC.onSurfaceVariant }}>{item.label}</span>
                  <p
                    style={{
                      margin: '2px 0 0 0',
                      fontSize: '12px',
                      fontFamily: 'Roboto Mono, monospace',
                      fontWeight: 500,
                      color: KC.onSurface,
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {item.value}
                  </p>
                </div>
              ))}
            </div>
            <details style={{ marginTop: '8px' }}>
              <summary
                style={{
                  cursor: 'pointer',
                  fontSize: '12px',
                  fontWeight: 500,
                  color: KC.primaryContainer,
                  listStyle: 'none',
                }}
              >
                View CadQuery Script
              </summary>
              <pre
                style={{
                  marginTop: '8px',
                  maxHeight: '320px',
                  overflow: 'auto',
                  borderRadius: '4px',
                  background: KC.surfaceLowest,
                  padding: '10px',
                  fontSize: '11px',
                  fontFamily: 'Roboto Mono, monospace',
                  color: KC.success,
                  border: `1px solid ${KC.border}`,
                }}
              >
                <code>{sr.script_text as string}</code>
              </pre>
            </details>
          </div>
        ));
      })()}
    </GlassPanel>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export function DesignAssistantPage() {
  const [prompt, setPrompt] = useState('');
  const [action, setAction] = useState<string>(ACTIONS[0].value);
  const [projectId, setProjectId] = useState<string>('');
  const [targetId, setTargetId] = useState<string>('');
  const [runId, setRunId] = useState<string | undefined>(undefined);

  const { data: projects } = useProjects();
  const submitRequest = useSubmitRequest();
  const { data: runStatus } = useRunStatus(runId);

  const isRunning =
    runStatus?.status === 'running' || runStatus?.status === 'pending';

  const selectedAction = ACTIONS.find((a) => a.value === action);
  const needsTarget = selectedAction?.needsTarget ?? true;

  const selectedProject = projects?.find((p) => p.id === projectId);
  const workProducts = selectedProject?.work_products ?? [];

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!projectId) return;
    if (needsTarget && !targetId) return;
    if (!needsTarget && !prompt.trim()) return;

    submitRequest.mutate(
      {
        action,
        target_id: needsTarget ? targetId : undefined,
        project_id: projectId,
        prompt: prompt.trim() || undefined,
        parameters: prompt.trim() ? { prompt: prompt.trim() } : {},
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
    setTargetId('');
    setRunId(undefined);
    submitRequest.reset();
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', gap: '0' }}>

      {/* Page header */}
      <div style={{ marginBottom: '16px' }}>
        <h1 style={{ margin: '0 0 3px 0', fontSize: '18px', fontWeight: 500, color: KC.onSurface, fontFamily: 'Inter, sans-serif' }}>
          Design Assistant
        </h1>
        <span style={{ fontSize: '12px', color: KC.onSurfaceVariant, fontFamily: 'Inter, sans-serif' }}>
          Submit a request to an agent, track progress in real-time, and download results.
        </span>
      </div>

      {/* Main content — scrollable */}
      <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '12px' }}>

        {/* Request form */}
        <GlassPanel style={{ padding: '16px' }}>
          <form onSubmit={handleSubmit}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>

              {/* Project */}
              <div>
                <KCLabel htmlFor="project-select">Project</KCLabel>
                <KCSelect
                  id="project-select"
                  value={projectId}
                  onChange={setProjectId}
                  disabled={!!runId}
                >
                  <option value="">Select a project...</option>
                  {projects?.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </KCSelect>
              </div>

              {/* Action */}
              <div>
                <KCLabel htmlFor="action-select">Action</KCLabel>
                <KCSelect
                  id="action-select"
                  value={action}
                  onChange={setAction}
                  disabled={!!runId}
                >
                  {ACTIONS.map((a) => (
                    <option key={a.value} value={a.value}>
                      {a.label}
                    </option>
                  ))}
                </KCSelect>
              </div>

              {/* Target work product */}
              {needsTarget && (
                <div>
                  <KCLabel htmlFor="target-select">Target work product</KCLabel>
                  <KCSelect
                    id="target-select"
                    value={targetId}
                    onChange={setTargetId}
                    disabled={!!runId || !projectId}
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
                  </KCSelect>
                </div>
              )}

              {/* Prompt */}
              <div>
                <KCLabel htmlFor="prompt-input">
                  {needsTarget ? 'Additional instructions (optional)' : 'Description / prompt'}
                </KCLabel>
                <KCInput
                  id="prompt-input"
                  value={prompt}
                  onChange={setPrompt}
                  disabled={!!runId}
                  placeholder={
                    needsTarget
                      ? 'e.g. focus on thermal stress at mounting points'
                      : 'e.g. simple bracket with two mounting holes'
                  }
                />
              </div>

              {/* Actions row */}
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  type="submit"
                  disabled={
                    (!needsTarget && !prompt.trim()) ||
                    (needsTarget && !targetId) ||
                    !projectId ||
                    submitRequest.isPending ||
                    !!runId
                  }
                  style={{
                    background: KC.primaryContainer,
                    color: KC.surface,
                    border: 'none',
                    borderRadius: '4px',
                    padding: '7px 16px',
                    fontSize: '13px',
                    fontWeight: 500,
                    fontFamily: 'Inter, sans-serif',
                    cursor: 'pointer',
                    opacity:
                      (!needsTarget && !prompt.trim()) ||
                      (needsTarget && !targetId) ||
                      !projectId ||
                      submitRequest.isPending ||
                      !!runId
                        ? 0.4
                        : 1,
                  }}
                >
                  {submitRequest.isPending ? 'Submitting...' : 'Submit request'}
                </button>
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

            </div>
          </form>
        </GlassPanel>

        {/* Submission error */}
        {submitRequest.isError && (
          <GlassPanel
            style={{
              padding: '12px 16px',
              borderColor: `rgba(255,180,171,0.3)`,
              background: KC.errorBg,
            }}
          >
            <p style={{ margin: '0 0 4px 0', fontSize: '13px', fontWeight: 500, color: KC.error }}>
              Request failed
            </p>
            <p style={{ margin: 0, fontSize: '12px', color: KC.error }}>
              {(submitRequest.error as Error)?.message ?? 'Unknown error'}
            </p>
          </GlassPanel>
        )}

        {/* Progress section */}
        {runId && runStatus && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
            <GlassPanel style={{ padding: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
                <h3 style={{ margin: 0, fontSize: '14px', fontWeight: 500, color: KC.onSurface }}>
                  Progress
                </h3>
                <StatusBadge status={runStatus.status} />
              </div>

              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', marginBottom: '14px' }}>
                <div>
                  <div style={{ fontSize: '13px', fontWeight: 500, color: KC.onSurface, fontFamily: 'Roboto Mono, monospace' }}>
                    {runStatus.run_id}
                  </div>
                  <div style={{ fontSize: '11px', color: KC.onSurfaceVariant }}>Run ID</div>
                </div>
                <div>
                  <div style={{ fontSize: '13px', fontWeight: 500, color: KC.onSurface }}>
                    {runStatus.completed_at
                      ? formatRelativeTime(runStatus.completed_at)
                      : isRunning
                        ? 'In progress...'
                        : '--'}
                  </div>
                  <div style={{ fontSize: '11px', color: KC.onSurfaceVariant }}>Completed</div>
                </div>
              </div>

              <div
                style={{
                  fontSize: '11px',
                  fontWeight: 500,
                  color: KC.onSurfaceVariant,
                  textTransform: 'uppercase',
                  letterSpacing: '0.04em',
                  marginBottom: '10px',
                }}
              >
                Steps
              </div>
              <StepTimeline
                steps={runStatus.steps as Record<string, StepInfo>}
              />
            </GlassPanel>

            {/* Failed run error */}
            {runStatus.status === 'failed' && (
              <GlassPanel
                style={{
                  padding: '12px 16px',
                  borderColor: 'rgba(255,180,171,0.3)',
                  background: KC.errorBg,
                }}
              >
                <p style={{ margin: '0 0 4px 0', fontSize: '13px', fontWeight: 500, color: KC.error }}>
                  Run failed
                </p>
                {Object.entries(
                  runStatus.steps as Record<string, StepInfo>,
                ).map(
                  ([stepId, step]) =>
                    step.error && (
                      <p key={stepId} style={{ margin: '2px 0 0 0', fontSize: '12px', color: KC.error }}>
                        [{step.agent_code}] {step.error}
                      </p>
                    ),
                )}
              </GlassPanel>
            )}

            {/* Results / downloads */}
            <ResultSection data={runStatus} />
          </div>
        )}

        {/* Waiting for first status poll */}
        {runId && !runStatus && (
          <GlassPanel style={{ padding: '12px 16px' }}>
            <p style={{ margin: 0, fontSize: '13px', color: KC.onSurfaceVariant }}>
              Waiting for run status...
            </p>
          </GlassPanel>
        )}

      </div>
    </div>
  );
}
