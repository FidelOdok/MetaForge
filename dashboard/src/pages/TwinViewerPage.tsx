import { useState, useRef, useCallback, useEffect } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Button } from '../components/ui/Button';
import { StatusBadge } from '../components/shared/StatusBadge';
import { formatRelativeTime } from '../utils/format-time';
import { useTwinNodes, useTwinNode, useTwinRelationships, useNodeVersionHistory } from '../hooks/use-twin';
import { useActiveProject } from '../hooks/use-active-project';
import { R3FViewer } from '../components/viewer/R3FViewer';
import { ComponentTree } from '../components/viewer/ComponentTree';
import { TwinGraphCanvas } from '../components/viewer/TwinGraphCanvas';
import { BomAnnotationPanel } from '../components/viewer/BomAnnotationPanel';
import { NodeProposals } from '../components/viewer/NodeProposals';
import { ExplodedViewControls } from '../components/viewer/ExplodedViewControls';
import { AssemblyExportPanel } from '../components/viewer/AssemblyExportPanel';
import { useViewerStore } from '../store/viewer-store';
import { useUploadAndConvert } from '../hooks/use-conversion';
import { getMockManifest, getMockGlbUrl } from '../api/endpoints/convert';
import { getNodeModel, nodeFileUrl, fetchNodeFileText } from '../api/endpoints/twin';
import { toDownloadHref, type ExportFile } from '../api/endpoints/cad-export';
import { useExportUrdf, useExportSdf, useExportUsd } from '../hooks/use-cad-export';
import { useToast } from '../components/ui/Toast';
import type { TwinNode } from '../types/twin';
import type { ModelManifest, PartInfo, PartTreeNode } from '../types/viewer';

// MET-720: names the cadquery adapter's material density table
// (tool_registry/tools/cadquery/materials.py) actually recognizes.
const CAD_EXPORT_MATERIALS = [
  'aluminum_6061', 'aluminum', 'steel', 'stainless_steel', 'titanium', 'brass',
  'copper', 'abs', 'pla', 'petg', 'nylon', 'polycarbonate', 'acrylic', 'wood',
  'carbon_fiber', 'rubber',
];

// MET-683: same response.data.detail extraction pattern as
// ProjectDetailPage.tsx's getErrorMessage, applied to a failed model load.
function getModelLoadErrorMessage(error: unknown): string {
  if (error && typeof error === 'object' && 'response' in error) {
    const response = (error as { response?: { data?: { detail?: string } } }).response;
    if (typeof response?.data?.detail === 'string') return response.data.detail;
  }
  return 'This work product has no viewable 3D model yet.';
}

// ── KC tokens ────────────────────────────────────────────────────────────────
const KC = {
  surface: '#111319',
  surfaceLow: '#191b22',
  surfaceContainer: 'rgba(30,31,38,0.88)',
  surfaceHigh: '#282a30',
  border: 'rgba(65,72,90,0.2)',
  borderMid: 'rgba(65,72,90,0.3)',
  onSurface: '#e2e2eb',
  onSurfaceVariant: '#9a9aaa',
  orange: '#e67e22',
  orangeFaint: 'rgba(230,126,34,0.15)',
  orangeBorder: 'rgba(230,126,34,0.45)',
  teal: '#86cfff',
  green: '#3dd68c',
  statusBar: 'rgba(12,14,20,0.95)',
} as const;

// ── Icon map ─────────────────────────────────────────────────────────────────
const NODE_ICONS: Record<TwinNode['type'], string> = {
  work_product: 'description',
  constraint: 'rule',
  relationship: 'link',
  version: 'label',
};

// ── Small helpers ─────────────────────────────────────────────────────────────

function GlassPanel({
  children,
  style,
  className,
}: {
  children: React.ReactNode;
  style?: React.CSSProperties;
  className?: string;
}) {
  return (
    <div
      className={className}
      style={{
        background: KC.surfaceContainer,
        backdropFilter: 'blur(16px)',
        WebkitBackdropFilter: 'blur(16px)',
        border: `1px solid ${KC.border}`,
        borderRadius: 6,
        ...style,
      }}
    >
      {children}
    </div>
  );
}

