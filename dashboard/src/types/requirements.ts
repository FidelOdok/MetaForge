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
