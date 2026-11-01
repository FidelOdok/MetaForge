import { useTwinNode } from '../../hooks/use-twin';
import { PreviewHost } from './PreviewHost';
import { PC } from './tokens';

/**
 * FORGE-531: the project page's on-demand preview of one work product row.
 * Loaded lazily (with the registry and engines) only when a row's Preview
 * toggle is first opened, then renders the same PreviewHost the twin
 * inspector and full-screen modal use, in compact form.
 */
export function ProjectRowPreview({ nodeId }: { nodeId: string }) {
  const { data: node, isLoading, isError } = useTwinNode(nodeId);
  if (isLoading) {
    return <div className="font-mono" style={{ fontSize: 11, color: PC.onSurfaceVariant, padding: 12 }}>Loading preview…</div>;
  }
  if (isError || !node) {
    return (
      <div className="font-mono" style={{ fontSize: 11, color: PC.onSurfaceVariant, padding: 12 }}>
        This work product is not in the digital twin, so there is nothing to preview.
      </div>
    );
  }
  return <PreviewHost node={node} mode="compact" />;
}
