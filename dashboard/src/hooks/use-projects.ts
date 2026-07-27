import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  getProjects,
  getProject,
  createProject,
  updateProject,
  deleteProject,
  type CreateProjectPayload,
  type UpdateProjectPayload,
} from '../api/endpoints/projects';

export const projectKeys = {
  all: ['projects'] as const,
  detail: (id: string) => [...projectKeys.all, id] as const,
};

export function useProjects() {
  return useQuery({
    queryKey: projectKeys.all,
    queryFn: getProjects,
    staleTime: 60_000,
  });
}

export function useProject(id: string | undefined) {
  return useQuery({
    queryKey: projectKeys.detail(id ?? ''),
    queryFn: () => getProject(id!),
    enabled: !!id,
    staleTime: 30_000,
  });
}

export function useCreateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateProjectPayload) => createProject(payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: projectKeys.all });
    },
  });
}

export function useUpdateProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: UpdateProjectPayload }) =>
      updateProject(id, payload),
    onSuccess: (updated) => {
      queryClient.invalidateQueries({ queryKey: projectKeys.all });
      queryClient.invalidateQueries({ queryKey: projectKeys.detail(updated.id) });
    },
  });
}

export function useDeleteProject() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteProject(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: projectKeys.all });
    },
  });
}
