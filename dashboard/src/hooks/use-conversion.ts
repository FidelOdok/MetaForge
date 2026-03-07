import { useMutation, useQuery } from '@tanstack/react-query';
import {
  uploadStep,
  getConversionResult,
} from '../api/endpoints/convert';
import { useViewerStore } from '../store/viewer-store';
import type { ModelManifest } from '../types/viewer';

export const conversionKeys = {
  all: ['conversion'] as const,
  result: (hash: string) => [...conversionKeys.all, hash] as const,
};

export function useUploadAndConvert() {
  const loadModel = useViewerStore((s) => s.loadModel);

  return useMutation({
    mutationFn: async ({ file, quality }: { file: File; quality?: string }) => {
      const result = await uploadStep(file, quality);
      return result;
    },
    onSuccess: (result) => {
      const manifest: ModelManifest = {
        parts: result.metadata.parts,
        meshToNodeMap: {},
        materials: result.metadata.materials,
        stats: result.metadata.stats,
      };
      loadModel(result.glb_url, manifest);
    },
  });
}

export function useConversionResult(hash: string | undefined) {
  return useQuery({
    queryKey: conversionKeys.result(hash ?? ''),
    queryFn: () => getConversionResult(hash!),
    enabled: !!hash,
    staleTime: Infinity,
  });
}
