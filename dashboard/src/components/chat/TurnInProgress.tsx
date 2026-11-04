import { useEffect, useMemo, useState } from 'react';
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

/** Present-tense line for what is happening now, from the latest step. */
function nowDoing(label: string): string {
  // "Read the design record" -> "Reading the design record"; anything else as is.
  const verbs: [RegExp, string][] = [
    [/^Read /, 'Reading '],
    [/^Searched /, 'Searching '],
    [/^Ran /, 'Running '],
    [/^Built /, 'Building '],
    [/^Measured /, 'Measuring '],
    [/^Proposed /, 'Proposing '],
    [/^Prepared /, 'Preparing '],
    [/^Recorded /, 'Recording '],
    [/^Saved /, 'Saving '],
    [/^Checked /, 'Checking '],
    [/^Simulated /, 'Simulating '],
    [/^Generated /, 'Generating '],
    [/^Looked for /, 'Looking for '],
    [/^Worked (on|with) /, 'Working $1 '],
    [/^Started /, 'Starting '],
    [/^Switched /, 'Switching '],
  ];
  for (const [re, to] of verbs) if (re.test(label)) return label.replace(re, to);
  return label;
}

interface TurnInProgressProps {
  /** Stream events received since this turn was sent. */
  events: StreamActivity[];
  /** When the turn was sent (ms since epoch), for the elapsed time. */
  startedAt: number;
  /** Who is answering, as the chat names it. */
  who?: string;
}

/**
 * The agent's side of a turn that has not answered yet: a moving indicator,
 * what it is doing right now, how long it has taken, and the steps done so
 * far. The conversation never sits silent while the model works.
 */
export function TurnInProgress({ events, startedAt, who = 'MetaForge' }: TurnInProgressProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);
  const steps = useMemo(() => summariseActivity(events), [events]);
  const running = [...steps].reverse().find((s) => s.state === 'running' || s.state === 'waiting');
  const seconds = Math.max(0, Math.round((now - startedAt) / 1000));
  const status = running
    ? running.state === 'waiting'
      ? running.label
      : `${nowDoing(running.label)}…`
    : steps.length > 0
      ? 'Working out the answer…'
      : 'Thinking…';
  return (
    <article className="mb-turn" aria-live="polite" aria-busy="true" data-testid="turn-in-progress">
      <header>
        <strong>{who}</strong>
        <span className="mb-dots" aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span className="mb-turn-status">{status}</span>
        <span className="mb-turn-time">{seconds}s</span>
      </header>
      {steps.length > 0 && (
        <ol className="mb-turn-steps">
          {steps.map((s) => {
            const Icon = ICON[s.state];
            return (
              <li key={s.key} className={`mb-step is-${s.state}`}>
                <Icon size={13} aria-hidden="true" className={s.state === 'running' ? 'mb-spin' : undefined} />
                <span>
                  {s.label}
                  {s.detail && <small>{s.detail}</small>}
                </span>
              </li>
            );
          })}
        </ol>
      )}
    </article>
  );
}
