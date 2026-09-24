import apiClient from '@/api/client';

// ---------------------------------------------------------------------------
// Harness credential + selection writes (api_gateway/harness/routes.py).
// Powers the Settings page "Bring your own API key" panel.
//
// When the gateway sets METAFORGE_HARNESS_ADMIN_TOKEN these routes require a
// matching `X-MetaForge-Admin` header; the optional `adminToken` is sent only
// for the one request and never stored.
// ---------------------------------------------------------------------------

function adminHeaders(adminToken?: string): Record<string, string> | undefined {
  const token = adminToken?.trim();
  return token ? { 'X-MetaForge-Admin': token } : undefined;
}

/** POST /harness/credentials: store an API key for a provider on the gateway. */
export async function saveProviderKey(
  provider: string,
  apiKey: string,
  adminToken?: string,
): Promise<void> {
  await apiClient.post(
    '/harness/credentials',
    { provider, method: 'api_key', api_key: apiKey },
    { headers: adminHeaders(adminToken) },
  );
}

/** DELETE /harness/credentials/{provider}: forget the stored key. */
export async function removeProviderKey(provider: string, adminToken?: string): Promise<void> {
  await apiClient.delete(`/harness/credentials/${encodeURIComponent(provider)}`, {
    headers: adminHeaders(adminToken),
  });
}

/** PUT /harness/selection: make a provider (and optional model) active. */
export async function selectProviderModel(
  provider: string,
  model: string,
  adminToken?: string,
): Promise<void> {
  await apiClient.put(
    '/harness/selection',
    { provider, model: model.trim() || null },
    { headers: adminHeaders(adminToken) },
  );
}
