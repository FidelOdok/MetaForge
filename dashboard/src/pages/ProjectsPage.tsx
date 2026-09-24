import { useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  ArrowRight,
  ArrowUpRight,
  Box,
  Boxes,
  CheckCheck,
  CircleAlert,
  FileBox,
  LayoutGrid,
  List,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Workflow,
  X,
  type LucideIcon,
} from 'lucide-react';
import { useProjects } from '../hooks/use-projects';
import { useRuns } from '../hooks/use-runs';
import { useHealth } from '../hooks/use-health';
import { formatRelativeTime } from '../utils/format-time';
import {
  CreateProjectDialog,
  type CreateProjectDialogHandle,
} from '../components/projects/CreateProjectDialog';
import type { Project, ProjectWorkProduct } from '../types/project';

type StatusFilter = 'all' | Project['status'];
type SortKey = 'updated' | 'name';
type ViewMode = 'grid' | 'list';

const STATUS_FILTERS: StatusFilter[] = ['all', 'active', 'draft', 'archived'];

interface WorkspaceArtifact extends ProjectWorkProduct {
  project: Project;
}

/** Two-digit metric value, or an em dash while loading / unavailable. */
function metricValue(n: number, unavailable: boolean, loading: boolean): string {
  return unavailable || loading ? '—' : n.toString().padStart(2, '0');
}

function filterLabel(status: StatusFilter): string {
  return status === 'all' ? 'All projects' : status.charAt(0).toUpperCase() + status.slice(1);
}

function ProjectCard({ project }: { project: Project }) {
  return (
    <Link className="project-card" to={`/projects/${project.id}`}>
      <div className="project-card-top">
        <span className="project-symbol">
          <Box size={23} />
        </span>
        <span className={`status-tag status-${project.status}`}>{project.status}</span>
      </div>
      <div className="project-card-copy">
        <h3>{project.name}</h3>
        <p>{project.description || 'No description added.'}</p>
      </div>
      <div className="project-card-meta">
        <span>
          <FileBox size={14} />
          {project.work_products.length} artifacts
        </span>
        <span>
          <Workflow size={14} />
          {project.agentCount} agent tasks
        </span>
      </div>
      <div className="project-card-bottom">
        <span>Updated {formatRelativeTime(project.lastUpdated)}</span>
        <ArrowUpRight size={17} aria-hidden="true" />
      </div>
    </Link>
  );
}

interface MetricCardProps {
  label: string;
  value: string;
  detail: string;
  icon: LucideIcon;
  to: string;
}

function MetricCard({ label, value, detail, icon: Icon, to }: MetricCardProps) {
  const body = (
    <>
      <div className="metric-label">
        {label}
        <Icon size={18} aria-hidden="true" />
      </div>
      <strong>{value}</strong>
      <span>{detail}</span>
    </>
  );
  return to.startsWith('#') ? (
    <a href={to} className="metric-card">
      {body}
    </a>
  ) : (
    <Link to={to} className="metric-card">
      {body}
    </Link>
  );
}

