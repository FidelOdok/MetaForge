export interface BomComponent {
  id: string;
  designator: string;
  partNumber: string;
  description: string;
  manufacturer: string;
  quantity: number;
  unitPrice: number;
  priceCurrency: string;
  status: 'available' | 'low_stock' | 'out_of_stock' | 'alternate_needed';
  category: string;
  projectId: string;
  imageUrl: string | null;
  purchaseUrl: string | null;
  datasheetUrl: string | null;
  footprint: string | null;
  cadModelUrl: string | null;
}

/** One derived EBOM line (FORGE-267) -- a leaf product-hierarchy position
 * and the real component/part that fulfils it. */
export interface HierarchicalBomLine {
  hierarchyNodeId: string;
  /** Ancestor names, root-first, ending with this leaf's own name. */
  path: string[];
  /** Product of every CONTAINS edge's quantity from the product root down. */
  quantity: number;
  source: 'instance_of' | 'realized_by';
  componentId: string;
  partNumber: string | null;
  manufacturer: string | null;
  description: string;
  unitCost: number | null;
}
