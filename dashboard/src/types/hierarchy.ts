/** One interface quantity (FORGE-313) -- a measurable property of an
 * interface between two components, e.g. "tip deflection <= 0.5mm". */
export interface InterfaceQuantitySummary {
  metric: string;
  unit: string;
  limit: number | null;
  op: string;
}

/** One interface (FORGE-313) touching a hierarchy node, from a
 * SYSTEM_ARCHITECTURE work product's structured interfaces list. */
export interface InterfaceSummary {
  otherComponent: string;
  interfaceType: string;
  description: string;
  quantities: InterfaceQuantitySummary[];
}

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
  /** FORGE-313: who's accountable for this node's allocation, if the
   * allocation set one. */
  massBudgetOwner: string | null;
  massBudgetDiscipline: string | null;
  costBudgetOwner: string | null;
  costBudgetDiscipline: string | null;
  /** FORGE-313: interfaces touching this node. */
  interfaces: InterfaceSummary[];
  /** FORGE-266 (gap G-C2): which real geometry (if any) this position has.
   * Both null means "placeholder". */
  realizedByWorkProductId: string | null;
  instanceOfBomItemId: string | null;
  /** FORGE-275 (gap G-E2): rolled up over this node's own CONTAINS subtree,
   * same as massKg/cost -- the hierarchy rollup has computed these since
   * FORGE-390, this just surfaces them (power-tree view). */
  drawPeakW: number;
  drawAverageW: number;
  outputW: number;
  dissipationW: number;
}
