import type { OperationView } from './operations-reducer';

/**
 * CDE slice 6: the state of the engineering checks behind the Create preview,
 * read from the exact-geometry build jobs on the project's operation stream.
 * Only the latest build counts: an older failure that a newer build replaced
 * is not reported.
 */

/** The job title the gateway gives `twin.build_exact_cad` operations. */
export const EXACT_BUILD_TITLE = 'Build exact cad';

export function exactChecksState(ops: OperationView[] | Record<string, OperationView>): 'running' | 'failed' | null {
  const list = Array.isArray(ops) ? ops : Object.values(ops);
  const builds = list
    .filter((op) => op.kind === 'job' && op.domain === 'mechanical' && op.title === EXACT_BUILD_TITLE)
    .sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''));
  const latest = builds[0];
  if (!latest) return null;
  if (latest.state === 'queued' || latest.state === 'running') return 'running';
  if (latest.state === 'failed') return 'failed';
  return null;
}
