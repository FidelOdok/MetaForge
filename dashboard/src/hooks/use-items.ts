import { useQuery } from '@tanstack/react-query';
import {
  getBaselineDiff,
  getCurrentView,
  getItemDiff,
  getItemHistory,
  getRevisionIndex,
  getRunChanges,
  listBaselines,
} from '../api/endpoints/items';

/* FORGE-526: the current view is the default; history, compare and baselines
 * are fetched only when their panel opens. */

export const itemKeys = {
  all: ['items'] as const,
  currentView: (projectId: string) => [...itemKeys.all, 'current', projectId] as const,
  history: (key: string, projectId?: string) => [...itemKeys.all, 'history', key, projectId ?? ''] as const,
  diff: (key: string, a?: number, b?: number) => [...itemKeys.all, 'diff', key, a ?? 0, b ?? 0] as const,
  baselines: (projectId: string) => [...itemKeys.all, 'baselines', projectId] as const,
  baselineDiff: (a: string, b: string) => [...itemKeys.all, 'baseline-diff', a, b] as const,
  runChanges: (runId: string) => [...itemKeys.all, 'run', runId] as const,
  revisionIndex: (projectId: string) => [...itemKeys.all, 'index', projectId] as const,
};

export function useCurrentView(projectId?: string) {
  return useQuery({
    queryKey: itemKeys.currentView(projectId ?? ''),
    queryFn: () => getCurrentView(projectId as string),
    enabled: Boolean(projectId),
    staleTime: 10_000,
  });
}

export function useItemHistory(key?: string | null, projectId?: string) {
  return useQuery({
    queryKey: itemKeys.history(key ?? '', projectId),
    queryFn: () => getItemHistory(key as string, projectId),
    enabled: Boolean(key),
    staleTime: 10_000,
  });
}

export function useItemDiff(key?: string | null, a?: number, b?: number, projectId?: string) {
  return useQuery({
    queryKey: itemKeys.diff(key ?? '', a, b),
    queryFn: () => getItemDiff(key as string, a, b, projectId),
    enabled: Boolean(key && a && b && a !== b),
    staleTime: 30_000,
  });
}

export function useBaselines(projectId?: string) {
  return useQuery({
    queryKey: itemKeys.baselines(projectId ?? ''),
    queryFn: () => listBaselines(projectId as string),
    enabled: Boolean(projectId),
    staleTime: 15_000,
  });
}

export function useBaselineDiff(a?: string, b?: string) {
  return useQuery({
    queryKey: itemKeys.baselineDiff(a ?? '', b ?? ''),
    queryFn: () => getBaselineDiff(a as string, b as string),
    enabled: Boolean(a && b && a !== b),
    staleTime: 30_000,
  });
}

export function useRunChanges(runId?: string, projectId?: string) {
  return useQuery({
    queryKey: itemKeys.runChanges(runId ?? ''),
    queryFn: () => getRunChanges(runId as string, projectId),
    enabled: Boolean(runId),
    staleTime: 10_000,
  });
}

export function useRevisionIndex(projectId?: string | null) {
  return useQuery({
    queryKey: itemKeys.revisionIndex(projectId ?? ''),
    queryFn: () => getRevisionIndex(projectId as string),
    enabled: Boolean(projectId),
    staleTime: 15_000,
  });
}
