import apiClient from '../client';
import { resolveGatewayHref } from '../../lib/gatewayConfig';
import type {
  HandoverChecklist,
  HandoverItem,
  HandoverReply,
  ImagineEvent,
  ImagineEventReply,
  ImagineStudio,
  ValidationReport,
} from '../../types/imagine';

/**
 * The Imagine stage (FORGE-820, gateway routes under
 * `/v1/twin/projects/{id}/imagine`): the studio, the events the studio page
 * writes, the handover checklist, the handover itself and rework.
 */

const enc = encodeURIComponent;
const base = (projectId: string) => `/twin/projects/${enc(projectId)}/imagine`;

export const imagineKeys = {
  all: ['imagine'] as const,
  studio: (projectId: string) => ['imagine', projectId, 'studio'] as const,
  handover: (projectId: string) => ['imagine', projectId, 'handover'] as const,
  validation: (projectId: string) => ['imagine', projectId, 'validation'] as const,
};

/** `GET .../imagine/studio`. */
export async function getStudio(projectId: string, signal?: AbortSignal): Promise<ImagineStudio> {
  const { data } = await apiClient.get<ImagineStudio>(`${base(projectId)}/studio`, { signal });
  return data;
}

/** `PUT .../imagine/studio`: a new studio revision from a product config. */
export async function putStudio(projectId: string, config: Record<string, unknown>): Promise<ImagineStudio> {
  const { data } = await apiClient.put<ImagineStudio>(`${base(projectId)}/studio`, { config });
  return data;
}

/** `POST .../imagine/events`: one write from the studio page, idempotent per `event_id`. */
export async function postImagineEvent(projectId: string, event: ImagineEvent): Promise<ImagineEventReply> {
  const { data } = await apiClient.post<ImagineEventReply>(`${base(projectId)}/events`, event);
  return data;
}

/** `GET .../imagine/handover`: the checklist from Imagine to Develop. */
export async function getHandover(projectId: string, signal?: AbortSignal): Promise<HandoverChecklist> {
  const { data } = await apiClient.get<HandoverChecklist>(`${base(projectId)}/handover`, { signal });
  return data;
}

/**
 * `GET .../imagine/validation`: what is left and who clears it, for the head
 * revision. The verification service decides it. When that service cannot be
 * reached the report says so (`blocked_external`) instead of failing.
 */
export async function getValidation(projectId: string, signal?: AbortSignal): Promise<ValidationReport> {
  const { data } = await apiClient.get<ValidationReport>(`${base(projectId)}/validation`, { signal });
  return data;
}

/** `POST .../imagine/handover`: 409 with the missing items when not ready. */
export async function postHandover(projectId: string, opId: string): Promise<HandoverReply> {
  const { data } = await apiClient.post<HandoverReply>(`${base(projectId)}/handover`, { op_id: opId });
  return data;
}

/** `POST .../imagine/rework`: reopen Imagine on a new studio revision. */
export async function postRework(projectId: string, opId: string, reason: string): Promise<ImagineStudio> {
  const { data } = await apiClient.post<ImagineStudio>(`${base(projectId)}/rework`, { op_id: opId, reason });
  return data;
}

/** The engine page's address, loadable from the browser (a gateway path made absolute). */
export function engineHref(studio: Pick<ImagineStudio, 'engine_url'>): string | null {
  return studio.engine_url ? resolveGatewayHref(studio.engine_url) : null;
}

function isItem(x: unknown): x is HandoverItem {
  return !!x && typeof x === 'object' && typeof (x as HandoverItem).id === 'string' && typeof (x as HandoverItem).label === 'string';
}

/**
 * The missing items of a refused handover (409), or null for any other
 * failure. The body may carry the items directly, as a checklist, or as
 * `missing`; only items that are not done are returned.
 */
export function handoverMissing(error: unknown): HandoverItem[] | null {
  const response = (error as { response?: { status?: number; data?: unknown } })?.response;
  if (response?.status !== 409) return null;
  const body = response.data as Record<string, unknown> | unknown[] | undefined;
  const detail = body && !Array.isArray(body) && 'detail' in body ? body.detail : body;
  let list: unknown[] = [];
  if (Array.isArray(detail)) list = detail;
  else if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>;
    if (Array.isArray(d.missing)) list = d.missing;
    else if (Array.isArray(d.items)) list = d.items;
  }
  return list.filter(isItem).filter((i) => i.status !== 'done');
}

/** A fresh id for an event or an operation. */
export function newId(prefix: string): string {
  const uuid =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
  return `${prefix}-${uuid}`;
}
