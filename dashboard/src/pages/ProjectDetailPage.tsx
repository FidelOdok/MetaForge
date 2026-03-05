import { Link, useParams } from 'react-router-dom';
import { useProject } from '../hooks/use-projects';
import { Card } from '../components/ui/Card';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { formatRelativeTime } from '../utils/format-time';

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: project, isLoading } = useProject(id);

  if (isLoading) {
    return <div className="text-sm text-zinc-500">Loading project...</div>;
  }

  if (!project) {
    return (
      <EmptyState
        title="Project not found"
        description="The project you're looking for doesn't exist."
      />
    );
  }

  return (
    <div>
      <div className="mb-1">
        <Link
          to="/projects"
          className="text-sm text-blue-600 hover:underline dark:text-blue-400"
        >
          &larr; Projects
        </Link>
      </div>

      <div className="mb-6 flex items-center gap-3">
        <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">
          {project.name}
        </h2>
        <StatusBadge status={project.status} />
      </div>

      <p className="mb-6 text-sm text-zinc-500">{project.description}</p>

      <div className="mb-6 grid gap-4 sm:grid-cols-3">
        <Card>
          <div className="text-2xl font-bold text-zinc-900 dark:text-zinc-100">
            {project.artifacts.length}
          </div>
          <div className="text-xs text-zinc-500">Artifacts</div>
        </Card>
        <Card>
          <div className="text-2xl font-bold text-zinc-900 dark:text-zinc-100">
            {project.agentCount}
          </div>
          <div className="text-xs text-zinc-500">Active Agents</div>
        </Card>
        <Card>
          <div className="text-2xl font-bold text-zinc-900 dark:text-zinc-100">
            {formatRelativeTime(project.lastUpdated)}
          </div>
          <div className="text-xs text-zinc-500">Last Updated</div>
        </Card>
      </div>

      <h3 className="mb-3 text-lg font-medium text-zinc-900 dark:text-zinc-100">
        Artifacts
      </h3>

      {project.artifacts.length === 0 ? (
        <EmptyState title="No artifacts" description="Run an agent to create artifacts." />
      ) : (
        <div className="space-y-2">
          {project.artifacts.map((artifact) => (
            <Card key={artifact.id} className="flex items-center justify-between py-3">
              <div>
                <span className="font-medium text-zinc-900 dark:text-zinc-100">
                  {artifact.name}
                </span>
                <span className="ml-2 text-xs text-zinc-400">{artifact.type}</span>
              </div>
              <StatusBadge status={artifact.status} />
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
