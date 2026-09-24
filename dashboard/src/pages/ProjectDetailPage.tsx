import { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { useProject, useUpdateProject, useDeleteProject } from '../hooks/use-projects';
import { useActiveProject } from '../hooks/use-active-project';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { Button } from '../components/ui/Button';
import { useToast } from '../components/ui/Toast';
import { formatRelativeTime } from '../utils/format-time';

function getErrorMessage(error: unknown, fallback: string): string {
  if (error && typeof error === 'object' && 'response' in error) {
    const response = (error as { response?: { data?: { detail?: string } } }).response;
    if (typeof response?.data?.detail === 'string') return response.data.detail;
  }
  return fallback;
}

const glassCard = {
  background: 'var(--mf-r-30-31-38-0p85)',
} as const;

const workProductTypeIcon: Record<string, string> = {
  schematic: 'schema',
  pcb: 'developer_board',
  cad_model: 'view_in_ar',
  firmware: 'memory',
  bom: 'list_alt',
  gerber: 'layers',
};

const statusDotColor: Record<string, string> = {
  valid: 'var(--mf-c-3dd68c)',
  warning: 'var(--mf-c-f59e0b)',
  error: 'var(--mf-c-ffb4ab)',
  unknown: 'var(--mf-c-9a9aaa)',
};

function getStatusDotColor(status: string): string {
  return statusDotColor[status] ?? 'var(--mf-c-9a9aaa)';
}

// Artifact update snapshot, derived from the project's work products (most
// recently updated first). This is a point-in-time view, not a live feed.
interface ActivityEntry {
  tag: string;
  tagColor: string;
  tagBg: string;
  message: string;
  timestamp: string;
}

function buildActivityFeed(workProducts: { name: string; type: string; status: string; updatedAt: string }[]): ActivityEntry[] {
  return [...workProducts]
    .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))
    .slice(0, 8)
    .map((wp) => ({
      tag: wp.type.replace('_', ' '),
      tagColor: 'var(--mf-c-86cfff)',
      tagBg: 'rgba(134,207,255,0.1)',
      message: `${wp.name} \u00b7 ${wp.status}`,
      timestamp: formatRelativeTime(wp.updatedAt),
    }));
}

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: project, isLoading, isError, refetch } = useProject(id);
  const navigate = useNavigate();
  const toast = useToast();
  const updateProject = useUpdateProject();
  const deleteProject = useDeleteProject();
  const { activeProjectId, setActiveProjectId } = useActiveProject();

  useEffect(() => {
    if (id && id !== activeProjectId) setActiveProjectId(id);
  }, [id, activeProjectId, setActiveProjectId]);

  const [isEditing, setIsEditing] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);

  if (isLoading) {
    return (
      <div>
        {/* Skeleton header */}
        <div className="flex items-center gap-2 mb-4">
          <div className="h-3 rounded w-16 animate-pulse" style={{ background: 'var(--mf-r-65-72-90-0p4)' }} />
        </div>
        <div className="grid grid-cols-4 gap-3 mb-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="glass rounded p-4 animate-pulse" style={glassCard}>
              <div className="h-7 rounded w-12 mb-2" style={{ background: 'var(--mf-r-65-72-90-0p4)' }} />
              <div className="h-2 rounded w-20" style={{ background: 'var(--mf-r-65-72-90-0p3)' }} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (isError) {
    return (
      <div role="alert" className="workspace-empty">
        <h1>Project could not be loaded</h1>
        <p>Check your gateway connection, then try again.</p>
        <Button onClick={() => void refetch()}>Try again</Button>
        <Link to="/settings">Connection settings</Link>
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

  const activity = buildActivityFeed(project.work_products);
  const errorCount = project.work_products.filter((wp) => wp.status === 'error').length;
  const validCount = project.work_products.filter((wp) => wp.status === 'valid').length;
  const readiness = project.work_products.length > 0
    ? Math.round((validCount / project.work_products.length) * 100)
    : 0;

  const startEditing = () => {
    setEditName(project.name);
    setEditDescription(project.description);
    setIsEditing(true);
  };

  const handleSaveEdit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!editName.trim()) return;
    updateProject.mutate(
      { id: project.id, payload: { name: editName.trim(), description: editDescription.trim() } },
      {
        onSuccess: () => {
          toast.success('Project updated');
          setIsEditing(false);
        },
        onError: (error) => {
          toast.error(getErrorMessage(error, 'Failed to update project'));
        },
      },
    );
  };

  const handleDelete = () => {
    deleteProject.mutate(project.id, {
      onSuccess: () => {
        toast.success('Project deleted');
        navigate('/projects');
      },
      onError: (error) => {
        toast.error(getErrorMessage(error, 'Failed to delete project'));
        setShowDeleteConfirm(false);
      },
    });
  };

  return (
    <div>
      {/* Back nav */}
      <div className="mb-4 flex items-center gap-1.5">
        <Link
          to="/projects"
          className="flex items-center gap-1 font-mono transition-opacity hover:opacity-80"
          style={{ fontSize: '11px', color: 'var(--mf-c-9a9aaa)' }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: '14px', color: 'var(--mf-c-9a9aaa)' }}>arrow_back</span>
          Projects
        </Link>
        <span style={{ fontSize: '11px', color: 'var(--mf-c-9a9aaa)' }}>/</span>
        <span className="font-mono" style={{ fontSize: '11px', color: 'var(--mf-c-e2e2eb)' }}>{project.name}</span>
      </div>

      {/* Page header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-baseline gap-2">
          <span style={{ fontSize: '18px', fontWeight: 500, color: 'var(--mf-c-e2e2eb)', letterSpacing: '-0.02em' }}>
            {project.name}
          </span>
          <StatusBadge status={project.status} />
        </div>
        <div className="flex items-center gap-3">
          <span className="font-mono" style={{ fontSize: '11px', color: 'var(--mf-c-9a9aaa)' }}>
            updated {formatRelativeTime(project.lastUpdated)}
          </span>
          {!isEditing && (
            <div className="flex items-center gap-1">
              <button
                type="button"
                aria-label="Rename project"
                onClick={startEditing}
                className="flex items-center justify-center rounded transition-colors hover:bg-surface-high"
                style={{ width: 26, height: 26, color: 'var(--mf-c-9a9aaa)' }}
              >
                <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>edit</span>
              </button>
              <button
                type="button"
                aria-label="Delete project"
                onClick={() => setShowDeleteConfirm(true)}
                className="flex items-center justify-center rounded transition-colors hover:bg-surface-high"
                style={{ width: 26, height: 26, color: 'var(--mf-c-9a9aaa)' }}
              >
                <span className="material-symbols-outlined" style={{ fontSize: '16px' }}>delete</span>
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Primary project actions */}
      <div className="project-tools" style={{ marginBottom: 24 }}>
        <Link className="action-primary" to={`/runs/new?project=${encodeURIComponent(project.id)}`}>
          Start design run
        </Link>
        <Link className="action-secondary" to="/twin">
          Open twin &amp; agent
        </Link>
        <Link className="action-secondary" to="/bom">
          Bill of materials
        </Link>
      </div>

      {/* Description, or the rename/redescribe form */}
      {isEditing ? (
        <form onSubmit={handleSaveEdit} className="glass rounded p-4 mb-4 space-y-3" style={glassCard}>
          <div>
            <label
              htmlFor="edit-project-name"
              className="block mb-1 font-mono"
              style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--mf-c-9a9aaa)' }}
            >
              Project name
            </label>
            <input
              id="edit-project-name"
              type="text"
              value={editName}
              onChange={(e) => setEditName(e.target.value)}
              className="w-full rounded px-3 py-1.5 text-xs outline-none focus:border-[var(--mf-r-65-72-90-0p6)]"
              style={{ background: 'var(--mf-c-1e1f26)', border: '1px solid var(--mf-r-65-72-90-0p3)', color: 'var(--mf-c-e2e2eb)' }}
            />
          </div>
          <div>
            <label
              htmlFor="edit-project-desc"
              className="block mb-1 font-mono"
              style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--mf-c-9a9aaa)' }}
            >
              Description
            </label>
            <textarea
              id="edit-project-desc"
              value={editDescription}
              onChange={(e) => setEditDescription(e.target.value)}
              rows={2}
              className="w-full rounded px-3 py-1.5 text-xs outline-none resize-none focus:border-[var(--mf-r-65-72-90-0p6)]"
              style={{ background: 'var(--mf-c-1e1f26)', border: '1px solid var(--mf-r-65-72-90-0p3)', color: 'var(--mf-c-e2e2eb)' }}
            />
          </div>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" size="sm" onClick={() => setIsEditing(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" size="sm" disabled={!editName.trim() || updateProject.isPending}>
              {updateProject.isPending ? 'Saving...' : 'Save'}
            </Button>
          </div>
        </form>
      ) : (
        project.description && (
          <p className="mb-4 font-mono" style={{ fontSize: '11px', color: 'var(--mf-c-9a9aaa)', lineHeight: '1.6' }}>
            {project.description}
          </p>
        )
      )}

      {/* Metrics row */}
      <div className="grid grid-cols-4 gap-3 mb-4">
        {/* Work Products */}
        <div className="glass rounded p-4 relative overflow-hidden" style={glassCard}>
          <div style={{ fontSize: '28px', fontWeight: 300, color: 'var(--mf-c-e2e2eb)', lineHeight: 1, letterSpacing: '-0.02em' }}>
            {project.work_products.length}
          </div>
          <div className="font-mono mt-1" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--mf-c-9a9aaa)' }}>
            Work Products
          </div>
        </div>

        {/* Gate Readiness */}
        <div className="glass rounded p-4 relative overflow-hidden" style={glassCard}>
          <div style={{ fontSize: '28px', fontWeight: 300, color: 'var(--mf-c-3dd68c)', lineHeight: 1, letterSpacing: '-0.02em' }}>
            {readiness}%
          </div>
          <div className="font-mono mt-1" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--mf-c-9a9aaa)' }}>
            Artifacts marked valid
          </div>
          <div className="absolute" style={{ right: '14px', bottom: '14px' }}>
            <svg width="38" height="38" viewBox="0 0 38 38">
              <circle cx="19" cy="19" r="14" fill="none" stroke="rgba(61,214,140,0.12)" strokeWidth="3" />
              <circle
                cx="19" cy="19" r="14" fill="none"
                stroke="var(--mf-c-3dd68c)"
                strokeWidth="3"
                strokeLinecap="round"
                strokeDasharray="87.96"
                strokeDashoffset={String(87.96 * (1 - readiness / 100))}
                transform="rotate(-90 19 19)"
                opacity="0.85"
              />
            </svg>
          </div>
        </div>

        {/* Active Agents */}
        <div className="glass rounded p-4 relative overflow-hidden" style={glassCard}>
          <div className="font-mono" style={{ fontSize: '28px', fontWeight: 300, color: 'var(--mf-c-86cfff)', lineHeight: 1, letterSpacing: '-0.02em' }}>
            {project.agentCount}
          </div>
          <div className="font-mono mt-1" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--mf-c-9a9aaa)' }}>
            Agent task count
          </div>
        </div>

        {/* Risk Alerts */}
        <div className="glass rounded p-4 relative overflow-hidden" style={glassCard}>
          <div style={{ fontSize: '28px', fontWeight: 300, color: errorCount > 0 ? 'var(--mf-c-ffb4ab)' : 'var(--mf-c-e2e2eb)', lineHeight: 1, letterSpacing: '-0.02em' }}>
            {errorCount}
          </div>
          <div className="font-mono mt-1" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: 'var(--mf-c-9a9aaa)' }}>
            Risk Alerts
          </div>
          {errorCount > 0 && (
            <div className="absolute" style={{ right: '14px', bottom: '18px' }}>
              <div className="pulse-ring relative" style={{ width: '6px', height: '6px', borderRadius: '50%', background: 'var(--mf-c-ffb4ab)' }} />
            </div>
          )}
        </div>
      </div>

      {/* Two-column: work products + activity */}
      <div className="project-detail-columns grid gap-3">

        {/* Work Products panel */}
        <div className="glass rounded overflow-hidden" style={glassCard}>
          {/* Panel header */}
          <div
            className="flex items-center justify-between px-4 py-2"
            style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}
          >
            <span className="font-mono" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--mf-c-9a9aaa)' }}>
              Work Products
            </span>
            <span className="font-mono" style={{ fontSize: '10px', color: 'var(--mf-c-9a9aaa)' }}>
              {project.work_products.length} total
            </span>
          </div>

          {project.work_products.length === 0 ? (
            <div className="px-4 py-8">
              <EmptyState title="No work products" description="Run an agent to create work products." />
            </div>
          ) : (
            project.work_products.map((wp) => (
              <Link
                key={wp.id}
                to={`/twin?node=${wp.id}`}
                className="flex items-center gap-3 px-4 hover:bg-surface-high cursor-pointer"
                style={{ height: '40px' }}
              >
                {/* Status dot */}
                <span
                  style={{
                    width: '6px',
                    height: '6px',
                    borderRadius: '50%',
                    background: getStatusDotColor(wp.status),
                    flexShrink: 0,
                    display: 'inline-block',
                  }}
                />

                {/* Type icon */}
                <span
                  className="material-symbols-outlined flex-shrink-0"
                  style={{ fontSize: '14px', color: 'var(--mf-c-9a9aaa)' }}
                >
                  {workProductTypeIcon[wp.type] ?? 'description'}
                </span>

                {/* Name */}
                <span
                  className="flex-1 text-sm font-medium"
                  style={{ color: 'var(--mf-c-e2e2eb)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
                >
                  {wp.name}
                </span>

                {/* Type pill */}
                <span
                  className="font-mono rounded flex-shrink-0"
                  style={{ fontSize: '10px', color: 'var(--mf-c-9a9aaa)', background: 'var(--mf-c-282a30)', padding: '1px 5px' }}
                >
                  {wp.type}
                </span>

                {/* Status badge */}
                <StatusBadge status={wp.status} />

                {/* Timestamp */}
                <span
                  className="font-mono flex-shrink-0"
                  style={{ fontSize: '10px', color: 'var(--mf-c-9a9aaa)', minWidth: '60px', textAlign: 'right' }}
                >
                  {formatRelativeTime(wp.updatedAt)}
                </span>
              </Link>
            ))
          )}
        </div>

        {/* Artifact updates panel */}
        <div className="glass rounded overflow-hidden" style={glassCard}>
          {/* Panel header */}
          <div
            className="flex items-center justify-between px-4 py-2"
            style={{ borderBottom: '1px solid var(--mf-r-65-72-90-0p2)' }}
          >
            <span className="font-mono" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.1em', color: 'var(--mf-c-9a9aaa)' }}>
              Artifact updates
            </span>
            <div className="flex items-center gap-1.5">
              <span
                className="live-dot"
                style={{ width: '5px', height: '5px', borderRadius: '50%', background: 'var(--mf-c-3dd68c)', display: 'inline-block' }}
              />
              <span className="font-mono" style={{ fontSize: '10px', color: 'var(--mf-c-3dd68c)', letterSpacing: '0.06em' }}>
                SNAPSHOT
              </span>
            </div>
          </div>

          {activity.length === 0 ? (
            <div className="px-4 py-6 text-center">
              <span className="font-mono" style={{ fontSize: '10px', color: 'var(--mf-c-9a9aaa)' }}>
                No work-product updates
              </span>
            </div>
          ) : (
            activity.map((entry, i) => (
              <div
                key={i}
                className="flex items-center gap-2 px-4 hover:bg-surface-high cursor-default"
                style={{ height: '32px' }}
              >
                <span
                  className="font-mono rounded flex-shrink-0"
                  style={{ fontSize: '10px', padding: '1px 6px', background: entry.tagBg, color: entry.tagColor }}
                >
                  {entry.tag}
                </span>
                <span
                  style={{ fontSize: '12px', color: 'var(--mf-c-9a9aaa)', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
                >
                  {entry.message}
                </span>
                <span className="font-mono flex-shrink-0" style={{ fontSize: '10px', color: 'var(--mf-c-9a9aaa)' }}>
                  {entry.timestamp}
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Delete confirmation */}
      {showDeleteConfirm && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center"
          style={{ background: 'rgba(0,0,0,0.5)' }}
        >
          <div className="w-full max-w-sm rounded p-5" style={{ background: 'var(--mf-c-1e1f26)', border: '1px solid var(--mf-r-65-72-90-0p3)' }}>
            <div className="flex items-center gap-2 mb-3">
              <span className="material-symbols-outlined" style={{ fontSize: '18px', color: 'var(--mf-c-ffb4ab)' }}>warning</span>
              <span style={{ fontSize: '14px', fontWeight: 500, color: 'var(--mf-c-e2e2eb)' }}>Delete project</span>
            </div>
            <p className="mb-4" style={{ fontSize: '12px', color: 'var(--mf-c-9a9aaa)', lineHeight: '1.5' }}>
              Delete <strong style={{ color: 'var(--mf-c-e2e2eb)' }}>{project.name}</strong>? This removes the
              project record and cannot be undone.
              {project.work_products.length > 0 && (
                <>
                  {' '}
                  Its {project.work_products.length} work product
                  {project.work_products.length === 1 ? '' : 's'} will remain in the Digital Twin,
                  unlinked from this project.
                </>
              )}
            </p>
            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setShowDeleteConfirm(false)}
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="danger"
                size="sm"
                onClick={handleDelete}
                disabled={deleteProject.isPending}
              >
                {deleteProject.isPending ? 'Deleting...' : 'Delete'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
