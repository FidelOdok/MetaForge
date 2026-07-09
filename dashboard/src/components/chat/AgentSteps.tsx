import { useState } from 'react';
import { useChatStore } from '@/store/chat-store';
import type { AgentStep } from '@/types/chat';

const KC = {
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  surfaceHigh: '#282a30',
  border: 'rgba(65,72,90,0.3)',
  orange: '#e67e22',
  green: '#3dd68c',
  red: '#f2555a',
};

function preview(value: unknown, max = 140): string {
  if (value === null || value === undefined) return '';
  const s = typeof value === 'string' ? value : JSON.stringify(value);
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

/**
 * Renders the agent's ReAct trace (from `agent.step` SSE events) as a compact,
 * collapsible tool-call timeline — the legibility component that turns the
 * agent from a black box into "queried the twin → proposed a change" (MET-552).
 *
 * Reads the in-flight steps for `threadId` directly from the chat store, so it
 * drops into any chat surface with no prop threading.
 */
export function AgentSteps({ threadId }: { threadId: string }) {
  const steps = useChatStore((s) => s.agentSteps[threadId]);
  if (!steps || steps.length === 0) return null;

  // The final reasoning step's content is the answer (streamed separately);
  // show only the acting steps + any non-empty reasoning.
  const shown = steps.filter((s) => s.tool !== null || s.error || (s.final && s.thought));
  if (shown.length === 0) return null;

  return (
    <div style={{ padding: '0 16px', margin: '4px 0' }}>
      <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
        {shown.map((step) => (
          <StepRow key={step.index} step={step} />
        ))}
      </ul>
    </div>
  );
}

function StepRow({ step }: { step: AgentStep }) {
  const [open, setOpen] = useState(false);
  const isTool = step.tool !== null;
  const hasError = Boolean(step.error);
  const accent = hasError ? KC.red : isTool ? KC.orange : KC.onSurfaceVariant;
  const icon = hasError ? 'error' : isTool ? 'build' : 'lightbulb';
  const label = isTool ? step.tool : 'reasoning';

  return (
    <li
      style={{
        border: `1px solid ${KC.border}`,
        borderRadius: 6,
        background: 'rgba(40,42,48,0.4)',
        overflow: 'hidden',
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          width: '100%',
          padding: '6px 10px',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <span className="material-symbols-outlined" style={{ fontSize: 15, color: accent }}>
          {icon}
        </span>
        <span
          className="font-mono"
          style={{ fontSize: 11, color: KC.onSurface, fontWeight: 500, flexShrink: 0 }}
        >
          {label}
        </span>
        <span
          className="font-mono truncate"
          style={{ fontSize: 11, color: KC.onSurfaceVariant, flex: 1, minWidth: 0 }}
        >
          {step.error
            ? preview(step.error)
            : isTool
              ? preview(step.arguments)
              : preview(step.thought)}
        </span>
        <span className="material-symbols-outlined" style={{ fontSize: 15, color: KC.onSurfaceVariant }}>
          {open ? 'expand_less' : 'expand_more'}
        </span>
      </button>

      {open && (
        <div style={{ padding: '0 10px 8px 33px', display: 'flex', flexDirection: 'column', gap: 6 }}>
          {isTool && step.thought && (
            <Field label="thought" value={step.thought} />
          )}
          {isTool && (
            <Field label="arguments" value={jsonBlock(step.arguments)} mono />
          )}
          {step.error ? (
            <Field label="error" value={step.error} mono accent={KC.red} />
          ) : (
            !step.final && <Field label="result" value={jsonBlock(step.observation)} mono />
          )}
        </div>
      )}
    </li>
  );
}

function jsonBlock(value: unknown): string {
  if (value === null || value === undefined) return '—';
  return typeof value === 'string' ? value : JSON.stringify(value, null, 2);
}

function Field({
  label,
  value,
  mono,
  accent,
}: {
  label: string;
  value: string;
  mono?: boolean;
  accent?: string;
}) {
  return (
    <div>
      <div
        className="font-mono uppercase"
        style={{ fontSize: 9, letterSpacing: '0.08em', color: KC.onSurfaceVariant, marginBottom: 2 }}
      >
        {label}
      </div>
      <pre
        style={{
          margin: 0,
          padding: '6px 8px',
          background: KC.surfaceHigh,
          borderRadius: 4,
          fontSize: 11,
          color: accent ?? KC.onSurface,
          fontFamily: mono ? "'Roboto Mono', monospace" : 'Inter, sans-serif',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          maxHeight: 220,
          overflow: 'auto',
        }}
      >
        {value}
      </pre>
    </div>
  );
}
