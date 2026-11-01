import apiClient from '../client';

export interface OverhangFace {
  name: string | null;
  areaMm2: number | null;
  normal: [number, number, number];
  tiltFromVerticalDeg: number;
  flagged: boolean;
}

export interface OverhangCheckResult {
  faces: OverhangFace[];
  flaggedCount: number;
  totalFaces: number;
  thresholdDeg: number;
  buildAxis: [number, number, number];
  dfmPass: boolean;
  evidenceNodeId: string;
}

interface OverhangFaceApi {
  name: string | null;
  area_mm2: number | null;
  normal: [number, number, number];
  tilt_from_vertical_deg: number;
  flagged: boolean;
}

interface OverhangCheckApiResponse {
  faces: OverhangFaceApi[];
  flagged_count: number;
  total_faces: number;
  threshold_deg: number;
  build_axis: [number, number, number];
  dfm_pass: boolean;
  evidence_node_id: string;
}

export interface OverhangCheckPayload {
  workProductId: string;
  projectId?: string;
  meshFile: string;
  buildAxis?: [number, number, number];
  thresholdDeg?: number;
}

/** FORGE-273: run the 3D-print overhang DFM check against a work product's
 * already-generated mesh, recording the result as Evidence pinned to that
 * work product. */
export async function runOverhangCheck(
  payload: OverhangCheckPayload,
): Promise<OverhangCheckResult> {
  const { data } = await apiClient.post<OverhangCheckApiResponse>('/dfm/overhang-check', {
    work_product_id: payload.workProductId,
    project_id: payload.projectId,
    mesh_file: payload.meshFile,
    build_axis: payload.buildAxis,
    threshold_deg: payload.thresholdDeg,
  });
  return {
    faces: data.faces.map((f) => ({
      name: f.name,
      areaMm2: f.area_mm2,
      normal: f.normal,
      tiltFromVerticalDeg: f.tilt_from_vertical_deg,
      flagged: f.flagged,
    })),
    flaggedCount: data.flagged_count,
    totalFaces: data.total_faces,
    thresholdDeg: data.threshold_deg,
    buildAxis: data.build_axis,
    dfmPass: data.dfm_pass,
    evidenceNodeId: data.evidence_node_id,
  };
}
