import { useMutation, useQueryClient } from '@tanstack/react-query';
import { importWorkProduct } from '../api/endpoints/twin';
import type { ImportWorkProductResponse } from '../types/twin';

interface ImportMutationVariables {
  formData: FormData;
  onProgress?: (pct: number) => void;
}

export function useImportWorkProduct() {
  const queryClient = useQueryClient();
  return useMutation<ImportWorkProductResponse, Error, ImportMutationVariables>({
    mutationFn: ({ formData, onProgress }) => importWorkProduct(formData, onProgress),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['twin'] });
      queryClient.invalidateQueries({ queryKey: ['projects'] });
    },
  });
}
