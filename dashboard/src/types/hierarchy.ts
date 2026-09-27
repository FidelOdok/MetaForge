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
  /** FORGE-264: set only when a budget entity allocates to this node.
   * null means no allocation targets it -- not "under budget". */
  massBudgetKg: number | null;
  massOverBudget: boolean | null;
  costBudget: number | null;
  costOverBudget: boolean | null;
}
