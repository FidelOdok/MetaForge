import { create } from 'zustand';
import { persist } from 'zustand/middleware';

// ---------------------------------------------------------------------------
// Global "active project" context — every page (BOM, Files, Sessions,
// Approvals, Twin, Design Assistant, Knowledge) previously kept its own
// independent `useState('')` for project scope, so switching projects on one
// page had no effect anywhere else and the choice was lost on navigation.
// This store is the single source of truth: `null` means "All projects".
// Persisted to localStorage so the choice survives a reload.
// ---------------------------------------------------------------------------

interface ProjectState {
  /** Active project id, or `null` for "All projects". */
  activeProjectId: string | null;
  /**
   * Whether an active project has ever been chosen (explicitly, or via the
   * one-time auto-select of the most-recently-updated project on first
   * load). Once true, nothing auto-picks on the user's behalf again — even
   * a deliberate switch back to "All projects" sets this.
   */
  hasSelected: boolean;
  setActiveProject: (projectId: string | null) => void;
  /** Auto-select the most-recently-updated project exactly once, the first
   * time any page has a project list available and nothing has been picked
   * yet. No-op after the first call ever succeeds. */
  autoSelectIfUnset: (projectId: string) => void;
}

export const useProjectStore = create<ProjectState>()(
  persist(
    (set, get) => ({
      activeProjectId: null,
      hasSelected: false,

      setActiveProject: (projectId) =>
        set({ activeProjectId: projectId, hasSelected: true }),

      autoSelectIfUnset: (projectId) => {
        if (get().hasSelected) return;
        set({ activeProjectId: projectId, hasSelected: true });
      },
    }),
    {
      name: 'metaforge.active-project',
      version: 1,
    },
  ),
);
