/** One FEA result, persisted as a SIMULATION_RESULT work product (FORGE-246,
 * listed by FORGE-279). Creation is agent-driven only (twin.record_document
 * after a real calculix.extract_results run) — there is no dashboard
 * "create result" form, unlike load cases. */
export interface SimulationResult {
  id: string;
  name: string;
  maxVonMisesMpa: number | null;
  maxDisplacementMm: number | null;
  /** Free-text name of which load case produced this result — not a
   * load-case node id. */
  loadCase: string | null;
  meshStats: Record<string, unknown> | null;
  projectId: string;
  createdAt: string;
  updatedAt: string;
}
