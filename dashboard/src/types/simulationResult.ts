import type { MeshConvergence } from './simulationField';

/** One FEA result, persisted as a SIMULATION_RESULT work product (FORGE-246,
 * listed by FORGE-279). Creation is agent-driven only (twin.record_document
 * after a real calculix run); there is no dashboard "create result" form,
 * unlike load cases. */
export interface SimulationResult {
  id: string;
  name: string;
  maxVonMisesMpa: number | null;
  maxDisplacementMm: number | null;
  /** Free-text name of which load case produced this result, not a
   * load-case node id. */
  loadCase: string | null;
  meshStats: Record<string, unknown> | null;
  projectId: string;
  createdAt: string;
  updatedAt: string;
  // FORGE-532: 3D result field and what was analysed. Optional so an older
  // gateway (or a result recorded before fields existed) still type-checks.
  maxTemperatureC?: number | null;
  analysisType?: string | null;
  /** True when a field blob is stored and `/results/{id}/field` serves it. */
  hasField?: boolean;
  fieldQuantities?: string[];
  fieldRanges?: Record<string, { min: number | null; max: number | null; unit?: string | null }> | null;
  fieldSizeBytes?: number | null;
  analysedGeometry?: { node_id: string; revision?: string | number; name?: string; content_hash?: string } | null;
  loadCaseSpec?: Record<string, unknown> | null;
  fixtures?: Array<Record<string, unknown>> | null;
  loads?: Array<Record<string, unknown>> | null;
  meshConvergence?: MeshConvergence | null;
}
