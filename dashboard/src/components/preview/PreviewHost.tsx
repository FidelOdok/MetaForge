import { lazy, Suspense, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { nodeFileUrl } from '../../api/endpoints/twin';
import { previewEngineFor, TEXT_ENGINES, type PreviewEngine } from './registry';
import { useNodeFileText } from './useNodeFileText';
import { PreviewUnavailable } from './PreviewUnavailable';
import { FeaSummaryCard } from './engines/FeaSummaryCard';
import { PC, type PreviewMode } from './tokens';
import type { TwinNode } from '../../types/twin';

// Heavy engines load on first use so the page bundles barely grow
// (three.js scene, tracespace, dxf-parser, the Markdown and table renderers).
const ModelPreview = lazy(() => import('./engines/ModelPreview').then((m) => ({ default: m.ModelPreview })));
const MarkdownView = lazy(() => import('./engines/DocumentEngines').then((m) => ({ default: m.MarkdownView })));
const RequirementsPreview = lazy(() =>
  import('./engines/DocumentEngines').then((m) => ({ default: m.RequirementsPreview })),
);
const PrdPreview = lazy(() => import('./engines/DocumentEngines').then((m) => ({ default: m.PrdPreview })));
const DecisionCard = lazy(() => import('./engines/DocumentEngines').then((m) => ({ default: m.DecisionCard })));
const CsvPreview = lazy(() => import('./engines/DataEngines').then((m) => ({ default: m.CsvPreview })));
const BomPreview = lazy(() => import('./engines/DataEngines').then((m) => ({ default: m.BomPreview })));
const JsonTree = lazy(() => import('./engines/DataEngines').then((m) => ({ default: m.JsonTree })));
const CodeView = lazy(() => import('./engines/DataEngines').then((m) => ({ default: m.CodeView })));
const GerberView = lazy(() => import('./engines/VectorEngines').then((m) => ({ default: m.GerberView })));
const DxfView = lazy(() => import('./engines/VectorEngines').then((m) => ({ default: m.DxfView })));

/**
 * Engines the twin inspector panel leaves to something else: the main 3D
 * viewer (cad3d, mesh3d, robot) or the full-screen Preview action (pdf,
 * html), which have no useful form at sidebar width.
 */
const PANEL_DEFERRED = new Set<PreviewEngine>(['cad3d', 'mesh3d', 'robot', 'pdf', 'html']);

/** Will `PreviewHost` render anything for this node in this mode? */
export function hasInlinePreview(node: TwinNode, mode: PreviewMode): boolean {
  return mode !== 'panel' || !PANEL_DEFERRED.has(previewEngineFor(node).engine);
}

interface PreviewHostProps {
  node: TwinNode;
  mode: PreviewMode;
  /** Robot descriptions: open the node in the main 3D viewer (modal only). */
  onOpenInViewer?: () => void;
}

function Status({ children }: { children: ReactNode }) {
  return (
    <div className="font-mono" style={{ fontSize: 12, color: PC.onSurfaceVariant, padding: 12 }}>
      {children}
    </div>
  );
}

/**
 * FORGE-531: renders a work product with the engine `previewEngineFor`
 * picks. Used by the twin inspector (`panel`), the full-screen modal
 * (`modal`) and project page rows (`compact`).
 */
export function PreviewHost({ node, mode, onOpenInViewer }: PreviewHostProps) {
  const { engine, format, language } = previewEngineFor(node);
  const needsText = TEXT_ENGINES.has(engine) && !(mode === 'panel' && PANEL_DEFERRED.has(engine));
  const file = useNodeFileText(node.id, needsText, node.updatedAt);
  const downloadUrl = nodeFileUrl(node.id, true);
  const inlineUrl = nodeFileUrl(node.id, false);

  if (mode === 'panel' && PANEL_DEFERRED.has(engine)) return null;

  const frame = (body: ReactNode) => (
    <div
      data-testid="preview-host"
      data-engine={engine}
      data-mode={mode}
      className={mode === 'modal' ? 'h-full w-full min-h-0' : undefined}
      style={
        mode === 'modal'
          ? { display: 'flex', flexDirection: 'column' }
          : { maxHeight: mode === 'compact' ? 320 : 360, overflow: 'auto' }
      }
    >
      <Suspense fallback={<Status>Loading preview…</Status>}>{body}</Suspense>
    </div>
  );

  // A document centred at reading width in the modal; edge to edge elsewhere.
  const doc = (body: ReactNode) =>
    mode === 'modal' ? (
      <div className="flex-1 min-h-0 overflow-auto flex justify-center" style={{ background: PC.canvas }}>
        <div style={{ width: '100%', maxWidth: engine === 'csv' || engine === 'bom' || engine === 'requirements' ? 1400 : 980, padding: '24px 20px' }}>{body}</div>
      </div>
    ) : (
      body
    );

  // ── Engines that need no file text ────────────────────────────────────
  switch (engine) {
    case 'sim':
      return frame(doc(<FeaSummaryCard node={node} mode={mode} />));
    case 'cad3d':
    case 'mesh3d':
      return frame(
        <div style={{ height: mode === 'modal' ? '100%' : 260, flex: mode === 'modal' ? 1 : undefined, minHeight: 0 }}>
          <ModelPreview node={node} engine={engine} format={format} mode={mode} />
        </div>,
      );
    case 'robot':
      return frame(
        <div className="flex flex-1 flex-col items-center justify-center gap-4" style={{ padding: 24, background: mode === 'modal' ? 'var(--mf-c-0a0b0f)' : undefined }}>
          <span className="material-symbols-outlined" style={{ fontSize: 40, color: PC.onSurfaceVariant }}>smart_toy</span>
          <div className="font-mono text-center" style={{ fontSize: 12, color: PC.onSurfaceVariant, maxWidth: 360 }}>
            Robot descriptions are best explored live: joints, physics and posing all run in the main 3D viewer.
          </div>
          {onOpenInViewer ? (
            <button type="button" onClick={onOpenInViewer} className="font-mono" style={{ fontSize: 12, color: PC.onSurface, background: PC.orange, border: 'none', borderRadius: 4, padding: '6px 14px', cursor: 'pointer' }}>
              <span className="material-symbols-outlined" style={{ fontSize: 15, marginRight: 6, verticalAlign: 'middle' }}>view_in_ar</span>
              Open in 3D Viewer
            </button>
          ) : (
            <Link to={`/twin?node=${encodeURIComponent(node.id)}`} className="font-mono" style={{ fontSize: 12, color: PC.orange }}>
              Open in the twin viewer
            </Link>
          )}
        </div>,
      );
    case 'image':
      return frame(
        <div className="flex flex-1 items-center justify-center" style={{ padding: mode === 'modal' ? 32 : 8, minHeight: 0, background: 'var(--mf-c-0a0b0f)' }}>
          <img src={inlineUrl} alt={node.name} data-testid="preview-image" style={{ maxWidth: '100%', maxHeight: mode === 'modal' ? '100%' : 260, objectFit: 'contain', borderRadius: 4 }} />
        </div>,
      );
    case 'pdf':
      return frame(
        <iframe src={inlineUrl} title={node.name} data-testid="preview-pdf" style={{ flex: 1, width: '100%', height: mode === 'modal' ? '100%' : 300, border: 'none', background: 'var(--mf-c-fff)' }} />,
      );
    case 'kicad':
      return frame(
        <PreviewUnavailable
          engine="kicad"
          format={format}
          downloadUrl={downloadUrl}
          compact={mode !== 'modal'}
          reason="No embeddable KiCad viewer is available in the dashboard yet. Download the file and open it in KiCad."
        />,
      );
    case 'none':
      return frame(<PreviewUnavailable engine="none" format={format} downloadUrl={downloadUrl} compact={mode !== 'modal'} />);
    default:
      break;
  }

  // ── Text engines ───────────────────────────────────────────────────────
  if (file.loading) return frame(<Status>Loading…</Status>);
  // FORGE-528: the derived prd has its own source; the stored prose is only
  // its fallback, so a missing file must not hide it.
  if (engine === 'prd') return frame(doc(<PrdPreview node={node} fallback={file.text} />));
  // A decision card still has its title, rationale and status from the node.
  if (engine === 'decision' && (file.error || file.text === null)) {
    return frame(doc(<DecisionCard node={node} source={null} />));
  }
  if (file.error || file.text === null) {
    return frame(<Status>{file.error ?? 'No file stored for this work product yet.'}</Status>);
  }
  const text = file.text;

  switch (engine) {
    case 'markdown':
      return frame(doc(<MarkdownView source={text} />));
    case 'requirements':
      return frame(doc(<RequirementsPreview source={text} />));
    case 'decision':
      return frame(doc(<DecisionCard node={node} source={text} />));
    case 'csv':
      return frame(doc(<CsvPreview source={text} format={format} mode={mode} />));
    case 'bom':
      return frame(doc(<BomPreview source={text} mode={mode} />));
    case 'json':
      return frame(doc(<JsonTree source={text} />));
    case 'code':
      return frame(doc(<CodeView source={text} language={language ?? ''} />));
    case 'text':
      return frame(doc(<CodeView source={text} language="" />));
    case 'gerber':
      return frame(doc(<GerberView source={text} name={node.name} format={format} mode={mode} downloadUrl={downloadUrl} />));
    case 'dxf':
      return frame(doc(<DxfView source={text} name={node.name} format={format} mode={mode} downloadUrl={downloadUrl} />));
    case 'html':
      return frame(
        <div className="flex flex-1 items-center justify-center" style={{ padding: mode === 'modal' ? 24 : 0, minHeight: 0, background: 'var(--mf-c-0a0b0f)' }}>
          <iframe
            title={node.name}
            srcDoc={text}
            sandbox="allow-scripts"
            data-testid="preview-html"
            style={{ width: '100%', height: mode === 'modal' ? '100%' : 300, maxWidth: 1400, border: `1px solid ${PC.border}`, borderRadius: 6, background: 'var(--mf-c-fff)' }}
          />
        </div>,
      );
    default:
      return frame(<PreviewUnavailable engine="none" format={format} downloadUrl={downloadUrl} compact={mode !== 'modal'} />);
  }
}
