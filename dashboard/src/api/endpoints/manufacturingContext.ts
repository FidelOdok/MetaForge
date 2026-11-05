import apiClient from '../client';

/**
 * The manufacturing context, read only, for what Create offers as choices:
 * `GET /v1/twin/projects/{id}/manufacturing-context`.
 */

interface ContextField {
  value?: unknown;
  authority?: string;
}

export interface ManufacturingContextView {
  ref: string;
  record: Record<string, ContextField | undefined>;
}

export async function getManufacturingContext(projectId: string, signal?: AbortSignal): Promise<ManufacturingContextView> {
  const { data } = await apiClient.get<ManufacturingContextView>(
    `/twin/projects/${encodeURIComponent(projectId)}/manufacturing-context`,
    { signal },
  );
  return data;
}

/** The material the context names, then its candidates, without repeats. Never invents one. */
export function materialChoices(view: ManufacturingContextView | null | undefined): { name: string; stated: boolean }[] {
  const out: { name: string; stated: boolean }[] = [];
  const add = (raw: unknown, stated: boolean) => {
    const name = typeof raw === 'string' ? raw.trim() : '';
    if (name && !out.some((m) => m.name.toLowerCase() === name.toLowerCase())) out.push({ name, stated });
  };
  const selected = view?.record?.selected_material;
  if (selected && selected.authority !== 'unknown') add(selected.value, selected.authority === 'user_stated' || selected.authority === 'decision');
  const candidates = view?.record?.material_candidates?.value;
  if (Array.isArray(candidates)) candidates.forEach((c) => add(c, false));
  return out;
}
