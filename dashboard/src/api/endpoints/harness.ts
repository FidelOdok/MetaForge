import apiClient from '@/api/client';

// ---------------------------------------------------------------------------
// Harness capability API — providers, models, tools (MET-548)
// Powers the chat model + tools/connectors selector.
// ---------------------------------------------------------------------------

/* eslint-disable @typescript-eslint/no-explicit-any */

export interface HarnessProvider {
  id: string;
  family: string;
  configured: boolean;
  baseUrl: string | null;
}

export interface HarnessProvidersResult {
  activeProvider: string | null;
  activeModel: string | null;
  providers: HarnessProvider[];
}

export interface HarnessTool {
  id: string;
  name: string;
  server: string;
  capability: string | null;
}

/** GET /harness/providers — registered providers + the active selection. */
export async function getHarnessProviders(): Promise<HarnessProvidersResult> {
  const res = await apiClient.get('/harness/providers');
  const d = res.data ?? {};
  return {
    activeProvider: d.active_provider ?? null,
    activeModel: d.active_model ?? null,
    providers: (d.providers ?? []).map((p: any) => ({
      id: p.id,
      family: p.family,
      configured: !!p.configured,
      baseUrl: p.base_url ?? null,
    })),
  };
}

/** GET /harness/models?provider= — model ids (empty for non-OpenAI families). */
export async function getHarnessModels(provider: string): Promise<string[]> {
  const res = await apiClient.get('/harness/models', { params: { provider } });
  return ((res.data?.models ?? []) as any[]).map((m) => String(m.id));
}

/** GET /harness/tools — MCP tools/connectors reachable via the gateway bridge. */
export async function getHarnessTools(): Promise<HarnessTool[]> {
  const res = await apiClient.get('/harness/tools');
  return ((res.data ?? []) as any[]).map((t) => ({
    id: t.id,
    name: t.name,
    server: t.server,
    capability: t.capability ?? null,
  }));
}
