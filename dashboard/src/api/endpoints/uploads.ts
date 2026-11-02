import apiClient from '../client';
import { resolveGatewayHref } from '../../lib/gatewayConfig';

/** One file in a staged upload. Mirrors `api_gateway.uploads.store.StagedFile`. */
export interface StagedFile {
  name: string;
  size: number;
  sha256: string;
  format: string;
  wp_type: string;
  domain: string;
  error: string | null;
  work_product_id: string | null;
}

export type UploadStatus = 'open' | 'pending' | 'approved' | 'rejected' | 'expired';

export interface StagedUpload {
  id: string;
  status: UploadStatus;
  source: 'inline' | 'link' | 'local' | 'dashboard' | 'api';
  project_id: string | null;
  description: string;
  requested_by: string | null;
  agent: string | null;
  files: StagedFile[];
  created_at: string;
  expires_at: string | null;
}

export interface CreateUploadOptions {
  projectId?: string | null;
  description?: string;
  /** Import now. The person picking the files in the dashboard is the approval. */
  approve?: boolean;
  /** File name to work product type, for a file whose extension says too little. */
  wpTypes?: Record<string, string>;
}

/** A file and the path it had inside a dropped folder. */
export interface PathedFile {
  file: File;
  path: string;
}

/** Every file the person picked. A file from a folder keeps its relative path. */
export async function createUpload(
  files: Array<File | PathedFile>,
  options: CreateUploadOptions = {},
): Promise<StagedUpload> {
  const form = new FormData();
  for (const item of files) {
    const file = item instanceof File ? item : item.file;
    const rel =
      item instanceof File
        ? (item as File & { webkitRelativePath?: string }).webkitRelativePath
        : item.path;
    form.append('files', file, rel || file.name);
  }
  if (options.projectId) form.append('project_id', options.projectId);
  if (options.description) form.append('description', options.description);
  if (options.wpTypes) form.append('wp_types', JSON.stringify(options.wpTypes));
  form.append('approve', options.approve ? 'true' : 'false');
  const { data } = await apiClient.post<StagedUpload>('/uploads', form, {
    headers: { 'X-MetaForge-Surface': 'dashboard' },
  });
  return data;
}

export async function listUploads(params: { projectId?: string; status?: UploadStatus } = {}) {
  const { data } = await apiClient.get<StagedUpload[]>('/uploads', {
    params: { project_id: params.projectId, status: params.status },
  });
  return data;
}

/**
 * Open a staged file in a new tab for review. Fetched with the client so the
 * gateway's auth applies; anything the gateway marks as a download downloads.
 */
export async function openStagedFile(uploadId: string, name: string): Promise<void> {
  const path = name.split('/').map(encodeURIComponent).join('/');
  const { data, headers } = await apiClient.get<Blob>(
    `/uploads/${encodeURIComponent(uploadId)}/files/${path}`,
    { responseType: 'blob' },
  );
  const url = URL.createObjectURL(data);
  const disposition = String(headers['content-disposition'] ?? '');
  if (disposition.startsWith('attachment')) {
    const a = document.createElement('a');
    a.href = url;
    a.download = name.split('/').pop() ?? 'file';
    a.click();
  } else {
    window.open(url, '_blank', 'noopener');
  }
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export interface DownloadLink {
  work_product_id: string;
  name: string;
  size: number;
  via: 'gateway' | 'object_store';
  url: string | null;
  path: string | null;
  expires_at: string;
}

/**
 * A signed link to a work product's file that works without signing in, for
 * a person to share. Absolute, so it can be pasted anywhere.
 */
export async function createDownloadLink(
  workProductId: string,
  path = '',
): Promise<{ url: string; link: DownloadLink }> {
  const { data } = await apiClient.post<DownloadLink>(
    `/files/${encodeURIComponent(workProductId)}/link`,
    { path },
  );
  const href = data.url ?? resolveGatewayHref(data.path ?? '');
  const url = /^[a-z][a-z0-9+.-]*:/i.test(href) ? href : `${window.location.origin}${href}`;
  return { url, link: data };
}
