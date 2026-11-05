import { useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { create } from 'zustand';
import { getAppearance, type AppearanceRecord } from '../api/endpoints/changeClassification';

/**
 * The active project's appearance record, for the viewer to apply as a
 * material override on every mesh it shows (3D-RC-007, SYNC-007).
 */
interface AppearanceState {
  record: AppearanceRecord | null;
  setRecord: (record: AppearanceRecord | null) => void;
}

export const useAppearanceStore = create<AppearanceState>()((set) => ({
  record: null,
  setRecord: (record) => set({ record }),
}));

export const appearanceQueryKey = (projectId: string | null | undefined) => ['appearance', projectId ?? ''] as const;

/** Load the project's appearance record into the viewer. */
export function useAppearanceSync(projectId: string | null | undefined): void {
  const setRecord = useAppearanceStore((s) => s.setRecord);
  const query = useQuery({
    queryKey: appearanceQueryKey(projectId),
    queryFn: () => getAppearance(projectId!),
    enabled: !!projectId,
    staleTime: 10_000,
  });
  const record = query.data?.record ?? null;
  useEffect(() => {
    setRecord(projectId ? record : null);
  }, [projectId, record, setRecord]);
}
