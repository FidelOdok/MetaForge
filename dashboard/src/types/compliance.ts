/**
 * Type definitions for the Compliance domain.
 *
 * Mirrors ``domain_agents/compliance/models.py`` and the wire shape of
 * ``api_gateway/compliance/routes.py`` — plain snake_case, no camelCase
 * aliasing (unlike the knowledge/twin endpoints).
 */

export type ComplianceRegime = 'UKCA' | 'CE' | 'FCC' | 'PSTI';

export type EvidenceStatus = 'MISSING' | 'UPLOADED' | 'REVIEWED' | 'APPROVED';

export type EvidenceType =
  | 'TEST_REPORT'
  | 'DECLARATION'
  | 'CERTIFICATE'
  | 'TECHNICAL_FILE'
  | 'RISK_ASSESSMENT';

/** A single requirement within a compliance checklist (``ChecklistItem``). */
export interface ChecklistItem {
  id: string;
  regime: ComplianceRegime;
  category: string;
  requirement: string;
  standard: string;
  evidence_type: EvidenceType;
  evidence_status: EvidenceStatus;
  evidence_work_product_id: string | null;
  notes: string;
}

/** Response from ``GET /v1/compliance/{project_id}/checklist``. */
export interface Checklist {
  project_id: string;
  target_markets: string[];
  total_items: number;
  evidenced_items: number;
  coverage_percent: number;
  items: ChecklistItem[];
}

/** Response from ``GET /v1/compliance/{project_id}/coverage``. */
export interface Coverage {
  project_id: string;
  total_items: number;
  evidenced_items: number;
  coverage_percent: number;
}

/** A piece of evidence linked to a checklist item (``EvidenceResponse``). */
export interface Evidence {
  id: string;
  checklist_item_id: string;
  evidence_type: EvidenceType;
  status: string;
  title: string;
  description: string;
  uploaded_at: string;
}

/** Payload for ``POST /v1/compliance/{project_id}/evidence``. */
export interface LinkEvidencePayload {
  checklist_item_id: string;
  evidence_type: EvidenceType;
  title: string;
  description?: string;
  work_product_id?: string;
}
