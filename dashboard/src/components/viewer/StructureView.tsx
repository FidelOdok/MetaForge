import { Suspense, useMemo, useState } from 'react';
import { Canvas } from '@react-three/fiber';
import { OrbitControls, useGLTF } from '@react-three/drei';
import { Boxes } from 'lucide-react';
import { useHierarchyTree, useRealizeHierarchyNode } from '../../hooks/use-hierarchy';
import { useBom } from '../../hooks/use-bom';
import { iconForHierarchyKind } from '../../utils/wp-icons';
import { DecisionList } from '../shared/DecisionList';
import { ImportZone } from '../ImportZone';
import { Button } from '../ui/Button';
import { useToast } from '../ui/Toast';
import { getNodeModel } from '../../api/endpoints/twin';
import { resolveGatewayHref } from '../../lib/gatewayConfig';
import type { HierarchyNode } from '../../types/hierarchy';
import type { ImportWorkProductResponse } from '../../types/twin';

/**
 * FORGE-261: Structure tab — the product hierarchy tree (product -> system
 * -> subsystem -> assembly), with each node's own rolled-up mass/cost.
 *
 * Deliberately trimmed from the full gap-list vision for this first pass:
 * no 3D cross-highlight, no drag-to-reparent, no context panel, no version
 * compare -- a tree-table with expand/collapse and rollup columns, mirroring
 * how ManufacturingView keeps its own first pass self-contained (no 3D
 * dependency, takes its scope as a prop).
 */

interface TreeNode extends HierarchyNode {
  children: TreeNode[];
}

