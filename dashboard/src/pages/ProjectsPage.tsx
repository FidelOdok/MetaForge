import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useProjects, useCreateProject } from '../hooks/use-projects';
import { useHealth } from '../hooks/use-health';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { formatRelativeTime } from '../utils/format-time';
import type { DependencyStatus } from '../types/health';

// Glass card style matching Kinetic Console spec
const glassCard = {
  background: 'rgba(30,31,38,0.85)',
} as const;

const statusDotColor: Record<string, string> = {
  active: '#3dd68c',
  running: '#3dd68c',
  draft: '#f59e0b',
  archived: '#9a9aaa',
  completed: '#86cfff',
  failed: '#ffb4ab',
};

function getStatusDotColor(status: string): string {
  return statusDotColor[status] ?? '#9a9aaa';
}

function isLiveStatus(status: string): boolean {
  return status === 'active' || status === 'running';
}

function SkeletonCard() {
  return (
    <div
      className="rounded p-4 animate-pulse"
      style={glassCard}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="h-3 rounded w-1/2" style={{ background: 'rgba(65,72,90,0.4)' }} />
        <div className="h-4 rounded w-14" style={{ background: 'rgba(65,72,90,0.4)' }} />
      </div>
      <div className="h-2 rounded w-3/4 mb-2" style={{ background: 'rgba(65,72,90,0.3)' }} />
      <div className="h-2 rounded w-1/2 mb-4" style={{ background: 'rgba(65,72,90,0.3)' }} />
      <div className="flex items-center justify-between">
        <div className="h-2 rounded w-16" style={{ background: 'rgba(65,72,90,0.3)' }} />
        <div className="h-2 rounded w-16" style={{ background: 'rgba(65,72,90,0.3)' }} />
        <div className="h-2 rounded w-16" style={{ background: 'rgba(65,72,90,0.3)' }} />
      </div>
    </div>
  );
}

// ── System Health row ─────────────────────────────────────────────────────────
// Backed by GET /health (api_gateway/health.py) — replaces a previous version
// that hardcoded Neo4j/Kafka/pgvector/Temporal/MinIO with fake latencies
// regardless of real status.

const DEPENDENCY_DOT_COLOR: Record<DependencyStatus, string> = {
  healthy: '#3dd68c',
  degraded: '#f59e0b',
  unhealthy: '#ffb4ab',
};

