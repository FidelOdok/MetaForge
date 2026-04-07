import { Link, useParams } from 'react-router-dom';
import { useProject } from '../hooks/use-projects';
import { StatusBadge } from '../components/shared/StatusBadge';
import { EmptyState } from '../components/ui/EmptyState';
import { formatRelativeTime } from '../utils/format-time';

const glassCard = {
  background: 'rgba(30,31,38,0.85)',
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
  valid: '#3dd68c',
  warning: '#f59e0b',
  error: '#ffb4ab',
  unknown: '#9a9aaa',
};

function getStatusDotColor(status: string): string {
  return statusDotColor[status] ?? '#9a9aaa';
}

// Simulated agent activity feed — derived from work products
interface ActivityEntry {
  tag: string;
  tagColor: string;
  tagBg: string;
  message: string;
  timestamp: string;
}

function buildActivityFeed(workProducts: { name: string; type: string; status: string; updatedAt: string }[]): ActivityEntry[] {
  return workProducts.slice(0, 8).map((wp) => {
    const tagMap: Record<string, { tag: string; color: string; bg: string }> = {
      schematic: { tag: 'twin.save', color: '#ffb783', bg: 'rgba(230,126,34,0.15)' },
      pcb: { tag: 'agent.run', color: '#86cfff', bg: 'rgba(134,207,255,0.15)' },
      cad_model: { tag: 'agent.run', color: '#86cfff', bg: 'rgba(134,207,255,0.15)' },
      firmware: { tag: 'twin.save', color: '#ffb783', bg: 'rgba(230,126,34,0.15)' },
      bom: { tag: 'bom.risk', color: '#ffb4ab', bg: 'rgba(255,180,171,0.15)' },
      gerber: { tag: 'gate.check', color: '#3dd68c', bg: 'rgba(61,214,140,0.15)' },
    };
    const style = tagMap[wp.type] ?? { tag: 'agent.run', color: '#86cfff', bg: 'rgba(134,207,255,0.15)' };
    const statusVerb = wp.status === 'valid' ? 'validated' : wp.status === 'warning' ? 'flagged' : wp.status === 'error' ? 'failed' : 'updated';

    return {
      tag: style.tag,
      tagColor: style.color,
      tagBg: style.bg,
      message: `${wp.name} ${statusVerb}`,
      timestamp: formatRelativeTime(wp.updatedAt),
    };
  });
}

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: project, isLoading } = useProject(id);

  if (isLoading) {
    return (
      <div>
        {/* Skeleton header */}
        <div className="flex items-center gap-2 mb-4">
          <div className="h-3 rounded w-16 animate-pulse" style={{ background: 'rgba(65,72,90,0.4)' }} />
        </div>
        <div className="grid grid-cols-4 gap-3 mb-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="glass rounded p-4 animate-pulse" style={glassCard}>
              <div className="h-7 rounded w-12 mb-2" style={{ background: 'rgba(65,72,90,0.4)' }} />
              <div className="h-2 rounded w-20" style={{ background: 'rgba(65,72,90,0.3)' }} />
            </div>
          ))}
        </div>
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

  return (
    <div>
      {/* Back nav */}
      <div className="mb-4 flex items-center gap-1.5">
        <Link
          to="/projects"
          className="flex items-center gap-1 font-mono transition-opacity hover:opacity-80"
          style={{ fontSize: '11px', color: '#9a9aaa' }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: '14px', color: '#9a9aaa' }}>arrow_back</span>
          Projects
        </Link>
        <span style={{ fontSize: '11px', color: '#9a9aaa' }}>/</span>
        <span className="font-mono" style={{ fontSize: '11px', color: '#e2e2eb' }}>{project.name}</span>
      </div>

      {/* Page header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-baseline gap-2">
          <span style={{ fontSize: '18px', fontWeight: 500, color: '#e2e2eb', letterSpacing: '-0.02em' }}>
            {project.name}
          </span>
          <StatusBadge status={project.status} />
        </div>
        <span className="font-mono" style={{ fontSize: '11px', color: '#9a9aaa' }}>
          updated {formatRelativeTime(project.lastUpdated)}
        </span>
      </div>

      {/* Description */}
      {project.description && (
        <p className="mb-4 font-mono" style={{ fontSize: '11px', color: '#9a9aaa', lineHeight: '1.6' }}>
          {project.description}
        </p>
      )}

      {/* Metrics row */}
      <div className="grid grid-cols-4 gap-3 mb-4">
        {/* Work Products */}
        <div className="glass rounded p-4 relative overflow-hidden" style={glassCard}>
          <div style={{ fontSize: '28px', fontWeight: 300, color: '#e2e2eb', lineHeight: 1, letterSpacing: '-0.02em' }}>
            {project.work_products.length}
          </div>
          <div className="font-mono mt-1" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: '#9a9aaa' }}>
            Work Products
          </div>
        </div>

        {/* Gate Readiness */}
        <div className="glass rounded p-4 relative overflow-hidden" style={glassCard}>
          <div style={{ fontSize: '28px', fontWeight: 300, color: '#3dd68c', lineHeight: 1, letterSpacing: '-0.02em' }}>
            {readiness}%
          </div>
          <div className="font-mono mt-1" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: '#9a9aaa' }}>
            Gate Readiness
          </div>
          <div className="absolute" style={{ right: '14px', bottom: '14px' }}>
            <svg width="38" height="38" viewBox="0 0 38 38">
              <circle cx="19" cy="19" r="14" fill="none" stroke="rgba(61,214,140,0.12)" strokeWidth="3" />
              <circle
                cx="19" cy="19" r="14" fill="none"
                stroke="#3dd68c"
                strokeWidth="3"
                strokeLinecap="round"
                strokeDasharray="75.4"
                strokeDashoffset={String(75.4 * (1 - readiness / 100))}
                transform="rotate(-90 19 19)"
                opacity="0.85"
              />
            </svg>
          </div>
        </div>

        {/* Active Agents */}
        <div className="glass rounded p-4 relative overflow-hidden" style={glassCard}>
          <div className="font-mono" style={{ fontSize: '28px', fontWeight: 300, color: '#86cfff', lineHeight: 1, letterSpacing: '-0.02em' }}>
            {project.agentCount}
          </div>
          <div className="font-mono mt-1" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: '#9a9aaa' }}>
            Active Agents
          </div>
        </div>

        {/* Risk Alerts */}
        <div className="glass rounded p-4 relative overflow-hidden" style={glassCard}>
          <div style={{ fontSize: '28px', fontWeight: 300, color: errorCount > 0 ? '#ffb4ab' : '#e2e2eb', lineHeight: 1, letterSpacing: '-0.02em' }}>
            {errorCount}
          </div>
          <div className="font-mono mt-1" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.07em', color: '#9a9aaa' }}>
            Risk Alerts
          </div>
          {errorCount > 0 && (
            <div className="absolute" style={{ right: '14px', bottom: '18px' }}>
              <div className="pulse-ring relative" style={{ width: '6px', height: '6px', borderRadius: '50%', background: '#ffb4ab' }} />
            </div>
          )}
        </div>
      </div>

      {/* Two-column: work products + activity */}
      <div className="grid gap-3" style={{ gridTemplateColumns: '1fr 300px' }}>

        {/* Work Products panel */}
        <div className="glass rounded overflow-hidden" style={glassCard}>
          {/* Panel header */}
          <div
            className="flex items-center justify-between px-4 py-2"
            style={{ borderBottom: '1px solid rgba(65,72,90,0.2)' }}
          >
            <span className="font-mono" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.1em', color: '#9a9aaa' }}>
              Work Products
            </span>
            <span className="font-mono" style={{ fontSize: '10px', color: '#9a9aaa' }}>
              {project.work_products.length} total
            </span>
          </div>

          {project.work_products.length === 0 ? (
            <div className="px-4 py-8">
              <EmptyState title="No work products" description="Run an agent to create work products." />
            </div>
          ) : (
            project.work_products.map((wp) => (
              <div
                key={wp.id}
                className="flex items-center gap-3 px-4 hover:bg-surface-high cursor-default"
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
                  style={{ fontSize: '14px', color: '#9a9aaa' }}
                >
                  {workProductTypeIcon[wp.type] ?? 'description'}
                </span>

                {/* Name */}
                <span
                  className="flex-1 text-sm font-medium"
                  style={{ color: '#e2e2eb', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
                >
                  {wp.name}
                </span>

                {/* Type pill */}
                <span
                  className="font-mono rounded flex-shrink-0"
                  style={{ fontSize: '10px', color: '#9a9aaa', background: '#282a30', padding: '1px 5px' }}
                >
                  {wp.type}
                </span>

                {/* Status badge */}
                <StatusBadge status={wp.status} />

                {/* Timestamp */}
                <span
                  className="font-mono flex-shrink-0"
                  style={{ fontSize: '10px', color: '#9a9aaa', minWidth: '60px', textAlign: 'right' }}
                >
                  {formatRelativeTime(wp.updatedAt)}
                </span>
              </div>
            ))
          )}
        </div>

        {/* Agent Activity panel */}
        <div className="glass rounded overflow-hidden" style={glassCard}>
          {/* Panel header */}
          <div
            className="flex items-center justify-between px-4 py-2"
            style={{ borderBottom: '1px solid rgba(65,72,90,0.2)' }}
          >
            <span className="font-mono" style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.1em', color: '#9a9aaa' }}>
              Activity
            </span>
            <div className="flex items-center gap-1.5">
              <span
                className="live-dot"
                style={{ width: '5px', height: '5px', borderRadius: '50%', background: '#3dd68c', display: 'inline-block' }}
              />
              <span className="font-mono" style={{ fontSize: '10px', color: '#3dd68c', letterSpacing: '0.06em' }}>
                LIVE
              </span>
            </div>
          </div>

          {activity.length === 0 ? (
            <div className="px-4 py-6 text-center">
              <span className="font-mono" style={{ fontSize: '10px', color: '#9a9aaa' }}>
                No recent activity
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
                  style={{ fontSize: '12px', color: '#9a9aaa', flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
                >
                  {entry.message}
                </span>
                <span className="font-mono flex-shrink-0" style={{ fontSize: '10px', color: '#9a9aaa' }}>
                  {entry.timestamp}
                </span>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
