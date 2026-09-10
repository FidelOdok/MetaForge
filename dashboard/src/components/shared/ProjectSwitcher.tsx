import { useActiveProject } from '../../hooks/use-active-project';

/**
 * Global project-context switcher (Context UI). Lives in the Topbar so it's
 * present on every page and drives the single active-project selection every
 * page now reads from — replacing the old pattern of each page keeping its
 * own independent, unshared `ProjectScopePicker`.
 */
export function ProjectSwitcher() {
  const { activeProjectId, projects, setActiveProjectId } = useActiveProject();

  return (
    <div className="flex items-center gap-1.5">
      <span
        className="material-symbols-outlined text-on-surface-variant"
        style={{ fontSize: '14px' }}
        aria-hidden="true"
      >
        grid_view
      </span>
      <select
        aria-label="active project"
        value={activeProjectId ?? ''}
        onChange={(e) => setActiveProjectId(e.target.value || null)}
        className="bg-surface-high border border-[rgba(65,72,90,0.3)] text-on-surface text-xs rounded px-2 py-1 outline-none focus:border-[rgba(65,72,90,0.6)] max-w-[180px]"
      >
        <option value="">All projects</option>
        {projects.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    </div>
  );
}
