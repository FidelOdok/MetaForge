import { useMemo } from 'react';
import { AlertCircle, CheckCircle2, Hand, Info, Loader2 } from 'lucide-react';
import '../../styles/chat-blocks.css';
import { summariseActivity, type StepState, type StreamActivity } from './activity';

const ICON: Record<StepState, typeof CheckCircle2> = {
  running: Loader2,
  done: CheckCircle2,
  failed: AlertCircle,
  waiting: Hand,
  info: Info,
};

const STATE_TEXT: Record<StepState, string> = {
  running: 'In progress',
  done: 'Done',
  failed: 'Did not work',
  waiting: 'Needs you',
  info: '',
};

interface ActivityStepsProps {
  events: StreamActivity[];
  /** Also offer the raw stream events, for debugging. */
  showRaw?: boolean;
  open?: boolean;
}

/** What the agent is doing, as a few plain steps rather than raw stream events. */
export function ActivitySteps({ events, showRaw, open }: ActivityStepsProps) {
  const steps = useMemo(() => summariseActivity(events), [events]);
  if (steps.length === 0 && !showRaw) return null;
  const running = steps.filter((s) => s.state === 'running').length;
  return (
    <details className="mb-activity" open={open}>
      <summary>
        {running ? `Working · ${running} step${running > 1 ? 's' : ''} in progress` : `What the agent did · ${steps.length} step${steps.length === 1 ? '' : 's'}`}
      </summary>
      <ol>
        {steps.map((s) => {
          const Icon = ICON[s.state];
          return (
            <li key={s.key} className={`mb-step is-${s.state}`}>
              <Icon size={14} aria-hidden="true" className={s.state === 'running' ? 'mb-spin' : undefined} />
              <span>
                {s.label}
                {STATE_TEXT[s.state] && <span className="mb-sr"> · {STATE_TEXT[s.state]}</span>}
                {s.detail && <small>{s.detail}</small>}
              </span>
            </li>
          );
        })}
      </ol>
      {showRaw && events.length > 0 && (
        <details className="mb-raw">
          <summary>Raw events ({events.length})</summary>
          <pre>{events.map((e) => `${e.kind}  ${JSON.stringify(e.data)}`).join('\n')}</pre>
        </details>
      )}
    </details>
  );
}
