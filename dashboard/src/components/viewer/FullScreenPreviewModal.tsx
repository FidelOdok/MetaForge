import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Button } from '../ui/Button';
import { useToast } from '../ui/Toast';
import { useViewerStore } from '../../store/viewer-store';
import { useApproveSketch } from '../../hooks/use-twin';
import { nodeFileUrl } from '../../api/endpoints/twin';
import { ENGINE_LABELS, previewEngineFor, TEXT_ENGINES } from '../preview/registry';
import { PreviewHost } from '../preview/PreviewHost';
import { useNodeFileText } from '../preview/useNodeFileText';
import type { TwinNode } from '../../types/twin';

// ── Kinetic Console tokens (mirrors TwinViewerPage's local KC) ──────────────
const KC = {
  surface: 'var(--mf-c-111319)',
  surfaceLow: 'var(--mf-c-191b22)',
  surfaceHigh: 'var(--mf-c-282a30)',
  border: 'var(--mf-r-65-72-90-0p2)',
  borderMid: 'var(--mf-r-65-72-90-0p35)',
  onSurface: 'var(--mf-c-e2e2eb)',
  onSurfaceVariant: 'var(--mf-c-9a9aaa)',
  orange: '#ff5a0a',
  orangeFaint: 'rgba(255, 90, 10,0.15)',
  orangeBorder: 'rgba(255, 90, 10,0.45)',
  green: 'var(--mf-c-3dd68c)',
  greenFaint: 'rgba(61,214,140,0.14)',
  greenBorder: 'rgba(61,214,140,0.4)',
} as const;

interface FullScreenPreviewModalProps {
  node: TwinNode;
  onClose: () => void;
}

/**
 * Full-viewport preview for any work product's file. FORGE-531: the body is
 * the shared PreviewHost, so the engine is the one `previewEngineFor` picks
 * (STEP and meshes in 3D, Markdown rendered, tables, decision cards, Gerber,
 * DXF...), the same answer the twin inspector and project rows get. Replaces the old
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
  const { engine, format: fmt } = previewEngineFor(node);
  const isSketch = wpType === 'design_sketch';
  // Shares PreviewHost's cached fetch; only text engines ever request text.
  const { text } = useNodeFileText(node.id, TEXT_ENGINES.has(engine), node.updatedAt);
  const [copied, setCopied] = useState(false);

  const toast = useToast();
  const approveSketch = useApproveSketch();
  const loadRobotDescription = useViewerStore((s) => s.loadRobotDescription);
  const setViewMode = useViewerStore((s) => s.setViewMode);

  const inlineUrl = nodeFileUrl(node.id, false);
  const downloadUrl = nodeFileUrl(node.id, true);

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
        background: 'var(--mf-r-8-9-13-0p92)',
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
            <span className="font-mono" data-testid="preview-engine-label" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
              · {ENGINE_LABELS[engine]}
            </span>
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
                  style={{ fontSize: 9, color: 'var(--mf-c-f5b04d)', background: 'rgba(245,176,77,0.14)', padding: '2px 6px', borderRadius: 3, letterSpacing: '0.06em' }}
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
              style={{ background: KC.green, border: 'none', color: 'var(--mf-c-0b1410)' }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 13, marginRight: 4, verticalAlign: 'middle' }}>check</span>
              {approveSketch.isPending ? 'Approving…' : 'Approve'}
            </Button>
          )}
          {TEXT_ENGINES.has(engine) && engine !== 'html' && text && (
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
        <PreviewHost node={node} mode="modal" onOpenInViewer={handleViewIn3D} />
      </div>
    </div>,
    document.body,
  );
}
