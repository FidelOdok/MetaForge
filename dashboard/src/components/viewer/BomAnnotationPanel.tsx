import { useMemo } from 'react';
import { ExternalLink, Package } from 'lucide-react';
import { useViewerStore } from '../../store/viewer-store';
import { useBom } from '../../hooks/use-bom';
import { useTwinNode } from '../../hooks/use-twin';
import { useScopedChat } from '../../hooks/use-scoped-chat';
import { StatusBadge } from '../shared/StatusBadge';
import { NodeChatPanel } from '../chat/integrations/NodeChatPanel';

export function BomAnnotationPanel() {
  const selectedMeshName = useViewerStore((s) => s.selectedMeshName);
  const manifest = useViewerStore((s) => s.manifest);

  // Resolve selected mesh → Twin node ID
  const nodeId = useMemo(() => {
    if (!selectedMeshName || !manifest) return undefined;
    return manifest.meshToNodeMap[selectedMeshName];
  }, [selectedMeshName, manifest]);

  const { data: twinNode } = useTwinNode(nodeId);
  const { data: bomComponents } = useBom();

  // Find matching part name from manifest
  const partInfo = useMemo(() => {
    if (!selectedMeshName || !manifest) return null;
    const findPart = (parts: typeof manifest.parts): (typeof manifest.parts)[0] | null => {
      for (const p of parts) {
        if (p.meshName === selectedMeshName) return p;
        const child = findPart(p.children);
        if (child) return child;
      }
      return null;
    };
    return findPart(manifest.parts);
  }, [selectedMeshName, manifest]);

  // Match BOM component by name similarity (heuristic — in production this would use node linkage)
  const bomMatch = useMemo(() => {
    if (!partInfo || !bomComponents) return null;
    const name = partInfo.name.toLowerCase();
    return (
      bomComponents.find((c) => c.description.toLowerCase().includes(name)) ??
      bomComponents.find((c) => name.includes(c.description.toLowerCase())) ??
      null
    );
  }, [partInfo, bomComponents]);

  const chat = useScopedChat({
    scopeKind: 'digital-twin-node',
    entityId: nodeId ?? selectedMeshName ?? '',
    label: partInfo?.name ?? 'Selected Part',
  });

  if (!selectedMeshName || !partInfo) {
    return (
      <div className="flex h-full items-center justify-center p-4">
        <p className="text-xs text-zinc-400">Click a part in the 3D view to see details</p>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto">
      {/* Part header */}
      <div className="border-b border-zinc-200 p-4 dark:border-zinc-700">
        <div className="flex items-center gap-2">
          <Package size={16} className="text-zinc-400" />
          <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            {partInfo.name}
          </h3>
        </div>
        <p className="mt-1 text-xs text-zinc-400">Mesh: {selectedMeshName}</p>
        {twinNode && (
          <div className="mt-2">
            <StatusBadge status={twinNode.status} />
          </div>
        )}
      </div>

      {/* Twin node properties */}
      {twinNode && (
        <div className="border-b border-zinc-200 p-4 dark:border-zinc-700">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
            Properties
          </h4>
          <dl className="space-y-1.5">
            {Object.entries(twinNode.properties).map(([key, value]) => (
              <div key={key} className="flex justify-between text-xs">
                <dt className="text-zinc-500">{key}</dt>
                <dd className="font-medium text-zinc-900 dark:text-zinc-100">{String(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
      )}

      {/* BOM data */}
      {bomMatch ? (
        <div className="border-b border-zinc-200 p-4 dark:border-zinc-700">
          <h4 className="mb-2 text-xs font-semibold uppercase tracking-wider text-zinc-500">
            BOM Entry
          </h4>
          <dl className="space-y-1.5">
            <div className="flex justify-between text-xs">
              <dt className="text-zinc-500">Part Number</dt>
              <dd className="font-mono font-medium text-zinc-900 dark:text-zinc-100">
                {bomMatch.partNumber}
              </dd>
            </div>
            <div className="flex justify-between text-xs">
              <dt className="text-zinc-500">Manufacturer</dt>
              <dd className="font-medium text-zinc-900 dark:text-zinc-100">
                {bomMatch.manufacturer}
              </dd>
            </div>
            <div className="flex justify-between text-xs">
              <dt className="text-zinc-500">Quantity</dt>
              <dd className="font-medium text-zinc-900 dark:text-zinc-100">{bomMatch.quantity}</dd>
            </div>
            <div className="flex justify-between text-xs">
              <dt className="text-zinc-500">Unit Price</dt>
              <dd className="font-medium text-zinc-900 dark:text-zinc-100">
                ${bomMatch.unitPrice.toFixed(2)}
              </dd>
            </div>
            <div className="flex justify-between text-xs">
              <dt className="text-zinc-500">Supply Status</dt>
              <dd>
                <StatusBadge status={bomMatch.status} />
              </dd>
            </div>
          </dl>

          <a
            href={`/bom?highlight=${bomMatch.id}`}
            className="mt-3 flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
          >
            View in BOM
            <ExternalLink size={10} />
          </a>
        </div>
      ) : (
        <div className="border-b border-zinc-200 p-4 dark:border-zinc-700">
          <p className="text-xs text-zinc-400">No BOM entry linked to this part</p>
        </div>
      )}

      {/* Chat panel scoped to node */}
      <div className="flex-1 p-4">
        <NodeChatPanel
          nodeId={nodeId ?? selectedMeshName}
          nodeName={partInfo.name}
          thread={chat.thread}
          messages={chat.messages}
          isTyping={chat.isTyping}
          onSendMessage={chat.sendMessage}
          onCreateThread={chat.createThread}
        />
      </div>
    </div>
  );
}
