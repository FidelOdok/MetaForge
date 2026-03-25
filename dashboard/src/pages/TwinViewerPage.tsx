import { useState, useRef, useCallback, useEffect } from 'react';
import { Upload, Box, GitBranch, ChevronDown, Eye, Loader2, FileInput, X } from 'lucide-react';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { SkeletonCard } from '../components/ui/Skeleton';
import { formatRelativeTime } from '../utils/format-time';
import { useTwinNodes, useTwinNode } from '../hooks/use-twin';
import { useScopedChat } from '../hooks/use-scoped-chat';
import { NodeChatPanel } from '../components/chat/integrations/NodeChatPanel';
import { R3FViewer } from '../components/viewer/R3FViewer';
import { ComponentTree } from '../components/viewer/ComponentTree';
import { BomAnnotationPanel } from '../components/viewer/BomAnnotationPanel';
import { ExplodedViewControls } from '../components/viewer/ExplodedViewControls';
import { useViewerStore } from '../store/viewer-store';
import { useUploadAndConvert } from '../hooks/use-conversion';
import { getMockManifest, getMockGlbUrl } from '../api/endpoints/convert';
import { getNodeModel } from '../api/endpoints/twin';
import type { TwinNode } from '../types/twin';
import type { ModelManifest, PartInfo, PartTreeNode } from '../types/viewer';
import { ImportZone } from '../components/ImportZone';

const TYPE_ICONS: Record<TwinNode['type'], string> = {
  work_product: '\uD83D\uDCC4',
  constraint: '\uD83D\uDD12',
  relationship: '\uD83D\uDD17',
  version: '\uD83C\uDFF7\uFE0F',
};

function NodeDetail({ node }: { node: TwinNode }) {
  const chat = useScopedChat({
    scopeKind: 'digital-twin-node',
    entityId: node.id,
    label: node.name,
  });
  const loadModel = useViewerStore((s) => s.loadModel);
  const setViewMode = useViewerStore((s) => s.setViewMode);
  const [loading3d, setLoading3d] = useState(false);

  const isCAD = node.properties.wp_type === 'cad_model';

  const handleView3D = useCallback(async () => {
    setLoading3d(true);
    try {
      const result = await getNodeModel(node.id);
      const manifest: ModelManifest = {
        parts: result.metadata.parts.map((p) => ({
          name: p.name,
          meshName: p.meshName ?? p.name,
          children: (p.children ?? []) as ModelManifest['parts'],
          boundingBox: p.boundingBox as PartTreeNode['boundingBox'],
        })),
        meshToNodeMap: {},
        materials: result.metadata.materials ?? [],
        stats: result.metadata.stats ?? { triangleCount: 0, fileSize: 0 },
      };
      // Prepend /api so the URL goes through the Vite dev proxy
      const glbUrl = result.glb_url.startsWith('/v1/')
        ? `/api${result.glb_url}`
        : result.glb_url;
      loadModel(glbUrl, manifest);
      setViewMode('3d');
    } catch (err) {
      console.error('Failed to load 3D model:', err);
    } finally {
      setLoading3d(false);
    }
  }, [node.id, loadModel, setViewMode]);

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center gap-2">
          <span className="text-lg">{TYPE_ICONS[node.type]}</span>
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">
            {node.name}
          </h3>
          <StatusBadge status={node.status} />
        </div>
        <div className="mt-1 text-xs text-zinc-400">
          {node.domain} &middot; {node.type} &middot; Updated {formatRelativeTime(node.updatedAt)}
        </div>
      </div>

      {isCAD && (
        <Button
          variant="primary"
          onClick={handleView3D}
          disabled={loading3d}
        >
          <Eye size={14} className="mr-1.5" />
          {loading3d ? 'Converting...' : 'View 3D Model'}
        </Button>
      )}

      <Card>
        <h4 className="mb-2 text-sm font-medium text-zinc-700 dark:text-zinc-300">
          Properties
        </h4>
        <dl className="grid grid-cols-2 gap-2">
          {Object.entries(node.properties).map(([key, value]) => (
            <div key={key}>
              <dt className="text-xs text-zinc-500">{key}</dt>
              <dd className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                {String(value)}
              </dd>
            </div>
          ))}
        </dl>
      </Card>

      <NodeChatPanel
        nodeId={node.id}
        nodeName={node.name}
        thread={chat.thread}
        messages={chat.messages}
        isTyping={chat.isTyping}
        onSendMessage={chat.sendMessage}
        onCreateThread={chat.createThread}
      />
    </div>
  );
}

