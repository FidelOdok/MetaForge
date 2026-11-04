import type { ReactNode } from 'react';
import type { SceneProps } from './types';

const STATE_WORDS: Record<SceneProps['state'], string> = {
  queued: 'waiting to start',
  running: 'in progress',
  waiting: 'paused for a decision',
  completed: 'finished',
  failed: 'stopped with a problem',
  stale: 'out of date',
};

/**
 * The shared shell of every scene: one `<svg>` with a fixed viewBox, a
 * failure mark, a stale label and the classes the stylesheet animates.
 *
 * Motion is CSS only (stroke-dash, transform, opacity). The `is-animated`
 * class is the single switch: it is absent under reduced motion, while
 * paused, and in every state but `running`, so a static frame is the default.
 */
export function SceneFrame({
  name,
  props,
  children,
  label,
}: {
  name: string;
  props: SceneProps;
  children: ReactNode;
  /** Replaces the "Illustrative" label when the scene draws real data. */
  label?: string;
}) {
  const { state, reducedMotion, paused, staleRevision } = props;
  const animated = state === 'running' && !reducedMotion && !paused;
  return (
    <svg
      className={`act-scene is-${state}${animated ? ' is-animated' : ''}`}
      data-scene={name}
      data-state={state}
      data-animated={animated ? 'true' : 'false'}
      data-grounded={label ? 'true' : 'false'}
      viewBox="0 0 160 100"
      role="img"
      aria-label={label ? `${label}, ${STATE_WORDS[state]}` : `Illustrative ${name} activity, ${STATE_WORDS[state]}`}
      focusable="false"
    >
      <g className="act-scene-body">{children}</g>
      {state === 'failed' && (
        <g className="act-scene-fail" aria-hidden="true">
          <circle cx="146" cy="14" r="9" />
          <path d="M141 9 L151 19 M151 9 L141 19" />
        </g>
      )}
      {state === 'completed' && (
        <g className="act-scene-done" aria-hidden="true">
          <circle cx="146" cy="14" r="9" />
          <path d="M141 14 L145 18 L152 10" />
        </g>
      )}
      {state === 'stale' && (
        <text className="act-scene-stale" x="80" y="96" textAnchor="middle">
          {staleRevision ? `Revision ${staleRevision}` : 'Previous revision'}
        </text>
      )}
    </svg>
  );
}
