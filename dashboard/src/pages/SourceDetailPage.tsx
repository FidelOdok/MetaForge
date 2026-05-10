import { Link, useParams } from 'react-router-dom';

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
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <Link
          to="/knowledge"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            fontSize: 12,
            color: '#9a9aaa',
            textDecoration: 'none',
          }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
            arrow_back
          </span>
          Back to knowledge
        </Link>
      </div>

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 12 }}>
        <span style={{ fontSize: 18, fontWeight: 500, color: '#e8e8ed' }}>
          Source detail
        </span>
        <span
          style={{
            fontFamily: 'monospace',
            fontSize: 11,
            color: '#9a9aaa',
            wordBreak: 'break-all',
          }}
        >
          {sourceId}
        </span>
      </div>

      <div
        style={{
          background: 'rgba(30,31,38,0.85)',
          backdropFilter: 'blur(16px)',
          border: '1px solid rgba(65,72,90,0.2)',
          borderRadius: 4,
          padding: 32,
          textAlign: 'center',
        }}
      >
        <span
          className="material-symbols-outlined"
          style={{ fontSize: 32, color: '#9a9aaa', opacity: 0.4 }}
        >
          construction
        </span>
        <p style={{ fontSize: 13, color: '#e2e2eb', marginTop: 12 }}>
          Source detail page — coming in v2.
        </p>
        <p
          style={{
            fontFamily: 'monospace',
            fontSize: 11,
            color: '#9a9aaa',
            marginTop: 6,
          }}
        >
          Chunk preview, metadata, and retrieval trace will land with L1-E4.
        </p>
      </div>
    </div>
  );
}