function GraphView() {
  const { data: nodes, isLoading, isError, refetch } = useTwinNodes();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data: selectedNode } = useTwinNode(selectedId ?? undefined);

  if (isLoading) {
    return (
      <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
        <SkeletonCard />
      </div>
    );
  }

  if (isError) {
    return (
      <Card className="flex flex-col items-center py-12 text-center">
        <p className="text-base font-medium text-red-600 dark:text-red-400">
          Failed to load twin graph
        </p>
        <p className="mt-1 text-sm text-zinc-500">
          There was a problem fetching Digital Twin nodes.
        </p>
        <Button variant="secondary" className="mt-4" onClick={() => void refetch()}>
          Retry
        </Button>
      </Card>
    );
  }

  const items = nodes ?? [];

  if (items.length === 0) {
    return (
      <EmptyState
        title="Empty twin"
        description="Digital Twin nodes will appear here when work_products are created."
      />
    );
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_1fr]">
      <Card className="max-h-[calc(100vh-12rem)] overflow-y-auto p-0">
        <ul className="divide-y divide-zinc-100 dark:divide-zinc-800">
          {items.map((node) => (
            <li key={node.id}>
              <button
                type="button"
                onClick={() => setSelectedId(node.id)}
                className={`flex w-full items-center gap-3 px-4 py-3 text-left transition-colors hover:bg-zinc-50 dark:hover:bg-zinc-800 ${
                  selectedId === node.id ? 'bg-blue-50 dark:bg-blue-900/20' : ''
                }`}
              >
                <span>{TYPE_ICONS[node.type]}</span>
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-zinc-900 dark:text-zinc-100">
                    {node.name}
                  </div>
                  <div className="text-xs text-zinc-400">
                    {node.domain} &middot; {node.type}
                  </div>
                </div>
                <StatusBadge status={node.status} />
              </button>
            </li>
          ))}
        </ul>
      </Card>

      <div>
        {selectedNode ? (
          <NodeDetail node={selectedNode} />
        ) : (
          <Card className="flex min-h-[200px] items-center justify-center">
            <p className="text-sm text-zinc-400">Select a node to view details</p>
          </Card>
        )}
      </div>
    </div>
  );
}

type ConversionPhase = 'idle' | 'uploading' | 'converting' | 'loading';

const PHASE_LABELS: Record<ConversionPhase, string> = {
  idle: 'Upload STEP',
  uploading: 'Uploading...',
  converting: 'Converting...',
  loading: 'Loading model...',
};

function ConversionProgressIndicator({ phase }: { phase: ConversionPhase }) {
  const steps: { key: ConversionPhase; label: string }[] = [
    { key: 'uploading', label: 'Upload' },
    { key: 'converting', label: 'Convert' },
    { key: 'loading', label: 'Load' },
  ];
  const activeIdx = steps.findIndex((s) => s.key === phase);

  return (
    <div className="flex items-center gap-1.5">
      {steps.map((step, idx) => {
        const isActive = idx === activeIdx;
        const isDone = idx < activeIdx;
        return (
          <div key={step.key} className="flex items-center gap-1.5">
            <div className="flex items-center gap-1">
              <div
                className={`h-1.5 w-1.5 rounded-full transition-colors ${
                  isActive
                    ? 'bg-blue-500'
                    : isDone
                    ? 'bg-green-500'
                    : 'bg-zinc-300 dark:bg-zinc-600'
                }`}
              />
              <span
                className={`text-xs ${
                  isActive
                    ? 'font-medium text-blue-600 dark:text-blue-400'
                    : isDone
                    ? 'text-green-600 dark:text-green-400'
                    : 'text-zinc-400'
                }`}
              >
                {step.label}
              </span>
            </div>
            {idx < steps.length - 1 && (
              <div className="h-px w-3 bg-zinc-200 dark:bg-zinc-600" />
            )}
          </div>
        );
      })}
    </div>
  );
}

