import apiClient from '../client';

// ---------------------------------------------------------------------------
// Change classification and the appearance record
// (api_gateway/twin/change_classification_routes.py).
// ---------------------------------------------------------------------------

export type ChangeClass =
  | 'appearance'
  | 'geometric'
  | 'engineering_geometry'
  | 'material_engineering'
  | 'performance_objective'
  | 'ambiguous_style'
  | 'ambiguous_appearance_or_material'
  | 'not_a_change';

export interface AppearanceLook {
  colour?: string | null;
  colour_name?: string | null;
  finish?: string | null;
  render_material?: string | null;
  texture_refs?: string[];
}

/** What a request would change, decided before anything changes (spec section 5). */
export interface ChangeClassification {
  change_class: ChangeClass;
  confidence: number;
  /** `rules`, `llm`, or `person` (the person chose the look or the material). */
  stage: string;
  also: ChangeClass[];
  target_component: string | null;
  target_component_id: string | null;
  target_feature: string | null;
  /** Routing code, e.g. `update_appearance`. */
  handling: string;
  /** The plain line Create shows. */
  message: string;
  signals: string[];
  appearance: AppearanceLook | null;
  proposed_context_update: Record<string, unknown> | null;
  choices: string[];
  stated_dimensions: string[];
}

export interface ComponentAppearance extends AppearanceLook {
  key: string;
  name: string | null;
  node_id: string | null;
}

export interface AppearanceRecord {
  project_id: string;
  product: AppearanceLook | null;
  components: ComponentAppearance[];
}

export interface AppearanceView {
  project_id: string;
  recorded: boolean;
  ref: string | null;
  record: AppearanceRecord;
}

/** POST /twin/projects/{id}/classify-change. Writes nothing. */
export async function classifyChange(
  projectId: string,
  body: { text: string; component?: string; component_id?: string },
): Promise<ChangeClassification> {
  const { data } = await apiClient.post<ChangeClassification>(
    `/twin/projects/${encodeURIComponent(projectId)}/classify-change`,
    body,
  );
  return data;
}

/** GET /twin/projects/{id}/appearance: the product's and each component's look. */
export async function getAppearance(projectId: string): Promise<AppearanceView> {
  const { data } = await apiClient.get<AppearanceView>(`/twin/projects/${encodeURIComponent(projectId)}/appearance`);
  return data;
}

/**
 * The person confirms a proposed material: stated on the manufacturing
 * context (PUT .../manufacturing-context), so dependent checks are marked
 * stale by the impact walk.
 */
export async function confirmMaterial(projectId: string, material: string): Promise<void> {
  await apiClient.put(`/twin/projects/${encodeURIComponent(projectId)}/manufacturing-context`, {
    fields: { selected_material: material },
    change_reason: `Material confirmed in Create: ${material}`,
  });
}
