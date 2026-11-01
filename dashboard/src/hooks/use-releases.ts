import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { createReleasePackage, listReleasePackages } from '../api/endpoints/releases';

export const releaseKeys = {
  all: ['releases'] as const,
  project: (projectId: string) => [...releaseKeys.all, projectId] as const,
};

/** FORGE-299: a project's release packages, oldest first, each carrying
 * its own already-computed diff against the immediately-prior package. */
export function useReleasePackages(projectId?: string) {
  return useQuery({
    queryKey: projectId ? releaseKeys.project(projectId) : releaseKeys.all,
    queryFn: () => listReleasePackages(projectId),
    staleTime: 15_000,
  });
}

/** FORGE-299: create a new release package -- gated on the G8 release gate
 * server-side (a 409 means "not release-ready yet", with the failing
 * checks in the error's response body). */
export function useCreateReleasePackage(projectId?: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (notes?: string) => {
      if (!projectId) throw new Error('useCreateReleasePackage: no active project');
      return createReleasePackage(projectId, notes);
    },
    onSuccess: () => {
      if (projectId) {
        queryClient.invalidateQueries({ queryKey: releaseKeys.project(projectId) });
      }
    },
  });
}
