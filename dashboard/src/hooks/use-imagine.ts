import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getHandover,
  getStudio,
  getValidation,
  imagineKeys,
  newId,
  postHandover,
  postRework,
} from '../api/endpoints/imagine';
import { creationKeys } from './use-creation';
import { twinKeys } from './use-twin';

// Polled, so a studio the agent starts or revises appears without a reload.
const POLL_MS = 10_000;

export function useStudio(projectId: string) {
  return useQuery({
    queryKey: imagineKeys.studio(projectId),
    queryFn: ({ signal }) => getStudio(projectId, signal),
    refetchInterval: POLL_MS,
    retry: false,
  });
}

export function useHandoverChecklist(projectId: string, enabled = true) {
  return useQuery({
    queryKey: imagineKeys.handover(projectId),
    queryFn: ({ signal }) => getHandover(projectId, signal),
    enabled,
    refetchInterval: POLL_MS,
    retry: false,
  });
}

/** The validation report, polled like the checklist so a person's step in the studio shows up. */
export function useValidation(projectId: string, enabled = true) {
  return useQuery({
    queryKey: imagineKeys.validation(projectId),
    queryFn: ({ signal }) => getValidation(projectId, signal),
    enabled,
    refetchInterval: POLL_MS,
    retry: false,
  });
}

/** Refresh everything a handover or a rework changes. */
function useRefreshAfter(projectId: string) {
  const qc = useQueryClient();
  return () => {
    void qc.invalidateQueries({ queryKey: ['imagine', projectId] });
    void qc.invalidateQueries({ queryKey: creationKeys.detail(projectId) });
    void qc.invalidateQueries({ queryKey: twinKeys.all });
  };
}

export function useHandover(projectId: string) {
  const refresh = useRefreshAfter(projectId);
  return useMutation({
    mutationFn: () => postHandover(projectId, newId('handover')),
    onSuccess: refresh,
    onError: refresh,
  });
}

export function useRework(projectId: string) {
  const refresh = useRefreshAfter(projectId);
  return useMutation({
    mutationFn: (reason: string) => postRework(projectId, newId('rework'), reason),
    onSuccess: refresh,
  });
}
