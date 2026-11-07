/**
 * The Imagine stage (FORGE-820): the per-product studio that captures intent
 * and concept, hosted by Create, and the handover that seeds the twin.
 *
 * Shapes follow the shared Imagine contract (gateway routes under
 * `/v1/twin/projects/{project_id}/imagine`). Do not rename a field here
 * without changing the gateway too.
 */

export type StudioStatus = 'none' | 'draft' | 'handed_over';

export interface HandoverRecord {
  revision?: number;
  at?: string;
  by?: string;
  created?: string[];
  [key: string]: unknown;
}

export interface ImagineStudio {
  project_id: string;
  status: StudioStatus;
  revision: number | null;
  config_ref: string | null;
  /** The engine page for this product, loaded into a sandboxed frame. */
  engine_url: string | null;
  config: Record<string, unknown> | null;
  updated_at: string | null;
  handover: HandoverRecord | null;
  /**
   * The twin-backed state of each studio collection, sent to the page as
   * `fg:snapshot`. Optional: a gateway that does not send it gives the page
   * an empty snapshot and the page starts from its embedded config.
   */
  collections?: Record<string, unknown>;
}

/** The collections the page writes, as named in its `fg:write` messages. */
export const STUDIO_COLLECTIONS = [
  'choices',
  'scenarios',
  'contextEdits',
  'assumptions',
  'requirements',
  'ledger',
  'screening',
] as const;
export type StudioCollection = (typeof STUDIO_COLLECTIONS)[number];

/** The event kinds `POST /imagine/events` accepts. */
export type ImagineEventKind =
  | 'choice'
  | 'scenario'
  | 'context_edit'
  | 'assumption_confirmed'
  | 'requirement'
  | 'ledger'
  | 'screening';

export interface ImagineEvent {
  event_id: string;
  kind: ImagineEventKind;
  data: Record<string, unknown>;
}

export interface ImagineEventReply {
  accepted: boolean;
  mapped_to: string[];
}

export type HandoverItemStatus = 'done' | 'missing' | 'assumed';

export interface HandoverItem {
  id: string;
  label: string;
  status: HandoverItemStatus;
  detail: string;
}

export interface HandoverChecklist {
  ready: boolean;
  items: HandoverItem[];
}

export interface HandoverReply {
  handed_over: boolean;
  revision: number;
  created: string[];
}
