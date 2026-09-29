/** FORGE-290 (gap G-G4): maturity-gate promotion -- evidence-gated
 * approval that blocks on `no_data`, with a human approve/reject-with-
 * comment veto on top (FORGE-319's original gate only ever refused on bad
 * evidence; this ticket adds the reviewer's own judgement call). */

export type MaturityLevel = 'concept' | 'sim_validated' | 'physically_validated' | 'released';

export type RequiredClaimDecision = 'pass' | 'uncertain' | 'fail' | 'waived';

export interface RequiredClaimResult {
  requirementId: string;
  requirementName: string;
  decision: RequiredClaimDecision;
  detail: string;
  waiverId: string | null;
}

export interface AttemptPromotionPayload {
  projectId: string;
  level: MaturityLevel;
  requiredClaimIds: string[];
  k?: number;
  decidedBy?: string;
  comment?: string;
  reject?: boolean;
}

export interface AttemptPromotionResult {
  gateId: string;
  level: MaturityLevel;
  promoted: boolean;
  blockedReason: string | null;
  decidedBy: string | null;
  comment: string | null;
  results: RequiredClaimResult[];
}

export interface MaturityGateSummary {
  gateId: string;
  level: MaturityLevel;
  promoted: boolean;
  blockedReason: string | null;
  decidedBy: string | null;
  comment: string | null;
  createdAt: string;
}

export interface PromotionHistoryReport {
  gates: MaturityGateSummary[];
}
