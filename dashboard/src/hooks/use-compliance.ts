import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getChecklist,
  getCoverage,
  getEvidenceForItem,
  linkEvidence,
} from '../api/endpoints/compliance';
import type { ComplianceRegime, LinkEvidencePayload } from '../types/compliance';

export const complianceKeys = {
  all: ['compliance'] as const,
  checklist: (projectId: string, markets: ComplianceRegime[]) =>
    [...complianceKeys.all, 'checklist', projectId, markets.join(',')] as const,
  coverage: (projectId: string) => [...complianceKeys.all, 'coverage', projectId] as const,
  evidence: (projectId: string, itemId: string) =>
    [...complianceKeys.all, 'evidence', projectId, itemId] as const,
};

export function useChecklist(projectId: string | undefined, markets: ComplianceRegime[]) {
  return useQuery({
    queryKey: complianceKeys.checklist(projectId ?? '', markets),
    queryFn: () => getChecklist(projectId!, markets),
    enabled: !!projectId,
    staleTime: 15_000,
  });
}

export function useCoverage(projectId: string | undefined) {
  return useQuery({
    queryKey: complianceKeys.coverage(projectId ?? ''),
    queryFn: () => getCoverage(projectId!),
    enabled: !!projectId,
    staleTime: 15_000,
  });
}

export function useEvidenceForItem(projectId: string | undefined, itemId: string | undefined) {
  return useQuery({
    queryKey: complianceKeys.evidence(projectId ?? '', itemId ?? ''),
    queryFn: () => getEvidenceForItem(projectId!, itemId!),
    enabled: !!projectId && !!itemId,
  });
}

export function useLinkEvidence(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: LinkEvidencePayload) => linkEvidence(projectId, payload),
    onSuccess: () => {
      // Refetch every checklist query for this project regardless of the
      // market filter currently selected — a partial key match covers all
      // of them.
      queryClient.invalidateQueries({ queryKey: [...complianceKeys.all, 'checklist', projectId] });
    },
  });
}
