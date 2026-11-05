import { useParams } from 'react-router-dom';
import { PageHeader } from '../components/ui/PageHeader';

/**
 * Placeholder for the per-source drill-in (L1-E4 — deferred to v2).
 *
 * L1-E2 only ships the sources index; this stub gives the row click in
 * ``KnowledgePage`` somewhere to land so deep-links can be shared today
 * without 404-ing. The real detail view (chunk preview, metadata
 * pretty-print, retrieval-trace links) lands with E4.
 */
export function SourceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const sourceId = id ?? '';

  return (
    <div>
      <PageHeader
        back={{ to: '/knowledge', label: 'Back to knowledge' }}
        title="Source detail"
        meta={<span style={{ wordBreak: 'break-all' }}>{sourceId}</span>}
      />

      <div
        style={{
          background: 'var(--mf-r-30-31-38-0p85)',
          backdropFilter: 'blur(16px)',
          border: '1px solid var(--mf-r-65-72-90-0p2)',
          borderRadius: 4,
          padding: 32,
          textAlign: 'center',
        }}
      >
        <span
          className="material-symbols-outlined"
          style={{ fontSize: 32, color: 'var(--mf-c-9a9aaa)', opacity: 0.4 }}
        >
          construction
        </span>
        <p style={{ fontSize: 13, color: 'var(--mf-c-e2e2eb)', marginTop: 12 }}>
          Source detail page — coming in v2.
        </p>
        <p
          style={{
            fontFamily: 'monospace',
            fontSize: 11,
            color: 'var(--mf-c-9a9aaa)',
            marginTop: 6,
          }}
        >
          Chunk preview, metadata, and retrieval trace will land with L1-E4.
        </p>
      </div>
    </div>
  );
}
