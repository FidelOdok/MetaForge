import apiClient from '../client';

export interface InterferenceCheckResult {
  workProductIdA: string;
  workProductIdB: string;
  interferes: boolean;
  interferenceVolumeMm3: number;
  interferenceAreaMm2: number;
}

interface InterferenceCheckApiResponse {
  work_product_id_a: string;
  work_product_id_b: string;
  interferes: boolean;
  interference_volume_mm3: number;
  interference_area_mm2: number;
}

export interface InterferenceCheckPayload {
  workProductIdA: string;
  workProductIdB: string;
}

/** FORGE-272: real boolean-intersection clearance/interference check
 * between two named parts' committed STEP geometry. A pairwise check, not
 * an all-pairs assembly sweep, and not an ISO 286 fit classification. */
export async function runInterferenceCheck(
  payload: InterferenceCheckPayload,
): Promise<InterferenceCheckResult> {
  const { data } = await apiClient.get<InterferenceCheckApiResponse>(
    '/twin/interference-check',
    {
      params: {
        work_product_id_a: payload.workProductIdA,
        work_product_id_b: payload.workProductIdB,
      },
    },
  );
  return {
    workProductIdA: data.work_product_id_a,
    workProductIdB: data.work_product_id_b,
    interferes: data.interferes,
    interferenceVolumeMm3: data.interference_volume_mm3,
    interferenceAreaMm2: data.interference_area_mm2,
  };
}