export function ProjectsPage() {
  const projectsQuery = useProjects();
  const runsQuery = useRuns();
  const healthQuery = useHealth();
  const projects = projectsQuery.data ?? [];
  const runs = runsQuery.data ?? [];

  const dialogRef = useRef<CreateProjectDialogHandle>(null);
  const newProjectButtonRef = useRef<HTMLButtonElement>(null);

  const [query, setQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [view, setView] = useState<ViewMode>('grid');
  const [sort, setSort] = useState<SortKey>('updated');

  const projectsUnavailable = projectsQuery.isError;
  const activeCount = projects.filter((p) => p.status === 'active').length;
  const artifacts: WorkspaceArtifact[] = projects.flatMap((p) =>
    p.work_products.map((wp) => ({ ...wp, project: p })),
  );
  const awaitingReview = runs.filter((r) => r.status === 'awaiting_approval');
  const inProgress = runs.filter((r) => r.status === 'running' || r.status === 'queued');

  const q = query.toLowerCase();
  const visibleProjects = projects
    .filter(
      (p) =>
        (statusFilter === 'all' || p.status === statusFilter) &&
        `${p.name} ${p.description ?? ''}`.toLowerCase().includes(q),
    )
    .sort((a, b) =>
      sort === 'name'
        ? a.name.localeCompare(b.name)
        : Date.parse(b.lastUpdated) - Date.parse(a.lastUpdated),
    );

  const recentArtifacts = [...artifacts]
    .sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt))
    .slice(0, 4);

  const openCreate = () => dialogRef.current?.open();
  const clearFilters = () => {
    setQuery('');
    setStatusFilter('all');
  };
  const retry = () => {
    void projectsQuery.refetch();
    void runsQuery.refetch();
    void healthQuery.refetch();
  };

  const metrics: MetricCardProps[] = [
    {
      label: 'Active projects',
      value: metricValue(activeCount, projectsUnavailable, projectsQuery.isLoading),
      detail: projectsUnavailable ? 'Project data unavailable' : `${projects.length} projects in workspace`,
      icon: Boxes,
      to: '#project-library',
    },
    {
      label: 'Runs in progress',
      value: metricValue(inProgress.length, runsQuery.isError, runsQuery.isLoading),
      detail: 'Queued and running',
      icon: Workflow,
      to: '/runs',
    },
    {
      label: 'Awaiting review',
      value: metricValue(awaitingReview.length, runsQuery.isError, runsQuery.isLoading),
      detail: 'Human approval required',
      icon: ShieldCheck,
      to: '/approvals',
    },
    {
      label: 'Work products',
      value: metricValue(artifacts.length, projectsUnavailable, projectsQuery.isLoading),
      detail: 'Across all projects',
      icon: FileBox,
      to: '/files',
    },
  ];

  let libraryBody: React.ReactNode;
  if (projectsQuery.isLoading) {
    libraryBody = (
      <div className="workspace-empty" role="status">
        <RefreshCw size={28} />
        <h3>Loading your workspace</h3>
        <p>Retrieving projects from the gateway.</p>
      </div>
    );
  } else if (projectsUnavailable) {
    libraryBody = (
      <div className="workspace-empty">
        <Boxes size={38} />
        <h3>Your workspace, connected.</h3>
        <p>Projects and their engineering evidence will appear here once your gateway is available.</p>
        <Link to="/settings" className="text-action">
          Configure gateway
          <ArrowRight size={16} />
        </Link>
      </div>
    );
  } else if (visibleProjects.length === 0) {
    const hasProjects = projects.length > 0;
    libraryBody = (
      <div className="workspace-empty">
        <Boxes size={34} />
        <h3>{hasProjects ? 'No matching projects' : 'Start with an engineering intent'}</h3>
        <p>
          {hasProjects
            ? 'Try a different search or status filter.'
            : 'Create your first project to bring requirements, work products, and agent tasks together.'}
        </p>
        <button type="button" className="action-secondary" onClick={hasProjects ? clearFilters : openCreate}>
          {hasProjects ? 'Clear filters' : 'Create a project'}
        </button>
      </div>
    );
  } else {
    libraryBody = (
      <div className={`project-collection ${view}`}>
        {visibleProjects.map((p) => (
          <ProjectCard key={p.id} project={p} />
        ))}
      </div>
    );
  }

  return (
    <div className="projects-workspace">
      <div className="page-heading">
        <div>
          <p className="eyebrow">YOUR ENGINEERING WORKSPACE</p>
          <h1>
            Projects<span className="heading-period">.</span>
          </h1>
          <p className="page-description">From intent to evidence. Keep every iteration in view.</p>
        </div>
        <button ref={newProjectButtonRef} type="button" className="action-primary" onClick={openCreate}>
          <Plus size={18} />
          New project
        </button>
      </div>

      <section className="workspace-metrics" aria-label="Workspace summary">
        {metrics.map((m) => (
          <MetricCard key={m.label} {...m} />
        ))}
      </section>

      {projectsUnavailable && (
        <section className="connection-notice" aria-label="Gateway connection">
          <div className="connection-icon">
            <CircleAlert size={22} />
          </div>
          <div>
            <h2>Connect your engineering gateway</h2>
            <p>
              Project data couldn&rsquo;t be loaded. Connect your MetaForge gateway to work with your projects,
              agents, and artifacts.
            </p>
          </div>
          <div className="connection-actions">
            <button type="button" className="action-secondary" onClick={retry}>
              <RefreshCw size={16} />
              Retry
            </button>
            <Link className="action-primary" to="/settings">
              Set up connection
              <ArrowRight size={16} />
            </Link>
          </div>
        </section>
      )}

      <div className="workspace-columns">
        <section id="project-library" className="project-library" aria-labelledby="project-library-title">
          <div className="section-heading">
            <h2 id="project-library-title">
              Project library{' '}
              <span className="count-badge">{projectsUnavailable ? '—' : projects.length}</span>
            </h2>
            <div className="view-switch" role="group" aria-label="Project view">
              <button type="button" aria-label="Grid view" aria-pressed={view === 'grid'} onClick={() => setView('grid')}>
                <LayoutGrid size={17} />
              </button>
              <button type="button" aria-label="List view" aria-pressed={view === 'list'} onClick={() => setView('list')}>
                <List size={18} />
              </button>
            </div>
          </div>

          <div className="project-toolbar">
            <label className="project-search">
              <Search size={17} aria-hidden="true" />
              <input
                aria-label="Search projects"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search projects…"
              />
              {query && (
                <button type="button" aria-label="Clear search" onClick={() => setQuery('')}>
                  <X size={15} />
                </button>
              )}
            </label>
            <select aria-label="Sort projects" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
              <option value="updated">Recently updated</option>
              <option value="name">Name A–Z</option>
            </select>
          </div>

          <div className="project-filters" role="group" aria-label="Filter by status">
            {STATUS_FILTERS.map((s) => (
              <button key={s} type="button" aria-pressed={statusFilter === s} onClick={() => setStatusFilter(s)}>
                {filterLabel(s)}
                {s === 'all' && <span>{projects.length}</span>}
              </button>
            ))}
          </div>

          {libraryBody}

          <div className="library-footer">
            <span>
              {projectsUnavailable ? 'Waiting for gateway' : `${visibleProjects.length} of ${projects.length} projects`}
            </span>
            <span>Versioned. Reviewable. Buildable.</span>
          </div>
        </section>

        <aside className="workspace-context">
          <section className="context-panel">
            <div className="section-heading">
              <h2>Review queue</h2>
              <ShieldCheck size={18} />
            </div>
            {runsQuery.isError ? (
              <div className="context-empty">
                <CircleAlert size={25} />
                <h3>Review data unavailable</h3>
                <p>Reconnect to see runs waiting for your decision.</p>
              </div>
            ) : awaitingReview.length > 0 ? (
              <div className="review-list">
                {awaitingReview.slice(0, 4).map((run) => (
                  <Link key={run.id} to={`/runs/${run.id}`}>
                    <span className="status-tag status-draft">Needs approval</span>
                    <strong>{String(run.request.goal ?? run.id)}</strong>
                    <span>{run.approvalReason || 'Review the evidence before continuing.'}</span>
                    <span className="text-action">
                      Review run
                      <ArrowUpRight size={15} />
                    </span>
                  </Link>
                ))}
              </div>
            ) : (
              <div className="context-empty">
                <CheckCheck size={28} />
                <h3>{runsQuery.isLoading ? 'Checking review queue' : 'No pending reviews'}</h3>
                <p>Runs paused at approval gates appear here.</p>
              </div>
            )}
            <Link to="/approvals" className="context-footer">
              Open approvals
              <ArrowRight size={16} />
            </Link>
          </section>

          <section className="context-panel">
            <div className="section-heading">
              <h2>Recent artifacts</h2>
              <FileBox size={18} />
            </div>
            {recentArtifacts.length > 0 ? (
              <div className="artifact-list">
                {recentArtifacts.map((a) => (
                  <Link key={`${a.project.id}-${a.id}`} to={`/projects/${a.project.id}`}>
                    <FileBox size={18} />
                    <span>
                      <strong>{a.name}</strong>
                      <small>
                        {a.project.name} · {a.status}
                      </small>
                    </span>
                  </Link>
                ))}
              </div>
            ) : (
              <div className="context-empty compact">
                <p>
                  {projectsUnavailable
                    ? 'Connect your gateway to view artifacts.'
                    : 'Your latest work products will appear here.'}
                </p>
              </div>
            )}
            <Link to="/files" className="context-footer">
              Browse files
              <ArrowRight size={16} />
            </Link>
          </section>

          {healthQuery.data && (
            <section className="context-panel">
              <div className="section-heading">
                <h2>System health</h2>
                <span className="status-tag">{healthQuery.data.status}</span>
              </div>
              <ul className="health-components">
                {healthQuery.data.components.map((c) => (
                  <li key={c.name}>
                    <span>{c.name}</span>
                    <span title={c.message ?? undefined}>
                      {c.status}
                      {c.latency_ms != null ? ` · ${Math.round(c.latency_ms)}ms` : ''}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="engineering-note">
            <span className="eyebrow">THE ENGINEERING LOOP</span>
            <h2>Evidence before execution.</h2>
            <p>Define the intent, inspect the design, test the assumptions, and review the result.</p>
            <div className="loop-labels">
              <span>Define</span>
              <span>Design</span>
              <span>Validate</span>
              <span>Review</span>
            </div>
          </section>
        </aside>
      </div>

      <footer className="workspace-footer">
        <span>METAFORGE / KINETIC CONSOLE</span>
        <span>Local-first engineering. Human-led decisions.</span>
      </footer>

      <CreateProjectDialog ref={dialogRef} returnFocusRef={newProjectButtonRef} />
    </div>
  );
}
