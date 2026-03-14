import { Link } from 'react-router-dom';
import { useProjects } from '../hooks/use-projects';
import { Card } from '../components/ui/Card';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { formatRelativeTime } from '../utils/format-time';

export function ProjectsPage() {
  const { data: projects, isLoading } = useProjects();

  if (isLoading) {
    return <div className="text-sm text-zinc-500">Loading projects...</div>;
  }

  if (!projects?.length) {
    return (
      <EmptyState
        title="No projects yet"
        description="Create a project with 'forge setup' to get started."
      />
    );
  }

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
          Projects
        </h2>
        <span className="text-sm text-zinc-500">{projects.length} projects</span>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {projects.map((project) => (
          <Link key={project.id} to={`/projects/${project.id}`}>
            <Card className="transition-shadow hover:shadow-md">
              <div className="mb-3 flex items-start justify-between">
                <h3 className="font-medium text-zinc-900 dark:text-zinc-100">
                  {project.name}
                </h3>
                <StatusBadge status={project.status} />
              </div>
              <p className="mb-4 text-sm text-zinc-500 line-clamp-2">
                {project.description}
              </p>
              <div className="flex items-center justify-between text-xs text-zinc-400">
                <span>{project.work_products.length} work_products</span>
                <span>{project.agentCount} agents</span>
                <span>{formatRelativeTime(project.lastUpdated)}</span>
              </div>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
