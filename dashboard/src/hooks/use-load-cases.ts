import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createLoadCase, getLoadCases, getNamedFaces } from '../api/endpoints/loadCases';

export const loadCaseKeys = {
  all: ['load-cases'] as const,
  project: (projectId: string) => [...loadCaseKeys.all, projectId] as const,
};

export function useLoadCases(projectId?: string) {
  return useQuery({
    queryKey: projectId ? loadCaseKeys.project(projectId) : loadCaseKeys.all,
    queryFn: () => getLoadCases(projectId),
    staleTime: 30_000,
  });
}

export function useCreateLoadCase(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: createLoadCase,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: loadCaseKeys.project(projectId) });
    },
  });
}

/** FORGE-277: named-face geometry for the load-case dialog's 3D face
 * picker. Disabled until a mesh file is known — there's nothing to fetch
 * until an earlier freecad.generate_mesh call produced one. */
export function useNamedFaces(meshFile: string | undefined) {
  return useQuery({
    queryKey: ['named-faces', meshFile] as const,
    queryFn: () => getNamedFaces(meshFile as string),
    enabled: !!meshFile,
    staleTime: 5 * 60_000,
  });
}
