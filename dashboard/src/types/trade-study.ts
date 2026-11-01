/** FORGE-262 (gap G-B2): concept generation + trade study. Only `mass_kg`
 * has any real measured source in this codebase today (a CAD work
 * product's own recorded mass) -- `evidence_backed_criteria` names which
 * of an option's criteria came from that rather than an assertion. */

export interface ConceptOption {
  id: string;
  title: string;
  criteria_scores: Record<string, number>;
  evidence_backed_criteria: string[];
}

export interface AddConceptOptionPayload {
  title: string;
  criteriaScores: Record<string, number>;
  evidenceBackedCriteria?: string[];
  projectId?: string;
}

export interface AddConceptOptionResult {
  node_id: string;
  entity_type: string;
  project_linked: boolean;
}

export interface SelectConceptPayload {
  optionIds: string[];
  selectedOptionId: string;
  weights: Record<string, number>;
  title: string;
  rationale: string;
  projectId?: string;
  requirementIds?: string[];
}

export interface SelectConceptResult {
  node_id: string;
  selected_option_id: string;
  scores: Array<ConceptOption & { weighted_score: number }>;
}