function buildForest(nodes: HierarchyNode[]): TreeNode[] {
  const byId = new Map<string, TreeNode>(nodes.map((n) => [n.id, { ...n, children: [] }]));
  const roots: TreeNode[] = [];
  for (const node of byId.values()) {
    const parent = node.parentId ? byId.get(node.parentId) : undefined;
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return roots;
}

function formatMass(massKg: number, budgetKg: number | null): string {
  if (massKg <= 0) return '—';
  return budgetKg != null ? `${massKg.toFixed(2)} / ${budgetKg.toFixed(2)} kg` : `${massKg.toFixed(2)} kg`;
}

function formatCost(cost: number, budget: number | null): string {
  if (cost <= 0) return '—';
  return budget != null ? `$${cost.toFixed(2)} / $${budget.toFixed(2)}` : `$${cost.toFixed(2)}`;
}

/** FORGE-313: "owner (discipline)" for a budget-allocation tooltip, or
 * undefined when neither was set (never renders an empty "()"). */
function ownerTitle(owner: string | null, discipline: string | null): string | undefined {
  if (!owner && !discipline) return undefined;
  return discipline ? `Owner: ${owner || 'unassigned'} (${discipline})` : `Owner: ${owner}`;
}

/** FORGE-313: one line per interface, for the interfaces badge's tooltip. */
function interfacesTitle(interfaces: HierarchyNode['interfaces']): string {
  return interfaces
    .map((iface) => {
      const quantities = iface.quantities
        .map((q) => `${q.metric}${q.limit != null ? ` ${q.op} ${q.limit}${q.unit}` : ''}`)
        .join(', ');
      const label = `${iface.otherComponent}${iface.interfaceType ? ` (${iface.interfaceType})` : ''}`;
      return quantities ? `${label}: ${quantities}` : label;
    })
    .join('\n');
}

/** Standalone GLB preview -- unlike R3FViewer, which is tightly bound to the
 * full twin-viewer page's own node-selection/store state, this is a small
 * self-contained Canvas scoped to just this panel (same "own small Canvas"
 * pattern UrdfPreviewPanel.tsx already uses for its own assembly preview). */
function GlbPreview({ glbUrl }: { glbUrl: string }) {
  const { scene } = useGLTF(glbUrl);
  return (
    <Canvas
      camera={{ position: [2, 2, 2], fov: 45 }}
      style={{ height: 180, background: 'var(--mf-c-191b22)', borderRadius: 6 }}
    >
      <ambientLight intensity={0.7} />
      <directionalLight position={[5, 5, 5]} intensity={0.9} />
      <Suspense fallback={null}>
        <primitive object={scene} />
      </Suspense>
      <OrbitControls />
    </Canvas>
  );
}

const FIELD_STYLE: React.CSSProperties = {
  background: 'var(--mf-c-191b22)',
  border: '1px solid var(--mf-r-65-72-90-0p3)',
};

/** FORGE-266 (gap G-C2): "Replace placeholder with part" -- attach or
 * replace one hierarchy node's real geometry. Two paths, not mutually
 * exclusive with each other's data model (a node can have both):
 * - Upload a real STEP file (reuses ImportZone's own POST /v1/twin/import
 *   unchanged -- real extracted bounding box/part-count metadata, then a
 *   real GLB preview via the SAME GET /v1/twin/nodes/{id}/model endpoint
 *   the main twin viewer uses) -> sets REALIZED_BY.
 * - Pick an already-recorded BOMItem (this project's flat BOM,
 *   twin.record_component_selection / twin.select_component) -> sets
 *   INSTANCE_OF. No 3D preview for this path -- a BOMItem's cad_model_url
 *   is a caller-asserted external link, not something this pipeline can
 *   fetch-and-convert; its own real fields (mpn/manufacturer/description)
 *   are the honest "preview" here. */
function RealizeNodePanel({
  node,
  projectId,
  onClose,
}: {
  node: HierarchyNode;
  projectId: string | null;
  onClose: () => void;
}) {
  const toast = useToast();
  const realize = useRealizeHierarchyNode();
  const { data: bomItems } = useBom(projectId ?? undefined);

  const [mode, setMode] = useState<'upload' | 'pick'>('upload');
  const [imported, setImported] = useState<ImportWorkProductResponse | null>(null);
  const [glbUrl, setGlbUrl] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState(false);
  const [selectedBomItemId, setSelectedBomItemId] = useState('');

  const handleImportSuccess = (result: ImportWorkProductResponse) => {
    setImported(result);
    getNodeModel(result.id)
      .then((model) => setGlbUrl(resolveGatewayHref(model.glb_url)))
      .catch(() => setPreviewError(true));
  };

  const confirmUpload = () => {
    if (!imported) return;
    realize.mutate(
      { nodeId: node.id, workProductId: imported.id },
      {
        onSuccess: () => {
          toast.success(`${node.name} realized with real geometry`);
          onClose();
        },
        onError: () => toast.error('Could not attach this geometry to the node'),
      },
    );
  };

  const confirmPick = () => {
    if (!selectedBomItemId) return;
    realize.mutate(
      { nodeId: node.id, bomItemId: selectedBomItemId },
      {
        onSuccess: () => {
          toast.success(`${node.name} realized with the selected part`);
          onClose();
        },
        onError: () => toast.error('Could not attach this part to the node'),
      },
    );
  };

  const hasExisting = !!node.realizedByWorkProductId || !!node.instanceOfBomItemId;

  return (
    <div
      data-testid="realize-node-panel"
      className="mt-2 rounded-lg p-3"
      style={{ background: 'var(--mf-r-30-31-38-0p85)', border: '1px solid var(--mf-r-65-72-90-0p2)' }}
    >
      <div className="mb-2 flex items-center justify-between">
        <span className="text-xs font-medium text-on-surface">
          {hasExisting ? `Replace ${node.name}'s part` : `Replace placeholder: ${node.name}`}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="text-xs text-on-surface-variant hover:text-on-surface"
        >
          Cancel
        </button>
      </div>

      <div className="mb-2 flex gap-1">
        <button
          type="button"
          data-testid="realize-mode-upload"
          onClick={() => setMode('upload')}
          className="rounded px-2 py-1 text-xs transition-colors"
          style={{
            background: mode === 'upload' ? 'var(--mf-c-282a30)' : 'transparent',
            color: mode === 'upload' ? 'var(--mf-c-e2e2eb)' : 'var(--mf-c-9a9aaa)',
          }}
        >
          Upload STEP file
        </button>
        <button
          type="button"
          data-testid="realize-mode-pick"
          onClick={() => setMode('pick')}
          className="rounded px-2 py-1 text-xs transition-colors"
          style={{
            background: mode === 'pick' ? 'var(--mf-c-282a30)' : 'transparent',
            color: mode === 'pick' ? 'var(--mf-c-e2e2eb)' : 'var(--mf-c-9a9aaa)',
          }}
        >
          Pick existing part
        </button>
      </div>

      {mode === 'upload' && (
        <div>
          {!imported && <ImportZone projectId={projectId ?? undefined} onSuccess={handleImportSuccess} />}
          {imported && (
            <div>
              <p className="mb-2 text-xs text-on-surface-variant">
                Imported <span className="text-on-surface">{imported.name}</span> -- real geometry
                extracted from the STEP file.
              </p>
              {glbUrl && !previewError && <GlbPreview glbUrl={glbUrl} />}
              {previewError && (
                <p className="mb-2 text-xs text-on-surface-variant">
                  (3D preview unavailable for this file -- the import itself still succeeded.)
                </p>
              )}
              <div className="mt-2 flex gap-2">
                <Button
                  size="sm"
                  data-testid="confirm-realize-upload"
                  disabled={realize.isPending}
                  onClick={confirmUpload}
                >
                  {realize.isPending ? 'Attaching…' : 'Use this geometry'}
                </Button>
                <Button variant="secondary" size="sm" onClick={() => setImported(null)}>
                  Import a different file
                </Button>
              </div>
            </div>
          )}
        </div>
      )}

      {mode === 'pick' && (
        <div className="flex flex-wrap items-end gap-2">
          <label className="flex flex-col gap-1 text-xs text-on-surface-variant" style={{ flex: 1, minWidth: '220px' }}>
            Part
            <select
              value={selectedBomItemId}
              onChange={(e) => setSelectedBomItemId(e.target.value)}
              data-testid="realize-bom-item-select"
              className="rounded px-2 py-1 text-xs text-on-surface focus:outline-none"
              style={{ ...FIELD_STYLE, width: '100%' }}
            >
              <option value="">choose a recorded part…</option>
              {(bomItems ?? []).map((c) => (
                <option key={c.id} value={c.id}>
                  {c.partNumber} -- {c.manufacturer}
                </option>
              ))}
            </select>
          </label>
          <Button
            size="sm"
            data-testid="confirm-realize-pick"
            disabled={!selectedBomItemId || realize.isPending}
            onClick={confirmPick}
          >
            {realize.isPending ? 'Attaching…' : 'Use this part'}
          </Button>
        </div>
      )}
    </div>
  );
}

function TreeRow({
  node,
  depth,
  collapsed,
  onToggle,
  onSelect,
  projectId,
  realizingId,
  onToggleRealize,
}: {
  node: TreeNode;
  depth: number;
  collapsed: Set<string>;
  onToggle: (id: string) => void;
  onSelect: (id: string) => void;
  projectId: string | null;
  realizingId: string | null;
  onToggleRealize: (id: string) => void;
}) {
  const hasChildren = node.children.length > 0;
  const isCollapsed = collapsed.has(node.id);
  const overBudget = node.massOverBudget === true || node.costOverBudget === true;
  const hasGeometry = !!node.realizedByWorkProductId || !!node.instanceOfBomItemId;
  const isRealizing = realizingId === node.id;

  return (
    <>
      <div
        className={`tw-structure-row${overBudget ? ' is-over-budget' : ''}`}
        style={{ paddingLeft: depth * 20 }}
      >
        <button
          type="button"
          className="tw-structure-toggle"
          aria-label={isCollapsed ? `Expand ${node.name}` : `Collapse ${node.name}`}
          onClick={() => hasChildren && onToggle(node.id)}
          disabled={!hasChildren}
        >
          {hasChildren ? (
            <span className="material-symbols-outlined">
              {isCollapsed ? 'chevron_right' : 'expand_more'}
            </span>
          ) : (
            <span className="tw-structure-leaf-dot" />
          )}
        </button>
        <span className="material-symbols-outlined tw-structure-kind-icon">
          {iconForHierarchyKind(node.kind)}
        </span>
        <button type="button" className="tw-structure-name" onClick={() => onSelect(node.id)}>
          {overBudget && (
            <span className="material-symbols-outlined tw-structure-warning" aria-label="Over budget">
              warning
            </span>
          )}
          {node.name}
        </button>
        {node.interfaces.length > 0 && (
          <span
            className="material-symbols-outlined tw-structure-interfaces-badge"
            aria-label={`${node.interfaces.length} interface${node.interfaces.length === 1 ? '' : 's'}`}
            title={interfacesTitle(node.interfaces)}
          >
            link
          </span>
        )}
        <span className="tw-structure-kind-label">{node.kind}</span>
        <span className="tw-structure-qty">{node.quantity ?? '—'}</span>
        <span
          className={`tw-structure-mass${node.massOverBudget ? ' is-over-budget' : ''}`}
          title={ownerTitle(node.massBudgetOwner, node.massBudgetDiscipline)}
        >
          {formatMass(node.massKg, node.massBudgetKg)}
        </span>
        <span
          className={`tw-structure-cost${node.costOverBudget ? ' is-over-budget' : ''}`}
          title={ownerTitle(node.costBudgetOwner, node.costBudgetDiscipline)}
        >
          {formatCost(node.cost, node.costBudget)}
        </span>
        <button
          type="button"
          data-testid={`realize-node-button-${node.id}`}
          onClick={() => onToggleRealize(node.id)}
          className="rounded px-2 py-0.5 text-[11px] text-on-surface-variant hover:text-on-surface transition-colors"
          style={{ background: 'var(--mf-c-282a30)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}
        >
          {hasGeometry ? 'Replace part' : 'Replace placeholder'}
        </button>
      </div>
      {isRealizing && (
        <div style={{ paddingLeft: depth * 20 + 28 }}>
          <RealizeNodePanel node={node} projectId={projectId} onClose={() => onToggleRealize(node.id)} />
        </div>
      )}
      {hasChildren && !isCollapsed && (
        <div>
          {node.children.map((child) => (
            <TreeRow
              key={child.id}
              node={child}
              depth={depth + 1}
              collapsed={collapsed}
              onToggle={onToggle}
              onSelect={onSelect}
              projectId={projectId}
              realizingId={realizingId}
              onToggleRealize={onToggleRealize}
            />
          ))}
        </div>
      )}
    </>
  );
}

export function StructureView({
  projectId,
  onSelect,
}: {
  projectId: string | null;
  onSelect: (id: string) => void;
}) {
  const { data: nodes, isLoading } = useHierarchyTree(projectId ?? undefined);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  // FORGE-289 (gap G-G3): "Decision cards linked from hierarchy nodes" --
  // tracked locally rather than through the parent's selectedNode/inspector
  // machinery, which assumes a WorkProduct (CAD) node shape a HierarchyNode
  // doesn't have.
  const [decisionNodeId, setDecisionNodeId] = useState<string | null>(null);
  // FORGE-266 (gap G-C2): which node's "Replace placeholder with part"
  // panel is open, at most one at a time.
  const [realizingId, setRealizingId] = useState<string | null>(null);

  const forest = useMemo(() => buildForest(nodes ?? []), [nodes]);

  const toggle = (id: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className="tw-assembly">
      <div className="tw-assembly-heading">
        <Boxes size={20} />
        <h2>Structure</h2>
        {nodes && nodes.length > 0 && (
          <span className="tw-mfg-count">{nodes.length} hierarchy nodes</span>
        )}
      </div>

      {!isLoading && !projectId && (
        <div className="tw-empty">
          <h2>No project selected</h2>
          <p>Select a project to view its product hierarchy.</p>
        </div>
      )}

      {!isLoading && projectId && forest.length === 0 && (
        <div className="tw-empty">
          <h2>No hierarchy yet</h2>
          <p>
            Build one with <code>twin.record_hierarchy_node</code> — a product, its subsystems, and
            the parts/components each one is realized by.
          </p>
        </div>
      )}

      {!isLoading && forest.length > 0 && (
        <div className="tw-structure-tree">
          <div className="tw-structure-row tw-structure-header">
            <span />
            <span />
            <span>Name</span>
            <span>Kind</span>
            <span>Qty</span>
            <span>Mass</span>
            <span>Cost</span>
            <span>Geometry</span>
          </div>
          {forest.map((root) => (
            <TreeRow
              key={root.id}
              node={root}
              depth={0}
              collapsed={collapsed}
              onToggle={toggle}
              onSelect={(id) => {
                onSelect(id);
                setDecisionNodeId(id);
              }}
              projectId={projectId}
              realizingId={realizingId}
              onToggleRealize={(id) => setRealizingId((cur) => (cur === id ? null : id))}
            />
          ))}
        </div>
      )}

      {decisionNodeId && (
        <div data-testid="structure-decisions-panel" className="mt-3">
          <DecisionList nodeId={decisionNodeId} heading="Decisions" />
        </div>
      )}
    </div>
  );
}
