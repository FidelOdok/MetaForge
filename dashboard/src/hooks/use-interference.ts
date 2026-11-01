import { useMutation } from '@tanstack/react-query';
import { runInterferenceCheck } from '../api/endpoints/interference';

/** FORGE-272: run a real boolean-intersection clearance/interference check
 * between two named parts' committed geometry. */
export function useInterferenceCheck() {
  return useMutation({ mutationFn: runInterferenceCheck });
}
