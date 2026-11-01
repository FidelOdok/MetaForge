import type { HarnessTool } from '@/api/endpoints/harness';

// Cloud adapters: API services a gateway can use in place of a local tool
// container. Each registers its tools only when its key is set on the
// gateway, so "connected" is read from the gateway's own tool list, never
// from a flag the browser keeps.

export interface CloudAdapter {
  /** Tool-id prefix the adapter registers under, e.g. `meshy` for `meshy.*`. */
  id: string;
  name: string;
  purpose: string;
  /** The local tooling this stands in for on a gateway without containers. */
  replaces: string;
  envVar: string;
}

export const CLOUD_ADAPTERS: CloudAdapter[] = [
  {
    id: 'meshy',
    name: 'Meshy AI',
    purpose: 'Text or image to 3D mesh (GLB, STL, 3MF)',
    replaces: 'Blender / FreeCAD mesh authoring',
    envVar: 'MESHY_API_KEY',
  },
];

export interface CloudAdapterStatus extends CloudAdapter {
  connected: boolean;
  toolCount: number;
}

export function cloudAdapterStatus(tools: HarnessTool[]): CloudAdapterStatus[] {
  return CLOUD_ADAPTERS.map((adapter) => {
    const toolCount = tools.filter((t) => t.server === adapter.id).length;
    return { ...adapter, connected: toolCount > 0, toolCount };
  });
}
