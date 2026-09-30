import { useMutation } from '@tanstack/react-query';
import {
  releaseForManufacture,
  triggerManufactureDownload,
  type ManufactureProcess,
} from '../api/endpoints/manufacture';

/** FORGE-294: release a work product's real geometry for manufacture
 * (STL for 3D printing, STEP for CNC), then trigger the browser
 * download of the real returned file. */
export function useManufactureRelease() {
  return useMutation({
    mutationFn: async (vars: { workProductId: string; process: ManufactureProcess }) => {
      const result = await releaseForManufacture(vars.workProductId, vars.process);
      triggerManufactureDownload(result);
      return result;
    },
  });
}
