// Shell layout state: desktop nav-rail collapse (persisted) and the mobile
// navigation drawer.
import { create } from 'zustand';

export const NAV_COLLAPSED_STORAGE_KEY = 'metaforge.nav-collapsed.v2';

function readCollapsed(): boolean {
  try {
    // Collapsed (icon rail) by default; only an explicit "false" expands.
    return localStorage.getItem(NAV_COLLAPSED_STORAGE_KEY) !== 'false';
  } catch {
    return true;
  }
}

interface LayoutStore {
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  mobileSidebarOpen: boolean;
  openMobileSidebar: () => void;
  closeMobileSidebar: () => void;
}

export const useLayoutStore = create<LayoutStore>((set) => ({
  sidebarCollapsed: readCollapsed(),
  toggleSidebar: () =>
    set((state) => {
      const next = !state.sidebarCollapsed;
      try {
        localStorage.setItem(NAV_COLLAPSED_STORAGE_KEY, String(next));
      } catch {
        // ignore
      }
      return { sidebarCollapsed: next };
    }),
  mobileSidebarOpen: false,
  openMobileSidebar: () => set({ mobileSidebarOpen: true }),
  closeMobileSidebar: () => set({ mobileSidebarOpen: false }),
}));
