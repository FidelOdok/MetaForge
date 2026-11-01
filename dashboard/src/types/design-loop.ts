/** FORGE-287 (gap G-G1): closed design loop -- propose -> build -> simulate
 * -> evaluate against constraints -> revise -> repeat, until pass or proven
 * infeasible. Field names deliberately mirror the REST route's own
 * pass-through shape (snake_case, same as
 * twin_core.models.design_loop_iteration.DesignLoopIteration) rather than
 * this codebase's usual camelCase dashboard contract, since the route
 * itself is a thin pass-through over the MCP tool's own output -- see
 * api_gateway/design_loop/routes.py. */

export type DesignLoopStatus = 'optimal' | 'infeasible' | 'already_feasible_at_min';

export interface DesignLoopIteration {
  id: string;
  iteration_number: number;
  parameter_name: string;
  parameter_value: number;
  metric: string;
  objective_value: number;
  constraints_status: Record<string, number>;
  feasible: boolean;
  status: 'candidate' | 'converged' | 'infeasible';
  is_winner: boolean;
  approved: boolean;
  approved_by: string | null;
}

export interface StartDesignLoopPayload {
  workProductId: string;
  loadN: number;
  deflectionLimitMm: number;
  sfLimit?: number;
  material?: string;
  wallMinMm?: number;
  wallMaxMm?: number;
  projectId?: string;
  requirementIds?: string[];
  maxIterations?: number;
}

export interface StartDesignLoopResult {
  loop_id: string;
  status: DesignLoopStatus;
  detail: string;
  winner: DesignLoopIteration | null;
  candidates: DesignLoopIteration[];
  iteration_count: number;
  iteration_ids: string[];
  /** FORGE-291 (gap G-G5): the real, honest iteration budget -- "tokens"
   * (the ticket's own dashboard wording) doesn't apply here, this loop
   * makes zero LLM calls. */
  max_iterations: number;
  /** FORGE-291: true when this call had identical inputs to an earlier
   * run -- loop_id/iteration_ids/winner are that prior run's. */
  duplicate: boolean;
}

export interface DesignLoopReport {
  loop_id: string;
  iterations: DesignLoopIteration[];
}
