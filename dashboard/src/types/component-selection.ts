/** FORGE-265 (gap G-C1): requirement-driven component selection. Spec
 * values are caller-asserted real datasheet numbers (e.g. a servo's
 * published torque_kg_cm) -- nothing in this codebase scrapes or parses a
 * datasheet, so a candidate's specs are typed in by whoever is comparing
 * parts, same honesty convention as trade-study's criteria_scores. */

export type MarginOp = '>=' | '<=';

export interface RequiredSpec {
  op: MarginOp;
  value: number;
}

export interface Candidate {
  mpn: string;
  manufacturer: string;
  specs: Record<string, number>;
}

export interface SpecMargin {
  pass: boolean;
  required: number;
  actual: number | null;
  op: MarginOp;
  margin: number | null;
  margin_pct: number | null;
  error?: string;
}

export interface ScoredCandidate extends Candidate {
  margins: Record<string, SpecMargin>;
}

export interface SelectComponentPayload {
  candidates: Candidate[];
  requiredSpecs: Record<string, RequiredSpec>;
  selectedMpn: string;
  category: string;
  purchaseUnit: string;
  title: string;
  rationale: string;
  quantity?: number;
  projectId?: string;
  requirementIds?: string[];
}

export interface SelectComponentResult {
  node_id: string;
  decision_node_id: string;
  selected_mpn: string;
  selected_meets_requirements: boolean;
  candidates: ScoredCandidate[];
}
