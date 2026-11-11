import { create } from 'zustand';

/**
 * How the Create view is laid out: the sketch drawer in Imagine and whether
 * the conversation is collapsed (VIEW-010), and how wide it is.
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

/**
 * The conversation's width, dragged by the person and kept in this browser.
 * Wider than the old fixed 320 px by default; held between limits so the
 * stage always keeps room for the model.
 */
export const CHAT_WIDTH_KEY = 'metaforge.create.chatWidth';
export const CHAT_WIDTH_DEFAULT = 400;
export const CHAT_WIDTH_MIN = 280;
export const CHAT_WIDTH_MAX = 760;

export function clampChatWidth(width: number): number {
  return Math.round(Math.min(CHAT_WIDTH_MAX, Math.max(CHAT_WIDTH_MIN, width)));
}

/** The stored width, or the default when storage is unavailable or holds nonsense. */
export function readChatWidth(): number {
  try {
    const raw = window.localStorage.getItem(CHAT_WIDTH_KEY);
    const n = raw === null ? NaN : Number(raw);
    return Number.isFinite(n) ? clampChatWidth(n) : CHAT_WIDTH_DEFAULT;
  } catch {
    return CHAT_WIDTH_DEFAULT;
  }
}

function writeChatWidth(width: number): void {
  try {
    window.localStorage.setItem(CHAT_WIDTH_KEY, String(width));
  } catch {
    // Storage unavailable: the width lasts for this page only.
  }
}

interface LayoutStore {
  stages: Record<string, StageLayout>;
  collapsed: CollapsedPanels;
  /** The conversation's width in px. */
  chatWidth: number;
  /** Set the width (clamped) and keep it in this browser. */
  setChatWidth: (width: number) => void;
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
    chatWidth: readChatWidth(),
    setChatWidth: (width) => {
      const next = clampChatWidth(width);
      writeChatWidth(next);
      set({ chatWidth: next });
    },
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
    reset: () => set({ stages: {}, collapsed: readCollapsed(), chatWidth: readChatWidth() }),
  };
});
