import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useProjects, useCreateProject } from '../hooks/use-projects';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { formatRelativeTime } from '../utils/format-time';

// Glass card style matching Kinetic Console spec
const glassCard = {
  background: 'rgba(30,31,38,0.85)',
} as const;

const glassPanel = {
  background: 'rgba(30,31,38,0.85)',
  backdropFilter: 'blur(16px)',
  borderRadius: 4,
  border: '1px solid rgba(65,72,90,0.2)',
} as const;

const panelHeader = {
  height: 36,
  borderBottom: '1px solid rgba(65,72,90,0.2)',
  padding: '0 16px',
  display: 'flex',
  alignItems: 'center',
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

// ── Data Flows panel ──────────────────────────────────────────────────────────

interface DataFlowRow {
  dotColor: string;
  name: string;
  statusLabel: string;
  statusColor: string;
  time: string;
  endpoint: string;
}

const DATA_FLOW_ROWS: DataFlowRow[] = [
  { dotColor: '#3dd68c', name: 'File Save → Twin',  statusLabel: 'active',         statusColor: '#9a9aaa', time: '2s ago',  endpoint: '/twin/save' },
  { dotColor: '#3dd68c', name: 'Graph Projection',  statusLabel: 'active',         statusColor: '#9a9aaa', time: '14s ago', endpoint: '/twin/project' },
  { dotColor: '#86cfff', name: 'RAG Query',         statusLabel: 'active',         statusColor: '#9a9aaa', time: '1m ago',  endpoint: '/knowledge/query' },
  { dotColor: '#f59e0b', name: 'BOM Risk Check',    statusLabel: 'degraded',       statusColor: '#f59e0b', time: '4m ago',  endpoint: '/bom/risk' },
  { dotColor: '#9a9aaa', name: 'Drift Check',       statusLabel: 'running · 60s',  statusColor: '#9a9aaa', time: '58s ago', endpoint: '/drift/check' },
];

function DataFlowsPanel() {
  return (
    <div style={glassPanel}>
      <div style={panelHeader}>
        <span
          className="font-mono"
          style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: '#9a9aaa' }}
        >
          Data Flows
        </span>
      </div>
      {DATA_FLOW_ROWS.map((row) => (
        <DataFlowRowItem key={row.endpoint} row={row} />
      ))}
    </div>
  );
}

function DataFlowRowItem({ row }: { row: DataFlowRow }) {
  const [hovered, setHovered] = useState(false);
  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        height: 36,
        padding: '0 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 12,
        background: hovered ? '#282a30' : 'transparent',
        transition: 'background 0.15s',
      }}
    >
      {/* Status dot */}
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: '50%',
          background: row.dotColor,
          flexShrink: 0,
          display: 'inline-block',
        }}
      />
      {/* Name */}
      <span style={{ flex: 1, fontSize: '12px', color: '#e2e2eb' }}>
        {row.name}
      </span>
      {/* Status label */}
      <span style={{ fontSize: '11px', color: row.statusColor }}>
        {row.statusLabel}
      </span>
      {/* Time */}
      <span
        className="font-mono"
        style={{ fontSize: '10px', color: '#9a9aaa', minWidth: 44, textAlign: 'right' }}
      >
        {row.time}
      </span>
      {/* Endpoint chip */}
      <span
        className="font-mono"
        style={{
          fontSize: '10px',
          color: '#9a9aaa',
          background: '#282a30',
          padding: '1px 5px',
          borderRadius: 2,
        }}
      >
        {row.endpoint}
      </span>
    </div>
  );
}

// ── Activity panel ────────────────────────────────────────────────────────────

interface ActivityRow {
  tag: string;
  tagBg: string;
  tagColor: string;
  description: string;
  time: string;
}

const ACTIVITY_ROWS: ActivityRow[] = [
  { tag: 'twin.save',  tagBg: 'rgba(230,126,34,0.15)',   tagColor: '#ffb783', description: 'Node updated: MCU_STM32H7',       time: '09:41:18' },
  { tag: 'agent.chat', tagBg: 'rgba(134,207,255,0.15)',  tagColor: '#86cfff', description: '42 chunks indexed',               time: '09:41:05' },
  { tag: 'twin.drift', tagBg: 'rgba(245,158,11,0.15)',   tagColor: '#f59e0b', description: 'Drift detected: BOM line 14',     time: '09:40:51' },
  { tag: 'bom.risk',   tagBg: 'rgba(255,180,171,0.15)',  tagColor: '#ffb4ab', description: 'Voltage check failed: rail 3V3',  time: '09:40:44' },
  { tag: 'gate.check', tagBg: 'rgba(61,214,140,0.15)',   tagColor: '#3dd68c', description: 'Gate G3 passed: mechanical',      time: '09:40:31' },
  { tag: 'agent.run',  tagBg: 'rgba(134,207,255,0.15)',  tagColor: '#86cfff', description: 'Datasheet: INA226 ingested',      time: '09:40:17' },
];

