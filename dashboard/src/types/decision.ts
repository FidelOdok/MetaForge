/** FORGE-289 (gap G-G3): decision records with alternatives and evidence
 * links -- field names mirror api_gateway/twin/decision_routes.py's own
 * pass-through shape (snake_case), same convention design-loop.ts already
 * uses for its own thin REST pass-through. */

export interface DecisionAlternative {
  option: string;
  reason_rejected: string;
}

export interface Decision {
  id: string;
  title: string;
  rationale: string;
  alternatives: DecisionAlternative[];
  parent_refs: string[];
  evidence_refs: string[];
  created_at: string | null;
}

export interface RelatedDecisionsReport {
  related_to: string;
  decisions: Decision[];
}