function ViewerToolbar({ onToggleImport, importOpen }: { onToggleImport: () => void; importOpen: boolean }) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploadMutation = useUploadAndConvert();
  const viewMode = useViewerStore((s) => s.viewMode);
  const setViewMode = useViewerStore((s) => s.setViewMode);
  const loadModel = useViewerStore((s) => s.loadModel);
  const glbUrl = useViewerStore((s) => s.glbUrl);
  const [quality, setQuality] = useState('standard');
  const [conversionPhase, setConversionPhase] = useState<ConversionPhase>('idle');

  // Advance through phases as the mutation progresses
  useEffect(() => {
    if (!uploadMutation.isPending) {
      // Brief "loading" phase after success before resetting
      if (uploadMutation.isSuccess && conversionPhase === 'converting') {
        setConversionPhase('loading');
        const t = setTimeout(() => setConversionPhase('idle'), 800);
        return () => clearTimeout(t);
      }
      if (!uploadMutation.isSuccess) {
        setConversionPhase('idle');
      }
    }
  }, [uploadMutation.isPending, uploadMutation.isSuccess, conversionPhase]);

  const handleUpload = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        setConversionPhase('uploading');
        // Simulate phase progression: uploading → converting mid-flight
        const t = setTimeout(() => setConversionPhase('converting'), 1200);
        uploadMutation.mutate(
          { file, quality },
          {
            onSettled: () => clearTimeout(t),
          },
        );
      }
    },
    [uploadMutation, quality],
  );

  const handleLoadMock = useCallback(() => {
    loadModel(getMockGlbUrl(), getMockManifest());
  }, [loadModel]);

  return (
    <div className="flex items-center gap-3">
      {/* Import toggle */}
      <button
        type="button"
        onClick={onToggleImport}
        className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors ${
          importOpen
            ? 'border-blue-300 bg-blue-50 text-blue-700 dark:border-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
            : 'border-zinc-200 text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800'
        }`}
      >
        <FileInput size={14} />
        Import
      </button>

      {/* View mode toggle */}
      <div className="flex rounded-lg border border-zinc-200 dark:border-zinc-700">
        <button
          type="button"
          onClick={() => setViewMode('3d')}
          className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium transition-colors ${
            viewMode === '3d'
              ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
              : 'text-zinc-600 hover:bg-zinc-50 dark:text-zinc-400 dark:hover:bg-zinc-800'
          }`}
        >
          <Box size={14} />
          3D Model
        </button>
        <button
          type="button"
          onClick={() => setViewMode('graph')}
          className={`flex items-center gap-1.5 border-l border-zinc-200 px-3 py-1.5 text-xs font-medium transition-colors dark:border-zinc-700 ${
            viewMode === 'graph'
              ? 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400'
              : 'text-zinc-600 hover:bg-zinc-50 dark:text-zinc-400 dark:hover:bg-zinc-800'
          }`}
        >
          <GitBranch size={14} />
          Graph
        </button>
      </div>

      {viewMode === '3d' && (
        <>
          {/* Quality selector */}
          <div className="relative">
            <select
              value={quality}
              onChange={(e) => setQuality(e.target.value)}
              className="appearance-none rounded-lg border border-zinc-200 bg-white px-3 py-1.5 pr-7 text-xs font-medium text-zinc-700 dark:border-zinc-700 dark:bg-zinc-800 dark:text-zinc-300"
            >
              <option value="preview">Preview</option>
              <option value="standard">Standard</option>
              <option value="fine">Fine</option>
            </select>
            <ChevronDown size={12} className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-zinc-400" />
          </div>

          {/* Upload button / progress indicator */}
          {conversionPhase !== 'idle' ? (
            <div className="flex items-center gap-2 rounded-lg border border-zinc-200 px-3 py-1.5 dark:border-zinc-700">
              <Loader2 size={13} className="animate-spin text-blue-500" />
              <ConversionProgressIndicator phase={conversionPhase} />
            </div>
          ) : (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadMutation.isPending}
              className="flex items-center gap-1.5 rounded-lg border border-zinc-200 px-3 py-1.5 text-xs font-medium text-zinc-700 transition-colors hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
            >
              <Upload size={14} />
              {PHASE_LABELS.idle}
            </button>
          )}
          <input
            ref={fileInputRef}
            type="file"
            accept=".step,.stp,.iges,.igs"
            className="hidden"
            onChange={handleUpload}
          />

          {/* Load mock button (dev only) */}
          {!glbUrl && (
            <button
              type="button"
              onClick={handleLoadMock}
              className="rounded-lg border border-dashed border-zinc-300 px-3 py-1.5 text-xs text-zinc-400 transition-colors hover:border-zinc-400 hover:text-zinc-500 dark:border-zinc-600 dark:hover:border-zinc-500"
            >
              Load Demo Model
            </button>
          )}
        </>
      )}
    </div>
  );
}

