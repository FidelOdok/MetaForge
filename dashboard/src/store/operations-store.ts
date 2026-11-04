import { create } from 'zustand';
import {
  applyEvents,
  EMPTY_OPERATIONS,
  mergeSnapshot,
  type OperationsState,
} from '../lib/operations-reducer';
import type { ConnectionHealth, OperationEvent, OperationsSnapshotResponse } from '../types/operations';

/**
 * Live operations per project (FORGE-786, RT-008, ASVG-008).
 *
 * One store shared by Create and Engineering, outside any component, so a
 * switch between the two views shows the same operation, stage, revision and
 * preview without replaying anything. It is not persisted: after a refresh
 * the gateway's snapshot is the source of truth, and `use-operations` fetches
 * it before streaming.
 */

export type OverlaySize = 'compact' | 'expanded';

export interface ProjectOperations {
  ops: OperationsState;
  health: ConnectionHealth;
  /** Finished operations the person closed; they stay closed across views. */
  dismissed: string[];
  size: OverlaySize;
  /** The person's own pause of decorative motion. */
  motionPaused: boolean;
}

export const EMPTY_PROJECT_OPERATIONS: ProjectOperations = {
  ops: EMPTY_OPERATIONS,
  health: 'connecting',
  dismissed: [],
  size: 'compact',
  motionPaused: false,
};

interface OperationsStore {
  projects: Record<string, ProjectOperations>;
  applyEvents: (projectId: string, events: readonly OperationEvent[]) => void;
  mergeSnapshot: (projectId: string, snapshot: OperationsSnapshotResponse) => void;
  setHealth: (projectId: string, health: ConnectionHealth) => void;
  dismiss: (projectId: string, operationId: string) => void;
  setSize: (projectId: string, size: OverlaySize) => void;
  setMotionPaused: (projectId: string, paused: boolean) => void;
  reset: () => void;
}

function patch(
  projects: Record<string, ProjectOperations>,
  projectId: string,
  change: (current: ProjectOperations) => Partial<ProjectOperations>,
): Record<string, ProjectOperations> {
  const current = projects[projectId] ?? EMPTY_PROJECT_OPERATIONS;
  return { ...projects, [projectId]: { ...current, ...change(current) } };
}

export const useOperationsStore = create<OperationsStore>((set) => ({
  projects: {},
  applyEvents: (projectId, events) => {
    if (events.length === 0) return;
    set((s) => ({ projects: patch(s.projects, projectId, (p) => ({ ops: applyEvents(p.ops, events) })) }));
  },
  mergeSnapshot: (projectId, snapshot) =>
    set((s) => ({ projects: patch(s.projects, projectId, (p) => ({ ops: mergeSnapshot(p.ops, snapshot) })) })),
  setHealth: (projectId, health) =>
    set((s) =>
      s.projects[projectId]?.health === health
        ? s
        : { projects: patch(s.projects, projectId, () => ({ health })) },
    ),
  dismiss: (projectId, operationId) =>
    set((s) => ({
      projects: patch(s.projects, projectId, (p) => ({
        dismissed: p.dismissed.includes(operationId) ? p.dismissed : [...p.dismissed, operationId],
      })),
    })),
  setSize: (projectId, size) => set((s) => ({ projects: patch(s.projects, projectId, () => ({ size })) })),
  setMotionPaused: (projectId, motionPaused) =>
    set((s) => ({ projects: patch(s.projects, projectId, () => ({ motionPaused })) })),
  reset: () => set({ projects: {} }),
}));

/** One project's slice; a stable empty value when nothing is known yet. */
export function useProjectOperations(projectId: string | null | undefined): ProjectOperations {
  return useOperationsStore((s) => (projectId ? s.projects[projectId] : undefined) ?? EMPTY_PROJECT_OPERATIONS);
}
