import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Button } from '../ui/Button';
import { useToast } from '../ui/Toast';
import { useViewerStore } from '../../store/viewer-store';
import { useApproveSketch } from '../../hooks/use-twin';
import { nodeFileUrl, fetchNodeFileText } from '../../api/endpoints/twin';
import type { TwinNode } from '../../types/twin';

// ── Kinetic Console tokens (mirrors TwinViewerPage's local KC) ──────────────
const KC = {
  surface: '#111319',
  surfaceLow: '#191b22',
  surfaceHigh: '#282a30',
  border: 'rgba(65,72,90,0.2)',
  borderMid: 'rgba(65,72,90,0.35)',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  orange: '#e67e22',
  orangeFaint: 'rgba(230,126,34,0.15)',
  orangeBorder: 'rgba(230,126,34,0.45)',
  green: '#3dd68c',
  greenFaint: 'rgba(61,214,140,0.14)',
  greenBorder: 'rgba(61,214,140,0.4)',
} as const;

const IMG_FORMATS = new Set(['png', 'jpg', 'jpeg', 'gif', 'svg']);
const TEXT_FORMATS = new Set([
  'txt', 'md', 'json', 'csv', 'log', 'kicad_sch', 'kicad_pcb', 'net', 'gbr', 'c', 'h',
  'urdf', 'xacro', 'sdf', 'usda',
]);

type PreviewKind = 'image' | 'pdf' | 'html' | 'text' | 'robot' | 'none';

function previewKind(fmt: string, wpType: string | undefined): PreviewKind {
  if (wpType === 'robot_description') return 'robot';
  if (fmt === 'html') return 'html';
  if (IMG_FORMATS.has(fmt)) return 'image';
  if (fmt === 'pdf') return 'pdf';
  if (TEXT_FORMATS.has(fmt)) return 'text';
  return 'none';
}

interface FullScreenPreviewModalProps {
  node: TwinNode;
  onClose: () => void;
}

/**
 * Full-viewport preview for any work product's file -- replaces the old
 * 320px-tall inline preview strip with a real reading/reviewing surface:
 * a properly sized image canvas, a full-height PDF/HTML frame, a code
 * viewer with copy-to-clipboard, or (for design_sketch) the human
 * approval-gate action itself (follow-up to MET-740/747's Prime Rule
 * work: a sketch meant to actually block a build decision needs a real
 * review surface, not a cramped sidebar strip).
 *
 * Rendered via a portal straight onto `document.body`, the same fix
 * MET-746 used for the robot-viewer popup -- a `position: fixed` child of
 * a `backdrop-filter` ancestor gets clipped to that ancestor's box instead
 * of the viewport, so this never nests inside the (backdrop-blurred)
 * NodeDetail glass panel.
 */
