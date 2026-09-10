import { useEffect, useMemo } from 'react';
import { useProjectStore } from '../store/project-store';
import { useProjects } from './use-projects';
import type { Project } from '../types/project';

/**
 * The single active-project context every page reads from (Context UI).
 * Replaces the per-page `useState('')` + `ProjectScopePicker` pattern that
 * used to make each page filter independently with no shared state.
 *
 * `activeProjectId` is `null` for "All projects". On first load, once the
 * project list resolves, the most-recently-updated project is auto-selected
 * exactly once (mirrors the previous per-page behavior on Knowledge/BOM);
 * any explicit pick — including switching back to "All projects" — makes
 * that permanent for the session (persisted).
 */
export function useActiveProject() {
  const activeProjectId = useProjectStore((s) => s.activeProjectId);
  const hasSelected = useProjectStore((s) => s.hasSelected);
  const setActiveProject = useProjectStore((s) => s.setActiveProject);
  const autoSelectIfUnset = useProjectStore((s) => s.autoSelectIfUnset);

  const { data: projects } = useProjects();

  const sortedProjects = useMemo(() => {
    if (!projects) return [];
    return [...projects].sort((a, b) => {
      const ta = new Date(a.lastUpdated).getTime() || 0;
      const tb = new Date(b.lastUpdated).getTime() || 0;
      return tb - ta;
    });
  }, [projects]);

  useEffect(() => {
    if (hasSelected) return;
    const newest = sortedProjects[0];
    if (!newest) return;
    autoSelectIfUnset(newest.id);
  }, [hasSelected, sortedProjects, autoSelectIfUnset]);

  const activeProject: Project | undefined = useMemo(
    () => sortedProjects.find((p) => p.id === activeProjectId),
    [sortedProjects, activeProjectId],
  );

  return {
    /** `null` = "All projects". */
    activeProjectId,
    activeProject,
    setActiveProjectId: setActiveProject,
    /** Projects sorted most-recently-updated first, for pickers. */
    projects: sortedProjects,
  };
}
