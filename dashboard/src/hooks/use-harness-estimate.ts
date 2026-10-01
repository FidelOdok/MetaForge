import { useQuery } from '@tanstack/react-query';
import { getHarnessEstimate } from '../api/endpoints/harnessEstimate';

/** FORGE-275: fetch the real per-joint cumulative cable-length estimate for
 * a work product's committed assembly.joints. A pure read (unlike
 * useCreateFirmwareScaffold's mutation) -- enabled only once a work product
 * id is known, same gating pattern as the rest of the Structure-tab panels. */
export function useHarnessEstimate(workProductId?: string) {
  return useQuery({
    queryKey: ['harness-estimate', workProductId],
    queryFn: () => getHarnessEstimate(workProductId as string),
    enabled: !!workProductId,
  });
}