export function TwinViewerPage() {
  const viewMode = useViewerStore((s) => s.viewMode);
  const manifest = useViewerStore((s) => s.manifest);
  const glbUrl = useViewerStore((s) => s.glbUrl);
  const selectPart = useViewerStore((s) => s.selectPart);
  const selectedMeshName = useViewerStore((s) => s.selectedMeshName);
  const { data: nodes } = useTwinNodes();
  const [importOpen, setImportOpen] = useState(false);

  const handlePartClick = useCallback(
    (part: PartInfo) => {
      selectPart(part.meshName);
    },
    [selectPart],
  );

  const items = nodes ?? [];

  return (
    <div className="flex h-[calc(100vh-4rem)] flex-col">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3 dark:border-zinc-700">
        <div>
          <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
            Digital Twin Viewer
          </h2>
          <p className="text-sm text-zinc-500">{items.length} nodes in the design graph</p>
        </div>
        <ViewerToolbar
          onToggleImport={() => setImportOpen((prev) => !prev)}
          importOpen={importOpen}
        />
      </div>

      {/* Import panel — slides in below the header when open */}
      {importOpen && (
        <div className="border-b border-zinc-200 bg-white px-4 py-4 dark:border-zinc-700 dark:bg-zinc-900">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-sm font-medium text-zinc-700 dark:text-zinc-300">
              Import Work Product
            </span>
            <button
              type="button"
              aria-label="Close import panel"
              onClick={() => setImportOpen(false)}
              className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200"
            >
              <X size={16} />
            </button>
          </div>
          <ImportZone onSuccess={() => setImportOpen(false)} />
        </div>
      )}

      {/* Content */}
      {viewMode === 'graph' ? (
        <div className="flex-1 overflow-y-auto p-4">
          <GraphView />
        </div>
      ) : (
        <div className="flex flex-1 overflow-hidden">
          {/* Component Tree Sidebar */}
          {manifest && (
            <div className="w-60 flex-shrink-0 border-r border-zinc-200 dark:border-zinc-700">
              <ComponentTree />
            </div>
          )}

          {/* 3D Viewer (center) */}
          <div className="relative flex-1">
            <R3FViewer onPartClick={handlePartClick} />

            {/* Exploded View Controls overlay */}
            {glbUrl && <ExplodedViewControls />}
          </div>

          {/* BOM Annotation Panel (right) */}
          {selectedMeshName && manifest && (
            <div className="w-80 flex-shrink-0 border-l border-zinc-200 dark:border-zinc-700">
              <BomAnnotationPanel />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