function ToolBtn({
  icon,
  active,
  title,
  onClick,
}: {
  icon: string;
  active?: boolean;
  title: string;
  onClick?: () => void;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      style={{
        width: 48,
        height: 48,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: active ? 'rgba(40,42,48,0.9)' : 'transparent',
        // Left accent via inset box-shadow — avoids mixing the `border`/`borderLeft`
        // shorthands with their longhands (React rerender warning, MET-511).
        border: 'none',
        boxShadow: active ? `inset 2px 0 0 ${KC.orange}` : 'none',
        color: active ? KC.orange : KC.onSurfaceVariant,
        cursor: 'pointer',
        transition: 'color 0.12s, background 0.12s',
        outline: 'none',
      }}
      onMouseEnter={(e) => {
        if (!active) (e.currentTarget as HTMLButtonElement).style.background = KC.surfaceHigh;
      }}
      onMouseLeave={(e) => {
        if (!active) (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
      }}
    >
      <span className="material-symbols-outlined" style={{ fontSize: 18 }}>{icon}</span>
    </button>
  );
}

// ── NodeDetail (right floating panel) ────────────────────────────────────────
// ── Work-product file: worktype + path + download / open / preview (MET-483) ──
const _PREVIEW_IMG_FORMATS = new Set(['png', 'jpg', 'jpeg', 'gif', 'svg']);
const _PREVIEW_TEXT_FORMATS = new Set([
  'txt', 'md', 'json', 'csv', 'log', 'kicad_sch', 'kicad_pcb', 'net', 'gbr', 'c', 'h',
]);

function FileActionBtn({
  icon,
  label,
  onClick,
}: {
  icon: string;
  label: string;
  onClick?: () => void;
}) {
  return (
    <Button variant="secondary" size="sm" className="text-xs" onClick={onClick}>
      <span className="material-symbols-outlined" style={{ fontSize: 13, marginRight: 4, verticalAlign: 'middle' }}>{icon}</span>
      {label}
    </Button>
  );
}

function WorkProductFileSection({ node }: { node: TwinNode }) {
  const wpType = node.properties.wp_type ? String(node.properties.wp_type) : undefined;
  const filePath = node.properties.file_path ? String(node.properties.file_path) : '';
  const fmt = (node.properties.format ? String(node.properties.format) : '').toLowerCase();
  const [preview, setPreview] = useState(false);
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const isImg = _PREVIEW_IMG_FORMATS.has(fmt);
  const isPdf = fmt === 'pdf';
  const isText = _PREVIEW_TEXT_FORMATS.has(fmt);
  const inlineUrl = nodeFileUrl(node.id, false);
  const downloadUrl = nodeFileUrl(node.id, true);

  const togglePreview = useCallback(async () => {
    if (preview) { setPreview(false); return; }
    setPreview(true);
    setError(null);
    if (isText) {
      setLoading(true);
      try {
        setText(await fetchNodeFileText(node.id));
      } catch {
        setError('No file stored for this work product yet.');
      } finally {
        setLoading(false);
      }
    }
  }, [preview, isText, node.id]);

  return (
    <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
      <div className="font-mono uppercase mb-1.5" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
        File
      </div>
      <div className="flex items-center gap-2 mb-1" style={{ flexWrap: 'wrap' }}>
        <span
          className="font-mono"
          style={{ fontSize: 10, color: KC.orange, background: 'rgba(230,126,34,0.1)', padding: '2px 6px', borderRadius: 3, textTransform: 'uppercase', letterSpacing: '0.06em' }}
        >
          {wpType ?? 'unknown'}
        </span>
        {fmt && <span className="font-mono" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>.{fmt}</span>}
      </div>
      <div
        className="font-mono mb-2"
        title={filePath || undefined}
        style={{ fontSize: 11, color: filePath ? KC.onSurface : KC.onSurfaceVariant, wordBreak: 'break-all' }}
      >
        {filePath || 'No file path on record'}
      </div>
      <div className="flex gap-1.5">
        <a href={downloadUrl} download style={{ textDecoration: 'none' }}><FileActionBtn icon="download" label="Download" /></a>
        <a href={inlineUrl} target="_blank" rel="noreferrer" style={{ textDecoration: 'none' }}><FileActionBtn icon="open_in_new" label="Open" /></a>
        <FileActionBtn icon="visibility" label={preview ? 'Hide' : 'Preview'} onClick={togglePreview} />
      </div>
      {preview && (
        <div className="mt-2" style={{ border: `1px solid ${KC.border}`, borderRadius: 4, overflow: 'hidden', maxHeight: 320 }}>
          {error ? (
            <div className="font-mono px-2 py-3" style={{ fontSize: 11, color: KC.onSurfaceVariant }}>{error}</div>
          ) : isImg ? (
            <img src={inlineUrl} alt={node.name} style={{ width: '100%', objectFit: 'contain', maxHeight: 320 }} />
          ) : isPdf ? (
            <iframe src={inlineUrl} title={node.name} style={{ width: '100%', height: 320, border: 'none', background: '#fff' }} />
          ) : isText ? (
            <pre style={{ margin: 0, padding: 8, fontSize: 10, color: KC.onSurface, maxHeight: 320, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
              {loading ? 'Loading…' : (text ?? '')}
            </pre>
          ) : (
            <div className="font-mono px-2 py-3" style={{ fontSize: 11, color: KC.onSurfaceVariant }}>
              Inline preview not available for .{fmt || 'this type'} — use Open or Download.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Export for robotics sim (MET-720) ────────────────────────────────────────
const _EXPORT_FORMATS = ['urdf', 'sdf', 'usd'] as const;
type _ExportFormat = (typeof _EXPORT_FORMATS)[number];

const _exportInputStyle: React.CSSProperties = {
  fontSize: 11,
  background: '#1e1f26',
  border: `1px solid ${KC.border}`,
  color: KC.onSurface,
};

function ExportForSimSection({ node, onClose }: { node: TwinNode; onClose: () => void }) {
  const toast = useToast();
  const urdfExport = useExportUrdf();
  const sdfExport = useExportSdf();
  const usdExport = useExportUsd();

  const [format, setFormat] = useState<_ExportFormat>('urdf');
  const [material, setMaterial] = useState('');
  const [density, setDensity] = useState('');
  const [linkName, setLinkName] = useState('base_link');
  const [modelName, setModelName] = useState('model');
  const [primName, setPrimName] = useState('model');
  const [xacro, setXacro] = useState(false);
  const [worldName, setWorldName] = useState('');
  const [staticFlag, setStaticFlag] = useState(false);
  const [result, setResult] = useState<{ outputFile: ExportFile; meshFile: ExportFile } | null>(null);

  const pending = urdfExport.isPending || sdfExport.isPending || usdExport.isPending;
  const densityKgM3 = density.trim() ? Number(density) : undefined;

  const handleSubmit = () => {
    setResult(null);
    const onSuccess = (data: { output_file: ExportFile; mesh_file: ExportFile }) => {
      setResult({ outputFile: data.output_file, meshFile: data.mesh_file });
      toast.success(`Exported ${data.output_file.filename}`);
    };
    const onError = () => toast.error(`${format.toUpperCase()} export failed`);

    if (format === 'urdf') {
      urdfExport.mutate(
        {
          node_id: node.id,
          link_name: linkName || undefined,
          material: material || undefined,
          density_kg_m3: densityKgM3,
          xacro,
        },
        { onSuccess, onError },
      );
    } else if (format === 'sdf') {
      sdfExport.mutate(
        {
          node_id: node.id,
          model_name: modelName || undefined,
          link_name: linkName || undefined,
          material: material || undefined,
          density_kg_m3: densityKgM3,
          static: staticFlag,
          world_name: worldName || undefined,
        },
        { onSuccess, onError },
      );
    } else {
      usdExport.mutate(
        {
          node_id: node.id,
          prim_name: primName || undefined,
          material: material || undefined,
          density_kg_m3: densityKgM3,
        },
        { onSuccess, onError },
      );
    }
  };

  return (
    <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
      <div className="flex items-center justify-between mb-1.5">
        <div className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
          Export for robotics sim
        </div>
        <button
          type="button"
          onClick={onClose}
          style={{ background: 'transparent', border: 'none', color: KC.onSurfaceVariant, cursor: 'pointer', padding: 2 }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>close</span>
        </button>
      </div>

      <div className="flex gap-1 mb-2">
        {_EXPORT_FORMATS.map((f) => (
          <button
            key={f}
            type="button"
            onClick={() => { setFormat(f); setResult(null); }}
            className="font-mono rounded px-2 py-1 uppercase"
            style={{
              fontSize: 10,
              background: format === f ? KC.orangeFaint : 'transparent',
              border: `1px solid ${format === f ? KC.orangeBorder : KC.border}`,
              color: format === f ? KC.orange : KC.onSurfaceVariant,
              cursor: 'pointer',
            }}
          >
            {f}
          </button>
        ))}
      </div>

      <div className="flex flex-col gap-1.5 mb-2">
        <div className="flex gap-1.5">
          <select
            value={material}
            onChange={(e) => setMaterial(e.target.value)}
            className="font-mono rounded px-2 py-1 flex-1"
            style={_exportInputStyle}
          >
            <option value="">No material (density only)</option>
            {CAD_EXPORT_MATERIALS.map((m) => (
              <option key={m} value={m}>{m.replace(/_/g, ' ')}</option>
            ))}
          </select>
          <input
            type="number"
            placeholder="density kg/m³"
            value={density}
            onChange={(e) => setDensity(e.target.value)}
            className="font-mono rounded px-2 py-1"
            style={{ ..._exportInputStyle, width: 110 }}
          />
        </div>

        {format === 'urdf' && (
          <div className="flex gap-1.5 items-center">
            <input
              type="text"
              placeholder="link name"
              value={linkName}
              onChange={(e) => setLinkName(e.target.value)}
              className="font-mono rounded px-2 py-1 flex-1"
              style={_exportInputStyle}
            />
            <label className="font-mono flex items-center gap-1" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
              <input type="checkbox" checked={xacro} onChange={(e) => setXacro(e.target.checked)} />
              xacro
            </label>
          </div>
        )}

        {format === 'sdf' && (
          <>
            <div className="flex gap-1.5">
              <input
                type="text"
                placeholder="model name"
                value={modelName}
                onChange={(e) => setModelName(e.target.value)}
                className="font-mono rounded px-2 py-1 flex-1"
                style={_exportInputStyle}
              />
              <input
                type="text"
                placeholder="link name"
                value={linkName}
                onChange={(e) => setLinkName(e.target.value)}
                className="font-mono rounded px-2 py-1 flex-1"
                style={_exportInputStyle}
              />
            </div>
            <div className="flex gap-1.5 items-center">
              <input
                type="text"
                placeholder="world name (optional)"
                value={worldName}
                onChange={(e) => setWorldName(e.target.value)}
                className="font-mono rounded px-2 py-1 flex-1"
                style={_exportInputStyle}
              />
              <label className="font-mono flex items-center gap-1" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
                <input type="checkbox" checked={staticFlag} onChange={(e) => setStaticFlag(e.target.checked)} />
                static
              </label>
            </div>
          </>
        )}

        {format === 'usd' && (
          <input
            type="text"
            placeholder="prim name"
            value={primName}
            onChange={(e) => setPrimName(e.target.value)}
            className="font-mono rounded px-2 py-1"
            style={_exportInputStyle}
          />
        )}
      </div>

      <Button variant="primary" size="sm" onClick={handleSubmit} disabled={pending} className="text-xs w-full">
        {pending ? 'Exporting…' : `Export ${format.toUpperCase()}`}
      </Button>

      {result && (
        <div className="flex gap-1.5 mt-2" style={{ flexWrap: 'wrap' }}>
          <a href={toDownloadHref(result.outputFile.download_url)} download style={{ textDecoration: 'none' }}>
            <FileActionBtn icon="download" label={result.outputFile.filename} />
          </a>
          <a href={toDownloadHref(result.meshFile.download_url)} download style={{ textDecoration: 'none' }}>
            <FileActionBtn icon="download" label={result.meshFile.filename} />
          </a>
        </div>
      )}
    </div>
  );
}

function NodeDetail({ node, onClose }: { node: TwinNode; onClose: () => void }) {
  const loadModel = useViewerStore((s) => s.loadModel);
  const setViewMode = useViewerStore((s) => s.setViewMode);
  const openBooleanCut = useViewerStore((s) => s.openBooleanCut);
  const [loading3d, setLoading3d] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const isCAD = node.properties.wp_type === 'cad_model';

  const handleView3D = useCallback(async () => {
    setLoading3d(true);
    try {
      const result = await getNodeModel(node.id);
      const manifest: ModelManifest = {
        parts: result.metadata.parts.map((p) => ({
          name: p.name,
          meshName: p.meshName ?? p.name,
          children: (p.children ?? []) as ModelManifest['parts'],
          boundingBox: p.boundingBox as PartTreeNode['boundingBox'],
        })),
        meshToNodeMap: {},
        materials: result.metadata.materials ?? [],
        stats: result.metadata.stats ?? { triangleCount: 0, fileSize: 0 },
      };
      const glbUrl = result.glb_url.startsWith('/v1/') ? `/api${result.glb_url}` : result.glb_url;
      loadModel(glbUrl, manifest);
      setViewMode('3d');
    } catch (err) {
      console.error('Failed to load 3D model:', err);
    } finally {
      setLoading3d(false);
    }
  }, [node.id, loadModel, setViewMode]);

  // Boolean-cut targets whichever node is open in the 3D panel (MET-612) —
  // ensure this node is actually loaded there first, then enter picking mode.
  const handleBooleanCut = useCallback(async () => {
    await handleView3D();
    openBooleanCut(node.id);
  }, [handleView3D, openBooleanCut, node.id]);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div
        className="flex items-center justify-between px-3 flex-shrink-0"
        style={{ height: 36, borderBottom: `1px solid ${KC.border}` }}
      >
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined" style={{ fontSize: 14, color: KC.orange }}>
            {NODE_ICONS[node.type]}
          </span>
          <span className="font-mono text-xs truncate" style={{ color: KC.onSurface, maxWidth: 180 }}>
            {node.name}
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          style={{ background: 'transparent', border: 'none', color: KC.onSurfaceVariant, cursor: 'pointer', padding: 4 }}
          onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = KC.onSurface; }}
          onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = KC.onSurfaceVariant; }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>close</span>
        </button>
      </div>

      <div className="flex-1 overflow-y-auto">
        {/* Status + meta */}
        <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
          <StatusBadge status={node.status} />
          <div className="font-mono mt-1" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
            {node.domain} · {node.type} · {formatRelativeTime(node.updatedAt)}
          </div>
        </div>

        {/* View 3D / Boolean cut (MET-612) */}
        {isCAD && (
          <div className="px-3 py-2 flex-shrink-0 flex gap-1.5" style={{ borderBottom: `1px solid ${KC.border}` }}>
            <Button variant="primary" size="sm" onClick={handleView3D} disabled={loading3d} className="text-xs flex-1">
              <span className="material-symbols-outlined" style={{ fontSize: 13, marginRight: 4, verticalAlign: 'middle' }}>view_in_ar</span>
              {loading3d ? 'Loading…' : 'View 3D Model'}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={handleBooleanCut}
              disabled={loading3d}
              className="text-xs"
              title="Boolean cut against another CAD node"
            >
              <span className="material-symbols-outlined" style={{ fontSize: 13, verticalAlign: 'middle' }}>content_cut</span>
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setExportOpen((v) => !v)}
              className="text-xs"
              title="Export for robotics sim (URDF/SDF/USD)"
            >
              <span className="material-symbols-outlined" style={{ fontSize: 13, verticalAlign: 'middle' }}>precision_manufacturing</span>
            </Button>
          </div>
        )}

        {/* Export for robotics sim (MET-720) */}
        {isCAD && exportOpen && (
          <ExportForSimSection node={node} onClose={() => setExportOpen(false)} />
        )}

        {/* File: worktype + path + download / open / preview (MET-483) */}
        <WorkProductFileSection node={node} />

        {/* Properties */}
        {Object.keys(node.properties).length > 0 && (
          <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
            <div className="font-mono uppercase mb-1.5" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
              Properties
            </div>
            <table className="w-full" style={{ borderCollapse: 'collapse' }}>
              <tbody>
                {Object.entries(node.properties).map(([k, v]) => (
                  <tr key={k} style={{ borderBottom: '1px solid rgba(65,72,90,0.1)' }}>
                    <td className="py-1 pr-3 font-mono" style={{ fontSize: 11, color: KC.onSurfaceVariant, width: '40%' }}>{k}</td>
                    <td className="py-1 font-mono" style={{ fontSize: 11, color: KC.onSurface }}>{String(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Revision history (GET /v1/twin/nodes/{id}/versions) */}
        <NodeHistorySection nodeId={node.id} />

        {/* Pending design-change proposals for this node (gated apply, MET-548) */}
        <div className="px-3 py-2 flex-shrink-0">
          <NodeProposals nodeId={node.id} onApplied={isCAD ? handleView3D : undefined} />
        </div>
      </div>
    </div>
  );
}

// ── NodeHistorySection ─────────────────────────────────────────────────────────
/**
 * Revision history for the selected node. `useNodeVersionHistory` and its
 * underlying endpoint already existed but had no UI consumer anywhere in
 * the dashboard — this is that missing consumer.
 */
function NodeHistorySection({ nodeId }: { nodeId: string }) {
  const { data: revisions, isLoading } = useNodeVersionHistory(nodeId);
  const [expanded, setExpanded] = useState(false);

  if (isLoading || !revisions || revisions.length === 0) return null;

  const sorted = [...revisions].sort((a, b) => b.revision - a.revision);
  const visible = expanded ? sorted : sorted.slice(0, 3);

  return (
    <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: `1px solid ${KC.border}` }}>
      <div className="flex items-center justify-between mb-1.5">
        <div className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
          History · {revisions.length}
        </div>
        {sorted.length > 3 && (
          <button
            type="button"
            onClick={() => setExpanded((e) => !e)}
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: KC.onSurfaceVariant, fontSize: 10, fontFamily: 'inherit' }}
          >
            {expanded ? 'Show less' : `Show all ${sorted.length}`}
          </button>
        )}
      </div>
      <div className="space-y-1.5">
        {visible.map((rev) => (
          <div key={rev.revision} className="flex items-start gap-2" style={{ fontSize: 11 }}>
            <span
              className="font-mono rounded px-1 flex-shrink-0"
              style={{ background: KC.surfaceHigh, color: KC.onSurfaceVariant, fontSize: 10 }}
            >
              v{rev.revision}
            </span>
            <div className="min-w-0">
              <div style={{ color: KC.onSurface }}>{rev.change_description}</div>
              <div className="font-mono" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
                {formatRelativeTime(rev.created_at)} · {rev.content_hash.slice(0, 8)}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── SceneDropdown ─────────────────────────────────────────────────────────────
function SceneDropdown({
  nodes,
  selectedId,
  onSelect,
}: {
  nodes: TwinNode[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    if (open) document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [open]);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 rounded px-3"
        style={{
          height: 28,
          background: 'rgba(30,31,38,0.85)',
          backdropFilter: 'blur(16px)',
          WebkitBackdropFilter: 'blur(16px)',
          border: `1px solid ${KC.border}`,
          fontSize: 11,
          letterSpacing: '0.08em',
          textTransform: 'uppercase',
          color: KC.onSurfaceVariant,
          cursor: 'pointer',
          fontFamily: "'Roboto Mono', monospace",
        }}
      >
        <span className="material-symbols-outlined" style={{ fontSize: 14 }}>account_tree</span>
        SCENE
        <span style={{ fontSize: 10, marginLeft: 1 }}>▾</span>
      </button>

      {open && (
        <GlassPanel
          style={{
            position: 'absolute',
            top: 36,
            left: 0,
            width: 232,
            zIndex: 60,
            overflow: 'hidden',
            background: 'rgba(25,27,34,0.96)',
          }}
        >
          <div className="px-4 py-2.5" style={{ borderBottom: `1px solid ${KC.border}` }}>
            <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
              Twin Nodes · {nodes.length}
            </span>
          </div>
          <div className="py-1" style={{ maxHeight: 280, overflowY: 'auto' }}>
            {nodes.length === 0 ? (
              <div className="px-3 py-2 font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>
                No nodes yet
              </div>
            ) : (
              nodes.map((n) => {
                const isActive = n.id === selectedId;
                return (
                  <button
                    key={n.id}
                    type="button"
                    onClick={() => { onSelect(n.id); setOpen(false); }}
                    className="flex items-center gap-2 w-full text-left"
                    style={{
                      padding: '6px 12px',
                      background: isActive ? KC.orangeFaint : 'transparent',
                      color: isActive ? KC.orange : KC.onSurfaceVariant,
                      fontSize: 12,
                      cursor: 'pointer',
                      border: 'none',
                      boxShadow: isActive ? `inset 2px 0 0 ${KC.orange}` : 'none',
                      width: '100%',
                      fontFamily: 'Inter, sans-serif',
                    }}
                    onMouseEnter={(e) => {
                      if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = KC.surfaceHigh;
                    }}
                    onMouseLeave={(e) => {
                      if (!isActive) (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
                    }}
                  >
                    <span className="material-symbols-outlined flex-shrink-0" style={{ fontSize: 14 }}>
                      {NODE_ICONS[n.type]}
                    </span>
                    <span className="truncate">{n.name}</span>
                  </button>
                );
              })
            )}
          </div>
        </GlassPanel>
      )}
    </div>
  );
}

// ── TwinViewerPage ────────────────────────────────────────────────────────────
type ConversionPhase = 'idle' | 'uploading' | 'converting' | 'loading';

export function TwinViewerPage() {
  // ── state ──
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [assemblyExportOpen, setAssemblyExportOpen] = useState(false);
  const [conversionPhase, setConversionPhase] = useState<ConversionPhase>('idle');
  const [quality, setQuality] = useState('standard');
  const [showTree, setShowTree] = useState(true);
  // Collapse toggle for the left pane (Nodes list in graph mode / Components
  // tree in 3D mode) so it can be tucked away to free up canvas space.
  const [leftPaneCollapsed, setLeftPaneCollapsed] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Capture the live WebGL canvas as a PNG download (Screenshot button).
  function handleScreenshot() {
    const canvas = document.querySelector<HTMLCanvasElement>('.twin-canvas canvas');
    if (!canvas) return;
    const url = canvas.toDataURL('image/png');
    const a = document.createElement('a');
    a.href = url;
    a.download = `twin-${Date.now()}.png`;
    a.click();
  }
  // Track which node's model is loaded so the auto-loader (MET-505) doesn't refetch.
  const [loadedModelNodeId, setLoadedModelNodeId] = useState<string | null>(null);
  // MET-683: distinguish "nothing loaded yet" from "we tried and the backend
  // rejected it" -- previously a failed conversion (e.g. an empty/invalid
  // STEP) silently fell back to the generic upload placeholder with no
  // indication a model was ever attempted, console.error only.
  const [modelLoadError, setModelLoadError] = useState<string | null>(null);
  // Deep-link: /twin?node=<id> preselects a node (e.g. from a project's work
  // product list, MET-514).
  const [searchParams] = useSearchParams();

  // ── project scope — Context UI ──
  // Reads the single global active-project selection every page now shares
  // (the Topbar switcher), instead of the page-local scope this used to keep.
  const { activeProjectId } = useActiveProject();

  // ── data ──
  const { data: nodes, isLoading, isFetching, dataUpdatedAt } = useTwinNodes(activeProjectId ?? undefined);
  const { data: selectedNode } = useTwinNode(selectedId ?? undefined);
  const { data: relationships = [] } = useTwinRelationships(activeProjectId ?? undefined);
  const items = nodes ?? [];

  // ── viewer store ──
  const viewMode = useViewerStore((s) => s.viewMode);
  const setViewMode = useViewerStore((s) => s.setViewMode);
  const manifest = useViewerStore((s) => s.manifest);
  const glbUrl = useViewerStore((s) => s.glbUrl);
  const selectPart = useViewerStore((s) => s.selectPart);
  const selectedMeshName = useViewerStore((s) => s.selectedMeshName);
  const loadModel = useViewerStore((s) => s.loadModel);
  const clearModel = useViewerStore((s) => s.clearModel);

  const uploadMutation = useUploadAndConvert();

  // MET-514: sync the selected node with the ?node= deep link. Symmetric --
  // clears the selection when the param disappears too (e.g. the sidebar's
  // plain /twin link doesn't remount this page, only re-renders it with
  // empty searchParams; MET-686 fixed a stale-selection bug where the
  // previous work product's detail panel and breadcrumb kept showing).
  useEffect(() => {
    setSelectedId(searchParams.get('node'));
  }, [searchParams]);

  // MET-674: clear the selected node (and its cached model) when the active
  // project changes -- otherwise the detail panel and breadcrumb keep
  // showing the PREVIOUS project's node after the node list/canvas has
  // already updated to the new project. Guarded to skip the null -> X
  // transition (MET-686): on a cold session (no project ever persisted),
  // useActiveProject's own "auto-select the newest project" effect can land
  // a moment after mount, racing the ?node= deep-link effect above and
  // wiping the just-navigated-to node before the user ever sees it.
  const prevProjectIdRef = useRef(activeProjectId);
  useEffect(() => {
    if (prevProjectIdRef.current !== null && prevProjectIdRef.current !== activeProjectId) {
      setSelectedId(null);
      setLoadedModelNodeId(null);
    }
    prevProjectIdRef.current = activeProjectId;
  }, [activeProjectId]);

  // MET-683: a stale error from a previously-selected node must not linger
  // once the user picks a different node (including a non-CAD one, which
  // never re-enters the load effect below to clear it itself).
  useEffect(() => {
    setModelLoadError(null);
  }, [selectedNode?.id]);

  // MET-505: in MODEL view, auto-load the selected node's geometry. Previously
  // the viewer only loaded via the graph-mode "View in 3D" button, so picking a
  // CAD node from the scene dropdown left the "upload a STEP file" placeholder.
  useEffect(() => {
    if (viewMode !== '3d') return;
    const n = selectedNode;
    if (!n || n.properties.wp_type !== 'cad_model') return;
    if (loadedModelNodeId === n.id) return;
    // MET-683: clear any PREVIOUS node's geometry before attempting this
    // node's load -- otherwise a failed load left the prior node's model on
    // screen under the new node's breadcrumb, with no error overlay (it was
    // gated on `!glbUrl`, which a stale-but-present model kept satisfying as
    // false), silently misleading the user rather than showing nothing/an
    // error for the node they actually just selected.
    clearModel();
    let cancelled = false;
    (async () => {
      try {
        const result = await getNodeModel(n.id);
        if (cancelled) return;
        const m: ModelManifest = {
          parts: result.metadata.parts.map((p) => ({
            name: p.name,
            meshName: p.meshName ?? p.name,
            children: (p.children ?? []) as ModelManifest['parts'],
            boundingBox: p.boundingBox as PartTreeNode['boundingBox'],
          })),
          meshToNodeMap: {},
          materials: result.metadata.materials ?? [],
          stats: result.metadata.stats ?? { triangleCount: 0, fileSize: 0 },
        };
        const url = result.glb_url.startsWith('/v1/') ? `/api${result.glb_url}` : result.glb_url;
        loadModel(url, m);
        setLoadedModelNodeId(n.id);
        setModelLoadError(null);
      } catch (err) {
        // MET-683: this used to be swallowed to a console.error only, leaving
        // the generic "Upload a STEP file..." placeholder up with no sign a
        // load was ever attempted -- confirmed live against a real node whose
        // STEP export was empty (OCCT 422 "Can't export empty scenes!"), the
        // dashboard gave zero indication anything was wrong.
        if (!cancelled) {
          console.error('Failed to auto-load 3D model:', err);
          setModelLoadError(getModelLoadErrorMessage(err));
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [viewMode, selectedNode, loadedModelNodeId, loadModel, clearModel]);

  useEffect(() => {
    if (!uploadMutation.isPending) {
      if (uploadMutation.isSuccess && conversionPhase === 'converting') {
        setConversionPhase('loading');
        const t = setTimeout(() => setConversionPhase('idle'), 800);
        return () => clearTimeout(t);
      }
      if (!uploadMutation.isSuccess) setConversionPhase('idle');
    }
  }, [uploadMutation.isPending, uploadMutation.isSuccess, conversionPhase]);

  const handleUpload = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (file) {
        setConversionPhase('uploading');
        const t = setTimeout(() => setConversionPhase('converting'), 1200);
        uploadMutation.mutate({ file, quality }, { onSettled: () => clearTimeout(t) });
      }
    },
    [uploadMutation, quality],
  );

  const handlePartClick = useCallback(
    (part: PartInfo) => { selectPart(part.meshName); },
    [selectPart],
  );

  const isGraphMode = viewMode === 'graph';

  // ── status bar label ──
  const statusLabel = isGraphMode ? 'GRAPH VIEW' : 'ORBIT MODE';
  // Was a hardcoded "X 0.0 Y 0.0 Z 0.0" that never reflected the actual
  // camera or selection — now shows the real selected part, or an honest
  // "no selection" state.
  const statusCenter = isGraphMode
    ? `${items.length} node${items.length !== 1 ? 's' : ''}`
    : selectedMeshName
      ? `selected · ${selectedMeshName}`
      : 'no selection';

  return (
    /*
     * Full-bleed canvas: escapes AppLayout's p-6 (24px) padding by using
     * negative margins, then fills the remaining viewport height.
     */
    <div
      style={{
        position: 'relative',
        margin: -24,
        height: 'calc(100vh - 40px)', // 40px = h-10 topbar
        overflow: 'hidden',
        background: KC.surface,
        backgroundImage: 'radial-gradient(circle, rgba(154,154,170,0.18) 1px, transparent 1px)',
        backgroundSize: '32px 32px',
      }}
    >

      {/* ═══════════════════════════════════════════
          CANVAS — full-bleed content area
      ════════════════════════════════════════════ */}
      <div style={{ position: 'absolute', inset: 0 }}>
        {isGraphMode ? (
          /* Graph mode: interactive node-link graph of the twin */
          isLoading ? (
            <div className="flex items-center justify-center h-full font-mono text-xs" style={{ color: KC.onSurfaceVariant }}>
              Loading twin graph…
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3">
              <span className="material-symbols-outlined" style={{ fontSize: 40, color: KC.onSurfaceVariant, opacity: 0.4 }}>hub</span>
              <span className="font-mono text-xs" style={{ color: KC.onSurfaceVariant }}>Empty twin</span>
              <span className="font-mono" style={{ fontSize: 11, color: KC.onSurfaceVariant, opacity: 0.6 }}>
                Work products will appear here when agents run.
              </span>
            </div>
          ) : (
            <TwinGraphCanvas
              nodes={items}
              relationships={relationships}
              selectedId={selectedId}
              onSelectNode={setSelectedId}
            />
          )
        ) : (
          /* 3D model mode */
          <>
            <R3FViewer onPartClick={handlePartClick} onBooleanCutComplete={setSelectedId} />
            {!glbUrl && modelLoadError && (
              <div
                style={{
                  position: 'absolute',
                  top: '50%',
                  left: '50%',
                  transform: 'translate(-50%, -50%)',
                  zIndex: 46,
                  maxWidth: 420,
                  textAlign: 'center',
                  pointerEvents: 'none',
                  // MET-683: an opaque backdrop so this overlay fully covers
                  // R3FViewer's own "Upload a STEP file..." placeholder text
                  // underneath instead of visually overlapping it.
                  background: KC.surface,
                  border: `1px solid ${KC.border}`,
                  borderRadius: 6,
                  padding: '20px 24px',
                }}
              >
                <span
                  className="material-symbols-outlined"
                  style={{ fontSize: 22, color: '#ffb4ab', display: 'block', marginBottom: 6 }}
                >
                  error
                </span>
                <p className="font-mono text-xs" style={{ color: '#ffb4ab', marginBottom: 4 }}>
                  Model failed to load
                </p>
                <p className="font-mono" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
                  {modelLoadError}
                </p>
              </div>
            )}
            {glbUrl && (
              <div
                style={{
                  position: 'absolute',
                  bottom: 88,
                  left: '50%',
                  transform: 'translateX(-50%)',
                  zIndex: 45,
                }}
              >
                <ExplodedViewControls />
              </div>
            )}
          </>
        )}
      </div>

      {/* ═══════════════════════════════════════════
          GRAPH MODE: floating node list (left)
      ════════════════════════════════════════════ */}
      {isGraphMode && items.length > 0 && (
        <GlassPanel
          style={{
            position: 'absolute',
            top: 56,
            left: 16,
            bottom: leftPaneCollapsed ? undefined : 80,
            width: 260,
            zIndex: 40,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          <div
            className="flex items-center gap-2 px-3 flex-shrink-0"
            style={{ height: 36, borderBottom: leftPaneCollapsed ? 'none' : `1px solid ${KC.border}` }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 14, color: KC.onSurfaceVariant }}>hub</span>
            <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
              Nodes
            </span>
            <span
              className="font-mono rounded px-1.5"
              style={{ fontSize: 10, background: KC.surfaceHigh, color: KC.onSurfaceVariant }}
            >
              {items.length}
            </span>
            <button
              type="button"
              onClick={() => setLeftPaneCollapsed((c) => !c)}
              className="ml-auto flex items-center justify-center"
              title={leftPaneCollapsed ? 'Expand' : 'Collapse'}
              aria-label={leftPaneCollapsed ? 'Expand nodes panel' : 'Collapse nodes panel'}
              style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 0, color: KC.onSurfaceVariant }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>
                {leftPaneCollapsed ? 'expand_more' : 'expand_less'}
              </span>
            </button>
          </div>
          {!leftPaneCollapsed && (
          <ul className="flex-1 overflow-y-auto" style={{ listStyle: 'none', margin: 0, padding: 0 }}>
            {items.map((node) => {
              const active = node.id === selectedId;
              return (
                <li key={node.id}>
                  <button
                    type="button"
                    onClick={() => setSelectedId(active ? null : node.id)}
                    className="flex w-full items-center gap-2 text-left"
                    style={{
                      height: 36,
                      padding: '0 12px',
                      background: active ? 'rgba(40,42,48,1)' : 'transparent',
                      cursor: 'pointer',
                      border: 'none',
                      boxShadow: active ? `inset 2px 0 0 ${KC.orange}` : 'none',
                      outline: 'none',
                      width: '100%',
                    }}
                    onMouseEnter={(e) => {
                      if (!active) (e.currentTarget as HTMLButtonElement).style.background = 'rgba(40,42,48,0.6)';
                    }}
                    onMouseLeave={(e) => {
                      if (!active) (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
                    }}
                  >
                    <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: node.status === 'valid' || node.status === 'active' ? KC.green : KC.onSurfaceVariant, flexShrink: 0 }} />
                    <span className="material-symbols-outlined flex-shrink-0" style={{ fontSize: 14, color: active ? KC.orange : KC.onSurfaceVariant }}>
                      {NODE_ICONS[node.type]}
                    </span>
                    <span className="flex-1 truncate font-mono" style={{ fontSize: 12, color: active ? KC.onSurface : KC.onSurfaceVariant }}>
                      {node.name}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          )}
        </GlassPanel>
      )}

      {/* ═══════════════════════════════════════════
          GRAPH MODE: floating node detail (right)
      ════════════════════════════════════════════ */}
      {isGraphMode && selectedNode && (
        <GlassPanel
          style={{
            position: 'absolute',
            top: 56,
            right: 72, // leave room for right toolbar (48px) + gap
            bottom: 80,
            width: 320,
            zIndex: 40,
            display: 'flex',
            flexDirection: 'column',
            overflow: 'hidden',
          }}
        >
          <NodeDetail node={selectedNode} onClose={() => setSelectedId(null)} />
        </GlassPanel>
      )}

      {/* ═══════════════════════════════════════════
          3D MODE: component tree (left panel)
      ════════════════════════════════════════════ */}
      {!isGraphMode && manifest && showTree && (
        <GlassPanel
          style={{
            position: 'absolute',
            top: 56,
            left: 16,
            bottom: leftPaneCollapsed ? undefined : 80,
            width: 240,
            zIndex: 40,
            overflow: 'hidden',
          }}
        >
          <ComponentTree
            collapsed={leftPaneCollapsed}
            onToggleCollapse={() => setLeftPaneCollapsed((c) => !c)}
          />
        </GlassPanel>
      )}

      {/* ═══════════════════════════════════════════
          3D MODE: BOM annotation panel (right)
      ════════════════════════════════════════════ */}
      {!isGraphMode && selectedMeshName && manifest && (
        <GlassPanel
          style={{
            position: 'absolute',
            top: 56,
            right: 72,
            bottom: 80,
            width: 300,
            zIndex: 40,
            overflow: 'hidden',
          }}
        >
          <BomAnnotationPanel />
        </GlassPanel>
      )}

      {/* ═══════════════════════════════════════════
          TOP-LEFT: Scene dropdown + breadcrumb pill
      ════════════════════════════════════════════ */}
      <div
        className="flex items-center gap-2"
        style={{ position: 'absolute', top: 16, left: 16, zIndex: 50 }}
      >
        <SceneDropdown nodes={items} selectedId={selectedId} onSelect={setSelectedId} />

        {/* Breadcrumb pill */}
        <div
          className="flex items-center gap-1.5 rounded px-3"
          style={{
            height: 28,
            background: 'rgba(30,31,38,0.7)',
            backdropFilter: 'blur(16px)',
            WebkitBackdropFilter: 'blur(16px)',
            border: `1px solid ${KC.border}`,
          }}
        >
          <span style={{ fontSize: 12, color: KC.onSurfaceVariant }}>Digital Twin</span>
          {selectedNode && (
            <>
              <span style={{ fontSize: 11, color: 'rgba(154,154,170,0.4)' }}>›</span>
              <span style={{ fontSize: 12, color: KC.onSurface }}>{selectedNode.name}</span>
            </>
          )}
          <span
            className="ml-2 rounded px-1.5 font-mono"
            style={{
              fontSize: 9,
              fontWeight: 600,
              background: isGraphMode ? '#00a3e4' : KC.orange,
              color: isGraphMode ? '#fff' : KC.surface,
              letterSpacing: '0.04em',
            }}
          >
            {isGraphMode ? 'GRAPH' : '3D'}
          </span>
        </div>
      </div>

      {/* ═══════════════════════════════════════════
          TOP-RIGHT: MODEL|GRAPH toggle + utility buttons
      ════════════════════════════════════════════ */}
      <div
        className="flex items-center gap-2"
        style={{ position: 'absolute', top: 16, right: 64, zIndex: 50 }}
      >
        {/* Import */}
        <button
          type="button"
          onClick={() => setImportOpen((p) => !p)}
          className="flex items-center gap-1.5 rounded px-2"
          style={{
            height: 28,
            background: importOpen ? KC.orangeFaint : 'rgba(30,31,38,0.85)',
            backdropFilter: 'blur(16px)',
            border: `1px solid ${importOpen ? KC.orangeBorder : KC.borderMid}`,
            color: importOpen ? KC.orange : KC.onSurfaceVariant,
            fontSize: 11,
            cursor: 'pointer',
            letterSpacing: '0.06em',
            fontFamily: "'Roboto Mono', monospace",
          }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 14 }}>file_upload</span>
          IMPORT
        </button>

        {/* Export assembly for sim (MET-721) — only meaningful with a node list to pick parts from */}
        {isGraphMode && items.length > 0 && (
          <button
            type="button"
            onClick={() => setAssemblyExportOpen((p) => !p)}
            className="flex items-center gap-1.5 rounded px-2"
            style={{
              height: 28,
              background: assemblyExportOpen ? KC.orangeFaint : 'rgba(30,31,38,0.85)',
              backdropFilter: 'blur(16px)',
              border: `1px solid ${assemblyExportOpen ? KC.orangeBorder : KC.borderMid}`,
              color: assemblyExportOpen ? KC.orange : KC.onSurfaceVariant,
              fontSize: 11,
              cursor: 'pointer',
              letterSpacing: '0.06em',
              fontFamily: "'Roboto Mono', monospace",
            }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>precision_manufacturing</span>
            ASSEMBLY
          </button>
        )}

        {/* MODEL | GRAPH segmented toggle */}
        <div
          className="flex items-center rounded overflow-hidden"
          style={{
            background: 'rgba(25,27,34,0.9)',
            backdropFilter: 'blur(16px)',
            border: `1px solid ${KC.borderMid}`,
          }}
        >
          <button
            type="button"
            onClick={() => setViewMode('3d')}
            style={{
              padding: '0 12px',
              height: 28,
              fontSize: 11,
              fontFamily: "'Roboto Mono', monospace",
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              background: !isGraphMode ? KC.orangeFaint : 'transparent',
              color: !isGraphMode ? KC.orange : KC.onSurfaceVariant,
              border: 'none',
              cursor: 'pointer',
              transition: 'color 0.12s, background 0.12s',
            }}
          >
            MODEL
          </button>
          <div style={{ width: 1, height: 16, background: 'rgba(65,72,90,0.4)' }} />
          <button
            type="button"
            onClick={() => setViewMode('graph')}
            style={{
              padding: '0 12px',
              height: 28,
              fontSize: 11,
              fontFamily: "'Roboto Mono', monospace",
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              background: isGraphMode ? KC.orangeFaint : 'transparent',
              color: isGraphMode ? KC.orange : KC.onSurfaceVariant,
              border: 'none',
              cursor: 'pointer',
              transition: 'color 0.12s, background 0.12s',
            }}
          >
            GRAPH
          </button>
        </div>

        {/* Screenshot — captures the 3D canvas (only meaningful in model mode) */}
        {!isGraphMode && glbUrl && (
          <button
            type="button"
            onClick={handleScreenshot}
            className="flex items-center justify-center rounded"
            style={{
              width: 32,
              height: 32,
              background: 'rgba(30,31,38,0.8)',
              backdropFilter: 'blur(16px)',
              border: `1px solid ${KC.border}`,
              color: KC.onSurfaceVariant,
              cursor: 'pointer',
            }}
            title="Screenshot"
          >
            <span className="material-symbols-outlined" style={{ fontSize: 18 }}>photo_camera</span>
          </button>
        )}
      </div>

      {/* ═══════════════════════════════════════════
          RIGHT: Vertical viewport toolbar
      ════════════════════════════════════════════ */}
      <GlassPanel
        style={{
          position: 'absolute',
          right: 16,
          top: '50%',
          transform: 'translateY(-50%)',
          zIndex: 50,
          padding: '4px 0',
          overflow: 'hidden',
        }}
      >
        <ToolBtn icon="hub" active={isGraphMode} title="Graph View" onClick={() => setViewMode('graph')} />
        <ToolBtn
          icon="account_tree"
          active={!isGraphMode && showTree}
          title="Tree View"
          onClick={() => setShowTree((v) => !v)}
        />
      </GlassPanel>

      {/* ═══════════════════════════════════════════
          IMPORT PANEL (slide-in under top bar)
      ════════════════════════════════════════════ */}
      {assemblyExportOpen && (
        <div style={{ position: 'absolute', top: 52, right: 16, zIndex: 50 }}>
          <AssemblyExportPanel items={items} onClose={() => setAssemblyExportOpen(false)} />
        </div>
      )}

      {importOpen && (
        <GlassPanel
          style={{
            position: 'absolute',
            top: 52,
            right: 16,
            width: 320,
            zIndex: 50,
            overflow: 'hidden',
          }}
        >
          <div
            className="flex items-center justify-between px-3"
            style={{ height: 36, borderBottom: `1px solid ${KC.border}` }}
          >
            <span className="font-mono uppercase" style={{ fontSize: 10, letterSpacing: '0.1em', color: KC.onSurfaceVariant }}>
              Import Work Product
            </span>
            <button
              type="button"
              onClick={() => setImportOpen(false)}
              style={{ background: 'transparent', border: 'none', color: KC.onSurfaceVariant, cursor: 'pointer', padding: 4 }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>close</span>
            </button>
          </div>
          <div className="p-3">
            {/* Quality + upload row */}
            <div className="flex items-center gap-2 mb-3">
              <select
                value={quality}
                onChange={(e) => setQuality(e.target.value)}
                className="flex-1 font-mono rounded px-2 py-1 text-xs cursor-pointer"
                style={{
                  background: 'rgba(40,42,48,0.9)',
                  border: `1px solid ${KC.border}`,
                  color: KC.onSurfaceVariant,
                }}
              >
                <option value="preview">Preview</option>
                <option value="standard">Standard</option>
                <option value="fine">Fine</option>
              </select>
              {conversionPhase !== 'idle' ? (
                <div className="flex items-center gap-1.5 text-xs font-mono" style={{ color: KC.onSurfaceVariant }}>
                  <span className="material-symbols-outlined" style={{ fontSize: 13, color: KC.orange }}>sync</span>
                  {conversionPhase === 'uploading' ? 'Uploading…' : conversionPhase === 'converting' ? 'Converting…' : 'Loading…'}
                </div>
              ) : (
                <button
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploadMutation.isPending}
                  className="flex items-center gap-1.5 rounded px-2 py-1 text-xs font-mono cursor-pointer"
                  style={{ background: KC.orange, color: KC.surface, border: 'none' }}
                >
                  <span className="material-symbols-outlined" style={{ fontSize: 13 }}>upload_file</span>
                  Upload STEP
                </button>
              )}
              {!glbUrl && (
                <button
                  type="button"
                  onClick={() => { loadModel(getMockGlbUrl(), getMockManifest()); setViewMode('3d'); setImportOpen(false); }}
                  className="rounded px-2 py-1 text-xs font-mono cursor-pointer"
                  style={{ background: 'transparent', border: `1px dashed ${KC.border}`, color: KC.onSurfaceVariant }}
                >
                  Demo
                </button>
              )}
            </div>

            {/* Drop zone */}
            <div
              className="flex flex-col items-center justify-center rounded cursor-pointer"
              style={{
                border: '2px dashed rgba(65,72,90,0.4)',
                padding: '20px 16px',
                textAlign: 'center',
              }}
              onMouseEnter={(e) => {
                (e.currentTarget as HTMLDivElement).style.borderColor = 'rgba(230,126,34,0.4)';
                (e.currentTarget as HTMLDivElement).style.background = 'rgba(230,126,34,0.04)';
              }}
              onMouseLeave={(e) => {
                (e.currentTarget as HTMLDivElement).style.borderColor = 'rgba(65,72,90,0.4)';
                (e.currentTarget as HTMLDivElement).style.background = 'transparent';
              }}
            >
              <span className="material-symbols-outlined mb-1.5" style={{ fontSize: 24, color: KC.onSurfaceVariant }}>file_upload</span>
              <p className="font-mono text-xs" style={{ color: KC.onSurface, marginBottom: 3 }}>
                Drag & drop or click to browse
              </p>
              <p className="font-mono" style={{ fontSize: 10, color: KC.onSurfaceVariant }}>
                .step .stp .iges .kicad_sch .kicad_pcb · max 100 MB
              </p>
            </div>
          </div>
          <input ref={fileInputRef} type="file" accept=".step,.stp,.iges,.igs" className="hidden" onChange={handleUpload} />
        </GlassPanel>
      )}

      {/* ═══════════════════════════════════════════
          BOTTOM-LEFT: Sessions button
      ════════════════════════════════════════════ */}
      <div style={{ position: 'absolute', bottom: 40, left: 16, zIndex: 50 }}>
        <Link to="/sessions" style={{ textDecoration: 'none' }}>
          <button
            type="button"
            className="flex items-center gap-1.5 rounded px-3"
            style={{
              height: 32,
              background: 'rgba(30,31,38,0.8)',
              backdropFilter: 'blur(16px)',
              border: `1px solid ${KC.border}`,
              fontSize: 10,
              letterSpacing: '0.08em',
              textTransform: 'uppercase',
              color: KC.onSurfaceVariant,
              cursor: 'pointer',
              fontFamily: "'Roboto Mono', monospace",
            }}
            onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.color = KC.onSurface; }}
            onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.color = KC.onSurfaceVariant; }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 14 }}>schedule</span>
            Sessions
          </button>
        </Link>
      </div>


      {/* ═══════════════════════════════════════════
          STATUS BAR — 32px pinned to bottom
      ════════════════════════════════════════════ */}
      <footer
        className="flex items-center justify-between px-4"
        style={{
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          height: 32,
          zIndex: 50,
          background: KC.statusBar,
        }}
      >
        <span
          className="font-mono uppercase"
          style={{ fontSize: 11, letterSpacing: '0.08em', color: KC.onSurfaceVariant, width: 140 }}
        >
          {statusLabel}
        </span>

        <span className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant, letterSpacing: '0.05em' }}>
          {statusCenter}
        </span>

        <div className="flex items-center gap-2" style={{ width: 140, justifyContent: 'flex-end' }}>
          {/* Was a static "Synced · live" regardless of actual fetch state —
              now reflects the real 10s twin-node poll (useTwinNodes). */}
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: '50%',
              background: isFetching ? '#f59e0b' : '#00a3e4',
              flexShrink: 0,
              display: 'inline-block',
            }}
          />
          <span className="font-mono" style={{ fontSize: 12, color: KC.onSurfaceVariant }}>
            {isFetching ? 'Syncing…' : 'Synced'}
          </span>
          {!isFetching && dataUpdatedAt > 0 && (
            <span className="font-mono" style={{ fontSize: 11, color: 'rgba(154,154,170,0.55)' }}>
              {formatRelativeTime(new Date(dataUpdatedAt).toISOString())}
            </span>
          )}
        </div>
      </footer>

    </div>
  );
}
