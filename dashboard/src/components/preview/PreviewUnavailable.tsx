import { ENGINE_LABELS, type PreviewEngine } from './registry';
import { PC } from './tokens';

/**
 * The honest fallback: say which engine is missing and offer the file. Never
 * a raw dump, so a binary is never shown as text.
 */
export function PreviewUnavailable({
  engine,
  format,
  downloadUrl,
  reason,
  compact = false,
}: {
  engine: PreviewEngine;
  format: string;
  downloadUrl?: string;
  reason?: string;
  compact?: boolean;
}) {
  const ext = format ? `.${format}` : 'this file type';
  const title =
    engine === 'none'
      ? `No preview engine for ${ext} files`
      : `${ENGINE_LABELS[engine]} not available`;
  return (
    <div
      role="status"
      data-testid="preview-unavailable"
      data-engine={engine}
      className="flex flex-col items-center justify-center gap-2 text-center"
      style={{ padding: compact ? 12 : 32, height: '100%' }}
    >
      <span className="material-symbols-outlined" style={{ fontSize: compact ? 24 : 40, color: PC.onSurfaceVariant }}>visibility_off</span>
      <div style={{ fontSize: 13, fontWeight: 600, color: PC.onSurface }}>{title}</div>
      <div className="font-mono" style={{ fontSize: 11, color: PC.onSurfaceVariant, maxWidth: 420 }}>
        {reason ?? 'Download the file to open it in its native tool.'}
      </div>
      {downloadUrl && (
        <a
          href={downloadUrl}
          download
          className="font-mono"
          style={{ fontSize: 11, color: PC.orange, textDecoration: 'none', border: `1px solid ${PC.orangeFaint}`, borderRadius: 4, padding: '4px 10px', marginTop: 4 }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 13, verticalAlign: 'middle', marginRight: 4 }}>download</span>
          Download {ext}
        </a>
      )}
    </div>
  );
}
