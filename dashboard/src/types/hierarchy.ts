/** One position in a project's product hierarchy tree (FORGE-261). */
export interface HierarchyNode {
  id: string;
  name: string;
  kind: 'product' | 'system' | 'subsystem' | 'assembly';
  parentId: string | null;
  quantity: number | null;
  placement: Record<string, unknown> | null;
  /** Rolled up over this node's own CONTAINS subtree. */
  massKg: number;
  cost: number;
}