export function FullScreenPreviewModal({ node, onClose }: FullScreenPreviewModalProps) {
  const wpType = node.properties.wp_type ? String(node.properties.wp_type) : undefined;
  const fmt = (node.properties.format ? String(node.properties.format) : '').toLowerCase();
  const kind = previewKind(fmt, wpType);
  const isSketch = wpType === 'design_sketch';

  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const toast = useToast();
  const approveSketch = useApproveSketch();
  const loadRobotDescription = useViewerStore((s) => s.loadRobotDescription);
  const setViewMode = useViewerStore((s) => s.setViewMode);

  const inlineUrl = nodeFileUrl(node.id, false);
  const downloadUrl = nodeFileUrl(node.id, true);

  useEffect(() => {
    if (kind !== 'text' && kind !== 'html') return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchNodeFileText(node.id)
      .then((t) => { if (!cancelled) setText(t); })
      .catch(() => { if (!cancelled) setError('No file stored for this work product yet.'); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [node.id, kind]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [onClose]);

  const handleCopy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      toast.error('Copy failed');
    }
  };

  const handleApprove = () => {
    approveSketch.mutate(
      { nodeId: node.id },
      {
        onSuccess: () => toast.success('Sketch approved -- cleared for CAD/build work'),
        onError: () => toast.error('Approve failed'),
      },
    );
  };

  const handleViewIn3D = () => {
    loadRobotDescription(node.id);
    setViewMode('3d');
    onClose();
  };

  const approved = node.properties.approved === true;
  const approvedBy = node.properties.approved_by ? String(node.properties.approved_by) : undefined;
  const description = node.properties.description_text ? String(node.properties.description_text) : undefined;

  return createPortal(
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Preview: ${node.name}`}
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 500,
        background: 'rgba(8,9,13,0.92)',
        backdropFilter: 'blur(6px)',
        display: 'flex',
        flexDirection: 'column',
        animation: 'fspm-fade-in 120ms ease-out',
      }}
    >
      <style>{`
        @keyframes fspm-fade-in { from { opacity: 0; } to { opacity: 1; } }
        @keyframes fspm-rise-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: translateY(0); } }
      `}</style>

      {/* Header */}
      <div
        className="flex items-center gap-3 flex-shrink-0"
        style={{
          padding: '14px 20px',
          borderBottom: `1px solid ${KC.borderMid}`,
          background: KC.surfaceLow,
          animation: 'fspm-rise-in 160ms ease-out',
        }}
      >
        <span className="material-symbols-outlined" style={{ fontSize: 20, color: KC.onSurfaceVariant }}>
          {isSketch ? 'design_services' : 'description'}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2" style={{ flexWrap: 'wrap' }}>
            <span style={{ fontSize: 14, fontWeight: 600, color: KC.onSurface }}>{node.name}</span>
            <span
              className="font-mono uppercase"
              style={{ fontSize: 9, color: KC.orange, background: KC.orangeFaint, padding: '2px 6px', borderRadius: 3, letterSpacing: '0.06em' }}
            >
              {wpType ?? 'unknown'}
            </span>
            {fmt && (
              <span className="font-mono" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>.{fmt}</span>
            )}
            {isSketch && (
              approved ? (
                <span
                  className="font-mono uppercase flex items-center gap-1"
                  style={{ fontSize: 9, color: KC.green, background: KC.greenFaint, padding: '2px 6px', borderRadius: 3, letterSpacing: '0.06em' }}
                >
                  <span className="material-symbols-outlined" style={{ fontSize: 11 }}>check_circle</span>
                  Approved{approvedBy ? ` · ${approvedBy}` : ''}
                </span>
              ) : (
                <span
                  className="font-mono uppercase"
                  style={{ fontSize: 9, color: '#f5b04d', background: 'rgba(245,176,77,0.14)', padding: '2px 6px', borderRadius: 3, letterSpacing: '0.06em' }}
                >
                  Needs approval
                </span>
              )
            )}
          </div>
          {description && (
            <div className="font-mono" style={{ fontSize: 11, color: KC.onSurfaceVariant, marginTop: 2 }}>
              {description}
            </div>
          )}
        </div>

        <div className="flex items-center gap-2 flex-shrink-0">
          {isSketch && !approved && (
            <Button
              size="sm"
              onClick={handleApprove}
              disabled={approveSketch.isPending}
              className="text-xs"
              style={{ background: KC.green, border: 'none', color: '#0b1410' }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 13, marginRight: 4, verticalAlign: 'middle' }}>check</span>
              {approveSketch.isPending ? 'Approving…' : 'Approve'}
            </Button>
          )}
          {kind === 'text' && text && (
            <Button variant="secondary" size="sm" onClick={handleCopy} className="text-xs">
              <span className="material-symbols-outlined" style={{ fontSize: 13, marginRight: 4, verticalAlign: 'middle' }}>{copied ? 'check' : 'content_copy'}</span>
              {copied ? 'Copied' : 'Copy'}
            </Button>
          )}
          <a href={downloadUrl} download style={{ textDecoration: 'none' }}>
            <Button variant="secondary" size="sm" className="text-xs">
              <span className="material-symbols-outlined" style={{ fontSize: 13, marginRight: 4, verticalAlign: 'middle' }}>download</span>
              Download
            </Button>
          </a>
          <a href={inlineUrl} target="_blank" rel="noreferrer" style={{ textDecoration: 'none' }}>
            <Button variant="secondary" size="sm" className="text-xs">
              <span className="material-symbols-outlined" style={{ fontSize: 13, verticalAlign: 'middle' }}>open_in_new</span>
            </Button>
          </a>
          <Button variant="ghost" size="sm" onClick={onClose} className="text-xs" aria-label="Close preview">
            <span className="material-symbols-outlined" style={{ fontSize: 18, verticalAlign: 'middle' }}>close</span>
          </Button>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0" style={{ animation: 'fspm-rise-in 200ms ease-out', display: 'flex' }}>
        {kind === 'image' && (
          <div className="flex-1 flex items-center justify-center p-8" style={{ background: '#0a0b0f', backgroundImage: 'linear-gradient(45deg, #12131a 25%, transparent 25%), linear-gradient(-45deg, #12131a 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #12131a 75%), linear-gradient(-45deg, transparent 75%, #12131a 75%)', backgroundSize: '20px 20px', backgroundPosition: '0 0, 0 10px, 10px -10px, -10px 0px' }}>
            <img
              src={inlineUrl}
              alt={node.name}
              style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', borderRadius: 4, boxShadow: '0 12px 40px rgba(0,0,0,0.5)' }}
            />
          </div>
        )}

        {kind === 'pdf' && (
          <iframe src={inlineUrl} title={node.name} style={{ flex: 1, border: 'none', background: '#fff' }} />
        )}

        {kind === 'html' && (
          <div className="flex-1 flex items-center justify-center p-6" style={{ background: '#0a0b0f' }}>
            {error ? (
              <div className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>{error}</div>
            ) : loading ? (
              <div className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>Loading…</div>
            ) : (
              <iframe
                title={node.name}
                srcDoc={text ?? ''}
                sandbox="allow-scripts"
                style={{ width: '100%', height: '100%', maxWidth: 1400, border: `1px solid ${KC.border}`, borderRadius: 6, background: '#fff', boxShadow: '0 12px 40px rgba(0,0,0,0.5)' }}
              />
            )}
          </div>
        )}

        {kind === 'text' && (
          <div className="flex-1 min-h-0 overflow-auto flex justify-center" style={{ background: '#0d0e13' }}>
            <div style={{ width: '100%', maxWidth: 980, padding: '24px 20px' }}>
              {error ? (
                <div className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>{error}</div>
              ) : (
                <pre
                  className="font-mono"
                  style={{
                    margin: 0,
                    padding: 16,
                    fontSize: 12,
                    lineHeight: 1.6,
                    color: KC.onSurface,
                    background: KC.surfaceLow,
                    border: `1px solid ${KC.border}`,
                    borderRadius: 6,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                  }}
                >
                  {loading ? 'Loading…' : (text ?? '')}
                </pre>
              )}
            </div>
          </div>
        )}

        {kind === 'robot' && (
          <div className="flex-1 flex flex-col items-center justify-center gap-4" style={{ background: '#0a0b0f' }}>
            <span className="material-symbols-outlined" style={{ fontSize: 48, color: KC.onSurfaceVariant }}>smart_toy</span>
            <div className="font-mono text-center" style={{ fontSize: 12, color: KC.onSurfaceVariant, maxWidth: 360 }}>
              Robot descriptions are best explored live -- joints, physics, and posing all run in the main 3D viewer.
            </div>
            <Button variant="primary" size="md" onClick={handleViewIn3D} className="text-xs">
              <span className="material-symbols-outlined" style={{ fontSize: 15, marginRight: 6, verticalAlign: 'middle' }}>view_in_ar</span>
              Open in 3D Viewer
            </Button>
          </div>
        )}

        {kind === 'none' && (
          <div className="flex-1 flex flex-col items-center justify-center gap-3" style={{ background: '#0a0b0f' }}>
            <span className="material-symbols-outlined" style={{ fontSize: 40, color: KC.onSurfaceVariant }}>visibility_off</span>
            <div className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>
              Inline preview not available for .{fmt || 'this type'} -- use Open or Download.
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