function SystemHealthRow() {
  const { data: health, isLoading } = useHealth();

  if (isLoading || !health || health.components.length === 0) return null;

  return (
    <div className="grid gap-3 mb-4" style={{ gridTemplateColumns: `repeat(${health.components.length}, 1fr)` }}>
      {health.components.map((component) => (
        <div
          key={component.name}
          title={component.message ?? undefined}
          style={{
            background: 'rgba(30,31,38,0.85)',
            padding: '10px 12px',
            borderRadius: 4,
            border: '1px solid rgba(65,72,90,0.2)',
          }}
        >
          <div style={{ fontSize: '12px', color: '#9a9aaa', marginBottom: 4 }}>
            {component.name}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: DEPENDENCY_DOT_COLOR[component.status],
                display: 'inline-block',
                flexShrink: 0,
              }}
            />
            <span className="font-mono" style={{ fontSize: '11px', color: DEPENDENCY_DOT_COLOR[component.status] }}>
              {component.latency_ms != null ? `${Math.round(component.latency_ms)}ms` : component.status}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

// ── Page component ────────────────────────────────────────────────────────────

export function ProjectsPage() {
  const { data: projects, isLoading } = useProjects();
  const { data: health } = useHealth();
  const createProject = useCreateProject();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [filter, setFilter] = useState('');

  const q = filter.trim().toLowerCase();
  const filteredProjects = q
    ? (projects ?? []).filter(
        (p) =>
          p.name.toLowerCase().includes(q) ||
          (p.description ?? '').toLowerCase().includes(q),
      )
    : (projects ?? []);

  const activeCount = projects?.filter((p) => p.status === 'active').length ?? 0;

  function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    createProject.mutate(
      { name: name.trim(), description: description.trim() },
      {
        onSuccess: () => {
          setName('');
          setDescription('');
          setShowForm(false);
        },
      },
    );
  }

  return (
    <div>
      {/* Page header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-baseline gap-2">
          <span style={{ fontSize: '18px', fontWeight: 500, color: '#e2e2eb', letterSpacing: '-0.02em' }}>
            Projects
          </span>
          <span style={{ fontSize: '12px', color: '#9a9aaa' }}>
            overview · {activeCount} active
          </span>
        </div>
        {health && (
          <span className="font-mono flex items-center gap-1.5" style={{ fontSize: '11px', color: '#9a9aaa' }}>
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                display: 'inline-block',
                background: DEPENDENCY_DOT_COLOR[health.status],
              }}
            />
            gateway {health.status} · checked {formatRelativeTime(health.timestamp)}
          </span>
        )}
      </div>

      {/* Metrics row */}
      <div className="grid grid-cols-4 gap-3 mb-4">
        {/* Total Projects */}
        <div className="glass rounded p-4 relative overflow-hidden" style={glassCard}>
          <div style={{ fontSize: '28px', fontWeight: 300, color: '#e2e2eb', lineHeight: 1, letterSpacing: '-0.02em' }}>
            {isLoading ? '—' : (projects?.length ?? 0)}
          </div>
          <div className="font-mono mt-1" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: '#9a9aaa' }}>
            Total Projects
          </div>
        </div>

        {/* Active */}
        <div className="glass rounded p-4 relative overflow-hidden" style={glassCard}>
          <div style={{ fontSize: '28px', fontWeight: 300, color: '#3dd68c', lineHeight: 1, letterSpacing: '-0.02em' }}>
            {isLoading ? '—' : activeCount}
          </div>
          <div className="font-mono mt-1" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: '#9a9aaa' }}>
            Active
          </div>
        </div>

        {/* Work Products */}
        <div className="glass rounded p-4 relative overflow-hidden" style={glassCard}>
          <div className="font-mono" style={{ fontSize: '28px', fontWeight: 300, color: '#86cfff', lineHeight: 1, letterSpacing: '-0.02em' }}>
            {isLoading ? '—' : (projects?.reduce((sum, p) => sum + p.work_products.length, 0) ?? 0)}
          </div>
          <div className="font-mono mt-1" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: '#9a9aaa' }}>
            Work Products
          </div>
        </div>

        {/* Agents */}
        <div className="glass rounded p-4 relative overflow-hidden" style={glassCard}>
          <div style={{ fontSize: '28px', fontWeight: 300, color: '#ffb783', lineHeight: 1, letterSpacing: '-0.02em' }}>
            {isLoading ? '—' : (projects?.reduce((sum, p) => sum + p.agentCount, 0) ?? 0)}
          </div>
          <div className="font-mono mt-1" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: '#9a9aaa' }}>
            Agent Tasks
          </div>
        </div>
      </div>

      {/* System Health row */}
      <SystemHealthRow />

      {/* Toolbar: search + new project */}
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined" style={{ fontSize: '16px', color: '#9a9aaa' }}>search</span>
          <input
            type="text"
            placeholder="Filter projects..."
            className="bg-surface-high border border-[rgba(65,72,90,0.3)] text-on-surface text-xs rounded px-3 py-1.5 placeholder:text-on-surface-variant outline-none focus:border-[rgba(65,72,90,0.6)]"
            style={{ width: '220px' }}
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
        </div>
        <button
          type="button"
          onClick={() => setShowForm(!showForm)}
          className="flex items-center gap-1.5 rounded px-3 py-1.5 text-xs font-medium transition-opacity hover:opacity-90"
          style={{ background: '#e67e22', color: '#fff' }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: '14px' }}>add</span>
          {showForm ? 'Cancel' : 'New Project'}
        </button>
      </div>

      {/* New project form */}
      {showForm && (
        <div className="glass rounded p-4 mb-4" style={glassCard}>
          <form onSubmit={handleCreate} className="space-y-3">
            <div>
              <label
                htmlFor="project-name"
                className="block mb-1 font-mono"
                style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: '#9a9aaa' }}
              >
                Project name
              </label>
              <input
                id="project-name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Drone Flight Controller"
                className="w-full rounded px-3 py-1.5 text-xs outline-none focus:border-[rgba(65,72,90,0.6)]"
                style={{
                  background: '#1e1f26',
                  border: '1px solid rgba(65,72,90,0.3)',
                  color: '#e2e2eb',
                }}
              />
            </div>
            <div>
              <label
                htmlFor="project-desc"
                className="block mb-1 font-mono"
                style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: '#9a9aaa' }}
              >
                Description
              </label>
              <textarea
                id="project-desc"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                rows={2}
                placeholder="Brief project description"
                className="w-full rounded px-3 py-1.5 text-xs outline-none resize-none focus:border-[rgba(65,72,90,0.6)]"
                style={{
                  background: '#1e1f26',
                  border: '1px solid rgba(65,72,90,0.3)',
                  color: '#e2e2eb',
                }}
              />
            </div>
            <button
              type="submit"
              disabled={!name.trim() || createProject.isPending}
              className="rounded px-3 py-1.5 text-xs font-medium transition-opacity hover:opacity-90 disabled:opacity-50"
              style={{ background: '#e67e22', color: '#fff' }}
            >
              {createProject.isPending ? 'Creating...' : 'Create Project'}
            </button>
          </form>
        </div>
      )}

      {/* Project cards */}
      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : !projects?.length ? (
        <EmptyState
          title="No projects yet"
          description="Create a project with the button above or run 'forge setup' to get started."
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filteredProjects.map((project) => (
            <Link key={project.id} to={`/projects/${project.id}`}>
              <div
                className="glass rounded p-4 cursor-pointer transition-colors hover:bg-[rgba(40,42,48,0.85)]"
                style={glassCard}
              >
                {/* Card header */}
                <div className="flex items-start justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <span
                      className={isLiveStatus(project.status) ? 'live-dot' : undefined}
                      style={{
                        width: '6px',
                        height: '6px',
                        borderRadius: '50%',
                        background: getStatusDotColor(project.status),
                        flexShrink: 0,
                        display: 'inline-block',
                      }}
                    />
                    <span className="text-sm font-medium" style={{ color: '#e2e2eb' }}>
                      {project.name}
                    </span>
                  </div>
                  <StatusBadge status={project.status} />
                </div>

                {/* Description */}
                {project.description && (
                  <p
                    className="font-mono mb-3 line-clamp-2"
                    style={{ fontSize: '11px', color: '#9a9aaa', lineHeight: '1.5' }}
                  >
                    {project.description}
                  </p>
                )}

                {/* Footer metadata */}
                <div className="flex items-center justify-between">
                  <span className="font-mono" style={{ fontSize: '10px', color: '#9a9aaa' }}>
                    {project.work_products.length} work products
                  </span>
                  <span className="font-mono" style={{ fontSize: '10px', color: '#9a9aaa' }}>
                    {project.agentCount} agents
                  </span>
                  <span className="font-mono" style={{ fontSize: '10px', color: '#9a9aaa' }}>
                    {formatRelativeTime(project.lastUpdated)}
                  </span>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
