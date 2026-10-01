import apiClient from '../client';

export interface TechnicalDrawingDimension {
  feature: string;
  nominalMm: number;
  tolerancePlusMm: number;
  toleranceMinusMm: number;
}

export interface TechnicalDrawingGdtCallout {
  feature: string;
  symbol: string;
  toleranceValueMm: number;
  datumRefs: string[];
}

export interface TechnicalDrawingSurfaceFinish {
  feature: string;
  raUm: number;
}

export interface TechnicalDrawingSummary {
  nodeId: string;
  createdAt: string;
  name: string;
  partName: string;
  dimensions: TechnicalDrawingDimension[];
  gdtCallouts: TechnicalDrawingGdtCallout[];
  surfaceFinishes: TechnicalDrawingSurfaceFinish[];
  inspectionRequirements: string[];
  approved: boolean;
  approvedAt: string | null;
  approvedBy: string | null;
}

interface TechnicalDrawingDimensionApi {
  feature: string;
  nominal_mm: number;
  tolerance_plus_mm: number;
  tolerance_minus_mm: number;
}

interface TechnicalDrawingGdtCalloutApi {
  feature: string;
  symbol: string;
  tolerance_value_mm: number;
  datum_refs: string[];
}

interface TechnicalDrawingSurfaceFinishApi {
  feature: string;
  ra_um: number;
}

interface TechnicalDrawingSummaryApi {
  node_id: string;
  created_at: string;
  name: string;
  part_name: string;
  dimensions: TechnicalDrawingDimensionApi[];
  gdt_callouts: TechnicalDrawingGdtCalloutApi[];
  surface_finishes: TechnicalDrawingSurfaceFinishApi[];
  inspection_requirements: string[];
  approved: boolean;
  approved_at: string | null;
  approved_by: string | null;
}

interface TechnicalDrawingListApiResponse {
  drawings: TechnicalDrawingSummaryApi[];
}

function fromApi(d: TechnicalDrawingSummaryApi): TechnicalDrawingSummary {
  return {
    nodeId: d.node_id,
    createdAt: d.created_at,
    name: d.name,
    partName: d.part_name,
    dimensions: d.dimensions.map((dim) => ({
      feature: dim.feature,
      nominalMm: dim.nominal_mm,
      tolerancePlusMm: dim.tolerance_plus_mm,
      toleranceMinusMm: dim.tolerance_minus_mm,
    })),
    gdtCallouts: d.gdt_callouts.map((g) => ({
      feature: g.feature,
      symbol: g.symbol,
      toleranceValueMm: g.tolerance_value_mm,
      datumRefs: g.datum_refs,
    })),
    surfaceFinishes: d.surface_finishes.map((s) => ({ feature: s.feature, raUm: s.ra_um })),
    inspectionRequirements: d.inspection_requirements,
    approved: d.approved,
    approvedAt: d.approved_at,
    approvedBy: d.approved_by,
  };
}

/** FORGE-293: a part's real recorded technical_drawing work products,
 * oldest first. The data (dimensions/GD&T/surface finishes/inspection
 * requirements) was already real and persisted before this ticket -- this
 * just reads it back for the dashboard. */
export async function listTechnicalDrawings(
  workProductId: string,
): Promise<TechnicalDrawingSummary[]> {
  const { data } = await apiClient.get<TechnicalDrawingListApiResponse>(
    '/technical-drawings',
    { params: { work_product_id: workProductId } },
  );
  return data.drawings.map(fromApi);
}

export interface ApproveTechnicalDrawingResult {
  nodeId: string;
  approved: boolean;
  approvedAt: string;
}

interface ApproveTechnicalDrawingApiResponse {
  node_id: string;
  approved: boolean;
  approved_at: string;
}

/** FORGE-293: human sign-off on a technical_drawing -- a real Twin state
 * transition (versioned), not a dashboard-local checkbox. */
export async function approveTechnicalDrawing(
  nodeId: string,
  approvedBy?: string,
): Promise<ApproveTechnicalDrawingResult> {
  const { data } = await apiClient.post<ApproveTechnicalDrawingApiResponse>(
    `/twin/nodes/${nodeId}/approve-technical-drawing`,
    { approved_by: approvedBy },
  );
  return { nodeId: data.node_id, approved: data.approved, approvedAt: data.approved_at };
}
