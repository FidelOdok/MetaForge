import { useMemo } from 'react';
import { Box } from 'lucide-react';
import { useViewerStore } from '../../store/viewer-store';
import type { AssemblyPart } from '../../types/twin';
import type { PartTreeNode } from '../../types/viewer';

/** "L x W x H mm" of a part's position box, largest extent first, or null. */
export function partDimensions(part: AssemblyPart): string | null {
  const box = part.position_bbox_mm;
  if (!box || !box.min || !box.max) return null;
  const ext = [0, 1, 2].map((i) => Math.abs((box.max[i] ?? NaN) - (box.min[i] ?? NaN)));
  if (ext.some((v) => !Number.isFinite(v))) return null;
  return `${ext
    .sort((a, b) => b - a)
    .map((v) => Number(v.toFixed(1)))
    .join(' x ')} mm`;
}

function findMesh(nodes: PartTreeNode[], name: string): string | null {
  for (const n of nodes) {
    if (n.name === name) return n.meshName;
    const inner = findMesh(n.children, name);
    if (inner) return inner;
  }
  return null;
}

interface Props {
  parts: AssemblyPart[];
  /** Also open the part's own cad_model node in the inspector (optional). */
  onOpenPart?: (nodeId: string) => void;
}

/**
 * FORGE-511: the part tree of an assembly cad_model (metadata.parts), with
 * per-part material and dimensions. Selecting a part highlights it here and
 * in the 3D viewer when the loaded model has a mesh of that name.
 */
export function AssemblyPartTree({ parts, onOpenPart }: Props) {
  const manifest = useViewerStore((s) => s.manifest);
  const selectedMeshName = useViewerStore((s) => s.selectedMeshName);
  const selectPart = useViewerStore((s) => s.selectPart);

  const meshByPart = useMemo(() => {
    const out = new Map<string, string>();
    for (const p of parts) {
      out.set(p.node_id, (manifest && findMesh(manifest.parts, p.name)) || p.name);
    }
    return out;
  }, [parts, manifest]);

  return (
    <div className="tw-assembly-tree" role="tree" aria-label="Assembly parts">
      {parts.map((part) => {
        const mesh = meshByPart.get(part.node_id) ?? part.name;
        const selected = selectedMeshName === mesh;
        const dims = partDimensions(part);
        return (
          <button
            key={part.node_id}
            role="treeitem"
            aria-selected={selected}
            data-selected={selected ? 'true' : undefined}
            onClick={() => {
              selectPart(selected ? null : mesh);
              if (!selected) onOpenPart?.(part.node_id);
            }}
          >
            <Box size={16} />
            {part.name}
            <small>{part.material || 'Material unspecified'}</small>
            {dims && <small>{dims}</small>}
          </button>
        );
      })}
    </div>
  );
}
