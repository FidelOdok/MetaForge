import { useMutation } from '@tanstack/react-query';
import { selectComponent } from '../api/endpoints/component-selection';

export function useSelectComponent() {
  return useMutation({
    mutationFn: selectComponent,
  });
}
