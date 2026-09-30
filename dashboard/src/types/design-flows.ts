/**
 * Design-flow catalogue, as served by `GET /v1/design-flows` (FORGE-395).
 *
 * These mirror the gateway's response schema, not `spec.py`. That distinction
 * is the point of the ticket: the previous version of this file hand-copied
 * the flows out of the Python under a comment asking people to keep it in
 * sync, and it was not in sync — `design_v1`, the default flow, was missing
 * entirely, so the flow a run gets when none is named could not be picked.
 */

export interface DesignFlowGate {
  name: string;
  autoApprove: boolean;
  criteria: string[];
  enforceConstraints: boolean;
  gateId: string | null;
}

export interface DesignFlowPhase {
  id: string;
  title: string;
  objective: string;
  expectedArtifacts: string[];
  requiredDeliverables: string[];
  enforceDeliverables: boolean;
  disciplines: string[];
  gate: DesignFlowGate | null;
}

export interface DesignFlow {
  id: string;
  /** Full descriptive title. */
  name: string;
  /** Short name for a chooser — served by the gateway, not held here. */
  label: string;
  description: string;
  version: string;
  isDefault: boolean;
  phases: DesignFlowPhase[];
  /** False when the flow breaks a server-enforced invariant (FORGE-397). */
  valid: boolean;
  violations: string[];
}

export interface DesignFlowCatalog {
  flows: DesignFlow[];
  defaultFlowId: string;
}

/** A saved, immutable flow version awaiting or carrying a decision (FORGE-399). */
export interface FlowVersion {
  versionId: string;
  approvalId: string;
  baseTemplateId: string;
  baseVersion: string;
  status: 'proposed' | 'approved' | 'rejected';
  origin: string;
  changes: string[];
  flow: DesignFlow;
  valid: boolean;
  violations: string[];
}

export interface FlowValidation {
  valid: boolean;
  violations: string[];
}

/** The editable shape the canvas sends back. */
export interface EditFlowRequest {
  baseTemplateId: string;
  phases: DesignFlowPhase[];
  name?: string;
}
