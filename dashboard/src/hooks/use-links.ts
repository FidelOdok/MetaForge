import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
  getNodeLink,
  getAllLinks,
  createLink,
  deleteLink,
  syncNode,
} from '../api/endpoints/twin';
import type { FileLinkTool } from '../types/twin';

export const linkKeys = {
  all: ['links'] as const,
  node: (nodeId: string) => ['link', nodeId] as const,
};

export function useNodeLink(nodeId: string | undefined) {
  return useQuery({
    queryKey: linkKeys.node(nodeId ?? ''),
    queryFn: () => getNodeLink(nodeId!),
    enabled: !!nodeId,
    staleTime: 15_000,
  });
}

export function useAllLinks() {
  return useQuery({
    queryKey: linkKeys.all,
    queryFn: getAllLinks,
    staleTime: 30_000,
  });
}

export function useCreateLink(nodeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { source_path: string; tool: FileLinkTool; watch: boolean }) =>
      createLink(nodeId, payload),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: linkKeys.node(nodeId) });
      void queryClient.invalidateQueries({ queryKey: linkKeys.all });
    },
  });
}

export function useDeleteLink(nodeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => deleteLink(nodeId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: linkKeys.node(nodeId) });
      void queryClient.invalidateQueries({ queryKey: linkKeys.all });
    },
  });
}

export function useSyncNode(nodeId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => syncNode(nodeId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: linkKeys.node(nodeId) });
    },
  });
}
