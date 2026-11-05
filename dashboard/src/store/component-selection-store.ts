import { create } from 'zustand';

/**
 * The component a person selected, shared by Create and Engineering
 * (3D-RC-009, VIEW-003).
 *
 * Kept per project and outside any component, so selecting a part on the
 * Create stage and switching to Engineering finds the same part selected,
 * and the other way round. A component is named by its stable id
 * (`CMP-...`), which every representation carries: an L1 mesh node's name
 * and extras, a CAD revision's metadata.
 */

export interface SelectedComponent {
  componentId: string;
  /** The part name, for display. */
  name?: string;
  /** Where it was picked: 'create' or 'engineering'. */
  from?: 'create' | 'engineering';
}

interface ComponentSelectionStore {
  selected: Record<string, SelectedComponent | null>;
  select: (projectId: string, component: SelectedComponent | null) => void;
}

export const useComponentSelection = create<ComponentSelectionStore>((set) => ({
  selected: {},
  select: (projectId, component) => set((s) => ({ selected: { ...s.selected, [projectId]: component } })),
}));

/** The key used when no project is active. */
export const NO_PROJECT = '_';

interface PickedObject {
  name?: string;
  userData?: Record<string, unknown>;
  parent?: PickedObject | null;
}

/**
 * The component a picked 3D object belongs to: the nearest object, walking
 * up its parents, whose glTF extras (`userData`) carry a `component_id`.
 */
export function componentOfObject(object: PickedObject | null | undefined): SelectedComponent | null {
  let current: PickedObject | null | undefined = object;
  for (let depth = 0; current && depth < 16; depth += 1) {
    const id = current.userData?.component_id;
    if (typeof id === 'string' && id) {
      const name = current.userData?.part_name;
      return { componentId: id, name: typeof name === 'string' ? name : current.name };
    }
    current = current.parent;
  }
  return null;
}
