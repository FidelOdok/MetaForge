import apiClient from '../client';
import type { ModelManifest } from '../../types/viewer';

interface ConversionResult {
  hash: string;
  glb_url: string;
  metadata: {
    parts: ModelManifest['parts'];
    materials: ModelManifest['materials'];
    stats: ModelManifest['stats'];
  };
  cached: boolean;
}

export async function uploadStep(file: File, quality = 'standard'): Promise<ConversionResult> {
  const form = new FormData();
  form.append('file', file);
  const { data } = await apiClient.post<ConversionResult>(`/convert?quality=${quality}`, form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120_000,
  });
  return data;
}

export async function getConversionResult(hash: string, quality = 'standard'): Promise<ConversionResult> {
  const { data } = await apiClient.get<ConversionResult>(`/convert/${hash}?quality=${quality}`);
  return data;
}

export function getGlbUrl(hash: string, quality = 'standard'): string {
  return `/api/v1/convert/${hash}/glb?quality=${quality}`;
}

// ── Mock fallback for development (no backend needed) ──────────────
const MOCK_MANIFEST: ModelManifest = {
  parts: [
    {
      name: 'Base Plate',
      meshName: 'mesh_0',
      children: [],
      boundingBox: { min: [-25, -15, -2.5], max: [25, 15, 2.5] },
    },
    {
      name: 'Support Bracket',
      meshName: 'mesh_1',
      children: [],
      boundingBox: { min: [-5, -5, 0], max: [5, 5, 30] },
    },
    {
      name: 'Top Cap',
      meshName: 'mesh_2',
      children: [],
      boundingBox: { min: [-10, -10, 28], max: [10, 10, 32] },
    },
  ],
  meshToNodeMap: {
    mesh_0: 'node-001',
    mesh_1: 'node-007',
    mesh_2: 'node-010',
  },
  materials: [
    { name: 'Aluminum 6061', color: '#b0b0b0' },
    { name: 'ABS Plastic', color: '#2a2a2a' },
  ],
  stats: { triangleCount: 2400, fileSize: 48000 },
};

export function getMockManifest(): ModelManifest {
  return MOCK_MANIFEST;
}

export function getMockGlbUrl(): string {
  return '/models/sample-assembly.glb';
}
