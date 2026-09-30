import { useMutation } from '@tanstack/react-query';
import { runOverhangCheck } from '../api/endpoints/dfm';

/** FORGE-273: run the 3D-print overhang DFM check for a work product. */
export function useOverhangCheck() {
  return useMutation({ mutationFn: runOverhangCheck });
}