function ActivityPanel() {
  return (
    <div style={glassPanel}>
      <div style={{ ...panelHeader, justifyContent: 'space-between' }}>
        <span
          className="font-mono"
          style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: '#9a9aaa' }}
        >
          Activity
        </span>
        <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: '#3dd68c',
              display: 'inline-block',
            }}
          />
          <span
            className="font-mono"
            style={{ fontSize: '10px', color: '#3dd68c', letterSpacing: '0.07em' }}
          >
            LIVE
          </span>
        </div>
      </div>
      {ACTIVITY_ROWS.map((row) => (
        <ActivityRowItem key={row.time + row.tag} row={row} />
      ))}
    </div>
  );
}

function ActivityRowItem({ row }: { row: ActivityRow }) {
  const [hovered, setHovered] = useState(false);
  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        height: 32,
        padding: '0 16px',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        background: hovered ? '#282a30' : 'transparent',
        transition: 'background 0.15s',
      }}
    >
      {/* Tag chip */}
      <span
        className="font-mono"
        style={{
          fontSize: '10px',
          color: row.tagColor,
          background: row.tagBg,
          padding: '1px 5px',
          borderRadius: 2,
          flexShrink: 0,
        }}
      >
        {row.tag}
      </span>
      {/* Description */}
      <span style={{ fontSize: '12px', color: '#9a9aaa', flex: 1 }}>
        {row.description}
      </span>
      {/* Time */}
      <span className="font-mono" style={{ fontSize: '10px', color: '#9a9aaa' }}>
        {row.time}
      </span>
    </div>
  );
}

// ── System Health row ─────────────────────────────────────────────────────────

interface HealthService {
  name: string;
  dotColor: string;
  valueText: string;
  valueColor: string;
}

const HEALTH_SERVICES: HealthService[] = [
  { name: 'Neo4j',    dotColor: '#86cfff', valueText: '4ms',      valueColor: '#86cfff' },
  { name: 'Kafka',    dotColor: '#3dd68c', valueText: '1ms',      valueColor: '#3dd68c' },
  { name: 'pgvector', dotColor: '#3dd68c', valueText: '12ms',     valueColor: '#3dd68c' },
  { name: 'Temporal', dotColor: '#86cfff', valueText: 'running',  valueColor: '#86cfff' },
  { name: 'MinIO',    dotColor: '#f59e0b', valueText: 'degraded', valueColor: '#f59e0b' },
];

function SystemHealthRow() {
  return (
    <div className="grid grid-cols-5 gap-3 mb-4">
      {HEALTH_SERVICES.map((svc) => (
        <div
          key={svc.name}
          style={{
            background: 'rgba(30,31,38,0.85)',
            padding: '10px 12px',
            borderRadius: 4,
            border: '1px solid rgba(65,72,90,0.2)',
          }}
        >
          <div style={{ fontSize: '12px', color: '#9a9aaa', marginBottom: 4 }}>
            {svc.name}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: svc.dotColor,
                display: 'inline-block',
                flexShrink: 0,
              }}
            />
            <span className="font-mono" style={{ fontSize: '11px', color: svc.valueColor }}>
              {svc.valueText}
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
  const createProject = useCreateProject();
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  const now = new Date();
  const lastSyncTime = `${String(now.getHours()).padStart(2, '0')}:${String(now.getMinutes()).padStart(2, '0')}:${String(now.getSeconds()).padStart(2, '0')}`;
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
        <span className="font-mono" style={{ fontSize: '11px', color: '#9a9aaa' }}>
          last sync {lastSyncTime}
        </span>
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

      {/* Data Flows + Activity two-column row */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 320px',
          gap: 12,
          marginBottom: 12,
        }}
      >
        <DataFlowsPanel />
        <ActivityPanel />
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
            disabled
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
          {projects.map((project) => (
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
