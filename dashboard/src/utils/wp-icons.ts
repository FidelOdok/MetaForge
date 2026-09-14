import type { TwinNode } from '../types/twin';

/**
 * Material Symbols icon per WorkProductType (mirrors twin_core/models/
 * enums.py). Every node list/tree/graph surface in the app was previously
 * keyed off the coarse `node.type` ('work_product' | 'constraint' |
 * 'relationship' | 'version') -- since the vast majority of twin nodes are
 * `work_product`, that gave every one of them the same generic icon
 * regardless of whether it was a CAD model, a design sketch, or a hazard
 * log, with nothing to tell them apart in a node list at a glance.
 *
 * Unlisted wp_types fall back to a generic icon (see iconForNode) rather
 * than erroring, so a newly added backend type never breaks rendering --
 * it just looks generic until someone adds a specific entry here.
 */
export const WP_TYPE_ICONS: Record<string, string> = {
  schematic: 'schema',
  pcb_layout: 'developer_board',
  bom: 'table_rows',
  cad_model: 'view_in_ar',
  firmware_source: 'memory',
  simulation_result: 'bar_chart',
  test_plan: 'checklist',
  test_result: 'fact_check',
  manufacturing_file: 'precision_manufacturing',
  constraint_set: 'rule',
  prd: 'description',
  pinmap: 'share',
  gerber: 'layers',
  pick_and_place: 'place',
  documentation: 'article',
  design_decision: 'gavel',
  cad_source_script: 'code',
  robot_description: 'smart_toy',
  design_sketch: 'design_services',
  hazard_analysis: 'warning',
  system_architecture: 'device_hub',
  technical_drawing: 'square_foot',
  compliance_checklist: 'verified',
  procurement_record: 'receipt_long',
};

/** Icon for the few graph-node kinds that aren't `work_product`. */
const NODE_TYPE_ICONS: Record<string, string> = {
  constraint: 'rule',
  relationship: 'link',
  version: 'label',
};

const FALLBACK_ICON = 'description';

/**
 * The icon a node should show anywhere in the Twin UI (node list, scene
 * dropdown, graph canvas, detail panel header) -- keyed by `wp_type` for
 * `work_product` nodes, falling back to the coarse `node.type` for the
 * handful of non-work-product kinds.
 */
export function iconForNode(node: Pick<TwinNode, 'type' | 'properties'>): string {
  if (node.type !== 'work_product') {
    return NODE_TYPE_ICONS[node.type] ?? FALLBACK_ICON;
  }
  const wpType = node.properties?.wp_type ? String(node.properties.wp_type) : '';
  return WP_TYPE_ICONS[wpType] ?? FALLBACK_ICON;
}
