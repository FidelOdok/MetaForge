import { create } from 'zustand';

/**
 * How the Create view is laid out: the sketch drawer in Imagine and whether
 * the conversation is collapsed (VIEW-010).
 *
 * The drawer lives per creation and outside any component, so a switch to
 * Engineering and back finds it as it was (3D-RC-009). The collapsed
 * conversation is the person's own preference, the same in every creation,
 * kept in this browser. Storage may be unavailable (a private window,
 * blocked site data); the view then simply starts expanded.
 */

/** The panels the sketch drawer can hold, one at a time. */
export const DRAWER_PANELS = ['editable', 'generate', 'plane', 'references'] as const;
export type DrawerPanel = (typeof DRAWER_PANELS)[number];

export const DRAWER_LABEL: Record<DrawerPanel, string> = {
  editable: 'Editable sketch',
  generate: 'Generate a concept',
  plane: 'Sketch plane',
  references: 'References',
};

export interface StageLayout {
  drawer: DrawerPanel | null;
  drawerCollapsed: boolean;
}

export const EMPTY_STAGE: StageLayout = {
  drawer: null,
  drawerCollapsed: false,
};

export type SidePanel = 'chat';
export type CollapsedPanels = Record<SidePanel, boolean>;

export const PANELS_KEY = 'metaforge.create.panels';
const NONE_COLLAPSED: CollapsedPanels = { chat: false };

/** The stored preference, or none collapsed when storage is unavailable. */
export function readCollapsed(): CollapsedPanels {
  try {
    const raw = window.localStorage.getItem(PANELS_KEY);
    if (!raw) return { ...NONE_COLLAPSED };
    const parsed = JSON.parse(raw) as Partial<CollapsedPanels>;
    return { chat: parsed.chat === true };
  } catch {
    return { ...NONE_COLLAPSED };
  }
}

function writeCollapsed(value: CollapsedPanels): void {
  try {
    window.localStorage.setItem(PANELS_KEY, JSON.stringify(value));
  } catch {
    // Storage unavailable: the preference lasts for this page only.
  }
}

interface LayoutStore {
  stages: Record<string, StageLayout>;
  collapsed: CollapsedPanels;
  stage: (projectId: string) => StageLayout;
  /** Open one panel in the drawer; any other panel closes. */
  openDrawer: (projectId: string, panel: DrawerPanel) => void;
  closeDrawer: (projectId: string) => void;
  setDrawerCollapsed: (projectId: string, collapsed: boolean) => void;
  setCollapsed: (panel: SidePanel, collapsed: boolean) => void;
  /** Re-read the stored preference (another tab, or a test). */
  reloadCollapsed: () => void;
  reset: () => void;
}

export const useCreateLayoutStore = create<LayoutStore>()((set, get) => {
  const patch = (projectId: string, change: Partial<StageLayout>) =>
    set((s) => ({ stages: { ...s.stages, [projectId]: { ...(s.stages[projectId] ?? EMPTY_STAGE), ...change } } }));
  return {
    stages: {},
    collapsed: readCollapsed(),
    stage: (projectId) => get().stages[projectId] ?? EMPTY_STAGE,
    openDrawer: (projectId, panel) => patch(projectId, { drawer: panel, drawerCollapsed: false }),
    closeDrawer: (projectId) => patch(projectId, { drawer: null, drawerCollapsed: false }),
    setDrawerCollapsed: (projectId, collapsed) => patch(projectId, { drawerCollapsed: collapsed }),
    setCollapsed: (panel, collapsed) => {
      const next = { ...get().collapsed, [panel]: collapsed };
      writeCollapsed(next);
      set({ collapsed: next });
    },
    reloadCollapsed: () => set({ collapsed: readCollapsed() }),
    reset: () => set({ stages: {}, collapsed: readCollapsed() }),
  };
});
