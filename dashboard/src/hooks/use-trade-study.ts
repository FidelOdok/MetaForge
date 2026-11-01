import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { addConceptOption, getConceptOptions, selectConcept } from '../api/endpoints/trade-study';

export const tradeStudyKeys = {
  options: (projectId?: string) => ['trade-study', 'options', projectId ?? ''] as const,
};

export function useConceptOptions(projectId?: string) {
  return useQuery({
    queryKey: tradeStudyKeys.options(projectId),
    queryFn: () => getConceptOptions(projectId),
    enabled: !!projectId,
    staleTime: 10_000,
  });
}

export function useAddConceptOption(projectId?: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: addConceptOption,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: tradeStudyKeys.options(projectId) });
    },
  });
}

export function useSelectConcept() {
  return useMutation({
    mutationFn: selectConcept,
  });
}
