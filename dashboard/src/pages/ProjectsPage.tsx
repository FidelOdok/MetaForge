import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useProjects, useCreateProject } from '../hooks/use-projects';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/Button';
import { Badge } from '../components/ui/Badge';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { SkeletonCard } from '../components/ui/Skeleton';
import { useToast } from '../components/ui/Toast';
import { formatRelativeTime } from '../utils/format-time';

// Map work product types to short domain labels
const DOMAIN_LABEL: Record<string, string> = {
  schematic: 'EE',
  pcb: 'PCB',
  cad_model: 'Mech',
  firmware: 'FW',
  bom: 'BOM',
  gerber: 'Mfg',
};

function getUniqueDomains(types: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const t of types) {
    const label = DOMAIN_LABEL[t] ?? t;
    if (!seen.has(label)) {
      seen.add(label);
      result.push(label);
    }
  }
  return result;
}

interface CreateProjectModalProps {
  onClose: () => void;
}

function CreateProjectModal({ onClose }: CreateProjectModalProps) {
  const createProject = useCreateProject();
  const toast = useToast();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    createProject.mutate(
      { name: name.trim(), description: description.trim() },
      {
        onSuccess: () => {
          setName('');
          setDescription('');
          onClose();
          toast.success('Project created successfully.');
        },
        onError: (err) => {
          toast.error((err as Error)?.message ?? 'Failed to create project.');
        },
      },
    );
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-md rounded-lg border border-zinc-200 bg-white p-6 shadow-xl dark:border-zinc-700 dark:bg-zinc-900">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="text-lg font-semibold text-zinc-900 dark:text-zinc-100">
            New Project
          </h3>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
          >
            &times;
          </button>
        </div>

        <form onSubmit={handleCreate} className="space-y-4">
          <div>
            <label
              htmlFor="project-name"
              className="mb-1 block text-sm font-medium text-zinc-700 dark:text-zinc-300"
            >
              Project name
            </label>
            <input
              id="project-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Drone Flight Controller"
              className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 shadow-sm placeholder:text-zinc-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100 dark:placeholder:text-zinc-500"
            />
          </div>
          <div>
            <label
              htmlFor="project-desc"
              className="mb-1 block text-sm font-medium text-zinc-700 dark:text-zinc-300"
            >
              Description
            </label>
            <textarea
              id="project-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="Brief project description"
              className="w-full rounded-md border border-zinc-300 bg-white px-3 py-2 text-sm text-zinc-900 shadow-sm placeholder:text-zinc-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100 dark:placeholder:text-zinc-500"
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" size="sm" type="button" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              disabled={!name.trim() || createProject.isPending}
            >
              {createProject.isPending ? 'Creating...' : 'Create Project'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function ProjectsPage() {
  const { data: projects, isLoading, isError, refetch } = useProjects();
  const [showModal, setShowModal] = useState(false);
  const [search, setSearch] = useState('');

  if (isLoading) {
    return (
      <div data-testid="loading-skeleton">
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">Projects</h2>
        </div>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <SkeletonCard key={i} />
          ))}
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div>
        <div className="mb-6 flex items-center justify-between">
          <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">Projects</h2>
        </div>
        <Card className="flex flex-col items-center py-12 text-center">
          <p className="text-base font-medium text-red-600 dark:text-red-400">
            Failed to load projects
          </p>
          <p className="mt-1 text-sm text-zinc-500">
            There was a problem fetching your projects.
          </p>
          <Button variant="secondary" className="mt-4" onClick={() => void refetch()}>
            Retry
          </Button>
        </Card>
      </div>
    );
  }

  const filtered = (projects ?? []).filter((p) =>
    p.name.toLowerCase().includes(search.toLowerCase()),
  );

  return (
    <div>
      {showModal && <CreateProjectModal onClose={() => setShowModal(false)} />}

      {/* Header row */}
      <div className="mb-6 flex flex-wrap items-center gap-3">
        <h2 className="text-xl font-semibold text-zinc-900 dark:text-zinc-100">Projects</h2>
        <span className="text-sm text-zinc-500">{projects?.length ?? 0} projects</span>

        <div className="ml-auto flex items-center gap-3">
          <input
            type="search"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search projects…"
            aria-label="Search projects"
            className="h-9 w-48 rounded-md border border-zinc-300 bg-white px-3 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-zinc-600 dark:bg-zinc-800 dark:text-zinc-100 dark:placeholder:text-zinc-500"
          />
          <Button
            type="button"
            variant="primary"
            onClick={() => setShowModal(true)}
          >
            New Project
          </Button>
        </div>
      </div>

      {/* Empty state — no projects at all */}
      {!projects?.length ? (
        <div className="flex flex-col items-center justify-center py-16 text-center">
          <svg
            className="mb-4 h-12 w-12 text-zinc-300 dark:text-zinc-600"
            fill="none"
            stroke="currentColor"
            strokeWidth={1.5}
            viewBox="0 0 24 24"
            aria-hidden="true"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M2.25 12.75V12A2.25 2.25 0 014.5 9.75h15A2.25 2.25 0 0121.75 12v.75m-8.69-6.44-2.12-2.12a1.5 1.5 0 00-1.061-.44H4.5A2.25 2.25 0 002.25 6v12a2.25 2.25 0 002.25 2.25h15A2.25 2.25 0 0021.75 18V9a2.25 2.25 0 00-2.25-2.25h-5.379a1.5 1.5 0 01-1.06-.44z"
            />
          </svg>
          <h3 className="text-lg font-medium text-zinc-700 dark:text-zinc-300">No projects yet</h3>
          <p className="mt-1 text-sm text-zinc-500">
            Create your first hardware project to get started.
          </p>
          <Button
            type="button"
            variant="primary"
            className="mt-4"
            onClick={() => setShowModal(true)}
          >
            Create your first project
          </Button>
        </div>
      ) : filtered.length === 0 ? (
        /* Empty state — search returned nothing */
        <EmptyState
          title="No matching projects"
          description={`No projects match "${search}".`}
        />
      ) : (
        /* Card grid */
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((project) => {
            const domains = getUniqueDomains(
              project.work_products.map((wp) => wp.type),
            );
            return (
              <div key={project.id} className="group relative">
                <Card className="flex h-full flex-col transition-shadow hover:shadow-md">
                  {/* Card header */}
                  <div className="mb-2 flex items-start justify-between">
                    <h3 className="font-medium text-zinc-900 dark:text-zinc-100">
                      {project.name}
                    </h3>
                    <StatusBadge status={project.status} />
                  </div>

                  {/* Description */}
                  <p className="mb-3 flex-1 text-sm text-zinc-500 line-clamp-2">
                    {project.description || 'No description'}
                  </p>

                  {/* Domain tags */}
                  {domains.length > 0 && (
                    <div className="mb-3 flex flex-wrap gap-1">
                      {domains.map((d) => (
                        <Badge key={d} variant="default">
                          {d}
                        </Badge>
                      ))}
                    </div>
                  )}

                  {/* Meta row */}
                  <div className="flex items-center justify-between text-xs text-zinc-400">
                    <span>
                      {project.work_products.length}{' '}
                      {project.work_products.length === 1 ? 'work product' : 'work products'}
                    </span>
                    <span>{formatRelativeTime(project.lastUpdated)}</span>
                  </div>

                  {/* Quick-action row — revealed on hover */}
                  <div className="mt-3 flex items-center gap-2 border-t border-zinc-100 pt-3 opacity-0 transition-opacity group-hover:opacity-100 dark:border-zinc-700">
                    <Link
                      to={`/projects/${project.id}`}
                      className="inline-flex h-7 items-center rounded-md bg-zinc-100 px-2.5 text-xs font-medium text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-600"
                    >
                      Open
                    </Link>
                    <Link
                      to={`/projects/${project.id}#sessions`}
                      className="inline-flex h-7 items-center rounded-md bg-zinc-100 px-2.5 text-xs font-medium text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-600"
                    >
                      Sessions
                    </Link>
                    <Link
                      to={`/projects/${project.id}#bom`}
                      className="inline-flex h-7 items-center rounded-md bg-zinc-100 px-2.5 text-xs font-medium text-zinc-700 hover:bg-zinc-200 dark:bg-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-600"
                    >
                      BOM
                    </Link>
                  </div>
                </Card>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
