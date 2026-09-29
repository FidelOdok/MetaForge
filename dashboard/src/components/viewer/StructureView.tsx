import { useMemo, useState } from 'react';
import { Boxes } from 'lucide-react';
import { useHierarchyTree } from '../../hooks/use-hierarchy';
import { iconForHierarchyKind } from '../../utils/wp-icons';
import type { HierarchyNode } from '../../types/hierarchy';

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

function TreeRow({
  node,
  depth,
  collapsed,
  onToggle,
  onSelect,
}: {
  node: TreeNode;
  depth: number;
  collapsed: Set<string>;
  onToggle: (id: string) => void;
  onSelect: (id: string) => void;
}) {
  const hasChildren = node.children.length > 0;
  const isCollapsed = collapsed.has(node.id);
  const overBudget = node.massOverBudget === true || node.costOverBudget === true;

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
      </div>
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
          </div>
          {forest.map((root) => (
            <TreeRow
              key={root.id}
              node={root}
              depth={0}
              collapsed={collapsed}
              onToggle={toggle}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
  );
}
