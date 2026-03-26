// layout-store.ts — retained for import compatibility.
// The nav rail is now always 48px (no collapse). This store is effectively unused.
import { create } from 'zustand';

interface LayoutStore {
  /** @deprecated Nav rail is always 48px — no collapse in Kinetic Console. */
  sidebarCollapsed: false;
  mobileSidebarOpen: boolean;
  openMobileSidebar: () => void;
  closeMobileSidebar: () => void;
}

export const useLayoutStore = create<LayoutStore>((set) => ({
  sidebarCollapsed: false,
  mobileSidebarOpen: false,
  openMobileSidebar: () => set({ mobileSidebarOpen: true }),
  closeMobileSidebar: () => set({ mobileSidebarOpen: false }),
}));
