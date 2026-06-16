import { useMemo } from 'react';
import { useProjects } from '../../hooks/use-projects';

interface ProjectScopePickerProps {
  /** Selected project id; empty string = all projects. */
  value: string;
  onChange: (projectId: string) => void;
  className?: string;
}

/**
 * Shared project-scope selector so every list page can be filtered to one
 * project (MET-515..518), mirroring the rollout started on Knowledge (MET-486)
 * and Twin (MET-491). Empty value means "All projects". Projects are sorted
 * most-recently-updated first.
 */
export function ProjectScopePicker({ value, onChange, className }: ProjectScopePickerProps) {
  const { data: projects } = useProjects();

  const options = useMemo(() => {
    if (!projects) return [];
    return [...projects].sort((a, b) => {
      const ta = new Date(a.lastUpdated).getTime() || 0;
      const tb = new Date(b.lastUpdated).getTime() || 0;
      return tb - ta;
    });
  }, [projects]);

  return (
    <select
      aria-label="project scope"
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={
        className ??
        'bg-surface-high border border-[rgba(65,72,90,0.3)] text-on-surface text-xs rounded px-3 py-1.5 outline-none focus:border-[rgba(65,72,90,0.6)]'
      }
    >
      <option value="">All projects</option>
      {options.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}
        </option>
      ))}
    </select>
  );
}
