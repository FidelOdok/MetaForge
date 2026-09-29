export type PassFail = 'pass' | 'fail';

/** One requirement's identity + quality diagnostics (FORGE-257). */
export interface RequirementRecord {
  id: string;
  name: string;
  text: string;
  severity: string;
  clarity: PassFail;
  atomicity: PassFail;
  quantified: PassFail;
  traceability: PassFail | null;
  verificationReady: PassFail;
  /** Ids of other requirements this one provably conflicts with. */
  conflicts: string[];
}

export interface ConflictPair {
  aId: string;
  aName: string;
  bId: string;
  bName: string;
  detail: string;
}

export interface CompletenessResult {
  productType: string;
  covered: string[];
  missing: string[];
}

export interface RequirementSetQualityReport {
  requirements: RequirementRecord[];
  conflicts: ConflictPair[];
  completeness: CompletenessResult;
}

export interface RequirementFixProposal {
  proposedText: string | null;
  rationale: string;
  conclusions: string[];
}

/** One piece of evidence cited by a claim against a requirement (FORGE-318). */
export interface EvidenceSummary {
  id: string;
  method: string;
  tier: number | null;
  value: number | null;
  limit: number | null;
  margin: number | null;
  staleness: string;
}

export type RequirementMatrixStatus = 'pass' | 'uncertain' | 'fail' | 'no_data' | 'stale';

/** One requirement's evidence-backed status (FORGE-318): requirements x
 * claims x evidence, derived live -- never a stored, driftable status. */
export interface RequirementMatrixRow {
  requirementId: string;
  requirementName: string;
  limitText: string;
  status: RequirementMatrixStatus;
  detail: string;
  artefactIds: string[];
  evidence: EvidenceSummary[];
}

export interface RequirementMatrixReport {
  rows: RequirementMatrixRow[];
}

/** FORGE-259: the dashboard constraint editor's payload -- a structured
 * measured-key binding (metric/operator/limit/unit/target node), no
 * hand-typed Python expression. */
export interface CreateConstraintPayload {
  projectId: string;
  name: string;
  metric: string;
  operator: string;
  limit: number;
  unit?: string;
  targetNodeType?: string;
  message?: string;
  severity?: string;
}

export interface CreateConstraintResult {
  constraintId: string;
  setWorkProductId: string | null;
}
