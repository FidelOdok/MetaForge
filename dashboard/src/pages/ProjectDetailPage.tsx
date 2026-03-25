import { useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { useProject } from '../hooks/use-projects';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { Skeleton } from '../components/ui/Skeleton';
import { formatRelativeTime } from '../utils/format-time';
import { ImportZone } from '../components/ImportZone';

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: project, isLoading, isError, refetch } = useProject(id);
  const [importOpen, setImportOpen] = useState(false);

  if (isLoading) {
    return (
      <div data-testid="loading-skeleton">
        <div className="mb-1">
          <Skeleton className="h-4 w-20" />
        </div>
        <div className="mb-6 flex items-center gap-3">
          <Skeleton className="h-7 w-48" />
          <Skeleton className="h-5 w-16 rounded-full" />
        </div>
        <Skeleton className="mb-6 h-4 w-full max-w-lg" />
        <div className="mb-6 grid gap-4 sm:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i}>
              <Skeleton className="mb-2 h-8 w-12" />
              <Skeleton className="h-3 w-24" />
            </Card>
          ))}
        </div>
        <Skeleton className="mb-3 h-6 w-32" />
        <div className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <Card key={i} className="flex items-center justify-between py-3">
              <div className="space-y-1">
                <Skeleton className="h-4 w-40" />
                <Skeleton className="h-3 w-24" />
              </div>
              <Skeleton className="h-5 w-16 rounded-full" />
            </Card>
          ))}
        </div>
      </div>
    );
  }

  if (isError) {
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
        <Card className="mt-4 flex flex-col items-center py-12 text-center">
          <p className="text-base font-medium text-red-600 dark:text-red-400">
            Failed to load project
          </p>
          <p className="mt-1 text-sm text-zinc-500">
            There was a problem fetching project details.
          </p>
          <Button variant="secondary" className="mt-4" onClick={() => void refetch()}>
            Retry
          </Button>
        </Card>
      </div>
    );
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
            {project.work_products.length}
          </div>
          <div className="text-xs text-zinc-500">Work Products</div>
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
        Work Products
      </h3>

      {project.work_products.length === 0 ? (
        <EmptyState title="No work products" description="Run an agent to create work products." />
      ) : (
        <div className="space-y-2">
          {project.work_products.map((work_product) => (
            <Card key={work_product.id} className="flex items-center justify-between py-3">
              <div>
                <span className="font-medium text-zinc-900 dark:text-zinc-100">
                  {work_product.name}
                </span>
                <span className="ml-2 text-xs text-zinc-400">{work_product.type}</span>
              </div>
              <StatusBadge status={work_product.status} />
            </Card>
          ))}
        </div>
      )}

      {/* Import File section */}
      <div className="mt-6">
        <button
          type="button"
          onClick={() => setImportOpen((prev) => !prev)}
          className="flex items-center gap-1.5 text-sm font-medium text-zinc-700 hover:text-zinc-900 dark:text-zinc-300 dark:hover:text-zinc-100"
        >
          {importOpen ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          Import File
        </button>

        {importOpen && (
          <Card className="mt-3">
            <ImportZone
              projectId={project.id}
              onSuccess={() => void refetch()}
            />
          </Card>
        )}
      </div>
    </div>
  );
}
